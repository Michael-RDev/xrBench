"""Deployment checks preserve the distinction between host tests and GPU evidence."""

import copy
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from engine import inference
from engine.qualification_build import verify_prebuilt


REQUEST = dict(operation="linear", input_shape=[1, 7, 5], output_shape=[1, 7, 9],
               dtype="float32", layout="texture3d", attributes=dict(bias=True, weight_shape=[9, 5]))
RULE = dict(request=REQUEST, variant="m1_wg8x4_texture2d", kind="performance")
TARGET = dict(model="Quest 3", gpu="Adreno (TM) 740", vendor_id=0x5143,
              device_id=1, driver="driver-1", api_version=1)


def production_session():
    model = dict(runtime_revision="runtime-1", model_id="fixture", inputs=[dict(shape=[1, 7, 5])],
                 outputs=[dict(shape=[1, 7, 9])])
    package = dict(package_sha256="a" * 64)
    record = dict(target=TARGET, qualification_sha256="b" * 64, rules=[RULE])
    info = dict(**model, build_type="production", backend="vulkan", device=TARGET,
                kernel_package=package, qualification_sha256=record["qualification_sha256"],
                qualification_status="qualified", qualification_eligible=True,
                decisions={"node1": dict(implementation="xrgpu", request=REQUEST, variant=RULE["variant"])})
    return info, model, package, record


def test_production_acceptance_requires_observed_qualified_native_execution():
    info, model, package, record = production_session()
    inference.check_session(info, model, package, record)
    for damage in ("development", "fallback", "package", "policy", "target", "selection", "variant", "model"):
        changed = copy.deepcopy(info)
        if damage == "development":
            changed["build_type"] = "development"
        elif damage == "fallback":
            changed.update(backend="cpu", fallback_reason="Missing qualified repair")
        elif damage == "package":
            changed["kernel_package"]["package_sha256"] = "f" * 64
        elif damage == "policy":
            changed["qualification_sha256"] = "f" * 64
        elif damage == "target":
            changed["device"]["driver"] = "different"
        elif damage == "selection":
            changed["decisions"] = {}
        elif damage == "variant":
            changed["decisions"]["node1"]["variant"] = "unknown"
        else:
            changed["model_id"] = "different-model"
        with pytest.raises(ValueError):
            inference.check_session(changed, model, package, record)


def test_host_acceptance_does_not_require_or_grant_gpu_policy():
    info, model, _, _ = production_session()
    info.update(backend="cpu", qualification_status="unqualified", qualification_eligible=False, decisions={})
    inference.check_session(info, model)


def test_release_preserves_model_kernel_coverage_without_requiring_changes_to_stock_regressions():
    info, model, package, record = production_session()
    inference.check_session(info, model, package, record, [RULE])
    info["decisions"] = {}
    with pytest.raises(ValueError, match="observed during qualification"):
        inference.check_session(info, model, package, record, [RULE])
    # An unaffected vision regression may remain on stock Vulkan kernels.
    inference.check_session(info, model, package, record, [])


def test_native_output_validation_rejects_wrong_results_and_missing_outputs(tmp_path):
    expected = np.array([1, 2, 3], dtype="<f4").tobytes()
    (tmp_path / "expected.bin").write_bytes(expected)
    fixture = dict(outputs=[dict(name="embedding", file="expected.bin", dtype="float32", shape=[3])])
    info = dict(output_files=["output0.bin"])
    assert inference.compare_outputs(tmp_path, fixture, info, lambda _: expected)[0]["passed"]
    with pytest.raises(ValueError, match="reference mismatch"):
        inference.compare_outputs(tmp_path, fixture, info, lambda _: np.zeros(3, dtype="<f4").tobytes())
    for files in ([], ["../output0.bin"], ["output0.bin", "output1.bin"]):
        with pytest.raises(ValueError, match="inventory"):
            inference.compare_outputs(tmp_path, fixture, dict(output_files=files), lambda _: expected)


