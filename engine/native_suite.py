"""Build typed, independent fixtures from a verified xrGPU native release.

This runs in the release pipeline. Visitors receive bytes in the signed APK;
neither Python references nor source downloads execute on their computers.
"""

from contextlib import contextmanager
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np

from engine.operators import verify_package, contained, tensor_spec
from engine.reports import canonical, digest


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def references(package):
    source = Path(package) / "source"
    previous = {k: v for k, v in sys.modules.items() if k == "kernels" or k.startswith("kernels.")}
    for key in previous:
        del sys.modules[key]
    sys.path.insert(0, str(source))
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        module = importlib.import_module("kernels.catalog")
        if not Path(module.__file__).resolve().is_relative_to(source.resolve()):
            raise ValueError("Native references must come from the verified package")
        yield module
    finally:
        sys.dont_write_bytecode = previous_bytecode
        sys.path.remove(str(source))
        for key in list(sys.modules):
            if key == "kernels" or key.startswith("kernels."):
                del sys.modules[key]
        sys.modules.update(previous)


def _legacy(row, helpers):
    dtype = "float32" if "float32" in row["dtypes"] else "int32"
    name = row["id"].split(".")[-1]
    shapes = [(2, 1, 19), (1, 3, 19)] if row["kind"] == "Binary" else [(67,)]
    if name == "MatMul":
        shapes = [(2, 1, 3, 5), (1, 2, 5, 7)]
    elif name == "PRelu":
        shapes = [(2, 3, 19), (1, 3, 1)]
    arrays = [
        helpers.values(
            dtype,
            shape,
            divisor=name == "Div" and i == 1,
            positive=name in {"Sqrt", "Log", "Reciprocal"},
        )
        for i, shape in enumerate(shapes)
    ]
    if name == "Acosh":
        arrays[0] += np.asarray(1.0, dtype=dtype)
    with np.errstate(all="ignore"):
        expected = helpers.reference(name, arrays)
    return dict(
        operation=row["id"],
        inputs=dict(zip(row["input_names"], arrays)),
        expected={row["output_name"]: expected},
        attrs={},
        args={},
        comparisons={row["output_name"]: dict(atol=2e-5, rtol=2e-5, allow_nan=True)},
    )


def _tensor(root, relative, name, value, descriptor=None):
    array = np.ascontiguousarray(value) if np.asarray(value).ndim else np.asarray(value)
    descriptor = descriptor or {}
    dtype = array.dtype.newbyteorder("<")
    array = array.astype(dtype, copy=False)
    spec = dict(
        name=name,
        dtype=descriptor.get("dtype", dtype.name),
        storage_dtype=dtype.name,
        shape=list(array.shape),
        file=relative,
        layout="contiguous",
    )
    tensor_spec(dict(spec, dtype=spec["storage_dtype"]))
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(array.tobytes(order="C"))
    return dict(spec, sha256=sha(path), bytes=path.stat().st_size)


def _metadata(root, identity, side, values):
    result = {}
    for index, (key, value) in enumerate(values.items()):
        if isinstance(value, dict) and {"dtype", "shape"}.issubset(value):
            data = value.get("values", value.get("data"))
            array = np.asarray(data, dtype=value.get("storage_dtype", value["dtype"])).reshape(
                value["shape"]
            )
            result[key] = _tensor(root, f"cases/{identity}/{side}-{index}.bin", key, array, value)
        else:
            result[key] = value
    return result


