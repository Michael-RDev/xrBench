"""Exploratory same-session comparisons. No pooled-sample significance claims."""

from engine.reports import GPU_SPAN_SCOPES

import hashlib
import json
import random
import statistics

from engine.reports import distribution


def dispatch_summary(row, shaders):
    groups = {}
    samples = row.get("samples", [])
    for sample in samples:
        for event in sample.get("dispatches", []):
            identity = {
                k: event.get(k)
                for k in (
                    "kernel_name",
                    "dispatch_id",
                    "operator",
                    "operator_id",
                    "global_size",
                    "local_size",
                )
            }
            key = json.dumps(identity, sort_keys=True)
            if key not in groups:
                groups[key] = dict(
                    **identity, durations=[], shader=shaders.get(event.get("kernel_name"))
                )
            groups[key]["durations"].append(event.get("ms"))
    result = []
    for group in groups.values():
        durations = group.pop("durations")
        group["timing"] = distribution(durations)
        group["complete"] = len(durations) == len(samples)
        result.append(group)
    return sorted(result, key=lambda g: -(g["timing"] or {}).get("median", 0))


def bootstrap_pairs(values, seed):
    """Percentile CI of mean within-pair percentage reductions, resampling pairs."""
    rng = random.Random(seed)
    draws = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(2000))
    return [draws[49], draws[1949]]


def summarize_experiment(report):
    spec = report.get("experiment")
    if not spec:
        return None
    results = []
    for case in report.get("configuration", {}).get("cases", []):
        rows = [r for r in report.get("results", []) if r.get("case") == case]
        reasons = []
        control=spec.get("validation_control","none")
        pairs = spec.get("pairs", 0)
        if type(pairs) is not int or pairs < 6 or pairs > 40:
            reasons.append("At least six planned pairs required")
            pairs = 0
        if report.get("status") != "complete" or len(rows) != pairs * 2:
            reasons.append("Complete planned experiment required")
        indexed = {(r.get("pair_index"), r.get("arm")): r for r in rows}
        if len(indexed) != len(rows):
            reasons.append("Duplicate experiment arm")
        for row in rows:
            if not row.get("timing_valid") or any(
                row.get(k, {}).get("passed") is not True
                for k in ("correctness_before", "correctness_after")
            ):
                reasons.append("Incorrect, incomplete or invalid measurement")
            variant = (
                "candidate"
                if row.get("arm") == "comparison" and spec.get("kind") == "candidate"
                else "stock"
            )
            if row.get("variant") != variant or any(
                s.get("kernel_variant") != variant
                or (variant == "candidate" and s.get("candidate_selected") is not True)
                or s.get("validation_control", "none") != control
                for s in row.get("samples", [])
            ):
                reasons.append("Executed kernel identity not verified")
        metrics = {}
        for metric in ("execution_ms", "model_ms", "gpu_span_ms"):
            reductions, tails = [], []
            for pair in range(pairs):
                a, b = [indexed.get((pair, arm)) for arm in ("baseline", "comparison")]
                if not a or not b:
                    continue
                if a.get('fixture_seed')!=b.get('fixture_seed'):reasons.append('Fixture seeds differ within pair')
                if any(
                    a.get(k) is None or a[k] != b.get(k)
                    for k in ("thermal_start", "charging_start")
                ):
                    reasons.append("Conditions differ within a pair")
                before, after = [r.get("timings", {}).get(metric) for r in (a, b)]
                if not before or not after or before["median"] <= 0:
                    continue
                if metric == "gpu_span_ms" and any(
                    s.get("gpu_span_scope") not in GPU_SPAN_SCOPES
                    for r in (a, b)
                    for s in r.get("samples", [])
                ):
                    continue
                reductions.append(100 * (1 - after["median"] / before["median"]))
                if before["p95"] > 0:
                    tails.append(100 * (after["p95"] / before["p95"] - 1))
            if len(reductions) == pairs and pairs:
                seed = int.from_bytes(
                    hashlib.sha256((report["run_id"] + case + metric).encode()).digest()[:8],
                    "little",
                )
                ci = bootstrap_pairs(reductions, seed)
                metrics[metric] = dict(
                    mean_improvement_percent=statistics.mean(reductions),
                    ci95_percent=ci,
                    pair_improvements_percent=reductions,
                    mean_p95_change_percent=statistics.mean(tails) if tails else None,
                )
        primary = metrics.get("execution_ms")
        if not primary:
            reasons.append("Primary timing unavailable")
        available = not reasons
        results.append(
            dict(
                case=case,
                available=available,
                reasons=sorted(set(reasons)),
                pairs=pairs,
                metrics=metrics if available else {},
                noise_control_warning=bool(
                    available
                    and spec.get("kind") == "aa"
                    and abs(primary["mean_improvement_percent"]) >= 5
                    and (primary["ci95_percent"][0] > 0 or primary["ci95_percent"][1] < 0)
                ),
                outcome=(
                    "validation_control" if control!="none" else
                    "improvement_observed"
                    if spec.get("kind") == "candidate"
                    and primary["mean_improvement_percent"] >= 5
                    and primary["ci95_percent"][0] > 0
                    else "noise_control"
                    if spec.get("kind") == "aa"
                    else "inconclusive"
                )
                if available
                else "unavailable",
            )
        )
    return dict(
        version=1,
        kind=spec.get("kind"),
        results=results,
        note="Exploratory 95% paired-block bootstrap intervals. Correlated thermal drift and multiple trials can invalidate nominal coverage. Confirm on separate sessions and the real application; this does not establish an optimized kernel.",
    )


def enrich(report):
    for row in report.get("results", []):
        row["dispatch_summary"] = dispatch_summary(row, report.get("shaders", {}))
    if report.get("experiment"):
        report["kernel_comparison"] = summarize_experiment(report)
    return report
