"""Export the regression suite and replacement boundaries as xrGPU model bundles."""

import argparse
import copy
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from engine.optimized_package import digest, sha, verify
from engine.paths import ENGINE, ROOT
from engine.qualification_campaign import HOLDOUT, SEEDS
from engine.reports import save


def cases():
    from layers import catalog
    values = copy.deepcopy(catalog("development")["cases"])
    for mode in ("none", "tanh"):
        for shape in ([1, 7, 5], [1, 32, 1536], [1, 128, 1536], [1, 256, 1536]):
            values.append(dict(id=f"qualification_gelu_{mode}_{shape[1]}_{shape[2]}", operation="gelu", kind="edge",
                               inputs=[dict(shape=shape, dtype="float32")], params=dict(approximate=mode)))
    for ci, co, groups in ((12, 15, 3), (10, 14, 2), (12, 20, 4)):
        values.append(dict(id=f"qualification_grouped_{ci}_{co}_{groups}", operation="conv2d", kind="edge",
                           inputs=[dict(shape=[1, ci, 15, 17], dtype="float32")],
                           params=dict(out_channels=co, kernel=3, padding=1, groups=groups)))
    for bias in (False, True):
        values.append(dict(id=f"qualification_linear_tails_bias{int(bias)}", operation="linear", kind="edge",
                           inputs=[dict(shape=[1, 7, 5], dtype="float32")], params=dict(out_features=9, bias=bias)))
    return values


