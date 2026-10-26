"""Typed operator fixtures and native Android Vulkan benchmark execution.

The standalone runner consumes a verified local kernel package. These commands
never download artifacts or run remote Python code.
Conformance evidence for an operator does not qualify a model or an XR session.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import uuid

import numpy as np

from engine.adb import ADB
from engine.paths import ROOT
from engine.reports import digest, save, summarize, validate

DTYPES = {
    "float16": np.dtype("<f2"), "float32": np.dtype("<f4"), "float64": np.dtype("<f8"),
    "int8": np.dtype("i1"), "int16": np.dtype("<i2"), "int32": np.dtype("<i4"),
    "int64": np.dtype("<i8"), "uint8": np.dtype("u1"), "uint16": np.dtype("<u2"),
    "uint32": np.dtype("<u4"), "uint64": np.dtype("<u8"), "bool": np.dtype("?"),
}
MAX_BYTES = 64 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contained(root, name):
    root = Path(root).absolute()
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./+-]+", name)
            or PurePosixPath(name).is_absolute()
            or any(p in ("", ".", "..") for p in name.split("/"))):
        raise ValueError("Invalid operator artifact path")
    path = root
    if root.is_symlink():
        raise ValueError("Operator artifact links are forbidden")
    for part in name.split("/"):
        path /= part
        if path.is_symlink():
            raise ValueError("Operator artifact links are forbidden")
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing operator artifact: " + name)
    return path


def verify_package(directory):
    """Verify an inventoried native package before loading its shared library."""
    root = Path(directory).absolute()
    manifest = json.loads(contained(root, "manifest.json").read_text())
    if (manifest.get("schema_version") != 1
            or manifest.get("package_id") != "xrgpu.native-operators.v1"
            or manifest.get("package_sha256") != digest({k: v for k, v in manifest.items() if k != "package_sha256"})):
        raise ValueError("Native operator package identity or digest mismatch")
    files = manifest.get("files")
    required = {"include/xrgpu/operators.h", "lib/android-arm64/libxrgpu_operators.so",
                "lib/android-arm64/libc++_shared.so", "LICENSE"}
    if not isinstance(files, dict) or not required.issubset(files):
        raise ValueError("Native operator package is missing its library, interface or license")
    if len({n.casefold() for n in files}) != len(files):
        raise ValueError("Ambiguous native operator package paths")
    for name, expected in files.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid native operator file digest")
        if sha(contained(root, name)) != expected:
            raise ValueError("Native operator package file digest mismatch: " + name)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Native operator package links are forbidden")
        if path.is_file() and path.relative_to(root).as_posix() not in {*files, "manifest.json"}:
            raise ValueError("Uninventoried native operator package file")
    return manifest


def tensor_spec(spec):
    """Validate typed storage without coercing integers through floating point."""
    dtype = DTYPES.get(spec.get("dtype"))
    shape = spec.get("shape")
    if dtype is None or not isinstance(shape, list) or len(shape) > 16:
        raise ValueError("Invalid typed tensor descriptor")
    count = 1
    for size in shape:
        if type(size) is not int or size < 0 or size > MAX_BYTES:
            raise ValueError("Invalid typed tensor dimension")
        count *= size
        if count * dtype.itemsize > MAX_BYTES:
            raise ValueError("Operator tensor exceeds memory limit")
    return dtype, tuple(shape), count * dtype.itemsize


def read_tensor(root, spec, *, verify_digest=True):
    dtype, shape, size = tensor_spec(spec)
    path = contained(root, spec["file"])
    if path.stat().st_size != size or (verify_digest and sha(path) != spec.get("sha256")):
        raise ValueError("Typed tensor byte length or digest mismatch")
    return np.frombuffer(path.read_bytes(), dtype=dtype).reshape(shape)


def compare(actual, expected, *, atol=1e-4, rtol=1e-4, equal_nan=False):
    """Compare exact shapes/types, exact integers, and declared float tolerances."""
    a, b = np.asarray(actual), np.asarray(expected)
    if a.shape != b.shape or a.dtype != b.dtype:
        return dict(passed=False, reason="Output shape or dtype mismatch")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (atol, rtol)):
        raise ValueError("Invalid comparison tolerance")
    if a.dtype.kind not in "fc":
        return dict(passed=bool(np.array_equal(a, b)), comparison="exact")
    same_nonfinite = (a == b) | (np.isnan(a) & np.isnan(b) if equal_nan else False)
    finite = np.isfinite(a) & np.isfinite(b)
    error = np.abs(a[finite].astype(np.float64) - b[finite].astype(np.float64))
    tolerance = atol + rtol * np.abs(b[finite].astype(np.float64))
    passed = bool(np.all(finite | same_nonfinite) and np.all(error <= tolerance))
    return dict(passed=passed, comparison="absolute_relative", atol=atol, rtol=rtol,
                equal_nan=equal_nan, max_abs_error=float(error.max()) if error.size else 0.0)


def _write_tensor(root, name, value):
    value = np.asarray(value)
    dtype = next((k for k, v in DTYPES.items() if v == value.dtype), None)
    if dtype is None:
        raise ValueError("Unsupported fixture dtype")
    spec = dict(name=name, shape=list(value.shape), dtype=dtype, file=name + ".bin")
    tensor_spec(spec)
    (root / spec["file"]).write_bytes(value.tobytes(order="C"))
    spec["sha256"] = sha(root / spec["file"])
    return spec


def fixtures(seed=20261026):
    """Independent NumPy references for the first implemented native contracts."""
    rng = np.random.default_rng(seed)
    def random(shape):
        return rng.uniform(-2, 2, shape).astype("<f4")
    for name, op, shapes in (
        ("add_broadcast", "Add", ((2, 3), (3,))),
        ("relu_signed", "Relu", ((2, 7),)),
        ("matmul_tails", "MatMul", ((7, 5), (5, 9))),
    ):
        inputs = [random(shape) for shape in shapes]
        if op == "Add":
            expected = inputs[0].astype(np.float64) + inputs[1].astype(np.float64)
        elif op == "Relu":
            inputs[0].flat[0] = -0.0
            inputs[0].flat[1] = 0.0
            expected = np.maximum(inputs[0].astype(np.float64), 0)
        else:
            expected = inputs[0].astype(np.float64) @ inputs[1].astype(np.float64)
        yield name, "ai.onnx." + op, inputs, np.asarray(expected, dtype="<f4")
    # Exercise the alpha's other implemented storage paths with exact small
    # values. Half-precision matrix products here are exactly representable, so
    # the native precheck need not reinterpret half bytes as host floats.
    for op, dtypes in (
        ("Add", ("float16", "int32", "uint32", "int8", "uint8")),
        ("Relu", ("float16", "int32", "int16", "int8")),
        ("MatMul", ("float16", "int32", "uint32")),
    ):
        for dtype in dtypes:
            shape = (3, 5) if op == "MatMul" else (7,)
            low = 0 if dtype.startswith("uint") else -4
            first = rng.integers(low, 5, shape).astype(DTYPES[dtype])
            inputs = [first]
            if op == "Add":
                second = np.array([1], dtype=DTYPES[dtype])
                if first.dtype.kind in "iu":
                    first.flat[0] = np.iinfo(first.dtype).max
                inputs.append(second)
                expected = first + second
            elif op == "Relu":
                expected = np.maximum(first, 0).astype(first.dtype)
            else:
                inputs.append(rng.integers(low, 5, (5, 7)).astype(DTYPES[dtype]))
                expected = inputs[0] @ inputs[1]
            yield f"{op.lower()}_{dtype}", "ai.onnx." + op, inputs, expected
    for name, op, shapes in (
        ("add_scalars", "Add", ((), ())),
        ("add_empty", "Add", ((0, 3), (1, 3))),
        ("relu_empty", "Relu", ((0, 3),)),
        ("matmul_vectors", "MatMul", ((5,), (5,))),
        ("matmul_batched", "MatMul", ((2, 1, 3, 5), (1, 4, 5, 7))),
        ("matmul_zero_inner", "MatMul", ((3, 0), (0, 7))),
    ):
        inputs = [random(shape) for shape in shapes]
        if op == "Add":
            expected = inputs[0].astype(np.float64) + inputs[1].astype(np.float64)
        elif op == "Relu":
            expected = np.maximum(inputs[0].astype(np.float64), 0)
        else:
            expected = inputs[0].astype(np.float64) @ inputs[1].astype(np.float64)
        yield name, "ai.onnx." + op, inputs, np.asarray(expected, dtype="<f4")


def prepare(output, package, seed=20261026):
    """Prepare fixtures bound to a verified native package; no remote execution."""
    manifest = verify_package(package)
    requested = {op for _, op, _, _ in fixtures(seed)}
    if not requested.issubset(manifest.get("operations", [])):
        raise ValueError("This native package does not implement the starter fixture operators")
    output = Path(output).absolute()
    if output.exists():
        raise FileExistsError("Choose a new operator fixture directory")
    output.mkdir(parents=True)
    cases = []
    for name, op, inputs, expected in fixtures(seed):
        path = output / name
        path.mkdir()
        input_names = ["x"] if op == "ai.onnx.Relu" else ["a", "b"]
        output_name = "c" if op == "ai.onnx.Add" else "y"
        cases.append(dict(id=name, operator=op, attributes={},
                          inputs=[_write_tensor(path, n, x) for n, x in zip(input_names, inputs)],
                          outputs=[_write_tensor(path, output_name, expected)],
                          comparison=dict(atol=1e-4, rtol=1e-4, equal_nan=False)))
    suite = dict(schema_version=1, kind="xrbench.operator-suite", seed=seed,
                 kernel_package=manifest, cases=cases)
    suite["suite_revision"] = digest(suite)
    save(output / "suite.json", suite)
    return suite


def verify_suite(root):
    root = Path(root).absolute()
    suite = json.loads(contained(root, "suite.json").read_text())
    if (suite.get("kind") != "xrbench.operator-suite" or suite.get("schema_version") != 1
            or suite.get("suite_revision") != digest({k: v for k, v in suite.items() if k != "suite_revision"})):
        raise ValueError("Operator fixture suite identity mismatch")
    ids = set()
    for case in suite["cases"]:
        name = case["id"]
        if not IDENTIFIER.fullmatch(name) or name in (".", "..") or name in ids:
            raise ValueError("Invalid or duplicate operator fixture identity")
        ids.add(name)
        for spec in (*case["inputs"], *case["outputs"]):
            if spec is not None:
                read_tensor(root / name, spec)
    return suite


def build(package, output, *, jobs=2, ndk=None):
    """Link the standalone Android runner to the package's inventoried library."""
    manifest = verify_package(package)
    package, output = Path(package).resolve(), Path(output).absolute()
    pins = json.loads((ROOT / "dependencies.json").read_text())
    ndk = Path(ndk) if ndk else ROOT / "artifacts/toolchain/android-sdk/ndk" / pins["android"]["ndk"]
    if not (ndk / "build/cmake/android.toolchain.cmake").is_file():
        raise FileNotFoundError("Android NDK missing; prepare the pinned Android toolchain")
    json_include = ROOT / "artifacts/deps/executorch/third-party/json/single_include"
    if not (json_include / "nlohmann/json.hpp").is_file():
        raise FileNotFoundError("Pinned native JSON headers missing; run python -m engine.prepare")
    output.mkdir(parents=True, exist_ok=True)
    native = output / "native"
    cmake = shutil.which("cmake")
    if cmake is None:
        raise FileNotFoundError("CMake required for the Android operator runner")
    subprocess.run([cmake, "-S", str(ROOT / "engine/operator_native"), "-B", str(native),
                    "-DXRGPU_OPERATOR_PACKAGE=" + str(package),
                    "-DNLOHMANN_JSON_INCLUDE=" + str(json_include),
                    "-DCMAKE_TOOLCHAIN_FILE=" + str(ndk / "build/cmake/android.toolchain.cmake"),
                    "-DANDROID_ABI=arm64-v8a", "-DANDROID_PLATFORM=android-29",
                    "-DANDROID_STL=c++_shared", "-DCMAKE_BUILD_TYPE=Release"], check=True)
    subprocess.run([cmake, "--build", str(native), "-j", str(jobs)], check=True)
    runner = output / "xrbench-operators"
    shutil.copy2(native / runner.name, runner)
    shutil.copy2(contained(package, "lib/android-arm64/libxrgpu_operators.so"), output)
    cxx = contained(package, "lib/android-arm64/libc++_shared.so")
    shutil.copy2(cxx, output)
    metadata = dict(schema_version=1, kind="xrbench.operator-runner", kernel_package=manifest,
                    files={p.name: sha(p) for p in (runner, output / "libxrgpu_operators.so", output / cxx.name)})
    save(output / "build.json", metadata)
    return metadata


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def assemble_report(suite_path, evidence_path, *, run_id, started_at, finished_at, target="unknown"):
    """Validate native evidence and independent output comparisons before reporting."""
    suite_path, evidence_path = Path(suite_path), Path(evidence_path)
    suite = verify_suite(suite_path)
    native = json.loads(contained(evidence_path, "native.json").read_text())
    if native.get("kind") != "xrbench.operator-evidence" or native.get("suite_revision") != suite["suite_revision"]:
        raise ValueError("Native operator evidence identity mismatch")
    if native.get("backend") != "xrgpu_vulkan":
        raise ValueError("Native operator evidence requires Vulkan execution")
    cases = {c["id"]: c for c in suite["cases"]}
    rows, seen = [], set()
    for result in native.get("results", []):
        name = result.get("case")
        if name not in cases or name in seen:
            raise ValueError("Native evidence contains an unknown or duplicate case")
        seen.add(name)
        case = cases[name]
        row = dict(case=name, operation=case["operator"], phase="isolated", trial=0,
                   inputs=case["inputs"], outputs=case["outputs"], attributes=case["attributes"],
                   status="interrupted", samples=result.get("samples", []),
                   operator_info=result.get("operator_info", {}),
                   timing_scope="Native invocation + Vulkan submission and wait; prepare and output readback excluded")
        if result.get("status") == "skipped":
            row.update(status="skipped", reason=result.get("reason", "Native capability unavailable"))
        elif result.get("status") == "complete":
            checks = {}
            for phase in ("before", "after"):
                actual_specs = result.get("outputs_" + phase, [])
                if len(actual_specs) != len(case["outputs"]):
                    checks[phase] = dict(passed=False, reason="Output count mismatch")
                    continue
                values = []
                for actual, expected in zip(actual_specs, case["outputs"]):
                    if actual.get("name") != expected.get("name"):
                        values.append(dict(passed=False, reason="Output name mismatch"))
                    else:
                        values.append(compare(read_tensor(evidence_path / name, actual, verify_digest=False),
                                              read_tensor(suite_path / name, expected), **case["comparison"]))
                checks[phase] = dict(passed=all(v["passed"] for v in values), outputs=values)
            row.update(status="passed" if all(v["passed"] for v in checks.values()) else "failed",
                       correctness=checks["after"], correctness_before=checks["before"], correctness_after=checks["after"])
        elif result.get("status") == "failed":
            row.update(status="failed", reason=result.get("reason", "Native operator failed"), correctness=dict(passed=False))
        rows.append(row)
    if native.get("status") == "complete" and seen != set(cases):
        raise ValueError("Complete native evidence omitted fixture cases")
    status = "canceled" if native.get("status") == "canceled" else "failed" if native.get("status") != "complete" or any(r["status"] == "failed" for r in rows) else "complete"
    report = dict(schema_version=2, run_id=run_id, status=status, backend="xrgpu_vulkan",
                  source="native_android", simulated=False, execution_location=target,
                  started_at=started_at, finished_at=finished_at, device=native.get("device", {}),
                  suite_revision=suite["suite_revision"], runtime_revision=suite["kernel_package"]["package_sha256"],
                  kernel_package=suite["kernel_package"],
                  configuration=dict(mode="isolated", preset="smoke", backend="xrgpu_vulkan", execution_target=target),
                  qualification="operator conformance only; hardware, model and XR qualification remain separate",
                  results=rows)
    return summarize(validate(report))


