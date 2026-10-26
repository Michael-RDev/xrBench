"""Protocol tests use synthetic fixtures only; they never ship a qualification."""

import copy
import json
from pathlib import Path
import shutil

import pytest

from engine import optimized_package as package
from engine import qualification_campaign as campaign
from engine.reports import save

TARGET = dict(model="Quest 3", gpu="Adreno (TM) 740", vendor_id=0x5143,
              device_id=1, driver="driver-1", api_version=1)
REQUEST = dict(operation="linear", input_shape=[1, 7, 5], output_shape=[1, 7, 9],
               dtype="float32", layout="texture3d", attributes=dict(bias=True, weight_shape=[9, 5]))
VARIANT = "m1_wg8x4_texture2d"
SHADER = "xrgpu_linear_m1_bias_texture3d_texture2d_float"


@pytest.fixture
def setup_campaign(tmp_path):
    root = tmp_path / "package"
    (root / "integration").mkdir(parents=True)
    contract = Path(__file__).resolve().parents[3] / "xrGPU/runtime/integration/qualification.py"
    if not contract.is_file():
        pytest.skip("Cross-repository shared xrGPU qualification contract required")
    shutil.copy2(contract, root / "integration/qualification.py")
    shader = root / ("spirv/" + SHADER + ".spv")
    shader.parent.mkdir()
    shader.write_bytes(b"fixture SPIR-V, never executed")
    files = {p.relative_to(root).as_posix(): package.sha(p) for p in root.rglob("*") if p.is_file()}
    manifest = dict(schema_version=1, package_id=package.PACKAGE_ID, runtime_revision="runtime-1", files=files,
                    selections=[], shaders={SHADER: dict(spirv_sha256=package.sha(shader))},
                    linear_variants=[dict(id=VARIANT, tile_m=1, weight_storage="texture2d", workgroup=[8, 4, 1])])
    manifest["package_sha256"] = package.digest(manifest)
    save(root / "manifest.json", manifest)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in ("cpu.pte", "vulkan.pte", "input.bin", "output.bin"):
        (bundle / name).write_bytes(b"fixture")
    fixture = dict(inputs=[dict(name="input", shape=[1, 7, 5], dtype="float32", file="input.bin")],
                   outputs=[dict(name="output", shape=[1, 7, 9], dtype="float32", file="output.bin")])
    model = dict(schema_version=2, bundle_type="xrgpu.model", model_id="fixture", runtime_revision="runtime-1",
                 kernel_package=dict(package_id=package.PACKAGE_ID, package_sha256=manifest["package_sha256"]),
                 programs=dict(cpu=dict(file="cpu.pte"), vulkan=dict(file="vulkan.pte")),
                 files={p.name: package.sha(p) for p in bundle.iterdir()},
                 fixtures=[dict(seed=seed, **fixture) for seed in (*campaign.SEEDS, *campaign.HOLDOUT)])
    save(bundle / "bundle.json", model)
    path = tmp_path / "campaign.json"
    value = campaign.prepare(root, manifest["package_sha256"], [dict(id="linear", type="operator", bundle=str(bundle), request=REQUEST)], TARGET, path)
    return path, value, manifest


def report(plan, *, stage="confirm", seed=None, session="session-1", improvement=10, variant=VARIANT, suffix=""):
    seed = seed or campaign.HOLDOUT[0]
    row = dict(schema_version=1, kind="xrgpu.qualification.evidence", campaign_id=plan["campaign_id"],
               campaign_sha256=plan["campaign_sha256"], run_id=f"run-{stage}-{seed}-{session}-{suffix}",
               session_id=session, stage=stage, source="native_headset", execution_target="headset", synthetic=False,
               installation_id="physical-installation", package_sha256=plan["package_sha256"], runtime_revision=plan["runtime_revision"],
               target=TARGET, status="complete", backend="executorch_vulkan", workload_id="linear", variant=variant,
               request=REQUEST, bundle_sha256=plan["workloads"][0]["bundle_sha256"], fixture_seed=seed,
               profiling="latency", warmups=20, decisions=[dict(request=REQUEST, variant=variant, implementation="xrgpu")], dispatches=[])
    if stage in ("correctness", "model", "sustained"):
        row["selection_policy"] = "exact_rules"
    condition = dict(thermal_status=0, charging=False)
    def arm(value):
        return dict(samples_ms=[value] * 100, conditions=condition,
                    correctness_before=dict(passed=True), correctness_after=dict(passed=True))
    row["pairs"] = [dict(index=i, order=["baseline", "comparison"] if i % 2 == 0 else ["comparison", "baseline"],
                         baseline=arm(10), comparison=arm(10 * (1 - improvement / 100))) for i in range(10)]
    return row


