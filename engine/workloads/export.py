import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
from pathlib import Path

from engine.workloads import catalog, correctness, fixtures, module, reference, revision
from engine.reports import save
from engine.paths import BUILD


def export_program(layer, inputs, destination, backend="executorch_vulkan"):
    from engine.backends import get_backend
    return get_backend(backend).export(layer, inputs, destination)


def export_suite(output):
    import torch
    from executorch.exir._serialize._flatbuffer import _get_flatc_path
    flatc = Path(__file__).resolve().parents[2] / "artifacts/toolchain/flatbuffers-build/Release/flatc.exe"
    if flatc.is_file():
        os.environ.setdefault("FLATC_EXECUTABLE", str(flatc))
    try:
        subprocess.run([_get_flatc_path(), "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("FlatBuffers compiler unavailable. Run python -m engine.prepare first, or set FLATC_EXECUTABLE.") from error
    torch.set_num_threads(1)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    spec = catalog("development")
    suite = dict(schema_version=2, seed=spec["seed"], precision="fp32", suite_revision=revision(),
                 atol=spec["atol"], rtol=spec["rtol"], reference="numpy_float64_rounded_to_fp32",
                 environment={k: importlib.metadata.version(k) for k in ("torch", "executorch", "numpy")}, cases=[])
    for case in spec["cases"]:
        print("Export " + case["id"], flush=True)
        row = json.loads(json.dumps(case))
        inputs, weights = fixtures(case)
        expected = reference(case, inputs, weights)
        layer = module(case, weights)
        tensors = tuple(torch.from_numpy(x) for x in inputs)
        with torch.inference_mode():
            check = correctness(layer(*tensors).numpy(), expected)
        if not check["passed"]: raise ValueError(f"Reference mismatch: {case['id']}: {check}")
        row["fixture_seed"] = case.get("fixture_seed",spec["seed"])
        row.update(output_shape=list(expected.shape), expected=case["id"] + ".expected.bin")
        expected.astype("<f4").tofile(output / row["expected"])
        for i, (value, entry) in enumerate(zip(inputs, row["inputs"])):
            entry["file"] = f"{case['id']}.input{i}.bin"
            value.astype("<i8" if entry["dtype"] == "int64" else "<f4").tofile(output / entry["file"])
        row["backends"] = {}
        for backend in ("executorch_vulkan", "executorch_cpu"):
            target = output / (case["id"] + (".cpu" if backend == "executorch_cpu" else "") + ".pte")
            target.unlink(missing_ok=True)
            try:
                coverage = export_program(layer, tensors, target, backend)
                entry = dict(export_status="ready", program=target.name, coverage=coverage)
            except Exception as error:
                target.unlink(missing_ok=True)
                entry = dict(export_status="unsupported", reason=f"{type(error).__name__}: {error}")
                print(f"  {backend}: {entry['reason']}", flush=True)
            row["backends"][backend] = entry
        # Retain the original Vulkan fields for existing consumers and saved fixtures.
        row.update(row["backends"]["executorch_vulkan"])
        suite["cases"].append(row)
    save(output / "suite.json", suite)
    paths = {"suite.json"}
    for case in suite["cases"]:
        paths.add(case["expected"])
        paths.update(x["file"] for x in case["inputs"])
        for entry in case["backends"].values():
            if entry["export_status"] == "ready": paths.add(entry["program"])
    save(output / "manifest.json", {"suite_revision":suite["suite_revision"], "files":{
        name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in sorted(paths)}})
    return suite


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Export the standalone native layer bundle with strict Vulkan coverage checks.')
    parser.add_argument("--output",type=Path, default=BUILD / "bundle")
    args = parser.parse_args()
    suite = export_suite(args.output)
    print(f"Exported {sum(c['export_status']=='ready' for c in suite['cases'])}/{len(suite['cases'])} cases")
