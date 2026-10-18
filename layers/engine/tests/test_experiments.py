import copy
import json

import pytest
from layers.engine.tests import test_controller as controller_tests

from layers.engine.evidence import inspect_report
from layers.engine.experiments import dispatch_summary
from layers.engine.reports import digest, summarize, validate

controller = controller_tests.controller


@pytest.fixture
def experiment(report):
    report["configuration"].update(cases=["matmul_odd"], experiment="candidate", experiment_pairs=6)
    report["experiment"] = dict(version=1, kind="candidate", pairs=6, seed=17)
    report["results"] = []
    for pair in range(6):
        for arm, variant, scale in [("baseline", "stock", 1), ("comparison", "candidate", 0.8)]:
            duration = (pair + 1) * scale
            report["results"].append(
                dict(
                    case="matmul_odd",
                    operation="matmul",
                    phase="isolated",
                    trial=pair * 2 + (arm == "comparison"),
                    pair_index=pair,
                    arm=arm,
                    variant=variant,
                    status="passed",
                    thermal_start=0,
                    charging_start=False,
                    correctness={"passed": True},
                    correctness_before={"passed": True},
                    correctness_after={"passed": True},
                    samples=[
                        dict(
                            execution_ms=duration,
                            model_ms=duration * 0.9,
                            kernel_variant=variant,
                            candidate_selected=variant == "candidate",
                        )
                        for _ in range(20)
                    ],
                )
            )
    return report


def test_paired_analysis_controls_between_block_drift_and_is_reproducible(experiment):
    validate(experiment)
    a = summarize(copy.deepcopy(experiment))["kernel_comparison"]
    b = summarize(copy.deepcopy(experiment))["kernel_comparison"]
    assert a == b
    result = a["results"][0]
    assert result["available"] and result["outcome"] == "improvement_observed"
    metric = result["metrics"]["execution_ms"]
    assert metric["mean_improvement_percent"] == pytest.approx(20)
    assert metric["ci95_percent"] == pytest.approx([20, 20])
    assert "gpu_span_ms" not in result["metrics"]


@pytest.mark.parametrize(
    "damage",
    [
        "incorrect",
        "incomplete",
        "thermal",
        "wrong_kernel",
        "unselected",
        "duplicate",
        "missing_after",
        "cross_pair_state",
    ],
)
def test_invalid_or_partial_experiments_cannot_claim_improvement(experiment, damage):
    row = experiment["results"][1]
    if damage == "incorrect":
        row["correctness_after"]["passed"] = False
    elif damage == "incomplete":
        experiment["results"].pop()
    elif damage == "thermal":
        row["thermal_discontinuity"] = True
    elif damage == "wrong_kernel":
        row["samples"][0]["kernel_variant"] = "stock"
    elif damage == "unselected":
        row["samples"][0]["candidate_selected"] = False
    elif damage == "duplicate":
        experiment["results"][-1] = copy.deepcopy(row)
    elif damage == "missing_after":
        row.pop("correctness_after")
    elif damage == "cross_pair_state":
        row["charging_start"] = True
    result = summarize(experiment)["kernel_comparison"]["results"][0]
    assert not result["available"] and result["metrics"] == {} and result["reasons"]


def test_aa_is_a_noise_control_even_when_one_arm_is_faster(experiment):
    experiment["experiment"]["kind"] = "aa"
    experiment["configuration"]["experiment"] = "aa"
    for row in experiment["results"]:
        row["variant"] = "stock"
        for sample in row["samples"]:
            sample["kernel_variant"] = "stock"
    result = summarize(experiment)["kernel_comparison"]["results"][0]
    assert result["outcome"] == "noise_control"
    assert result["noise_control_warning"]


def test_dispatch_groups_preserve_source_and_workgroup_changes():
    event = dict(
        kernel_name="shader", dispatch_id=3, global_size=[8, 8, 1], local_size=[4, 4, 1], ms=2
    )
    row = dict(samples=[{"dispatches": [event]}, {"dispatches": [{**event, "ms": 4}]}])
    result = dispatch_summary(
        row, {"shader": {"spirv_sha256": "a" * 64, "source": "kernels/example.glsl"}}
    )
    assert result[0]["timing"]["median"] == 3 and result[0]["complete"]
    assert result[0]["shader"]["source"] == "kernels/example.glsl"
    row["samples"][1]["dispatches"][0]["local_size"] = [2, 8, 1]
    assert len(dispatch_summary(row, {})) == 2
    assert not any(r["complete"] for r in dispatch_summary(row, {}))


def test_offline_inspection_recomputes_claims_and_keeps_original(experiment, tmp_path):
    experiment["kernel_comparison"] = {"fabricated_claim": True}
    file = tmp_path / "returned-report.json"
    file.write_text(json.dumps(experiment))
    before = file.read_bytes()
    result = inspect_report(file)
    assert file.read_bytes() == before
    assert result["source_sha256"] == digest(experiment)
    assert result["kernel_comparison"]["results"][0]["available"]
    assert "fabricated_claim" not in result["kernel_comparison"]


def test_controller_requires_declared_built_candidate_and_valid_workload(controller):
    from layers.engine.kernels import source_identity
    from layers.engine.reports import save

    path = controller.build / "apk.json"
    metadata = json.loads(path.read_text())
    with pytest.raises(ValueError, match="Build"):
        controller.configuration({"experiment": "candidate", "operations": ["matmul"]})
    metadata.update(kernel_experiments_version=1, candidate_revision=source_identity())
    save(path, metadata)
    config, _ = controller.configuration(
        {"experiment": "candidate", "operations": ["matmul"], "preset": "standard"}
    )
    assert config["experiment_pairs"] == 10 and config["cases"] == [
        "matmul_rectangular",
        "matmul_odd",
    ]
    for request in ({"operations": ["linear"]}, {"mode": "xr"}, {"preset": "sustained"}):
        with pytest.raises(ValueError):
            controller.configuration(
                {"experiment": "candidate", "operations": ["matmul"], **request}
            )