def test_release_refuses_pending_evidence_before_any_build_or_write(tmp_path, monkeypatch):
    monkeypatch.setattr(inference, "assess", lambda *a: dict(record=dict(status="pending"), missing=["hardware"]))
    def unexpected(*args, **kwargs):
        pytest.fail("Pending evidence must not reach a native build")
    monkeypatch.setattr(inference, "command", unexpected)
    with pytest.raises(ValueError, match="Hardware qualification pending: hardware"):
        inference.release(tmp_path, "campaign", "shortlist", [], tmp_path / "release")
    assert not (tmp_path / "release").exists()


def test_pipeline_detects_changed_inventory_before_creating_campaign(tmp_path):
    workloads = tmp_path / "workloads.json"
    workloads.write_text("[]")
    value = dict(kind="xrbench.native.pipeline", workloads=str(workloads), workloads_sha256=inference.sha(workloads))
    value["pipeline_sha256"] = inference.digest(value)
    pipeline = tmp_path / "pipeline.json"
    pipeline.write_text(json.dumps(value))
    workloads.write_text('[{"tampered":true}]')
    with pytest.raises(ValueError, match="inventory changed"):
        inference.campaign(pipeline, TARGET, tmp_path / "campaign.json")


@pytest.mark.parametrize("damage", [None, "development", "package", "policy", "binary"])
def test_release_handoff_preserves_evidence_and_rejects_wrong_builds(tmp_path, monkeypatch, damage):
    # Mock the hardware assessment/build boundary; these bytes are never run or
    # published, and this test cannot qualify a real package.
    package = tmp_path / "package"
    package.mkdir()
    (package / "kernel").write_bytes(b"test-only kernel payload")
    manifest = dict(schema_version=1, package_id="xrgpu.quest3.fp32.v1", runtime_revision="r1",
                    selections=[], files={"kernel": inference.sha(package / "kernel")})
    manifest["package_sha256"] = inference.digest(manifest)
    (package / "manifest.json").write_text(json.dumps(manifest))
    model = tmp_path / "model"
    model.mkdir()
    (model / "bundle.json").write_text('{"test_fixture":true}')
    plan = dict(package=str(package), workloads=[dict(id="minilm", type="model", bundle=str(model))])
    campaign = tmp_path / "campaign.json"
    campaign.write_text(json.dumps(plan))
    shortlist = tmp_path / "shortlist.json"
    shortlist.write_text('{}')
    report = tmp_path / "original.json"
    original = b'{"synthetic":true,"test_fixture":"original bytes"}\n'
    report.write_bytes(original)
    record = dict(status="qualified", runtime_revision="r1", package_sha256=manifest["package_sha256"], qualification_sha256="b" * 64)
    monkeypatch.setattr(inference, "assess", lambda *a: dict(record=record, model_selections=dict(minilm=[RULE])))
    monkeypatch.setattr(inference, "xrgpu_modules", lambda *a: (
        SimpleNamespace(bind_package=lambda a, p, b: shutil.copytree(a, b)),
        SimpleNamespace(verify_implementation=lambda p: manifest)))
    calls = []
    def build(source, arguments, log):
        calls.append(arguments)
        root = Path(arguments[arguments.index("--output") + 1])
        (root / "bin").mkdir(parents=True)
        (root / "native").mkdir()
        (root / "native/temporary.o").write_bytes(b"not a deployment artifact")
        (root / "bin/xrgpu_run").write_bytes(b"test-only runner")
        (root / "bin/libxrgpu_runtime.so").write_bytes(b"test-only library")
        metadata = dict(kind="xrgpu.native.build", schema_version=1, platform="android-arm64", runtime_revision="r1",
                        runner="bin/xrgpu_run", libraries=["bin/libxrgpu_runtime.so"],
                        build_type="development" if damage == "development" else "production",
                        package_sha256="wrong" if damage == "package" else record["package_sha256"],
                        qualification_sha256="wrong" if damage == "policy" else record["qualification_sha256"],
                        files={"bin/xrgpu_run": "wrong" if damage == "binary" else inference.sha(root / "bin/xrgpu_run"),
                               "bin/libxrgpu_runtime.so": inference.sha(root / "bin/libxrgpu_runtime.so")})
        (root / "build.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(inference, "command", build)
    destination = tmp_path / "release"
    if damage:
        with pytest.raises(ValueError, match="Production"):
            inference.release(tmp_path, campaign, shortlist, [report], destination)
        assert not (destination / "release.json").exists()
    else:
        result = inference.release(tmp_path, campaign, shortlist, [report], destination)
        assert result["package"] == "kernel-package"
        assert result["production_device_acceptance"] == "pending"
        assert result["model_selections"] == dict(minilm=[RULE])
        assert "--experiments" not in calls[0]
        assert (destination / "evidence" / (inference.sha(report) + ".json")).read_bytes() == original
        assert not any(name.startswith("runtime/native/") for name in result["files"])
        moved = tmp_path / "moved-release"
        shutil.move(destination, moved)
        for name, expected in result["files"].items():
            assert inference.sha(inference.contained(moved, name)) == expected
    assert report.read_bytes() == original


def test_build_inventory_binds_deployable_files_and_build_mode(tmp_path):
    source = Path(__file__).resolve().parents[3] / "xrGPU"
    if not source.exists():
        pytest.skip("xrGPU checkout required")
    inference.xrgpu_modules(source)
    from runtime.build import write_build_manifest
    native, install, output = tmp_path / "native", tmp_path / "install", tmp_path / "release"
    native.mkdir()
    (native / "xrgpu_run").write_bytes(b"native runner fixture")
    (native / "libxrgpu_runtime.dylib").write_bytes(b"native library fixture")
    args = SimpleNamespace(host=True, experiments=False, package=None, qualification=None)
    write_build_manifest(args, native, install, output)
    manifest = json.loads((output / "build.json").read_text())
    assert manifest["build_type"] == "production" and manifest["platform"] == "host"
    assert manifest["qualification_sha256"] is None
    for name, expected in manifest["files"].items():
        assert inference.sha(output / name) == expected
    assert manifest["runner"] in manifest["files"]
    assert all(n in manifest["files"] for n in manifest["libraries"])


def test_qualification_rejects_production_or_modified_prebuilt_core(tmp_path):
    binary = tmp_path / "bin/libxrgpu_runtime.so"
    binary.parent.mkdir()
    binary.write_bytes(b"native build fixture")
    package = dict(package_sha256="a" * 64, runtime_revision="r1")
    metadata = dict(kind="xrgpu.native.build", build_type="development", platform="android-arm64", **package,
                    libraries=["bin/libxrgpu_runtime.so"], files={"bin/libxrgpu_runtime.so": inference.sha(binary)})
    receipt = tmp_path / "build.json"
    receipt.write_text(json.dumps(metadata))
    assert verify_prebuilt(binary, package)["build_type"] == "development"
    metadata["build_type"] = "production"
    receipt.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="development core"):
        verify_prebuilt(binary, package)
    metadata["build_type"] = "development"
    receipt.write_text(json.dumps(metadata))
    binary.write_bytes(b"different library")
    with pytest.raises(ValueError, match="development core"):
        verify_prebuilt(binary, package)