def test_verified_optimized_package_is_distinct_from_benchmark_source_package(setup_campaign):
    path, plan, manifest = setup_campaign
    assert package.verify(plan["package"], plan["package_sha256"])["package_id"] == package.PACKAGE_ID
    assert package.PACKAGE_ID != "xrgpu.benchmark-kernels.v1"
    assert package.qualification_module(plan["package"]).BASE_GATES
    shader = Path(plan["package"]) / ("spirv/" + SHADER + ".spv")
    shader.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        campaign.load_campaign(path)


def test_campaign_freezes_package_models_and_seed_split(setup_campaign, tmp_path):
    path, plan, _ = setup_campaign
    with pytest.raises(FileExistsError):
        campaign.prepare(plan["package"], plan["package_sha256"], [dict(id="linear", type="operator", bundle=plan["workloads"][0]["bundle"], request=REQUEST)], TARGET, path)
    changed = copy.deepcopy(plan)
    changed["holdout_seeds"] = changed["discovery_seeds"]
    save(tmp_path / "changed.json", changed)
    with pytest.raises(ValueError, match="changed"):
        campaign.load_campaign(tmp_path / "changed.json")
    changed["campaign_sha256"] = campaign.campaign_digest(changed)
    save(tmp_path / "changed.json", changed)
    with pytest.raises(ValueError, match="disjoint"):
        campaign.load_campaign(tmp_path / "changed.json")


@pytest.mark.parametrize("damage", ["host", "synthetic", "cpu", "package", "model", "target", "discovery", "wrong", "samples", "conditions", "order", "selection", "trace"])
def test_bad_evidence_never_grants_eligibility(setup_campaign, damage):
    _, plan, _ = setup_campaign
    value = report(plan)
    if damage == "host":
        value["source"] = "native_host"
    if damage == "synthetic":
        value["synthetic"] = True
    if damage == "cpu":
        value["backend"] = "executorch_cpu"
    if damage == "package":
        value["package_sha256"] = "f" * 64
    if damage == "model":
        value["bundle_sha256"] = "f" * 64
    if damage == "target":
        value["target"] = dict(TARGET, driver="other")
    if damage == "discovery":
        value["fixture_seed"] = campaign.SEEDS[0]
    if damage == "wrong":
        value["pairs"][0]["comparison"]["correctness_after"]["passed"] = False
    if damage == "samples":
        value["pairs"][0]["comparison"]["samples_ms"] = [1]
    if damage == "conditions":
        value["pairs"][0]["comparison"]["conditions"] = dict(thermal_status=1, charging=False)
    if damage == "order":
        for pair in value["pairs"]:
            pair["order"] = ["baseline", "comparison"]
    if damage == "selection":
        value["decisions"] = []
    if damage == "trace":
        value["dispatches"] = [dict(kernel_name=SHADER)]
    valid, failures = campaign.evidence(plan, [value])
    assert not valid and len(failures) == 1


def test_pair_bootstrap_recomputes_from_raw_blocks_and_rejects_duplicate_runs(setup_campaign):
    _, plan, _ = setup_campaign
    value = report(plan)
    metrics = campaign.pair_metrics(value, plan["protocol"])
    assert metrics["improvement_percent"] == pytest.approx(10)
    assert metrics["ci95_percent"] == pytest.approx([10, 10])
    assert metrics["p95_change_percent"] == pytest.approx(-10)
    valid, failures = campaign.evidence(plan, [value, value])
    assert len(valid) == 1 and len(failures) == 1


def test_shortlist_requires_complete_screening_and_final_evidence_cannot_reuse_it(setup_campaign, tmp_path):
    path, plan, _ = setup_campaign
    reports = []
    for seed in campaign.SEEDS:
        p = tmp_path / f"screen-{seed}.json"
        save(p, report(plan, stage="screen", seed=seed))
        reports.append(p)
    with pytest.raises(ValueError, match="every variant"):
        campaign.shortlist(path, reports[:2], tmp_path / "partial.json")
    frozen = campaign.shortlist(path, reports, tmp_path / "shortlist.json")
    assert frozen["selections"][0]["variants"] == [VARIANT]
    result = campaign.assess(path, reports, tmp_path / "shortlist.json")
    assert result["record"]["status"] == "pending" and not result["record"]["rules"]
    assert not result["gates"]["holdout"]


