"""Fail-closed consistency checks for operator-attested physical evidence.

These checks do not authenticate JSON or replace a headset operator.
"""

from dataclasses import dataclass, field
from datetime import datetime
import math
import re

from engine.native_reports import _equal

ORIGIN = "http://localhost:8765"
ENDPOINT = ORIGIN + "/api/reports"
MAX_CLOCK_RTT = .250
UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")


def need(condition, message):
    if not condition:
        raise ValueError(message)


def instant(value):
    need(isinstance(value, str), "Evidence timestamps must be timezone-qualified strings")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    need(result.tzinfo is not None, "Evidence timestamps require a timezone")
    return result.timestamp()


def number(value):
    need(type(value) in (int, float) and abs(value) <= 2**53 and math.isfinite(value),
         "Expected a finite numeric measurement")
    return value


def identity(value):
    need(isinstance(value, str) and UUID.fullmatch(value), "Missing observed installation UUID")
    return value


@dataclass
class Bundle:
    reports: list = field(default_factory=list)
    snapshots: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    deliveries: list = field(default_factory=list)
    events: list = field(default_factory=list)
    receipts: list = field(default_factory=list)
    exports: list = field(default_factory=list)
    installations: list = field(default_factory=list)


def observations(check, action, *, report=None):
    rows = check.get("observations", [])
    need(rows and all(isinstance(o, dict) for o in rows), "Missing operator observations")
    matches = [o for o in rows if o.get("action") == action]
    need(matches, f"Missing specific operator observation: {action}")
    for row in rows:
        instant(row["at"])
        need(all(isinstance(row.get(k), str) and row[k].strip() for k in ("operator", "action", "observed")),
             "Incomplete operator observation")
        if report is not None:
            need(row.get("run_id") == report["run_id"], "Observation belongs to a different run")
    return matches


def clock(diagnostic):
    calibration = diagnostic.get("clock", {})
    start, end, device = [instant(calibration[k]) for k in
                          ("host_started_at", "host_finished_at", "device_at")]
    need(0 <= end - start <= MAX_CLOCK_RTT, "Device/host clock calibration is missing or too uncertain")
    return device - end, device - start


def on_device(at, diagnostic):
    low, high = clock(diagnostic)
    value = instant(at)
    return value + low, value + high


def event_series(rows):
    need(rows and all(isinstance(row, dict) for row in rows), "Missing timestamped event series")
    times = [instant(row["at"]) for row in rows]
    elapsed = [number(row["elapsed_ms"]) / 1000 for row in rows]
    need(times == sorted(times) and elapsed == sorted(elapsed) and elapsed[0] >= 0,
         "Event times or monotonic measurements are out of order")
    anchors = [at - ms for at, ms in zip(times, elapsed)]
    need(max(anchors) - min(anchors) <= .100, "UTC and monotonic event clocks disagree")
    return times


def lifecycle(bundle, report, manifest):
    matches = [d for d in bundle.diagnostics if d.get("run_id") == report["run_id"]]
    need(matches, "Missing lifecycle diagnostics for this run")
    # A later, incomplete snapshot must not silently replace conflicting originals.
    diagnostic = max(matches, key=lambda d: instant(d["captured_at"]))
    need(diagnostic.get("package") == manifest["package"]
         and diagnostic.get("apk_sha256") == report["configuration"]["apk_sha256"],
         "Diagnostic package/release mismatch")
    need(not any(k in diagnostic for k in ("error", "lifecycle_error", "clock_error")),
         "Diagnostics contain a capture error")
    clock(diagnostic)
    value = diagnostic.get("lifecycle", {})
    need(value.get("run_id") == report["run_id"], "Lifecycle run ID mismatch")
    rows = value.get("events", [])
    event_series(rows)
    names = [r["event"] for r in rows]
    required = ("service_started", "wake_acquired", "worker_finished", "wake_released",
                "service_finished", "service_destroyed")
    need(all(names.count(name) == 1 for name in required), "Missing or duplicate terminal lifecycle events")
    need([names.index(name) for name in required] == sorted(names.index(name) for name in required),
         "Service destruction/resource cleanup is out of order")
    at = {row["event"]: instant(row["at"]) for row in rows if row["event"] in required}
    need(at["wake_acquired"] <= instant(report["started_at"])
         <= instant(report["finished_at"]) <= at["worker_finished"],
         "Lifecycle does not enclose native execution")
    need(on_device(diagnostic["captured_at"], diagnostic)[0] >= at["service_destroyed"],
         "Diagnostics were captured before cleanup completed")
    need(on_device(diagnostic["captured_at"], diagnostic)[1] - at["service_destroyed"] <= 60,
         "Cleanup diagnostics or clock calibration are stale")
    for older in matches:
        older_rows = older.get("lifecycle", {}).get("events", [])
        need(_equal(older_rows, rows[:len(older_rows)]), "Conflicting lifecycle snapshots")
    return diagnostic, rows


