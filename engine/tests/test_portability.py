import copy
import json

import numpy as np
import pytest

from engine.workloads import catalog, fixtures, reference, module, correctness
from engine.backends import discovery, get_backend
from engine.profiles import assess as assess_profile, identity
from engine.qualification import request, assess
from engine.reports import save, validate, summarize
from engine.tests import test_controller as controller_tests
from engine.tests import test_experiments as experiment_tests

controller = controller_tests.controller
experiment = experiment_tests.experiment


@pytest.mark.parametrize(
    "model", ["Quest 3", "Quest 3S", "PICO test device", "Unknown glasses", "Phone", "Compute puck"]
)
def test_eligibility_depends_on_runtime_not_product_name(model):
    d = dict(model=model, sdk=29, abi="arm64-v8a,armeabi-v7a")
    assert discovery(d)["support_status"] == "probe_required"
    assert discovery(d)["supported"]
    assert discovery(d)["xr_available"] == (model == "Quest 3")
    assert not discovery(dict(d, sdk=28))["supported"]
    assert not discovery(dict(d, abi="x86_64"))["supported"]


def test_new_device_isolated_run_and_quest_scene_are_independent(controller):
    controller.adb.devices = lambda: dict(
        devices=[
            dict(
                serial="glasses",
                model="Unknown glasses",
                sdk=33,
                abi="arm64-v8a",
                state="device",
                **discovery(dict(model="Unknown glasses", sdk=33, abi="arm64-v8a")),
            )
        ]
    )
    with pytest.raises(ValueError, match="XR scene"):
        controller.create(dict(mode="xr"))
    job = controller.create(dict(operations=["matmul"], execution_target="glasses"))
    assert job["configuration"]["backend"] == "executorch_vulkan"
    assert job["configuration"]["execution_target"] == "glasses"


def test_backend_rejects_misleading_scope_and_control_requests(controller):
    meta = json.loads((controller.build / "apk.json").read_text())
    meta["backends"] = ["executorch_vulkan", "vulkan_resident"]
    save(controller.build / "apk.json", meta)
    for body in (
        dict(backend="invented"),
        dict(backend="vulkan_resident"),
        dict(execution_target="cloud"),
        dict(validation_control="incorrect"),
    ):
        with pytest.raises(ValueError):
            controller.configuration(body)
    c, _ = controller.configuration(
        dict(
            backend="vulkan_resident",
            operations=["matmul"],
            execution_target="phone",
            workload_suite="development",
        )
    )
    assert len(c["cases"]) == 14 and c["execution_target"] == "phone"


def test_profile_discovery_is_separate_from_benchmark_and_persists(controller):
    meta = json.loads((controller.build / "apk.json").read_text())
    meta["device_profiles_version"] = 1
    save(controller.build / "apk.json", meta)
    profile = dict(
        profile_version=1,
        model="New glasses",
        installation_id="test-installation",
        gpu="Test GPU",
        driver=1,
        backends={"executorch_vulkan": dict(compatible=True, status="compatible_unvalidated")},
    )
    controller.adb.install = lambda *args: None
    controller.adb.probe = lambda serial: profile
    controller.probe_request(dict(serial="test-headset"))
    with pytest.raises(ValueError, match="capability"):
        controller.create({})
    controller.probe_device("test-headset", meta)
    assert not controller.jobs
    stored = controller.dashboard_status()["device_profiles"][0]
    assert stored["profile_id"] == identity(profile)
    assert assess_profile(profile, "executorch_vulkan")["status"] == "compatible_unvalidated"
    assert assess_profile(profile, "vulkan_resident")["status"] == "unsupported"
    restored = type(controller)(
        controller.adb, None, controller.state, controller.build, start_workers=False
    )
    assert restored.profiles == controller.profiles