def request(case, output_shape):
    op, shape, params = case["operation"], case["inputs"][0]["shape"], case.get("params", {})
    attributes = {}
    if op == "linear":
        attributes = dict(bias=params.get("bias", True), weight_shape=[params["out_features"], shape[-1]])
    elif op == "gelu":
        attributes = dict(approximate=params.get("approximate", "none"))
    elif op == "conv2d" and params.get("groups", 1) > 1:
        op = "grouped_conv2d"
        attributes = dict(weight_shape=[params["out_channels"], shape[1] // params["groups"], params["kernel"], params["kernel"]],
                          groups=params["groups"], bias=True, stride=[params.get("stride", 1)] * 2,
                          padding=[params.get("padding", 0)] * 2, dilation=[1, 1], transposed=False,
                          clamp=False, out_min=0.0, out_max=0.0)
    else:
        return None
    return dict(operation=op, input_shape=shape, output_shape=output_shape,
                dtype="float32", layout="texture3d", attributes=attributes)


def reuse_programs(case, source_case, source, source_manifest, package, tensors, model, destination, exporter, source_revision):
    """Reuse hash-verified programs, validating new inputs against original weights."""
    import torch
    staging = Path(tempfile.mkdtemp(prefix=".qualification-", dir=destination.parent))
    try:
        programs = {}
        for key, backend in (("cpu", "executorch_cpu"), ("vulkan", "executorch_vulkan")):
            entry = source_case["backends"][backend]
            if entry["export_status"] != "ready":
                raise ValueError("Cached program unavailable for " + case["id"])
            original = source / entry["program"]
            if sha(original) != source_manifest["files"].get(entry["program"]):
                raise ValueError("Cached program digest changed")
            target = staging / (key + ".pte")
            shutil.copyfile(original, target)
            programs[key] = dict(file=target.name, coverage=entry["coverage"], source_sha256=sha(original))
        inputs = [dict(name=f"input_{i}", shape=list(x.shape), dtype="int64" if x.dtype == torch.int64 else "float32")
                  for i, x in enumerate(tensors[0][1])]
        with torch.inference_mode():
            reference = model(*tensors[0][1]).contiguous()
        outputs = [dict(name="output_0", shape=list(reference.shape), dtype="float32")]
        fixtures = []
        for index, (seed, values) in enumerate(tensors):
            with torch.inference_mode():
                expected = model(*values).contiguous()
            fixture = dict(seed=seed, inputs=[], outputs=[])
            for key, data, specs in (("inputs", values, inputs), ("outputs", (expected,), outputs)):
                for i, (value, spec) in enumerate(zip(data, specs)):
                    filename = f"fixture.{index}.{key}.{i}.bin"
                    value.numpy().astype("<i8" if spec["dtype"] == "int64" else "<f4").tofile(staging / filename)
                    fixture[key].append(dict(file=filename, **spec))
            fixtures.append(fixture)
        manifest = dict(schema_version=2, bundle_type="xrgpu.model", model_id="xrbench/" + case["id"],
                        model_revision=source_revision, runtime_revision=package["runtime_revision"], precision="fp32",
                        inputs=inputs, outputs=outputs, programs=programs, fixtures=fixtures,
                        kernel_package={k: package[k] for k in ("package_id", "package_sha256")},
                        metadata=dict(case=case, source_suite_revision=source_revision,
                                      fixed_weight_seed=source_case.get("fixture_seed"), reused_verified_programs=True))
        manifest["files"] = {p.name: sha(p) for p in staging.iterdir()}
        manifest["bundle_sha256"] = digest(manifest)
        save(staging / "bundle.json", manifest)
        exporter.verify_bundle(staging, package["runtime_revision"])
        exporter.verify_cpu_reference(staging)
        staging.rename(destination)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def export(package_path, xrgpu_source, output, *, selected=None):
    import numpy as np
    import torch
    from layers import correctness, fixtures, module, reference, revision
    package = verify(package_path)
    xrgpu_source, output = Path(xrgpu_source).resolve(), Path(output).resolve()
    if not (xrgpu_source / "runtime/model_bundle.py").is_file():
        raise ValueError("An xrGPU checkout with the shared model exporter is required")
    sys.path.insert(0, str(xrgpu_source))
    try:
        exporter = importlib.import_module("runtime.model_bundle")
    finally:
        sys.path.remove(str(xrgpu_source))
    if Path(exporter.__file__).resolve() != xrgpu_source / "runtime/model_bundle.py":
        raise ValueError("A different runtime exporter was already imported")
    torch.set_num_threads(1)
    output.mkdir(parents=True, exist_ok=True)
    source = ENGINE / ".build/bundle"
    source_suite = json.loads((source / "suite.json").read_text()) if (source / "suite.json").is_file() else {}
    source_manifest = json.loads((source / "manifest.json").read_text()) if (source / "manifest.json").is_file() else {}
    if (source_suite.get("suite_revision") == revision() and source_manifest.get("suite_revision") == revision()
            and sha(source / "suite.json") == source_manifest.get("files", {}).get("suite.json")):
        cached = {c["id"]: c for c in source_suite["cases"]}
    else:
        cached = {}
    workloads = []
    for case in cases():
        if selected and case["id"] not in selected:
            continue
        path = output / case["id"]
        print("Qualification export " + case["id"], flush=True)
        cached_case = cached.get(case["id"]) if not path.exists() else None
        if cached_case and (cached_case["params"] != case.get("params", {}) or
                            [{k: i[k] for k in ("shape", "dtype")} for i in cached_case["inputs"]] != case["inputs"]):
            raise ValueError("Cached suite case differs from source catalog")
        weight_seed = cached_case.get("fixture_seed", source_suite.get("seed")) if cached_case else SEEDS[0]
        _, weights = fixtures(case, weight_seed)
        model = module(case, weights)
        if case["operation"] == "gelu" and case.get("params", {}).get("approximate") == "tanh":
            model = torch.nn.GELU(approximate="tanh").eval()
        tensors = []
        for seed in (*SEEDS, *HOLDOUT):
            inputs, _ = fixtures(case, seed)
            if case["operation"] == "gelu" and case.get("params", {}).get("approximate") == "tanh":
                x = inputs[0].astype(np.float64)
                expected = (.5 * x * (1 + np.tanh(np.sqrt(2 / np.pi) * (x + .044715 * x**3)))).astype(np.float32)
            else:
                expected = reference(case, inputs, weights)
            values = tuple(torch.from_numpy(x) for x in inputs)
            with torch.inference_mode():
                actual = model(*values).numpy()
            if not correctness(actual, expected)["passed"]:
                raise ValueError("Independent numerical oracle differs for " + case["id"])
            tensors.append((seed, values))
        if path.exists():
            manifest = exporter.verify_bundle(path, package["runtime_revision"])
            if manifest.get("kernel_package", {}).get("package_sha256") != package["package_sha256"]:
                raise ValueError("Existing fixture bundle uses another package; choose a new output")
        elif cached_case:
            manifest = reuse_programs(case, cached_case, source, source_manifest, package, tensors, model, path, exporter, revision())
        else:
            manifest = exporter.export_bundle(model, tensors[0][1], path, model_id="xrbench/" + case["id"],
                model_revision=revision(), kernel_package=package, metadata=dict(case=case), seed=SEEDS[0],
                additional_fixtures=tensors[1:], strict_vulkan=True)
        workloads.append(dict(id=case["id"], type="regression", bundle=str(path)))
        descriptor = request(case, manifest["outputs"][0]["shape"])
        if descriptor:
            workloads.append(dict(id="operator_" + case["id"], type="operator", bundle=str(path), request=descriptor))
    save(output / "workloads.json", workloads)
    return workloads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--xrgpu-source", type=Path, default=ROOT.parent / "xrGPU")
    parser.add_argument("--output", type=Path, default=ENGINE / ".build/qualification-fixtures")
    parser.add_argument("--case", action="append", help="Small export validation; full qualification still requires the complete suite")
    args = parser.parse_args()
    result = export(args.package, args.xrgpu_source, args.output, selected=args.case)
    print(json.dumps(dict(workloads=len(result), path=str(args.output / "workloads.json")), indent=2))


if __name__ == "__main__":
    main()
