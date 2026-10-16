import hashlib
import json
import math
import os
import re
import statistics
from datetime import datetime
from pathlib import Path

RUN_ID = re.compile(r"run-[0-9a-f]{32}\Z")
TERMINAL = {"complete", "failed", "canceled"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def distribution(values):
    if not values or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values):
        return None
    ordered = sorted(values)
    def percentile(p):
        pos = (len(ordered) - 1) * p
        low, high = math.floor(pos), math.ceil(pos)
        return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)
    return dict(count=len(values), median=percentile(.5), p95=percentile(.95),
                min=ordered[0], max=ordered[-1], mean=statistics.mean(values))


def summarize(report):
    for result in report.get("results", []):
        samples = result.get("samples", [])
        result["timings"] = {key: distribution([s.get(key) for s in samples])
                             for key in ("execution_ms", "model_ms", "gpu_ms", "input_staging_ms", "output_staging_ms", "submit_and_wait_ms")}
        result["timing_valid"] = (result.get("status") == "passed"
                                  and not result.get("thermal_discontinuity", False)
                                  and not result.get("focus_lost", False))
    report["counts"] = {s: sum(r.get("status") == s for r in report.get("results", []))
                        for s in ("passed", "failed", "skipped", "interrupted")}
    if report.get("status") == "running":
        report["counts"]["interrupted"] = 0  # These rows are still being measured.
    if report.get("configuration", {}).get("mode") == "xr":
        pairs = {}
        for row in report.get("results", []):
            pairs.setdefault((row["case"], row["trial"]), {})[row["phase"]] = row
        comparisons = []
        for (case, trial), phases in pairs.items():
            comparison = dict(case=case, trial=trial, valid=False, frame_p95_delta_ms=None,
                              missed_frame_rate_delta=None, reason="Incomplete baseline/load pair")
            if "baseline" in phases and "loaded" in phases:
                baseline, loaded = phases["baseline"], phases["loaded"]
                if not all(r["timing_valid"] for r in (baseline, loaded)):
                    comparison["reason"] = "Interrupted, incorrect, or thermal/focus transition"
                elif not all(r.get("xr_metrics_available", False) for r in (baseline, loaded)):
                    comparison["reason"] = "XR frame metrics unavailable or incomplete"
                elif (baseline.get("thermal_start"), baseline.get("charging_start")) != (loaded.get("thermal_start"), loaded.get("charging_start")):
                    comparison["reason"] = "Thermal or charging state differs between windows"
                else:
                    before, after = [distribution(r.get("xr", {}).get("frame_ms", [])) for r in (baseline, loaded)]
                    if before and after:
                        comparison.update(valid=True, reason=None, baseline_frame_p95_ms=before["p95"],
                                          loaded_frame_p95_ms=after["p95"], frame_p95_delta_ms=after["p95"]-before["p95"])
                        a, b = [r["xr"].get("missed_frame_rate") for r in (baseline, loaded)]
                        if a is not None and b is not None:comparison["missed_frame_rate_delta"] = b-a
                    else:comparison["reason"] = "XR frame metrics unavailable or invalid"
            comparisons.append(comparison)
        report["xr_comparisons"] = comparisons
    return report


def validate(report):
    canonical(report)  # Reject NaN/Infinity anywhere in the envelope.
    if not isinstance(report, dict) or report.get("schema_version") != 2:
        raise ValueError("Expected native benchmark schema version 2")
    if not RUN_ID.fullmatch(str(report.get("run_id", ""))):
        raise ValueError("Invalid run identity")
    if report.get("status") not in TERMINAL:
        raise ValueError("Only finished reports can be stored")
    if report.get("execution_location") != "headset" or report.get("backend") != "executorch_vulkan":
        raise ValueError("Expected native headset Vulkan execution")
    config = report.get("configuration", {})
    if config.get("mode") not in ("isolated", "xr") or config.get("preset") not in ("smoke", "standard", "sustained"):
        raise ValueError("Invalid configuration")
    if not isinstance(report.get("device"), dict):
        raise ValueError("Missing device metadata")
    for field in ("suite_revision", "runtime_revision"):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError("Missing revision")
    start, end = [datetime.fromisoformat(report[f].replace("Z", "+00:00"))
                  for f in ("started_at", "finished_at")]
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError("Invalid report timestamps")
    results = report.get("results")
    if not isinstance(results, list) or len(results) > 10000:
        raise ValueError("Invalid results")
    identities = set()
    for result in results:
        key = (result.get("case"), result.get("phase"), result.get("trial"))
        if (not isinstance(key[0], str) or not key[0] or key[1] not in ("isolated", "baseline", "loaded")
                or type(key[2]) is not int or key[2] < 0 or key in identities):
            raise ValueError("Invalid or duplicate result identity")
        identities.add(key)
        if result.get("status") not in ("passed", "failed", "skipped", "interrupted") or not result.get("operation"):
            raise ValueError("Invalid result status/operation")
        if result["status"] == "passed" and result.get("correctness", {}).get("passed") is not True:
            raise ValueError("Passed result requires correctness evidence")
        samples = result.get("samples")
        if not isinstance(samples, list) or len(samples) > 100000:
            raise ValueError("Invalid samples")
        if result["status"] == "passed" and result["phase"] != "baseline" and not samples:
            raise ValueError("Passed result requires timing samples")
        for sample in samples:
            for name in ("execution_ms", "model_ms", "gpu_ms"):
                value = sample.get(name)
                if (name == "execution_ms" or value is not None) and (
                        type(value) not in (int, float) or value < 0 or not math.isfinite(value)):
                    raise ValueError("Invalid timing")
    return report