@pytest.mark.parametrize(
    "case",
    [c for c in catalog("development")["cases"] if c.get("fixture_seed") is not None],
    ids=lambda c: c["id"],
)
def test_development_fixtures_match_independent_reference(case):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    inputs, weights = fixtures(case)
    with torch.inference_mode():
        actual = module(case, weights)(*[torch.from_numpy(x) for x in inputs]).numpy()
    assert correctness(actual, reference(case, inputs, weights))["passed"]
    again = fixtures(case)
    for a, b in zip(inputs, again[0]):
        np.testing.assert_array_equal(a, b)


def test_fixture_seeds_change_inputs_without_changing_shape():
    case = next(c for c in catalog("development")["cases"] if c.get("fixture_seed") == 17)
    a = fixtures(case)[0][0]
    b = fixtures(dict(case, fixture_seed=41))[0][0]
    assert a.shape == b.shape and not np.array_equal(a, b)


def test_resident_reports_do_not_claim_headset_execution_or_graph_scope(experiment):
    experiment.update(backend="vulkan_resident", execution_location="phone")
    experiment["configuration"].update(backend="vulkan_resident", execution_target="phone")
    for row in experiment["results"]:
        for sample in row["samples"]:
            sample.update(
                gpu_span_ms=0.1,
                gpu_span_scope="single dispatch sequence, common queue timestamp domain",
            )
    result = summarize(validate(experiment))
    assert result["kernel_comparison"]["results"][0]["available"]
    experiment["execution_location"] = "headset"
    with pytest.raises(ValueError, match="target"):
        validate(experiment)


def test_validation_controls_never_receive_a_speedup_label(experiment):
    experiment["configuration"]["validation_control"] = "slower"
    experiment["experiment"]["validation_control"] = "slower"
    for row in experiment["results"]:
        for sample in row["samples"]:
            sample["validation_control"] = "slower"
    result = summarize(validate(experiment))["kernel_comparison"]["results"][0]
    assert result["available"] and result["outcome"] == "validation_control"
    experiment["results"][0]["samples"][0]["validation_control"] = "none"
    result = summarize(validate(experiment))["kernel_comparison"]["results"][0]
    assert not result["available"] and "Executed kernel identity not verified" in result["reasons"]


def test_qualification_requires_hardware_evidence_and_matching_identity(experiment):
    assert assess([])["status"] == "pending"
    experiment["configuration"]["preset"] = "standard"
    result = assess([experiment])
    assert result["status"] == "pending" and not result["gates"]["incorrect_rejected"]
    assert "Missing physical installation or build provenance" in result["missing"]
    changed = copy.deepcopy(experiment)
    changed["run_id"] = "run-" + "f" * 32
    changed["device"]["gpu"] = "other"
    result = assess([experiment, changed])
    assert any("different device" in x for x in result["missing"])
    assert "Duplicate run identity" in assess([experiment, experiment])["missing"]


def test_hardware_stage_requests_are_explicit_and_bounded():
    for backend in ("executorch_vulkan", "vulkan_resident"):
        for stage in ("stock", "noise", "incorrect", "slower", "candidate"):
            config = request(stage, backend, "compute_puck")
            get_backend(backend).check_request(config, [dict(operation="matmul")])
            assert config["mode"] == "isolated" and config["preset"] == "standard"
            assert config["validation_control"] == (
                stage if stage in ("incorrect", "slower") else "none"
            )