def run(suite_path, package, runner_path, output, *, serial=None, adb=None, samples=20,
        warmups=5, timeout=60, target="unknown"):
    """Execute locally prepared artifacts through ADB; retain partial evidence."""
    if not 1 <= samples <= 1000 or not 0 <= warmups <= 100 or not 5 <= timeout <= 300:
        raise ValueError("Use 1–1000 samples, 0–100 warmups, and a 5–300 second runtime limit")
    suite_path, package, runner_path, output = map(lambda p: Path(p).absolute(), (suite_path, package, runner_path, output))
    suite, manifest = verify_suite(suite_path), verify_package(package)
    if suite["kernel_package"] != manifest:
        raise ValueError("Fixture suite requires a different native package")
    metadata = json.loads(contained(runner_path, "build.json").read_text())
    if metadata.get("kernel_package") != manifest or metadata.get("kind") != "xrbench.operator-runner":
        raise ValueError("Runner was built against another native package")
    for name, expected in metadata["files"].items():
        if sha(contained(runner_path, name)) != expected:
            raise ValueError("Operator runner build inventory changed")
    if output.exists():
        raise FileExistsError("Choose a new evidence output directory")
    transport = adb or ADB()
    devices = [d for d in transport.devices()["devices"] if d["state"] == "device" and d.get("supported")]
    if serial is None and len(devices) == 1:
        serial = devices[0]["serial"]
    if serial not in {d["serial"] for d in devices}:
        raise ValueError("Select one authorized Android 10+ arm64-v8a device")
    output.mkdir(parents=True)
    run_id = "run-" + uuid.uuid4().hex
    remote = "/data/local/tmp/xrbench-operators/" + run_id
    transport.shell(serial, "mkdir", "-p", remote)
    for local, name in ((suite_path, "suite"), (package, "package")):
        transport.command("push", str(local), remote + "/" + name, serial=serial, timeout=120)
    for name in metadata["files"]:
        transport.command("push", str(runner_path / name), remote + "/" + name, serial=serial, timeout=30)
    job = dict(schema_version=1, kind="xrbench.operator-job", suite=remote + "/suite",
               package=remote + "/package", output=remote + "/evidence", warmups=warmups,
               samples=samples, timeout_seconds=timeout, profiling=True)
    save(output / "job.json", job)
    transport.command("push", str(output / "job.json"), remote + "/job.json", serial=serial)
    transport.shell(serial, "chmod", "700", remote + "/xrbench-operators")
    started = _utc()
    save(output / "receipt.json", dict(run_id=run_id, remote_evidence=remote + "/evidence", started_at=started))
    error = None
    try:
        transport.shell(serial, "env", "LD_LIBRARY_PATH=" + remote, remote + "/xrbench-operators", remote + "/job.json", timeout=timeout + 15)
    except (RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt) as caught:
        error = caught
        try:
            transport.shell(serial, "touch", remote + "/evidence/cancel")
        except (RuntimeError, OSError):
            pass
    finally:
        save(output / "receipt.json", dict(run_id=run_id, remote_evidence=remote + "/evidence", started_at=started,
                                            finished_at=_utc(), transport_error=type(error).__name__ if error else None))
        try:
            transport.command("pull", remote + "/evidence", str(output / "evidence"), serial=serial, timeout=30)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            raise RuntimeError("Native evidence could not be retrieved; its device path is preserved in receipt.json") from None
    report = assemble_report(suite_path, output / "evidence", run_id=run_id, started_at=started, finished_at=_utc(), target=target)
    save(output / "report.json", report)
    return report