def prepare(package, output):
    package, output = Path(package).absolute(), Path(output).absolute()
    manifest = verify_package(package)
    if output.exists():
        raise FileExistsError("Choose a fresh native fixture output")
    for name in (
        "include/xrgpu/extended_operators.h",
        "lib/android-arm64/libxrgpu_extended_operators.so",
    ):
        if name not in manifest["files"]:
            raise ValueError("The complete typed native xrGPU release is required")
    source_catalog = manifest.get("source", {}).get("native_catalog_path")
    if source_catalog != "source/kernels/catalog.json":
        raise ValueError("Expected the native kernels-layout source release")
    output.mkdir(parents=True)
    try:
        with references(package) as native:
            catalog = native.load(package / "source/kernels")
            legacy = importlib.import_module("kernels._shared.verify_local")
            tensor = importlib.import_module("kernels._shared.extended.tensor_reference")
            spatial = importlib.import_module("kernels._shared.extended.verify_spatial")
            complex_ = importlib.import_module("kernels._shared.extended.complex_reference")
            representatives = {c["operation"]: c for c in tensor.representatives()}
            for case in spatial.cases():
                representatives.setdefault(case["operation"], case)
            representatives.update({c["operation"]: c for c in complex_.representatives()})
            for row in native.legacy_records(package / "source/kernels"):
                representatives[row["id"]] = _legacy(row, legacy)
            if set(representatives) != {e["id"] for e in catalog["entries"]}:
                raise ValueError("Every frozen operator needs a real executable fixture")
            cases = []
            for entry in catalog["entries"]:
                case = representatives[entry["id"]]
                raw_inputs = case["inputs"]
                if isinstance(raw_inputs, list):
                    raw_inputs = {
                        v["name"]: np.asarray(
                            v["values"], dtype=v.get("storage_dtype", v["dtype"])
                        ).reshape(v["shape"])
                        for v in raw_inputs
                    }
                inputs = [
                    _tensor(
                        output,
                        f"cases/{entry['id']}/input-{i}.bin",
                        name,
                        array,
                        case.get("input_descriptors", {}).get(name),
                    )
                    for i, (name, array) in enumerate(raw_inputs.items())
                ]
                outputs = []
                for i, (name, array) in enumerate(case["expected"].items()):
                    spec = _tensor(
                        output,
                        f"cases/{entry['id']}/expected-{i}.bin",
                        name,
                        array,
                        case.get("output_descriptors", {}).get(name),
                    )
                    rule = case.get("comparisons", {}).get(name, {})
                    spec["comparison"] = dict(
                        atol=rule.get("atol", case.get("atol", 2e-5)),
                        rtol=rule.get("rtol", case.get("rtol", 2e-5)),
                        equal_nan=rule.get("allow_nan", True),
                    )
                    outputs.append(spec)
                contract = json.loads(
                    contained(package, "source/kernels/" + entry["contract"]).read_text()
                )
                cases.append(
                    dict(
                        id=entry["id"],
                        operation=entry["id"],
                        native_api=entry["native"]["api"],
                        inputs=inputs,
                        outputs=outputs,
                        attrs=_metadata(output, entry["id"], "attrs", case["attrs"]),
                        args=_metadata(output, entry["id"], "args", case["args"]),
                        contract_revision=entry["provenance"]["revision"],
                        contract_sha256=entry["provenance"]["files"][
                            entry["provenance"]["contract"]
                        ],
                        supported_types=contract["type_constraints"],
                        oracle=case.get("oracle", "independent NumPy reference"),
                        shaders=[v["output"] for v in entry["variants"]],
                    )
                )
            runtime = output / "runtime"
            runtime_files = {}
            for name, expected in manifest["files"].items():
                if name.startswith("shaders/") or name in {
                    "LICENSE",
                    "NOTICE",
                    "THIRD_PARTY_NOTICES.md",
                }:
                    target = runtime / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(contained(package, name), target)
                    runtime_files[name] = expected
            value = dict(
                schema_version=1,
                kind="xrbench.native-operator-suite",
                operator_count=221,
                source_package_sha256=manifest["package_sha256"],
                native_catalog_sha256=catalog["catalog_sha256"],
                source_catalog_revision="v1",
                runtime_files=runtime_files,
                cases=cases,
                physical_qualification=False,
                full_contract_conformance=False,
                scope="One independent representative per frozen operator; no CPU numerical fallback",
            )
            value["suite_revision"] = digest(value)
            (output / "suite.json").write_bytes(canonical(value) + b"\n")
            files = {
                p.relative_to(output).as_posix(): sha(p)
                for p in sorted(output.rglob("*"))
                if p.is_file()
            }
            (output / "inventory.json").write_bytes(
                canonical(dict(schema_version=1, files=files)) + b"\n"
            )
            return value
    except BaseException:
        shutil.rmtree(output)
        raise