def test_isolated_build_does_not_write_into_existing_project_artifacts(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3] / "xrGPU"
    if not source.exists():
        pytest.skip("xrGPU checkout required")
    inference.xrgpu_modules(source)
    from runtime import build
    project = tmp_path / "project"
    project.mkdir()
    (project / "dependencies.json").write_text(json.dumps(dict(executorch=dict(commit="runtime-1"),
                                                              android=dict(ndk="test-ndk"))))
    output = tmp_path / "isolated"
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if "-S" in args:
            native = Path(args[args.index("-B") + 1])
            native.mkdir(parents=True)
            (native / "xrgpu_run").write_bytes(b"runner fixture")
            (native / "libxrgpu_runtime.so").write_bytes(b"library fixture")
    monkeypatch.setattr(build, "ROOT", project)
    monkeypatch.setattr(build.subprocess, "run", run)
    monkeypatch.setattr(build.shutil, "which", lambda *a, **kw: "/test/cmake")
    args = SimpleNamespace(host=True, experiments=False, package=None, qualification=None,
                           output=output, jobs=2, native_only=True)
    build.build_library(args)
    assert calls[0][calls[0].index("-B") + 1] == str(output / "native")
    assert "-DCMAKE_INSTALL_PREFIX=" + str(output / "install") in calls[0]
    assert not (project / "artifacts").exists()
    assert json.loads((output / "build.json").read_text())["runtime_revision"] == "runtime-1"