def cancel(receipt, serial=None, adb=None):
    """Request cooperative cancellation while leaving device evidence in place."""
    value = json.loads(Path(receipt).read_text())
    run_id = value.get("run_id", "")
    if not re.fullmatch(r"run-[0-9a-f]{32}", run_id):
        raise ValueError("Invalid operator run receipt")
    expected = "/data/local/tmp/xrbench-operators/" + run_id + "/evidence"
    if value.get("remote_evidence") != expected:
        raise ValueError("Invalid operator evidence destination")
    transport = adb or ADB()
    devices = [d for d in transport.devices()["devices"] if d["state"] == "device"]
    if serial is None and len(devices) == 1:
        serial = devices[0]["serial"]
    if serial not in {d["serial"] for d in devices}:
        raise ValueError("Select one authorized Android device")
    transport.shell(serial, "mkdir", "-p", expected)
    transport.shell(serial, "touch", expected + "/cancel")
    return dict(run_id=run_id, cancellation="requested", remote_evidence=expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="Create typed independent reference fixtures")
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=int, default=20261026)
    p = commands.add_parser("verify", help="Verify a local fixture suite")
    p.add_argument("--suite", type=Path, required=True)
    p = commands.add_parser("build", help="Build the typed Android native runner")
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--jobs", type=int, choices=range(1, 9), default=2)
    p = commands.add_parser("run", help="Run verified operators on an authorized Android device")
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--suite", type=Path, required=True)
    p.add_argument("--runner", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--serial")
    p.add_argument("--samples", type=int, default=20)
    p.add_argument("--warmups", type=int, default=5)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--execution-target", choices=("unknown", "headset", "glasses", "phone", "compute_puck"), default="unknown")
    p = commands.add_parser("cancel", help="Cooperatively stop a run and preserve evidence")
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--serial")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.output, args.package, args.seed)
        elif args.command == "verify":
            result = verify_suite(args.suite)
        elif args.command == "build":
            result = build(args.package, args.output, jobs=args.jobs)
        elif args.command == "run":
            result = run(args.suite, args.package, args.runner, args.output, serial=args.serial,
                         samples=args.samples, warmups=args.warmups, timeout=args.timeout, target=args.execution_target)
        else:
            result = cancel(args.receipt, args.serial)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, indent=2))
    if args.command == "run" and result["status"] != "complete":
        parser.exit(1)


if __name__ == "__main__":
    main()