def test_no_evidence_preserves_pending_hardware_gate(setup_campaign):
    path, _, _ = setup_campaign
    result = campaign.assess(path, [])
    assert result["record"]["status"] == "pending" and not result["record"]["rules"]
    assert "hardware" in result["missing"]


def test_model_evidence_cannot_use_blanket_development_repairs(setup_campaign):
    _, plan, _ = setup_campaign
    value = report(plan, stage="model")
    value["selection_policy"] = "development"
    valid, failures = campaign.evidence(plan, [value])
    assert not valid and "exact production" in failures[0]["reason"]


def test_model_speedup_requires_observed_final_performance_kernel():
    rule = dict(request=REQUEST, variant=VARIANT, kind="performance")
    value = dict(decisions=[dict(implementation="stock", request=REQUEST, variant="")])
    assert campaign.selected_by_rules(value, [rule])
    assert not campaign.uses_performance_rule(value, [rule])
    value["decisions"][0].update(implementation="xrgpu", variant=VARIANT)
    assert campaign.uses_performance_rule(value, [rule])
    value["decisions"][0]["request"] = dict(REQUEST, input_shape=[1, 8, 5])
    assert not campaign.selected_by_rules(value, [rule])
    assert not campaign.uses_performance_rule(value, [rule])


def test_sustained_gate_requires_measured_native_frames(setup_campaign):
    _, plan, _ = setup_campaign
    value = report(plan, stage="sustained")
    value.update(duration_seconds=600, refresh_hz=72, focus_lost=False, order=["baseline", "comparison"],
                 arm_duration_seconds=dict(baseline=600.1, comparison=600.2),
                 frames=dict(baseline=[10] * 100, comparison=[10] * 100),
                 missed_frame_rate=dict(baseline=.01, comparison=.015))
    assert not campaign.evidence(plan, [value])[1]
    value["arm_duration_seconds"]["comparison"] = 30
    assert campaign.evidence(plan, [value])[1]


def test_package_path_traversal_and_symlinks_are_rejected(tmp_path):
    (tmp_path / "file").write_text("value")
    with pytest.raises(ValueError, match="relative"):
        package.contained(tmp_path, "../file")
    (tmp_path / "link").symlink_to(tmp_path / "file")
    with pytest.raises(ValueError, match="Symlinks"):
        package.contained(tmp_path, "link")


