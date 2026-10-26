import copy
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from engine import operators
from engine.backends import BACKENDS, get_backend
from engine.reports import digest, save, validate
from engine.paths import ROOT


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    files = {}
    for name in ("include/xrgpu/operators.h", "lib/android-arm64/libxrgpu_operators.so",
                 "lib/android-arm64/libc++_shared.so", "LICENSE"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic inventory fixture")
        files[name] = operators.sha(target)
    manifest = dict(schema_version=1, package_id="xrgpu.native-operators.v1",
                    operations=["ai.onnx.Add", "ai.onnx.Relu", "ai.onnx.MatMul"], files=files)
    manifest["package_sha256"] = digest(manifest)
    save(root / "manifest.json", manifest)
    return root


def test_typed_fixtures_are_reproducible_independent_and_verified(package, tmp_path):
    first = operators.prepare(tmp_path / "first", package)
    second = operators.prepare(tmp_path / "second", package)
    assert first == second
    assert operators.verify_suite(tmp_path / "first") == first
    assert {c["operator"] for c in first["cases"]} == {"ai.onnx.Add", "ai.onnx.Relu", "ai.onnx.MatMul"}
    matmul = next(c for c in first["cases"] if c["id"] == "matmul_tails")
    root = tmp_path / "first" / matmul["id"]
    a, b = [operators.read_tensor(root, s) for s in matmul["inputs"]]
    expected = operators.read_tensor(root, matmul["outputs"][0])
    assert expected.shape == (7, 9)
    np.testing.assert_array_equal(expected, (a.astype(np.float64) @ b.astype(np.float64)).astype(np.float32))
    (root / matmul["inputs"][0]["file"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="length or digest"):
        operators.verify_suite(tmp_path / "first")


def test_integer_comparison_never_coerces_large_values_to_float():
    a = np.array([2**63 - 1], dtype=np.int64)
    b = np.array([2**63 - 2], dtype=np.int64)
    assert not operators.compare(a, b)["passed"]
    assert operators.compare(a, a)["passed"]
    assert not operators.compare(a, a.astype(np.uint64))["passed"]


def test_float_comparison_nonfinite_and_empty_contracts():
    assert operators.compare(np.array([], np.float32), np.array([], np.float32))["passed"]
    assert operators.compare(np.array([np.inf], np.float32), np.array([np.inf], np.float32))["passed"]
    nan = np.array([np.nan], np.float32)
    assert not operators.compare(nan, nan)["passed"]
    assert operators.compare(nan, nan, equal_nan=True)["passed"]
    assert not operators.compare(np.array([1.], np.float32), np.array([2.], np.float32))["passed"]
    with pytest.raises(ValueError, match="tolerance"):
        operators.compare(np.array([1.]), np.array([1.]), atol=-1)


def test_tensor_descriptors_support_scalars_empty_and_typed_bytes():
    assert operators.tensor_spec(dict(dtype="int64", shape=[]))[2] == 8
    assert operators.tensor_spec(dict(dtype="float16", shape=[2, 0, 3]))[2] == 0
    for shape in ([True], [-1], [2**30], [4096, 4096, 2]):
        with pytest.raises(ValueError):
            operators.tensor_spec(dict(dtype="float32", shape=shape))
    with pytest.raises(ValueError):
        operators.tensor_spec(dict(dtype="invented", shape=[1]))


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a//b", "a/./b", "a\\b"])
def test_fixture_paths_cannot_escape(tmp_path, name):
    with pytest.raises(ValueError, match="path"):
        operators.contained(tmp_path, name)


def test_package_inventory_prevents_native_code_substitution(package):
    operators.verify_package(package)
    library = package / "lib/android-arm64/libxrgpu_operators.so"
    library.write_bytes(b"substituted native library")
    with pytest.raises(ValueError, match="digest mismatch"):
        operators.verify_package(package)


def test_package_rejects_uninventoried_code_and_links(package):
    (package / "unexpected.py").write_text("ignored executable")
    with pytest.raises(ValueError, match="Uninventoried"):
        operators.verify_package(package)
    (package / "unexpected.py").unlink()
    header = package / "include/xrgpu/operators.h"
    contents = header.read_bytes()
    header.unlink()
    outside = package.parent / "outside.h"
    outside.write_bytes(contents)
    header.symlink_to(outside)
    with pytest.raises(ValueError, match="links"):
        operators.verify_package(package)


def evidence(suite_root, target, *, corrupted=False):
    suite = operators.verify_suite(suite_root)
    target.mkdir()
    rows = []
    for case in suite["cases"]:
        path = target / case["id"]
        path.mkdir()
        expected = operators.read_tensor(suite_root / case["id"], case["outputs"][0])
        if corrupted:
            expected = expected + 10
        outputs = [operators._write_tensor(path, case["outputs"][0]["name"], expected)]
        rows.append(dict(case=case["id"], status="complete", outputs_before=outputs, outputs_after=outputs,
                         samples=[dict(execution_ms=.2, gpu_ms=None)], operator_info=dict(backend="vulkan")))
    native = dict(kind="xrbench.operator-evidence", suite_revision=suite["suite_revision"], backend="xrgpu_vulkan",
                  status="complete", device=dict(gpu="Fixture only"), results=rows)
    save(target / "native.json", native)
    return native


def test_native_evidence_report_requires_independent_output_checks(package, tmp_path):
    suite_root = tmp_path / "suite"
    operators.prepare(suite_root, package)
    evidence(suite_root, tmp_path / "evidence")
    kwargs = dict(run_id="run-" + "a" * 32, started_at="2026-10-26T00:00:00Z", finished_at="2026-10-26T00:00:01Z")
    report = operators.assemble_report(suite_root, tmp_path / "evidence", **kwargs)
    count = len(operators.verify_suite(suite_root)["cases"])
    assert report["status"] == "complete" and report["counts"]["passed"] == count
    assert validate(report)["backend"] == "xrgpu_vulkan"
    assert all(row["timing_valid"] for row in report["results"])
    evidence(suite_root, tmp_path / "wrong", corrupted=True)
    report = operators.assemble_report(suite_root, tmp_path / "wrong", **kwargs)
    # Empty outputs remain equal even when the test deliberately adds ten.
    empty = sum(np.prod(c["outputs"][0]["shape"]) == 0 for c in operators.verify_suite(suite_root)["cases"])
    assert report["status"] == "failed" and report["counts"]["failed"] == count - empty
    assert all(not row["timing_valid"] for row in report["results"] if row["status"] == "failed")


def test_native_evidence_cannot_claim_cpu_fallback_or_omit_cases(package, tmp_path):
    suite_root, target = tmp_path / "suite", tmp_path / "evidence"
    operators.prepare(suite_root, package)
    native = evidence(suite_root, target)
    kwargs = dict(run_id="run-" + "a" * 32, started_at="2026-10-26T00:00:00Z", finished_at="2026-10-26T00:00:01Z")
    bad = copy.deepcopy(native)
    bad["backend"] = "cpu"
    save(target / "native.json", bad)
    with pytest.raises(ValueError, match="Vulkan"):
        operators.assemble_report(suite_root, target, **kwargs)
    native["results"].pop()
    save(target / "native.json", native)
    with pytest.raises(ValueError, match="omitted"):
        operators.assemble_report(suite_root, target, **kwargs)


def test_native_backend_is_valid_for_reports_but_separate_from_legacy_controller():
    assert get_backend("xrgpu_vulkan").id == "xrgpu_vulkan"
    assert "xrgpu_vulkan" not in BACKENDS
    with pytest.raises(ValueError, match="website's native suite"):
        get_backend("xrgpu_vulkan").check_request({}, [])


def test_cancel_uses_validated_receipt_without_deleting_evidence(tmp_path):
    class Transport:
        def __init__(self):
            self.calls = []

        def devices(self):
            return dict(devices=[dict(serial="fixture", state="device")])

        def shell(self, *args):
            self.calls.append(args)

    transport = Transport()
    run_id = "run-" + "a" * 32
    remote = "/data/local/tmp/xrbench-operators/" + run_id + "/evidence"
    receipt = tmp_path / "receipt.json"
    save(receipt, dict(run_id=run_id, remote_evidence=remote))
    assert operators.cancel(receipt, adb=transport)["cancellation"] == "requested"
    assert transport.calls[-1] == ("fixture", "touch", remote + "/cancel")
    save(receipt, dict(run_id=run_id, remote_evidence="/data/local/tmp/other"))
    with pytest.raises(ValueError, match="destination"):
        operators.cancel(receipt, adb=transport)


def test_fixture_descriptors_match_real_native_planner(package, tmp_path):
    source = ROOT.parent / "xrGPU"
    planner = source / "operators/planner.cpp"
    includes = ROOT / "artifacts/deps/executorch/third-party/json/single_include"
    compiler = shutil.which("c++")
    if compiler is None or not planner.is_file() or not (includes / "nlohmann/json.hpp").is_file():
        pytest.skip("Cross-repository native planner and pinned JSON headers required")
    fixture_root = tmp_path / "suite"
    operators.prepare(fixture_root, package)
    binary = tmp_path / "planner-fixtures"
    subprocess.run([compiler, "-std=c++17", "-I", str(source / "runtime/include"),
                    "-I", str(includes), str(Path(__file__).with_name("native_operator_fixtures.cpp")),
                    str(planner), "-o", str(binary)], check=True, capture_output=True)
    result = subprocess.check_output([str(binary), str(fixture_root / "suite.json")], text=True)
    assert "match native operator API" in result
