import copy
import pytest
from layers import catalog
from engine.diagnostics import cost_model, efficiency
from engine.reports import summarize, save
from engine.tests import test_controller as controller_tests
from engine.tests.test_controller import FakeADB

from engine.controller import Controller

controller = controller_tests.controller


def test_known_costs_and_unmodeled_work():
    rows = {c["id"]: c for c in catalog()["cases"]}
    assert cost_model(rows["matmul_odd"])["flops"] == 2 * 7 * 13 * 17
    assert cost_model(rows["matmul_odd"])["logical_bytes"] == 4 * (7 * 13 + 13 * 17 + 7 * 17)
    assert cost_model(rows["linear_no_bias"])["flops"] == 2 * 31 * 63 * 65
    assert cost_model(rows["add_broadcast"])["flops"] == 2 * 7 * 13
    assert cost_model(rows["mean_odd"])["flops"] == 2 * 7 * 13
    assert (
        cost_model(rows["conv2d_grouped"])["flops"]
        == 2 * (1 * 12 * 15 * 17) * (8 // 4) * 9 + 1 * 12 * 15 * 17
    )
    assert cost_model(rows["transpose_heads"])["flops"] == 0
    for op in ("gelu_tokens", "attention_short", "embedding_tokens"):
        assert cost_model(rows[op]) is None
    assert cost_model({"operation": "matmul", "inputs": []}) is None


def calibrated_row():
    row = copy.deepcopy(next(c for c in catalog()["cases"] if c["id"] == "matmul_odd"))
    row.update(
        phase="isolated",
        status="passed",
        timing_valid=True,
        correctness={"passed": True},
        thermal_start=0,
        charging_start=False,
        samples=[
            {
                "gpu_span_ms": 0.01,
                "gpu_span_scope": "single graph, common queue timestamp domain",
                "dispatch_count": 1,
                "execution_ms": 0.04,
            }
        ]
        * 3,
    )
    profile = dict(
        version=1,
        available=True,
        compute_gflops=10,
        memory_gbps=1,
        dispatch_ms=0.02,
        thermal_start=0,
        charging_start=False,
    )
    return row, profile


def test_estimates_preserve_above_reference_and_invalid_reasons():
    row, profile = calibrated_row()
    result = efficiency(row, profile)
    assert result["available"] and result["efficiency_percent"] == 200
    assert result["gap_ms"] == 0 and result["limiting_factor"] == "dispatch"
    for key, value in [
        ("phase", "loaded"),
        ("timing_valid", False),
        ("thermal_start", 1),
        ("charging_start", True),
    ]:
        modified = dict(row, **{key: value})
        assert not efficiency(modified, profile)["available"]
    assert not efficiency(row, {**profile, "compute_gflops": 0})["available"]
    row["samples"][0] = dict(execution_ms=0.04, dispatch_count=1)
    assert not efficiency(row, profile)["available"]


def test_summary_preserves_explicit_invalid_timing_and_legacy_fields(report):
    report["results"][0]["timing_valid"] = False
    summary = summarize(report)
    assert not summary["results"][0]["timing_valid"]
    assert not summary["results"][0]["efficiency"]["available"]
    assert summary["results"][0]["timings"]["gpu_span_ms"] is None


def test_subset_request_retains_catalog_order_and_policy(controller):
    job = controller.create({"operations": ["matmul", "linear"], "calibration": "refresh"})
    expected = [c["id"] for c in catalog()["cases"] if c["operation"] in ("linear", "matmul")]
    assert job["configuration"]["cases"] == expected
    assert job["configuration"]["operations"] == ["linear", "matmul"]
    assert job["configuration"]["calibration"] == "refresh"


def prepare_watch(controller):
    clock = [0.0]
    controller.watch.clock = lambda: clock[0]
    controller.device_snapshot = controller.adb.devices()
    return clock, dict(
        owner="a" * 32, action="enable", serial="test-headset", operations=["matmul"]
    )


def test_watch_deduplicates_poll_reconnect_failure_and_manual_run(controller):
    clock, request = prepare_watch(controller)
    manual = controller.create({"operations": ["matmul"]})
    controller.update(manual["run_id"], status="complete")
    controller.watch_request(request)
    assert len(controller.jobs) == 1
    controller.watch_tick()
    controller.device_snapshot = {"devices": []}
    controller.watch_tick()
    controller.device_snapshot = controller.adb.devices()
    controller.watch_tick()
    assert len(controller.jobs) == 1
    config = controller.build / "apk.json"
    metadata = __import__("json").loads(config.read_text())
    metadata["apk_sha256"] = "b" * 64
    save(config, metadata)
    controller.watch_tick()
    assert len(controller.jobs) == 2
    last = list(controller.jobs)[-1]
    controller.update(last, status="failed")
    controller.watch_tick()
    assert len(controller.jobs) == 2
    controller.watch_request(dict(request, operations=["relu"]))
    assert len(controller.jobs) == 3
    controller.stop(list(controller.jobs)[-1])
    assert not controller.watch.public()


def test_watch_leases_visibility_expiry_and_restart(controller):
    clock, request = prepare_watch(controller)
    controller.watch_request(request)
    with pytest.raises(ValueError):
        controller.watch_request(dict(request, owner="b" * 32))
    controller.update(next(iter(controller.jobs)), status="complete")
    clock[0] = 20
    controller.watch_request(dict(request, action="heartbeat"))
    clock[0] = 49
    assert controller.watch.public()
    clock[0] = 51
    assert not controller.watch.public()
    with pytest.raises(ValueError):
        controller.watch_request(dict(request, action="heartbeat"))
    controller.watch_request(dict(request, owner="b" * 32))
    assert len(controller.jobs) == 1
    controller.watch_request(dict(action="disable", owner="b" * 32))
    assert not controller.watch.public()
    restarted = Controller(FakeADB(), None, controller.state, controller.build, start_workers=False)
    assert (
        restarted.watch.generation != controller.watch.generation and not restarted.watch.public()
    )
    restarted.device_snapshot = restarted.adb.devices()
    restarted.watch_request(request)
    assert len(restarted.jobs) == 2


def test_disconnected_run_blocks_watch_and_expiry_does_not_cancel(controller):
    clock, request = prepare_watch(controller)
    controller.watch_request(request)
    id = next(iter(controller.jobs))
    controller.update(id, status="disconnected")
    controller.watch_request(dict(request, operations=["relu"]))
    assert len(controller.jobs) == 1
    clock[0] = 31
    controller.watch_tick()
    assert not controller.watch.public()
    assert (
        controller.jobs[id]["status"] == "disconnected"
        and not controller.jobs[id]["cancel_requested"]
    )