def stages(report):
    rows = report.get("stage_events", [])
    times = event_series(rows)
    valid = {"preparation", "readback", "warmup", "execution", "finished"}
    cases = report["configuration"]["cases"]
    need(all(r["stage"] in valid and (r["case"] is None or r["case"] in cases) for r in rows),
         "Invalid native stage inventory")
    start, end = instant(report["started_at"]), instant(report["finished_at"])
    need(rows[0]["stage"] == "preparation" and rows[0]["case"] is None
         and rows[-1]["stage"] == "finished" and rows[-1]["case"] is None
         and abs(times[0] - start) <= .100 and abs(times[-1] - end) <= .100,
         "Stage markers do not cover this native run")
    need(all(start - .100 <= t <= end + .100 for t in times), "Stage marker outside the native run")
    windows = []
    results = {row["case"]: row for row in report["results"]}
    for a, b in zip(rows, rows[1:]):
        if a["stage"] in {"warmup", "execution"}:
            need(a["case"] in results and (b["case"] == a["case"] or
                 b["stage"] == "finished" and report["status"] in {"failed", "canceled"}),
                 "Stage spans different native cases")
            windows.append((a, b))
    for result in report["results"]:
        samples = result["samples"]
        if not samples:
            continue
        need(result.get("runtime_info", {}).get("backend") == "vulkan"
             and result["runtime_info"].get("cpu_compute_fallback") is False,
             "Sampled work lacks physical Vulkan provenance")
        intervals = [(a, b) for a, b in windows if a["stage"] == "execution" and a["case"] == result["case"]]
        need(len(intervals) == 1, "Missing or duplicate execution stage for sampled work")
        a, b = intervals[0]
        sample_times = [instant(s["at"]) for s in samples]
        sample_elapsed = [number(s["elapsed_ms"]) for s in samples]
        need(sample_times == sorted(sample_times) and sample_elapsed == sorted(sample_elapsed),
             "Sample timestamps are out of order")
        need(all(instant(a["at"]) <= t <= instant(b["at"]) for t in sample_times)
             and all(a["elapsed_ms"] <= t <= b["elapsed_ms"] for t in sample_elapsed),
             "Sample falls outside its execution stage")
        anchor = times[0] - rows[0]["elapsed_ms"] / 1000
        need(all(abs(t - ms / 1000 - anchor) <= .100 for t, ms in zip(sample_times, sample_elapsed)),
             "Sample UTC/monotonic clocks disagree")
    return rows, windows


def complete(report, display=None, *, timing=True):
    need(report["status"] == "complete", "A complete native run is required")
    passed = [row for row in report["results"] if row["status"] == "passed"]
    need(passed and all(r["status"] in {"passed", "unsupported"} for r in report["results"]),
         "Supported operators must all pass; unsupported operators are exclusions")
    for row in passed:
        need(row["shaders"], "Missing shader evidence")
        names = [output["name"] for output in row["outputs"]]
        for key in ("correctness_before", "correctness_after", "correctness"):
            verdict = row.get(key, {})
            outputs = verdict.get("outputs", [])
            need(verdict.get("passed") is True and [v.get("name") for v in outputs] == names
                 and all(v.get("passed") is True and number(v["max_abs_error"]) >= 0 for v in outputs),
                 "Missing or contradictory per-output correctness evidence")
        need(_equal(row["correctness"], row["correctness_after"]),
             "Aggregate correctness differs from the final comparison")
        if timing:
            need(row.get("timing_valid") is not False and not row.get("thermal_discontinuity")
                 and not row.get("focus_lost"), "Invalid timing evidence")
            for condition, initial in (("thermal_status", "thermal_start"), ("charging", "charging_start")):
                conditions = {sample[condition] for sample in row["samples"]}
                if row.get(initial) is not None:
                    conditions.add(row[initial])
                need(len(conditions) == 1, "Operating conditions changed within a case")
            need(all(s["thermal_status"] < 3 for s in row["samples"]), "Severe thermal samples cannot pass readiness")
        if display is not None:
            need(all(s["display_interactive"] is display for s in row["samples"]),
                 "Measured display state does not match the worn/unworn gate")
    return passed


