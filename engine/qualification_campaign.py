"""Reproducible local qualification of the compiled xrGPU inference package.

Native evidence is self-reported measurement, not device attestation. Host
fixtures exercise the protocol but cannot authorize production selections.
"""

import argparse
import copy
import hashlib
import json
import math
import random
import shlex
import statistics
import time
import uuid
from pathlib import Path, PurePosixPath

from engine.adb import ADB
from engine.optimized_package import canonical, contained, digest, qualification_module, sha, verify
from engine.paths import STATE
from engine.reports import save

TARGET_FIELDS = ("model", "gpu", "vendor_id", "device_id", "driver", "api_version")
STAGES = ("screen", "confirm", "correctness", "noise", "incorrect", "slower", "trace", "model", "sustained")
SEEDS = (20261025, 20261026, 20261027)
HOLDOUT = (20261125, 20261126, 20261127)
MINILM_ID = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_REVISION = "c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
MINILM_WEIGHTS = "53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db"
MOBILENET_ID = "torchvision/mobilenet_v2"
MOBILENET_WEIGHTS = "b03531047ffacf1e2488318dcd2aba1126cde36e3bfe1aa5cb07700aeeee9889"


def read(path):
    return json.loads(Path(path).read_text())


def target_identity(value):
    if any(k not in value or value[k] is None or value[k] == "" for k in TARGET_FIELDS):
        raise ValueError("Exact model, GPU, vendor/device IDs, driver and API version required")
    if any(not isinstance(value[k], str) or not value[k].strip() for k in ("model", "gpu")):
        raise ValueError("Device and GPU names must be nonempty strings")
    if any(type(value[k]) is not int or value[k] < 0 for k in ("vendor_id", "device_id", "api_version")):
        raise ValueError("GPU vendor/device/API IDs must be nonnegative integers")
    if not ((type(value["driver"]) is int and value["driver"] >= 0) or
            (isinstance(value["driver"], str) and value["driver"].strip())):
        raise ValueError("Driver identity must be an encoded version or nonempty string")
    return {k: value[k] for k in TARGET_FIELDS}


def campaign_digest(value):
    return digest({k: v for k, v in value.items() if k != "campaign_sha256"})


def load_campaign(path):
    campaign = read(path)
    if campaign.get("schema_version") != 1 or campaign.get("kind") != "xrgpu.qualification.campaign":
        raise ValueError("Unsupported qualification campaign")
    if campaign.get("campaign_sha256") != campaign_digest(campaign):
        raise ValueError("Campaign changed after preparation")
    verify(campaign["package"], campaign["package_sha256"], campaign["runtime_revision"])
    target_identity(campaign["target"])
    if set(campaign["discovery_seeds"]) & set(campaign["holdout_seeds"]):
        raise ValueError("Discovery and final validation seeds must be disjoint")
    for work in campaign["workloads"]:
        manifest = read(Path(work["bundle"]) / "bundle.json")
        if digest(manifest) != work["bundle_sha256"]:
            raise ValueError("Workload changed after campaign preparation")
        for name, expected in manifest["files"].items():
            if sha(contained(Path(work["bundle"]).resolve(), name)) != expected:
                raise ValueError("Model fixture changed after campaign preparation")
    return campaign


def variants(package, request):
    op = request["operation"]
    if op == "linear":
        return [v["id"] for v in package["linear_variants"]]
    if op == "gelu":
        return ["semantics_v1"]
    if op == "grouped_conv2d":
        return ["grouped_v1"]
    raise ValueError("No replacement variants for operation " + op)