def test_complete_campaign_exports_rules_consumable_by_xrgpu_then_missing_model_fails(setup_campaign, tmp_path):
    path, plan, manifest = setup_campaign
    from engine.qualification_fixtures import cases
    from layers import revision
    bundle = Path(plan["workloads"][0]["bundle"])
    workloads = [dict(id="linear", type="operator", bundle=str(bundle), request=REQUEST)]
    for case in cases():
        case_bundle = tmp_path / ("regression-" + case["id"])
        shutil.copytree(bundle, case_bundle)
        model = json.loads((case_bundle / "bundle.json").read_text())
        model.update(model_id="xrbench/" + case["id"], model_revision=revision(), metadata=dict(case=case))
        model["inputs"] = [dict(name=f"input_{i}", shape=value["shape"], dtype=value["dtype"]) for i, value in enumerate(case["inputs"])]
        shape, params = case["inputs"][0]["shape"], case.get("params", {})
        if case["operation"] == "linear":
            output_shape = shape[:-1] + [params["out_features"]]
        elif case["operation"] == "gelu":
            output_shape = shape
        elif case["operation"] == "conv2d":
            output_shape = [shape[0], params["out_channels"], *[(n + 2 * params.get("padding", 0) - params["kernel"]) // params.get("stride", 1) + 1 for n in shape[-2:]]]
        else:
            output_shape = [1, 1]
        model["outputs"] = [dict(name="output_0", shape=output_shape, dtype="float32")]
        save(case_bundle / "bundle.json", model)
        workloads.append(dict(id=case["id"], type="regression", bundle=str(case_bundle)))
    for name, shape, model_id in [("minilm32", [1, 32], campaign.MINILM_ID), ("minilm128", [1, 128], campaign.MINILM_ID),
                                   ("minilm256", [1, 256], campaign.MINILM_ID), ("vision", [1, 3, 224, 224], campaign.MOBILENET_ID)]:
        model_bundle = tmp_path / name
        shutil.copytree(bundle, model_bundle)
        model = json.loads((model_bundle / "bundle.json").read_text())
        model["model_id"] = model_id
        if model_id == campaign.MINILM_ID:
            model["inputs"] = [dict(name=n, shape=shape, dtype="int64") for n in ("input_ids", "attention_mask", "token_type_ids")]
            model["outputs"] = [dict(name="embedding", shape=[1, 384], dtype="float32")]
            model["model_revision"] = campaign.MINILM_REVISION
            model["metadata"] = dict(weights_sha256=campaign.MINILM_WEIGHTS)
            model["preprocessing"] = dict(tokenizer="WordPiece", sequence_length=shape[-1], pooling="attention_mask_mean",
                                          output_normalization="L2", padding="max_length", truncation=True)
        else:
            model["inputs"] = [dict(name="image", shape=shape, dtype="float32")]
            model["outputs"] = [dict(name="logits", shape=[1, 1000], dtype="float32")]
            model["model_revision"] = "IMAGENET1K_V1:" + campaign.MOBILENET_WEIGHTS
            model["metadata"] = dict(weights_sha256=campaign.MOBILENET_WEIGHTS)
            model["preprocessing"] = dict(color="RGB", input_layout="NCHW", input_range=[0, 1], resize_shorter=256, center_crop=[224, 224],
                                          interpolation="bilinear", antialias=True, mean=[.485, .456, .406], std=[.229, .224, .225])
        save(model_bundle / "bundle.json", model)
        workloads.append(dict(id=name, type="model", bundle=str(model_bundle), model_id=model_id))
    path = tmp_path / "complete-campaign.json"
    plan = campaign.prepare(plan["package"], plan["package_sha256"], workloads, TARGET, path)
    evidence_paths = []
    def preserve(value):
        destination = tmp_path / (value["run_id"] + ".json")
        save(destination, value)
        evidence_paths.append(destination)
    for seed in campaign.SEEDS:
        preserve(report(plan, stage="screen", seed=seed, session="discovery"))
    frozen_path = tmp_path / "shortlist.json"
    frozen = campaign.shortlist(path, evidence_paths, frozen_path)
    rule = dict(request=REQUEST, variant=VARIANT, kind="performance")
    rules_hash = package.digest([rule])
    for index, seed in enumerate(campaign.HOLDOUT):
        for stage, gain in (("confirm", 10), ("noise", 0), ("slower", -50), ("trace", 10), ("incorrect", 10)):
            value = report(plan, stage=stage, seed=seed, session=f"fresh-{index}", improvement=gain)
            value["shortlist_sha256"] = frozen["shortlist_sha256"]
            if stage == "trace":
                value["profiling"] = "trace"
                value["dispatches"] = [dict(kernel_name=SHADER, spirv_sha256=manifest["shaders"][SHADER]["spirv_sha256"],
                                            local_size=[8, 4, 1], global_size=[3, 7, 1]),
                                        dict(kernel_name="stock_add", shader_origin="stock")]
            if stage == "incorrect":
                value["incorrect_rejected"] = True
            preserve(value)
        for work in plan["workloads"]:
            if work["type"] == "operator":
                continue
            value = report(plan, stage="correctness" if work["type"] == "regression" else "model", seed=seed,
                           session=f"fresh-{index}", suffix=work["id"], improvement=15)
            value.update(workload_id=work["id"], bundle_sha256=work["bundle_sha256"], shortlist_sha256=frozen["shortlist_sha256"],
                         rules_sha256=rules_hash, model_id=work.get("model_id"), request=None)
            preserve(value)
            if work["type"] == "model":
                value = copy.deepcopy(value)
                value.update(stage="sustained", run_id=value["run_id"] + "-xr", duration_seconds=600,
                             refresh_hz=72, focus_lost=False, order=["baseline", "comparison"],
                             arm_duration_seconds=dict(baseline=600.1, comparison=600.2),
                             frames=dict(baseline=[10] * 100, comparison=[10] * 100),
                             missed_frame_rate=dict(baseline=.01, comparison=.015))
                preserve(value)
    result = campaign.assess(path, evidence_paths, frozen_path)
    assert not result["failures"], result["failures"]
    assert all(result["gates"].values()), result["gates"]
    contract = package.qualification_module(plan["package"])
    assert contract.eligible_rules(result["record"], manifest, TARGET, manifest["runtime_revision"]) == [rule]
    # One model length cannot stand in for another static model workload.
    removed = [p for p in evidence_paths if "minilm256" not in p.name]
    broken = campaign.assess(path, removed, frozen_path)
    assert not broken["gates"]["models"] and not broken["gates"]["sustained"]
    assert broken["record"]["status"] == "pending"
    # Reusing a discovery hardware session also prevents promotion.
    sample_path = next(p for p in evidence_paths if "confirm" in p.name)
    altered = json.loads(sample_path.read_text())
    altered["session_id"] = "discovery"
    save(sample_path, altered)
    confirmation_only = [p for p in evidence_paths if "screen" not in p.name]
    assert not campaign.assess(path, confirmation_only, frozen_path)["gates"]["holdout"]
    # Correctness evidence must exercise the final rule set across the full suite.
    altered["session_id"] = "fresh-0"
    save(sample_path, altered)
    regression_path = next(p for p in evidence_paths if "correctness" in p.name)
    altered = json.loads(regression_path.read_text())
    altered["rules_sha256"] = package.digest([])
    save(regression_path, altered)
    assert not campaign.assess(path, evidence_paths, frozen_path)["gates"]["correctness"]


def test_resealed_shortlist_cannot_change_requests_or_choose_unknown_variants(setup_campaign, tmp_path):
    path, plan, _ = setup_campaign
    report_paths = []
    for seed in campaign.SEEDS:
        destination = tmp_path / f"discovery-{seed}.json"
        save(destination, report(plan, stage="screen", seed=seed))
        report_paths.append(destination)
    original = campaign.shortlist(path, report_paths, tmp_path / "shortlist.json")
    for field, value in (("request", dict(REQUEST, input_shape=[1, 8, 5])), ("variants", ["unknown"]), ("kind", "correctness_repair")):
        altered = copy.deepcopy(original)
        altered["selections"][0][field] = value
        altered["shortlist_sha256"] = package.digest({k: v for k, v in altered.items() if k != "shortlist_sha256"})
        save(tmp_path / "changed-shortlist.json", altered)
        with pytest.raises(ValueError):
            campaign.load_shortlist(tmp_path / "changed-shortlist.json", plan)


def test_v1_model_gate_rejects_toy_names_missing_tensor_inputs_and_unpinned_weights():
    value = dict(model_id="my-minilm-toy", model_revision=campaign.MINILM_REVISION, weights_sha256=campaign.MINILM_WEIGHTS,
                 input_specs=[dict(name="input_ids", shape=[1, 32], dtype="int64")],
                 output_specs=[dict(name="embedding", shape=[1, 384], dtype="float32")], preprocessing={})
    assert campaign.model_contract(value) is None
    value["model_id"] = campaign.MINILM_ID
    assert campaign.model_contract(value) is None


def test_regression_id_cannot_alias_an_unrelated_bundle(setup_campaign, tmp_path):
    _, plan, _ = setup_campaign
    with pytest.raises(ValueError, match="Regression bundle identity"):
        campaign.prepare(plan["package"], plan["package_sha256"],
                         [dict(id="linear_up_1", type="regression", bundle=plan["workloads"][0]["bundle"])],
                         TARGET, tmp_path / "alias.json")


def test_shared_spirv_does_not_substitute_for_selected_workgroup_or_tile(setup_campaign):
    _, plan, manifest = setup_campaign
    value = report(plan, stage="trace")
    value["profiling"] = "trace"
    value["dispatches"] = [dict(kernel_name=SHADER, spirv_sha256=manifest["shaders"][SHADER]["spirv_sha256"],
                                local_size=[8, 4, 1], global_size=[3, 7, 1])]
    assert not campaign.evidence(plan, [value])[1]
    value["dispatches"][0]["local_size"] = [16, 4, 1]
    assert campaign.evidence(plan, [value])[1]
    value["dispatches"][0]["local_size"] = [8, 4, 1]
    value["dispatches"][0]["global_size"] = [3, 4, 1]
    assert campaign.evidence(plan, [value])[1]