def browser_events(bundle, run_id):
    events = {}
    for event in bundle.events:
        if event.get("run_id") != run_id:
            continue
        need(isinstance(event.get("event_id"), str) and event["event_id"].startswith("event-")
             and isinstance(event.get("page_id"), str) and event["page_id"].startswith("page-"),
             "Browser events lack durable event/page identities")
        instant(event["at"])
        previous = events.setdefault(event["event_id"], event)
        need(_equal(previous, event), "Conflicting browser event identity")
    ordered = sorted(events.values(), key=lambda e: instant(e["at"]))
    need(sum(e["event"] == "launch_intent" for e in ordered) == 1,
         "Missing launch intent or possible duplicate launch")
    return ordered


def checkpoint_chain(snapshots):
    """Check that recovered snapshots extend, rather than replace, durable work."""
    unique = {}
    for report in snapshots:
        progress = report.get("progress", {})
        sequence = progress.get("sequence")
        need(type(sequence) is int and sequence > 0, "Missing native checkpoint sequence")
        previous = unique.setdefault(sequence, report)
        need(_equal(previous, report), "Conflicting checkpoint sequence")
    ordered = [unique[key] for key in sorted(unique)]
    for old, new in zip(ordered, ordered[1:]):
        need(old["status"] == "running", "Evidence changed after a terminal report")
        need(all(_equal(old[k], new[k]) for k in ("run_id", "configuration", "device", "started_at")),
             "Checkpoint identity changed")
        need(_equal(old.get("stage_events", []), new.get("stage_events", [])[:len(old.get("stage_events", []))]),
             "Recovered stage history lost or changed evidence")
        newer = {row["case"]: row for row in new["results"]}
        for row in old["results"]:
            need(row["case"] in newer, "Recovered checkpoint lost a native case")
            target = newer[row["case"]]
            need(_equal(row["samples"], target["samples"][:len(row["samples"])]),
                 "Recovered checkpoint lost or changed samples")
            if row["status"] != "interrupted":
                need(_equal(row, target), "Recovered checkpoint rewrote completed work")
            else:
                mutable = {"status", "error", "samples", "correctness", "correctness_after",
                           "actual_outputs", "readback_after_ms", "warmup_execution_ms"}
                need(all(k in target and _equal(v, target[k]) for k, v in row.items() if k not in mutable),
                     "Recovered checkpoint rewrote earlier correctness or provenance")
                need(target.get("warmup_execution_ms", 0) >= row.get("warmup_execution_ms", 0),
                     "Recovered checkpoint lost warmup measurements")
    return ordered


def cleanup(diagnostic, rows, manifest):
    ended = next(e["at"] for e in rows if e["event"] == "service_destroyed")
    expected = {"service": ["dumpsys", "activity", "services", manifest["package"]],
                "power": ["dumpsys", "power"],
                "notification": ["dumpsys", "notification", manifest["package"]]}
    output = {}
    for key, args in expected.items():
        command = diagnostic.get("commands", {}).get(key, {})
        need(command.get("code") == 0 and command.get("args") == args
             and not command.get("error") and not command.get("stderr", "").strip(),
             "Missing successful, correctly scoped OS cleanup snapshot")
        need(on_device(command["started_at"], diagnostic)[0] >= instant(ended)
             and instant(command["finished_at"]) >= instant(command["started_at"])
             and instant(command["finished_at"]) <= instant(diagnostic["captured_at"]),
             "OS snapshot predates resource cleanup")
        output[key] = command["stdout"]
    service = output["service"]
    need("ACTIVITY MANAGER SERVICES" in service and not any(
        "ServiceRecord{" in line and manifest["package"] in line and "BenchmarkService" in line
        for line in service.splitlines()), "OS still reports an active benchmark service")
    power = re.search(r"Wake Locks:\s*size=(\d+)(.*?)(?:\n\s*\n|\Z)", output["power"], re.S)
    need(power is not None and len(re.findall(r"Wake Locks:\s*size=", output["power"])) == 1
         and "xrBench:benchmark" not in output["power"],
         "OS wake-lock snapshot is unrecognized or still holds the benchmark lock")
    notification = output["notification"]
    need("Notification List:" in notification and
         ("Notification service" in notification or "Notification Manager" in notification),
         "Unrecognized notification snapshot")
    need(not any("NotificationRecord" in line and ("pkg=" + manifest["package"]) in line
                 for line in notification.splitlines()), "OS still reports a benchmark notification")
