"""Optional real native-core checks against the host software Vulkan adapter."""

import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest


@pytest.fixture
def native_run(tmp_path):
    runner = os.environ.get("XRBENCH_NATIVE_TEST_RUNNER")
    suite_root = os.environ.get("XRBENCH_NATIVE_TEST_SUITE")
    if not runner or not suite_root:
        pytest.skip("Set XRBENCH_NATIVE_TEST_RUNNER and XRBENCH_NATIVE_TEST_SUITE")
    suite = json.loads((Path(suite_root) / "suite.json").read_text())

    def run(control=None, thermal=False):
        output = tmp_path / uuid.uuid4().hex
        output.mkdir()
        config = dict(run_id="run-" + uuid.uuid4().hex, backend="xrgpu_vulkan", mode="isolated",
                      execution_target="headset", warmups=1, samples=1,
                      suite_revision=suite["suite_revision"], runtime_revision="a" * 40)
        if control:
            config["test_control"] = control
        job = output / "command.json"
        job.write_text(json.dumps(config))
        environment = dict(thermal_status=3 if thermal else 0, charging=False, display_interactive=False)
        if thermal:
            environment["stop_reason"] = "Thermal state severe; measurements stopped"
        (output / "environment.json").write_text(json.dumps(environment))
        result = subprocess.run([str(Path(runner).resolve()), str(Path(suite_root).resolve()),
                                 str(job), str(output)], capture_output=True, text=True, timeout=300)
        assert (output / "report.json").exists(), result.stderr
        report = json.loads((output / "report.json").read_text())
        return report, result

    return run


def test_native_stage_markers_and_complete_samples(native_run):
    report, result = native_run()
    assert result.returncode == 0, result.stderr
    assert report["status"] == "complete" and report["test_only"] is True
    assert len(report["results"]) == 221
    assert all(row["status"] == "passed" and len(row["samples"]) == 1 for row in report["results"])
    assert all(row["warmup_execution_ms"] >= 0 and row["samples"][0]["elapsed_ms"] >= 0
               and row["samples"][0]["at"] for row in report["results"])
    stages = report["stage_events"]
    assert stages[0]["stage"] == "preparation" and stages[-1]["stage"] == "finished"
    assert [s["elapsed_ms"] for s in stages] == sorted(s["elapsed_ms"] for s in stages)
    assert [s["at"] for s in stages] == sorted(s["at"] for s in stages)
    for row in report["results"]:
        assert {"preparation", "warmup", "execution", "readback"} <= {
            s["stage"] for s in stages if s["case"] == row["case"]}


@pytest.mark.parametrize("stage,samples", [("preparation", 0), ("warmup", 0), ("execution", 1)])
def test_native_cancellation_preserves_current_case_without_claiming_pass(native_run, stage, samples):
    report, result = native_run(dict(stage=stage, samples=samples, action="cancel"))
    assert result.returncode == 1
    assert report["status"] == "canceled"
    assert len(report["results"]) == 1
    assert report["results"][0]["status"] == "interrupted"
    assert report["progress"]["completed_cases"] == 0
    assert len(report["results"][0]["samples"]) == samples
    assert report["stage_events"][-1]["stage"] == "finished"


def test_native_deadline_retains_samples_and_terminal_failure(native_run):
    report, result = native_run(dict(stage="execution", samples=1, action="deadline"))
    assert result.returncode == 1 and report["status"] == "failed"
    assert "deadline" in report["error"]
    assert len(report["results"][0]["samples"]) == 1
    assert report["results"][0]["status"] != "passed"


def test_native_abrupt_process_loss_keeps_inflight_durable_checkpoint(native_run):
    report, result = native_run(dict(stage="execution", samples=1, action="crash"))
    assert result.returncode == 23 and report["status"] == "running"
    assert report["progress"]["completed_cases"] == 0
    assert len(report["results"]) == 1 and len(report["results"][0]["samples"]) == 1
    assert report["results"][0]["status"] == "interrupted"


def test_native_real_control_path_stops_on_environment_thermal_reason(native_run):
    report, result = native_run(thermal=True)
    assert result.returncode == 1 and report["status"] == "failed"
    assert report["results"] == [] and "Thermal state severe" in report["error"]