def test_qualification_recovers_evidence_without_relaunching(
    controller, report, monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from engine import qualification

    job = controller.create({"operations": ["linear"]})
    controller.update(job["run_id"], status="disconnected")
    report.update(
        run_id=job["run_id"], configuration=job["configuration"], execution_location="unknown"
    )
    raw = copy.deepcopy(report)
    monkeypatch.setattr(qualification, "Controller", lambda *args, **kwargs: controller)
    monkeypatch.setattr(
        qualification, "ADB", lambda *args: SimpleNamespace(read=lambda *args: report)
    )
    args = SimpleNamespace(
        adb=None, serial="test-headset", run_id=job["run_id"], output=tmp_path / "evidence"
    )
    report["status"] = "running"
    with pytest.raises(RuntimeError, match="Partial report"):
        qualification.recover(args)
    assert controller.jobs[job["run_id"]]["status"] == "disconnected"
    report["status"] = raw["status"]
    qualification.recover(args)
    assert json.loads((args.output / (job["run_id"] + ".json")).read_text()) == raw
    assert controller.jobs[job["run_id"]]["status"] == raw["status"]
    assert report == raw
    args.serial = "different-device"
    with pytest.raises(ValueError, match="original run"):
        qualification.recover(args)


def test_complete_synthetic_qualification_then_missing_or_mixed_evidence_fails(experiment):
    """Synthetic contract test only; never written to the hardware profile directory."""
    base = copy.deepcopy(experiment)
    base["configuration"].update(
        preset="standard",
        experiment_pairs=10,
        apk_sha256="a" * 64,
        workload_suite="development",
        cases=["a", "b", "c"],
    )
    base["experiment"]["pairs"] = 10
    base["device"].update(
        installation_id="synthetic-only", gpu="test-gpu", driver=1, runner_revision="test-build"
    )
    base["kernel_build"] = {"candidate_revision": "b" * 64}
    base["shaders"] = {"test_shader": {"spirv_sha256": "c" * 64}}

    def make(kind, profiling="trace", control="none"):
        r = copy.deepcopy(base)
        r["configuration"].update(experiment=kind, profiling=profiling, validation_control=control)
        r["experiment"].update(kind=kind, validation_control=control)
        r["results"] = []
        for case, seed in zip(("a", "b", "c"), (17, 41, 103)):
            for pair in range(10):
                for arm in ("baseline", "comparison"):
                    row = copy.deepcopy(experiment["results"][0])
                    variant = (
                        "candidate" if arm == "comparison" and kind == "candidate" else "stock"
                    )
                    duration = (
                        8
                        if arm == "comparison" and control == "slower"
                        else 0.8
                        if variant == "candidate"
                        else 1
                    )
                    sample = dict(
                        execution_ms=duration,
                        model_ms=duration,
                        kernel_variant=variant,
                        candidate_selected=variant == "candidate",
                        profiling=profiling,
                        validation_control=control,
                    )
                    if profiling == "trace":
                        sample["dispatches"] = [
                            dict(kernel_name="test_shader", start_ns=0, end_ns=1000, ms=0.001)
                        ]
                    row.update(
                        case=case,
                        fixture_seed=seed,
                        variant=variant,
                        arm=arm,
                        pair_index=pair,
                        trial=pair * 2 + (arm == "comparison"),
                        samples=[copy.deepcopy(sample) for _ in range(100)],
                    )
                    r["results"].append(row)
        return r

    reports = [
        make("aa", "trace"),
        make("aa", "latency"),
        make("candidate", "trace"),
        *[make("candidate", "latency") for _ in range(3)],
        make("candidate", "trace", "slower"),
    ]
    stock = make("none")
    stock.pop("experiment")
    stock["results"] = [
        x for x in stock["results"] if x["pair_index"] == 0 and x["arm"] == "baseline"
    ]
    reports.append(stock)
    bad = make("candidate", "trace", "incorrect")
    bad["results"] = [
        x for x in bad["results"] if x["pair_index"] == 0 and x["arm"] == "comparison"
    ]
    for row in bad["results"]:
        row.update(
            status="failed",
            samples=[],
            correctness={"passed": False},
            correctness_before={"passed": False},
            validation_execution=dict(
                kernel_variant="candidate", candidate_selected=True, validation_control="incorrect"
            ),
        )
    reports.append(bad)
    for i, r in enumerate(reports):
        r["run_id"] = "run-" + f"{i:032x}"
    result = assess(reports)
    assert result["status"] == "validated" and set(result["reproduced_improvements"]) == {
        "a",
        "b",
        "c",
    }
    assert assess(reports[:-1])["status"] == "pending"
    altered = copy.deepcopy(reports)
    altered[0]["device"]["driver"] = 2
    assert assess(altered)["status"] == "pending"