def regression_contract(row, manifest, definitions, suite_revision):
    case = definitions.get(row["id"])
    if (case is None or manifest.get("model_id") != "xrbench/" + row["id"]
            or manifest.get("model_revision") != suite_revision or manifest.get("metadata", {}).get("case") != case):
        raise ValueError("Regression bundle identity or case definition differs from the current suite")
    inputs = [dict(name=f"input_{i}", shape=v["shape"], dtype=v["dtype"]) for i, v in enumerate(case["inputs"])]
    outputs = manifest.get("outputs", [])
    if manifest.get("inputs") != inputs or len(outputs) != 1 or outputs[0].get("dtype") != "float32":
        raise ValueError("Regression tensor interface differs from the declared case")
    shape, params = case["inputs"][0]["shape"], case.get("params", {})
    expected = None
    if case["operation"] == "linear":
        expected = shape[:-1] + [params["out_features"]]
    elif case["operation"] == "gelu":
        expected = shape
    elif case["operation"] == "conv2d":
        stride, padding, kernel = params.get("stride", 1), params.get("padding", 0), params["kernel"]
        expected = [shape[0], params["out_channels"], *[(n + 2 * padding - kernel) // stride + 1 for n in shape[-2:]]]
    if expected is not None and outputs[0].get("shape") != expected:
        raise ValueError("Regression output shape differs from the operator definition")


def prepare(package_path, expected_hash, workloads, target, output, *, discovery=SEEDS, holdout=HOLDOUT):
    package_path = Path(package_path).resolve()
    package = verify(package_path, expected_hash)
    target = target_identity(target)
    if len(set(discovery)) < 3 or len(set(holdout)) < 3 or set(discovery) & set(holdout):
        raise ValueError("At least three distinct discovery and held-out seeds required")
    from engine.qualification_fixtures import cases
    from engine.workloads import revision
    definitions = {case["id"]: case for case in cases()}
    suite_revision = revision()
    entries, ids = [], set()
    for value in workloads:
        row = copy.deepcopy(value)
        if row.get("type") not in ("operator", "regression", "model") or row.get("id") in ids:
            raise ValueError("Unique workload IDs and operator/regression/model types required")
        ids.add(row["id"])
        row["bundle"] = str(Path(row["bundle"]).resolve())
        manifest = read(Path(row["bundle"]) / "bundle.json")
        if manifest.get("runtime_revision") != package["runtime_revision"]:
            raise ValueError("Model and kernel package runtime revisions differ")
        if "kernel_package" in manifest and manifest["kernel_package"].get("package_sha256") != package["package_sha256"]:
            raise ValueError("Model requires a different compiled package")
        if "vulkan" not in manifest.get("programs", {}) or not manifest.get("fixtures"):
            raise ValueError("Vulkan program and hashed reference fixtures required")
        for name, expected in manifest["files"].items():
            if sha(contained(Path(row["bundle"]), name)) != expected:
                raise ValueError("Model file changed before campaign preparation")
        if row["type"] == "regression":
            regression_contract(row, manifest, definitions, suite_revision)
        row["bundle_sha256"] = digest(manifest)
        row["fixture_seeds"] = [f["seed"] for f in manifest["fixtures"]]
        if row["type"] == "model":
            if row.get("model_id", manifest.get("model_id")) != manifest.get("model_id"):
                raise ValueError("Model workload identity differs from hashed model bundle")
            row["model_id"] = manifest["model_id"]
            row["input_specs"] = manifest["inputs"]
            row["output_specs"] = manifest["outputs"]
            row["model_revision"] = manifest.get("model_revision")
            row["weights_sha256"] = manifest.get("metadata", {}).get("weights_sha256")
            row["preprocessing"] = manifest.get("preprocessing", {})
        if not set(discovery).issubset(row["fixture_seeds"]) or not set(holdout).issubset(row["fixture_seeds"]):
            raise ValueError("Each workload needs all discovery and confirmation fixtures")
        if row["type"] == "operator":
            row["variants"] = variants(package, row["request"])
            row["rule_kind"] = "performance" if row["request"]["operation"] == "linear" else "correctness_repair"
        entries.append(row)
    if not entries:
        raise ValueError("Qualification needs explicit workloads")
    campaign = dict(schema_version=1, kind="xrgpu.qualification.campaign", campaign_id="campaign-" + uuid.uuid4().hex,
                    package=str(package_path), package_id=package["package_id"], package_sha256=package["package_sha256"],
                    runtime_revision=package["runtime_revision"], target=target, workloads=entries,
                    discovery_seeds=list(discovery), holdout_seeds=list(holdout),
                    protocol=dict(pairs=10, warmups=20, samples=100, sessions=3, refresh_hz=72, sustained_seconds=600))
    campaign["campaign_sha256"] = campaign_digest(campaign)
    output = Path(output)
    if output.exists():
        raise FileExistsError("Choose a new campaign path; prepared campaigns are immutable")
    save(output, campaign)
    load_campaign(output)
    return campaign


def bootstrap(values, seed=20261025):
    if min(values) == max(values):
        return [values[0], values[0]]
    rng = random.Random(seed)
    draws = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(2000))
    return [draws[49], draws[1949]]


def percentile(values, q):
    values = sorted(values)
    index = (len(values) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def samples(arm, count):
    values = arm.get("samples_ms", [])
    if len(values) < count or any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in values):
        raise ValueError("Incomplete or invalid raw timing samples")
    return values


def pair_metrics(report, protocol):
    pairs = report.get("pairs", [])
    if len(pairs) < protocol["pairs"] or report.get("warmups", 0) < protocol["warmups"]:
        raise ValueError("Insufficient paired measurements or warmups")
    if {p.get("index") for p in pairs} != set(range(len(pairs))):
        raise ValueError("Duplicate or missing pair index")
    orders = [p.get("order") for p in pairs]
    if any(o not in (["baseline", "comparison"], ["comparison", "baseline"]) for o in orders):
        raise ValueError("Invalid paired arm order")
    if abs(orders.count(["baseline", "comparison"]) - orders.count(["comparison", "baseline"])) > 1:
        raise ValueError("Paired order must be balanced")
    changes, tails = [], []
    for pair in pairs:
        for arm in (pair["baseline"], pair["comparison"]):
            if any(arm.get(k, {}).get("passed") is not True for k in ("correctness_before", "correctness_after")):
                raise ValueError("A timed arm failed numerical validation")
        if pair["baseline"].get("conditions") != pair["comparison"].get("conditions") or not pair["baseline"].get("conditions"):
            raise ValueError("Thermal and charging conditions must match within pairs")
        a, b = [samples(pair[k], protocol["samples"]) for k in ("baseline", "comparison")]
        changes.append(100 * (1 - statistics.median(b) / statistics.median(a)))
        tails.append(100 * (percentile(b, .95) / percentile(a, .95) - 1))
    return dict(improvement_percent=statistics.mean(changes), ci95_percent=bootstrap(changes),
                p95_change_percent=statistics.mean(tails), pairs=len(pairs))


def operator_trace(request, variant, dispatches, package):
    op, attributes = request["operation"], request["attributes"]
    if op == "linear":
        specification = next(v for v in package["linear_variants"] if v["id"] == variant)
        tile, storage = specification["tile_m"], specification["weight_storage"]
        name = f"xrgpu_linear_m{tile}" + ("_bias" if attributes.get("bias") else "") + f"_texture3d_{storage}_float"
        output = request["output_shape"]
        expected_global = [(output[-1] + 3) // 4, (output[-2] + tile - 1) // tile, output[-3] if len(output) == 3 else 1]
        if not any(d.get("kernel_name") == name and d.get("local_size") == specification["workgroup"]
                   and d.get("global_size") == expected_global for d in dispatches):
            raise ValueError("Linear trace differs from selected tile, weight storage or dispatch geometry")
    elif op == "gelu":
        mode = "erf" if attributes.get("approximate") == "none" else "tanh"
        name = f"xrgpu_gelu_{mode}_float_{request['layout']}"
        if not any(d.get("kernel_name") == name for d in dispatches):
            raise ValueError("GELU trace differs from requested semantics or storage")
    elif op == "grouped_conv2d":
        name = "xrgpu_grouped_conv2d" + ("_clamp" if attributes.get("clamp") else "") + "_float"
        if not any(d.get("kernel_name") == name for d in dispatches):
            raise ValueError("Grouped convolution trace differs from requested clamp semantics")


def evidence(campaign, reports):
    seen = set()
    package = verify(campaign["package"], campaign["package_sha256"])
    work = {w["id"]: w for w in campaign["workloads"]}
    valid, failures = [], []
    for report in reports:
        try:
            identity = report.get("run_id")
            if not identity or identity in seen:
                raise ValueError("Duplicate or missing run identity")
            seen.add(identity)
            if report.get("kind") != "xrgpu.qualification.evidence" or report.get("schema_version") != 1:
                raise ValueError("Unsupported native evidence")
            if (report.get("source") != "native_headset" or report.get("execution_target") != "headset"
                    or report.get("synthetic") is not False or not report.get("installation_id")):
                raise ValueError("Physical headset evidence required")
            for name in ("campaign_id", "package_sha256", "runtime_revision"):
                if report.get(name) != campaign[name]:
                    raise ValueError("Evidence differs from campaign " + name)
            if report.get("campaign_sha256") != campaign["campaign_sha256"] or target_identity(report["target"]) != campaign["target"]:
                raise ValueError("Evidence target or immutable campaign differs")
            if report.get("status") != "complete" or report.get("backend") != "executorch_vulkan":
                raise ValueError("Complete Vulkan execution required; CPU fallback cannot qualify GPU kernels")
            row = work[report["workload_id"]]
            if report.get("bundle_sha256") != row["bundle_sha256"]:
                raise ValueError("Evidence has a different model or fixture bundle")
            if report.get("request") != row.get("request"):
                raise ValueError("Evidence has a different operation request")
            stage = report["stage"]
            if stage not in STAGES:
                raise ValueError("Unknown qualification stage")
            if stage in ("correctness", "model", "sustained") and report.get("selection_policy") != "exact_rules":
                raise ValueError("Full-model and regression evidence must use exact production candidate rules")
            seeds = campaign["discovery_seeds"] if stage == "screen" else campaign["holdout_seeds"]
            if report.get("fixture_seed") not in seeds:
                raise ValueError("Discovery evidence cannot be reused for confirmation")
            if report.get("profiling") not in ("latency", "trace") or not report.get("session_id"):
                raise ValueError("Session and profiling identities required")
            if row["type"] == "operator":
                if report.get("request") != row["request"] or report.get("variant") not in row["variants"]:
                    raise ValueError("Exact operator descriptor and packaged variant required")
                if stage not in ("noise", "correctness") and not any(
                    d.get("request") == row["request"] and d.get("variant") == report["variant"] and d.get("implementation") == "xrgpu"
                    for d in report.get("decisions", [])
                ):
                    raise ValueError("Candidate execution was not observed")
            if report["profiling"] == "trace":
                dispatches = report.get("dispatches", [])
                replacements = [d for d in dispatches if d.get("kernel_name") in package["shaders"]]
                if (not replacements or any(d.get("spirv_sha256") != package["shaders"][d["kernel_name"]]["spirv_sha256"] for d in replacements)
                        or any(d.get("kernel_name") not in package["shaders"] and d.get("shader_origin") != "stock" for d in dispatches)):
                    raise ValueError("Trace does not identify packaged shader binaries")
                if row["type"] == "operator":
                    operator_trace(row["request"], report["variant"], dispatches, package)
            elif report.get("dispatches"):
                raise ValueError("Timing evidence must be collected without dispatch tracing")
            item = copy.deepcopy(report)
            if stage == "incorrect":
                if item.get("incorrect_rejected") is not True:
                    raise ValueError("Deliberately incorrect output was not rejected")
            elif stage == "sustained":
                if item.get("duration_seconds", 0) < 600 or item.get("refresh_hz") != 72 or item.get("focus_lost") is not False:
                    raise ValueError("Sustained evidence requires ten minutes at focused 72 Hz")
                if item.get("order") not in (["baseline", "comparison"], ["comparison", "baseline"]):
                    raise ValueError("Sustained arm order must be preserved")
                for arm in ("baseline", "comparison"):
                    frames = item.get("frames", {}).get(arm, [])
                    duration = item.get("arm_duration_seconds", {}).get(arm, 0)
                    missed = item.get("missed_frame_rate", {}).get(arm)
                    if (duration < 600 or len(frames) < 100 or any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in frames)
                            or type(missed) not in (int, float) or not math.isfinite(missed) or not 0 <= missed <= 1):
                        raise ValueError("Raw real frame times, bounded missed rates and measured arm durations required")
            else:
                item["metrics"] = pair_metrics(item, campaign["protocol"])
            valid.append(item)
        except (ValueError, KeyError, TypeError, ZeroDivisionError) as error:
            failures.append(dict(run_id=report.get("run_id"), reason=str(error)))
    return valid, failures


def shortlist(campaign_path, report_paths, output):
    campaign = load_campaign(campaign_path)
    reports, failures = evidence(campaign, [read(p) for p in report_paths])
    if failures or any(r["stage"] != "screen" for r in reports):
        raise ValueError("Shortlisting requires valid discovery-stage reports only")
    selections = []
    for work in campaign["workloads"]:
        if work["type"] != "operator":
            continue
        scores = []
        for variant in work["variants"]:
            rows = [r for r in reports if r["workload_id"] == work["id"] and r["variant"] == variant]
            if {r["fixture_seed"] for r in rows} != set(campaign["discovery_seeds"]):
                raise ValueError("Screen every variant on all discovery fixtures before freezing the shortlist")
            scores.append((statistics.mean(r["metrics"]["improvement_percent"] for r in rows), variant))
        winners = [v for _, v in sorted(scores, reverse=True)[:3]]
        selections.append(dict(workload_id=work["id"], request=work["request"], variants=winners, kind=work["rule_kind"]))
    result = dict(schema_version=1, kind="xrgpu.qualification.shortlist", campaign_sha256=campaign["campaign_sha256"],
                  reports=[digest(read(p)) for p in report_paths], selections=selections,
                  discovery_session_ids=sorted({r["session_id"] for r in reports}))
    result["shortlist_sha256"] = digest(result)
    if Path(output).exists():
        raise FileExistsError("Shortlists are immutable; choose a new output")
    save(output, result)
    return result


def load_shortlist(path, campaign):
    value = read(path)
    if value.get("schema_version") != 1 or value.get("kind") != "xrgpu.qualification.shortlist":
        raise ValueError("Unsupported shortlist contract")
    if value.get("campaign_sha256") != campaign["campaign_sha256"] or value.get("shortlist_sha256") != digest({k: v for k, v in value.items() if k != "shortlist_sha256"}):
        raise ValueError("Shortlist changed or belongs to another campaign")
    work = {w["id"]: w for w in campaign["workloads"] if w["type"] == "operator"}
    seen, requests = set(), set()
    for selection in value["selections"]:
        key = selection.get("workload_id")
        if key not in work or key in seen:
            raise ValueError("Shortlist must identify each declared operator exactly once")
        seen.add(key)
        expected = work[key]
        request_key = canonical(selection.get("request"))
        if request_key in requests or selection.get("request") != expected["request"] or selection.get("kind") != expected["rule_kind"]:
            raise ValueError("Shortlist operation descriptor or rule kind differs from campaign")
        requests.add(request_key)
        choices = selection["variants"]
        if not 1 <= len(choices) <= 3 or len(set(choices)) != len(choices) or not set(choices) <= set(expected["variants"]):
            raise ValueError("At most three distinct packaged finalists per descriptor")
    if seen != set(work) or not value.get("discovery_session_ids") or not value.get("reports"):
        raise ValueError("Complete discovery evidence and session identities required")
    return value


def model_contract(work):
    """The v1 application gate names actual pinned models and complete interfaces."""
    if work["model_id"] == MINILM_ID:
        inputs = work["input_specs"]
        if not inputs or len(inputs[0].get("shape", [])) != 2:
            return None
        length = inputs[0]["shape"][-1]
        expected = [dict(name=n, shape=[1, length], dtype="int64") for n in ("input_ids", "attention_mask", "token_type_ids")]
        preprocessing = dict(tokenizer="WordPiece", sequence_length=length, pooling="attention_mask_mean", output_normalization="L2",
                             padding="max_length", truncation=True)
        if (length in (32, 128, 256) and inputs == expected and work["output_specs"] == [dict(name="embedding", shape=[1, 384], dtype="float32")]
                and work["model_revision"] == MINILM_REVISION and work["weights_sha256"] == MINILM_WEIGHTS
                and all(work["preprocessing"].get(k) == v for k, v in preprocessing.items())):
            return ("minilm", length)
    elif work["model_id"] == MOBILENET_ID:
        preprocessing = dict(color="RGB", input_layout="NCHW", input_range=[0, 1], resize_shorter=256, center_crop=[224, 224],
                             interpolation="bilinear", antialias=True, mean=[.485, .456, .406], std=[.229, .224, .225])
        if (work["input_specs"] == [dict(name="image", shape=[1, 3, 224, 224], dtype="float32")]
                and work["output_specs"] == [dict(name="logits", shape=[1, 1000], dtype="float32")]
                and work["model_revision"] == "IMAGENET1K_V1:" + MOBILENET_WEIGHTS and work["weights_sha256"] == MOBILENET_WEIGHTS
                and all(work["preprocessing"].get(k) == v for k, v in preprocessing.items())):
            return ("mobilenetv2", 224)
    return None


def selected_by_rules(report, rules):
    """Only selections observed in the frozen rule set may validate deployment."""
    return all(any(d.get("request") == r["request"] and d.get("variant") == r["variant"] for r in rules)
               for d in report.get("decisions", []) if d.get("implementation") == "xrgpu")


def uses_performance_rule(report, rules):
    return any(d.get("implementation") == "xrgpu" and d.get("request") == r["request"]
               and d.get("variant") == r["variant"] for d in report.get("decisions", [])
               for r in rules if r["kind"] == "performance")


def assess(campaign_path, report_paths, shortlist_path=None):
    campaign = load_campaign(campaign_path)
    package = verify(campaign["package"], campaign["package_sha256"])
    reports = [read(p) for p in report_paths]
    valid, failures = evidence(campaign, reports)
    frozen = load_shortlist(shortlist_path, campaign) if shortlist_path else None
    gates = dict(hardware=bool(valid) and not failures, provenance=bool(valid) and not failures,
                 correctness=False, controls=False, holdout=False, performance=False, models=False, sustained=False)
    confirmation = [r for r in valid if r["stage"] != "screen"]
    discovery_sessions = set(frozen["discovery_session_ids"]) if frozen else set()
    fresh_sessions = not discovery_sessions & {r["session_id"] for r in confirmation}
    finals = frozen["selections"] if frozen else []
    selected_rules, rule_results = [], []
    for final in finals:
        for variant in final["variants"]:
            rows = [r for r in confirmation if r["workload_id"] == final["workload_id"] and r.get("variant") == variant]
            timed = [r for r in rows if r["stage"] == "confirm" and r["profiling"] == "latency"
                     and r.get("shortlist_sha256") == frozen["shortlist_sha256"]]
            sessions = {r["session_id"] for r in timed}
            seeds = {r["fixture_seed"] for r in timed}
            passing = len(sessions) >= 3 and seeds == set(campaign["holdout_seeds"])
            speed = passing and all(r["metrics"]["improvement_percent"] >= 5 and r["metrics"]["ci95_percent"][0] > 0
                                    and r["metrics"]["p95_change_percent"] <= 5 for r in timed)
            def covered(stage, predicate):
                return {r["fixture_seed"] for r in rows if r["stage"] == stage and predicate(r)} == set(campaign["holdout_seeds"])
            controls = (covered("noise", lambda r: abs(r["metrics"]["improvement_percent"]) < 5)
                        and covered("incorrect", lambda r: True)
                        and covered("slower", lambda r: r["metrics"]["ci95_percent"][1] < -5)
                        and covered("trace", lambda r: r["profiling"] == "trace"))
            rule_results.append(dict(workload_id=final["workload_id"], variant=variant, correctness=passing,
                                     performance=speed, controls=controls, sessions=len(sessions)))
        # Freeze only a confirmed winner; passing exploratory variants never become rules.
        eligible = [r for r in rule_results if r["workload_id"] == final["workload_id"] and r["correctness"] and r["controls"]]
        if eligible:
            winners = [r for r in eligible if r["performance"]] if final["kind"] == "performance" else eligible
            if winners:
                winner = winners[0]
                selected_rules.append(dict(request=final["request"], variant=winner["variant"], kind=final["kind"]))
    candidate_correctness = bool(finals) and all(any(
        r["workload_id"] == final["workload_id"] and r["correctness"] and r["controls"] for r in rule_results) for final in finals)
    expected_regressions = {w["id"] for w in campaign["workloads"] if w["type"] == "regression"}
    from engine.qualification_fixtures import cases
    required_regressions = {w["id"] for w in cases()}
    final_rules_hash = digest(selected_rules)
    regression_rows = [r for r in confirmation if r["stage"] == "correctness" and r.get("rules_sha256") == final_rules_hash
                       and selected_by_rules(r, selected_rules)]
    regressions = all({r["fixture_seed"] for r in regression_rows if r["workload_id"] == key} == set(campaign["holdout_seeds"])
                      for key in expected_regressions)
    gates["correctness"] = candidate_correctness and regressions and required_regressions <= expected_regressions
    gates["controls"] = bool(selected_rules) and all(any(x["workload_id"] == s["workload_id"] and x["controls"] for x in rule_results) for s in finals)
    gates["holdout"] = bool(frozen) and bool(confirmation) and fresh_sessions and all(r.get("shortlist_sha256") == frozen["shortlist_sha256"] for r in confirmation)
    performance_rules = [r for r in selected_rules if r["kind"] == "performance"]
    gates["performance"] = bool(performance_rules) and all(any(
        x["variant"] == r["variant"] and x["performance"] and any(
            final["workload_id"] == x["workload_id"] and final["request"] == r["request"] for final in finals)
        for x in rule_results) for r in performance_rules)
    model_rows = [r for r in confirmation if r["stage"] == "model" and r.get("rules_sha256") == final_rules_hash
                  and selected_by_rules(r, selected_rules)]
    model_workloads = [w for w in campaign["workloads"] if w["type"] == "model"]
    model_passes, model_wins = [], []
    for work in model_workloads:
        rows = [r for r in model_rows if r.get("model_id") == work["model_id"] and r["workload_id"] == work["id"]]
        enough = (len({r["session_id"] for r in rows}) >= 3
                  and {r["fixture_seed"] for r in rows} == set(campaign["holdout_seeds"]))
        model_passes.append(enough and all(r["metrics"]["improvement_percent"] >= -5 and r["metrics"]["p95_change_percent"] <= 5 for r in rows))
        model_wins.append(enough and all(r["metrics"]["improvement_percent"] >= 10
                                        and uses_performance_rule(r, selected_rules) for r in rows))
    contracts = {model_contract(w) for w in model_workloads}
    gates["models"] = ({("minilm", 32), ("minilm", 128), ("minilm", 256), ("mobilenetv2", 224)} <= contracts
                       and None not in contracts
                       and all(model_passes) and any(model_wins))
    sustained = [r for r in confirmation if r["stage"] == "sustained" and r.get("rules_sha256") == final_rules_hash
                 and selected_by_rules(r, selected_rules)]
    sustained_coverage = bool(model_workloads) and all(
        len({r["session_id"] for r in sustained if r["workload_id"] == w["id"]}) >= 3
        and {r["fixture_seed"] for r in sustained if r["workload_id"] == w["id"]} == set(campaign["holdout_seeds"])
        for w in model_workloads)
    gates["sustained"] = sustained_coverage and all(
        percentile(r["frames"]["comparison"], .95) <= percentile(r["frames"]["baseline"], .95) * 1.05
        and r["missed_frame_rate"]["comparison"] - r["missed_frame_rate"]["baseline"] <= .01 for r in sustained)
    contract = qualification_module(campaign["package"], campaign["package_sha256"])
    record = contract.make_record(package, campaign["target"], selected_rules, gates, [digest(r) for r in reports], synthetic=False)
    model_selections = {w["id"]: [rule for rule in record["rules"] if any(
        row["workload_id"] == w["id"] and any(d.get("implementation") == "xrgpu"
            and d.get("request") == rule["request"] and d.get("variant") == rule["variant"]
            for d in row.get("decisions", [])) for row in model_rows)] for w in model_workloads}
    return dict(record=record, gates=gates, missing=[k for k, passed in gates.items() if not passed],
                failures=failures, rules=rule_results, candidate_rules=selected_rules,
                model_selections=model_selections,
                candidate_correctness=candidate_correctness,
                candidate_rules_sha256=final_rules_hash,
                evidence_note="Self-reported native evidence; not physical-device attestation")


def run(campaign_path, runner, serial, stage, workload_id, seed, session_id, output, *, variant=None,
        shortlist_path=None, adb_path=None, timeout=7200, rules=None, xr=False):
    campaign = load_campaign(campaign_path)
    workload = next(w for w in campaign["workloads"] if w["id"] == workload_id)
    if not 30 <= timeout <= 7200 or not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A fresh session identity and a 30-7200 second deadline required")
    if stage not in STAGES or seed not in (campaign["discovery_seeds"] if stage == "screen" else campaign["holdout_seeds"]):
        raise ValueError("Stage and fixture seed do not match campaign split")
    if stage != "screen" and not shortlist_path:
        raise ValueError("Freeze the discovery shortlist before confirmation")
    frozen = load_shortlist(shortlist_path, campaign) if shortlist_path else None
    if ((stage in ("screen", "confirm", "noise", "incorrect", "slower") and workload["type"] != "operator")
            or (stage == "correctness" and workload["type"] != "regression")
            or (stage in ("model", "sustained") and workload["type"] != "model")):
        raise ValueError("This stage does not match the declared workload type")
    if stage in ("correctness", "model", "sustained"):
        if not isinstance(rules, list) or not rules or len(rules) != len(frozen["selections"]):
            raise ValueError("Full-suite and model validation require the frozen candidate rule array")
        contract = qualification_module(campaign["package"], campaign["package_sha256"])
        expected = {canonical(s["request"]): s for s in frozen["selections"]}
        seen = set()
        for rule in rules:
            contract.validate_rule(rule)
            key = canonical(rule["request"])
            choice = expected.get(key)
            if key in seen or choice is None or rule["kind"] != choice["kind"] or rule["variant"] not in choice["variants"]:
                raise ValueError("Candidate rules differ from the frozen shortlist")
            seen.add(key)
    if workload["type"] == "operator" and variant not in workload["variants"]:
        raise ValueError("Choose an exact packaged candidate variant")
    if frozen and workload["type"] == "operator" and variant not in next(s["variants"] for s in frozen["selections"] if s["workload_id"] == workload_id):
        raise ValueError("Confirmation candidate was not shortlisted")
    runner = Path(runner).resolve()
    metadata = read(runner.with_suffix(".json"))
    if metadata.get("package_sha256") != campaign["package_sha256"] or metadata.get("runner_sha256") != sha(runner):
        raise ValueError("Build the native qualification runner for this exact package")
    run_id = "run-" + uuid.uuid4().hex
    package = verify(campaign["package"], campaign["package_sha256"])
    job = dict(schema_version=1, kind="xrgpu.qualification.evidence", campaign_id=campaign["campaign_id"],
               campaign_sha256=campaign["campaign_sha256"], run_id=run_id, session_id=session_id, stage=stage,
               workload_id=workload_id, bundle_sha256=workload["bundle_sha256"], fixture_seed=seed, variant=variant or "rules",
               request=workload.get("request"), model_id=workload.get("model_id"), target=campaign["target"],
               package_sha256=campaign["package_sha256"], runtime_revision=campaign["runtime_revision"],
               runner_sha256=metadata["runner_sha256"],
               protocol=campaign["protocol"], shortlist_sha256=frozen["shortlist_sha256"] if frozen else None,
               rules=rules or [], rules_sha256=digest(rules or []), profiling="trace" if stage == "trace" else "latency",
               timeout_seconds=timeout, shader_hashes={name: s["spirv_sha256"] for name, s in package["shaders"].items()})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    adb = ADB(adb_path)
    if not adb.connected(serial):
        raise ValueError("Selected headset is not connected and authorized")
    remote = "/data/local/tmp/xrgpu-qualification/" + run_id
    job["bundle"] = remote + "/bundle"
    job["output"] = remote + "/report.json"
    job["installation_id"] = hashlib.sha256(adb.shell(serial, "settings", "get", "secure", "android_id")).hexdigest()
    use_xr = xr or stage == "sustained"
    package_name = "org.xrbench.qualification"
    if use_xr:
        apk = Path(metadata.get("xr_apk", ""))
        if not apk.is_file() or sha(apk) != metadata.get("xr_apk_sha256"):
            raise ValueError("Build the qualification runner with --xr before Android activity collection")
        adb.command("install", "-r", str(apk), serial=serial, timeout=120)
        adb.shell(serial, "run-as", package_name, "mkdir", "-p", "files/jobs", "files/runs")
        private = adb.shell(serial, "run-as", package_name, "pwd").decode().strip()
        if not private.startswith(("/data/user/", "/data/data/")) or not private.endswith("/" + package_name):
            raise ValueError("Unexpected Android application data directory")
        job["output"] = private + "/files/runs/" + run_id + ".json"
        job["bundle"] = private + "/files/bundles/" + run_id
    job_path = output / (run_id + "-job.json")
    save(job_path, job)
    if use_xr:
        # Stream verified files through run-as. Public /data/local/tmp files are
        # not reliably readable by an Android app under SELinux.
        bundle = Path(workload["bundle"])
        manifest = read(bundle / "bundle.json")
        for name in ("bundle.json", *manifest["files"]):
            relative = "files/bundles/" + run_id + "/" + name
            adb.shell(serial, "run-as", package_name, "mkdir", "-p", str(PurePosixPath(relative).parent))
            adb.shell(serial, "run-as", package_name, "sh", "-c", "cat > " + shlex.quote(relative), data=(bundle / name).read_bytes(), timeout=300)
    else:
        adb.shell(serial, "mkdir", "-p", remote)
        adb.command("push", str(runner), remote + "/runner", serial=serial, timeout=120)
        for library in runner.parent.glob("*.so"):
            if sha(library) != metadata.get("libraries", {}).get(library.name):
                raise ValueError("Native runner dependency differs from build provenance")
            adb.command("push", str(library), remote + "/" + library.name, serial=serial, timeout=120)
        adb.command("push", workload["bundle"], remote + "/bundle", serial=serial, timeout=300)
        adb.command("push", str(job_path), remote + "/job.json", serial=serial)
        adb.shell(serial, "chmod", "700", remote + "/runner")
    save(output / (run_id + "-transport.json"), dict(run_id=run_id, remote=remote, serial=serial, runner_sha256=sha(runner),
                                                   android_activity=use_xr, started=time.time()))
    try:
        if use_xr:
            adb.shell(serial, "run-as", package_name, "sh", "-c", "cat > files/jobs/" + run_id + ".json", data=canonical(job))
            adb.shell(serial, "am", "start", "-W", "-n", package_name + "/.QualificationActivity", "--es", "run_id", run_id, timeout=30)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    report_path = recover(output / (run_id + "-transport.json"), output, adb_path)
                    if read(report_path).get("status") in ("complete", "failed"):
                        return report_path
                except (OSError, RuntimeError, ValueError):
                    pass
                time.sleep(1)
            raise TimeoutError("Qualification timed out; recover the original device report")
        else:
            adb.shell(serial, "env", "LD_LIBRARY_PATH=" + remote, remote + "/runner", remote + "/job.json", timeout=timeout)
    finally:
        # Failed or interrupted runs keep their original job and device report for recovery.
        try:
            recover(output / (run_id + "-transport.json"), output, adb_path)
        except (OSError, RuntimeError, ValueError):
            pass
    return output / (run_id + ".json")


def recover(transport_path, output, adb_path=None):
    transport = read(transport_path)
    run_id = transport["run_id"]
    if not run_id.startswith("run-") or any(c not in "0123456789abcdef" for c in run_id[4:]) or len(run_id) != 36:
        raise ValueError("Invalid saved run identity")
    expected_remote = "/data/local/tmp/xrgpu-qualification/" + run_id
    if transport["remote"] != expected_remote:
        raise ValueError("Saved transport path differs from run identity")
    adb = ADB(adb_path)
    if transport.get("android_activity"):
        raw = adb.shell(transport["serial"], "run-as", "org.xrbench.qualification", "cat", "files/runs/" + run_id + ".json", timeout=15)
    else:
        raw = adb.shell(transport["serial"], "cat", expected_remote + "/report.json", timeout=15)
    if len(raw) > 128 * 1024 * 1024:
        raise ValueError("Evidence report exceeds size limit")
    value = json.loads(raw)
    if value.get("run_id") != run_id:
        raise ValueError("Recovered device report has another identity")
    destination = Path(output) / (run_id + (".json" if value.get("status") in ("complete", "failed") else "-partial.json"))
    if value.get("status") in ("complete", "failed") and destination.exists() and destination.read_bytes() != raw:
        raise FileExistsError("Preserve original report bytes; recover to a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--package-sha256", required=True)
    p.add_argument("--workloads", type=Path, required=True)
    p.add_argument("--model-bundle", action="append", type=Path, default=[], help="Append a complete static model bundle to the campaign")
    p.add_argument("--target", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("shortlist")
    p.add_argument("--campaign", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("reports", nargs="+", type=Path)
    for command in ("assess", "candidates", "export"):
        p = commands.add_parser(command)
        p.add_argument("--campaign", type=Path, required=True)
        p.add_argument("--shortlist", type=Path)
        p.add_argument("--output", type=Path)
        p.add_argument("reports", nargs="*", type=Path)
    p = commands.add_parser("run")
    p.add_argument("--campaign", type=Path, required=True)
    p.add_argument("--runner", type=Path, required=True)
    p.add_argument("--serial", required=True)
    p.add_argument("--stage", choices=STAGES, required=True)
    p.add_argument("--workload", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--variant")
    p.add_argument("--shortlist", type=Path)
    p.add_argument("--rules", type=Path)
    p.add_argument("--xr", action="store_true", help="Collect inside the Android OpenXR activity")
    p.add_argument("--adb")
    p.add_argument("--timeout", type=int, default=7200)
    p.add_argument("--output", type=Path, default=STATE / "qualification-evidence")
    p = commands.add_parser("probe")
    p.add_argument("--serial", required=True)
    p.add_argument("--adb")
    p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("recover")
    p.add_argument("transport", type=Path)
    p.add_argument("--adb")
    p.add_argument("--output", type=Path, default=STATE / "qualification-evidence")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        workloads = read(args.workloads)
        for bundle in args.model_bundle:
            manifest = read(bundle / "bundle.json")
            workloads.append(dict(id="model_" + bundle.name, type="model", bundle=str(bundle.resolve()), model_id=manifest["model_id"]))
        result = prepare(args.package, args.package_sha256, workloads, read(args.target), args.output)
    elif args.command == "shortlist":
        result = shortlist(args.campaign, args.reports, args.output)
    elif args.command in ("assess", "candidates", "export"):
        protected = [*args.reports, args.campaign] + ([args.shortlist] if args.shortlist else [])
        if args.output and args.output.resolve() in [p.resolve() for p in protected]:
            parser.error("Choose a separate output; preserve the campaign, shortlist and original reports")
        result = assess(args.campaign, args.reports, args.shortlist)
        if args.command == "candidates":
            base = ("hardware", "provenance", "controls", "holdout")
            if any(not result["gates"][g] for g in base) or not result["candidate_correctness"] or not result["candidate_rules"]:
                parser.error("Confirm candidate correctness and controls before freezing model rules")
            result = result["candidate_rules"]
        if args.command == "export":
            if result["record"]["status"] != "qualified":
                parser.error("Hardware qualification is pending: " + ", ".join(result["missing"]))
            result = result["record"]
        if args.output:
            if args.command in ("candidates", "export") and args.output.exists():
                parser.error("Choose a new output; candidate sets and qualification records are immutable")
            save(args.output, result)
    elif args.command == "run":
        result = str(run(args.campaign, args.runner, args.serial, args.stage, args.workload, args.seed, args.session,
                         args.output, variant=args.variant, shortlist_path=args.shortlist, adb_path=args.adb,
                         timeout=args.timeout, rules=read(args.rules) if args.rules else None, xr=args.xr))
    elif args.command == "probe":
        result = target_identity(ADB(args.adb).probe(args.serial))
        save(args.output, result)
    else:
        result = str(recover(args.transport, args.output, args.adb))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
