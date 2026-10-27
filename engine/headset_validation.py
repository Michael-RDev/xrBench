"""Private Quest 3 campaign preparation and read-only evidence assessment.

This checks evidence consistency and operator attestations, not hardware authenticity.
It never launches workloads, replays reports, or writes to PostgreSQL.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

from engine.native_reports import _equal, validate
from engine.paths import ROOT
from engine.public_build import signing_package
from engine.headset_evidence import (Bundle, ORIGIN, ENDPOINT, need, instant, number, identity,
                                     observations, lifecycle, stages, complete, browser_events,
                                     checkpoint_chain, cleanup, on_device)


GATES = {
    "installation.usb_authorization": "Approve browser USB and headset debugging",
    "installation.existing_app_preserved": "Preserve an existing app and its data",
    "installation.same_key_upgrade": "Upgrade in place with the same signing key",
    "installation.same_key_downgrade": "Perform a supported same-key version downgrade",
    "installation.update_runner": "Recover an old checkpoint and update without replay",
    "correctness.worn": "Complete the full standard suite while worn",
    "correctness.unworn": "Complete the full standard suite with the display off",
    "removal.preparation": "Remove the headset during preparation",
    "removal.warmup": "Remove the headset during warmup",
    "removal.execution": "Remove the headset during timed execution",
    "recovery.usb_reconnect": "Disconnect and reconnect USB, recovering the same run",
    "recovery.refresh": "Refresh the tab, recovering the same run",
    "recovery.disconnected_stop": "Request Stop while disconnected and recover cancellation",
    "service.browser_stop": "Stop from the browser and record cancellation latency",
    "service.notification_stop": "Stop from the notification and record cancellation latency",
    "service.cleanup": "Verify service, notification, and wake-lock cleanup",
    "service.deadline": "Observe the actual 30-minute deadline and cleanup",
    "thermal.sustained": "Collect up to 30 minutes of sustained execution and conditions",
    "thermal.severe_stop": "Observe stopping at a real OS severe thermal threshold",
    "delivery.online": "Deliver a real completed run to PostgreSQL",
    "delivery.offline_retry": "Recover queued delivery after an outage and tab refresh",
    "delivery.idempotency": "Repeat the exact submission and compare both receipts",
}
IDENTITIES = ("apk_sha256", "package", "signing_certificate_sha256", "suite_revision",
              "runner_revision", "runtime_revision", "native_package_sha256",
              "native_catalog_sha256")
DEVICE_FIELDS = ("model", "product", "device", "os", "gpu", "sdk", "abi")
STATUSES = {"passed", "failed", "blocked", "pending"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


MAX_EVIDENCE_BYTES = 128 * 1024 * 1024


def read_evidence(path):
    import stat

    path = Path(path)
    need(stat.S_ISREG(path.stat().st_mode), "Evidence must be a regular file")
    with path.open("rb") as stream:
        data = stream.read(MAX_EVIDENCE_BYTES + 1)
    need(len(data) <= MAX_EVIDENCE_BYTES, "Evidence exceeds the size budget")
    return data


def decode(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, "Duplicate JSON member")
            value[key] = item
        return value

    # Bound nesting before json.loads recurses, including escaped JSON body strings.
    depth = 0
    quoted = escaped = False
    for char in data.decode("utf-8"):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            need(depth <= 64, "Evidence nesting exceeds the budget")
        elif char in "]}":
            depth -= 1
    value = json.loads(data, object_pairs_hook=unique,
                       parse_constant=lambda _: need(False, "Non-finite JSON"))
    pending = [value]
    count = 0
    while pending:
        item = pending.pop()
        count += 1
        need(count <= 2000000, "Evidence complexity exceeds the budget")
        if isinstance(item, float):
            number(item)
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return value


def load(path):
    return decode(read_evidence(path))


def contained(root, name):
    need(isinstance(name, str) and not Path(name).is_absolute(), "Use attempt-relative evidence paths")
    path = (root / name).resolve()
    need(path.is_relative_to(root.resolve()) and path != root.resolve(), "Evidence path escapes attempt")
    return path


def release_identity(manifest):
    return {key: manifest[key] for key in IDENTITIES}


def check_release(manifest):
    import re
    from engine.reports import digest

    release_identity(manifest)
    need(all(re.fullmatch(r"[a-f0-9]{64}", manifest[k]) for k in
             ("apk_sha256", "signing_certificate_sha256", "suite_revision", "runner_revision",
              "native_package_sha256", "native_catalog_sha256"))
         and re.fullmatch(r"[a-f0-9]{40}", manifest["runtime_revision"]),
         "Malformed release hashes/revision")
    config, catalog = manifest["configurations"]["gpu"], manifest["catalog"]
    cases = [row["id"] for row in catalog["cases"]]
    need(manifest.get("protocol_version") == 2 and len(cases) == len(set(cases)) == 221
         and config["cases"] == cases and config["backend"] == "xrgpu_vulkan"
         and config["mode"] == "isolated" and config["preset"] == "standard"
         and type(config["warmups"]) is int and config["warmups"] == 20
         and type(config["samples"]) is int and config["samples"] == 100,
         "Expected the complete standard 221-operator release")
    need(all(config.get(k) == manifest[k] for k in IDENTITIES
             if k not in {"package", "signing_certificate_sha256"}),
         "Manifest and runner configuration identities disagree")
    need(catalog.get("suite_revision") == manifest["suite_revision"]
         and catalog.get("operator_count") == 221
         and catalog.get("source_package_sha256") == manifest["native_package_sha256"]
         and catalog.get("native_catalog_sha256") == manifest["native_catalog_sha256"]
         and digest({k: v for k, v in catalog.items() if k != "suite_revision"}) == manifest["suite_revision"],
         "Frozen catalog digest mismatch")
    need(manifest["package"] == signing_package(manifest["signing_certificate_sha256"])
         and manifest["apk"] == manifest["apk_sha256"] + ".apk"
         and type(manifest["version_code"]) is int and manifest["version_code"] > 0,
         "Invalid signed package/version identity")


def site_identity(directory):
    import re
    from engine.reports import digest

    directory = Path(directory)
    value = load(directory / "site-manifest.json")
    need(value.get("schema_version") == 1
         and set(value.get("files", {})) == {"index.html", "bench/app.js", "bench/style.css"}
         and digest(value["files"]) == value.get("site_sha256"), "Invalid browser site manifest")
    for name, expected in value["files"].items():
        data = read_evidence(directory / name)
        if name == "index.html":
            marker = re.findall(rb'\n  <meta name="xrbench-site-sha256" content="([a-f0-9]{64})">', data)
            need(len(marker) == 1 and marker[0].decode() == value["site_sha256"], "Missing browser site identity marker")
            data = re.sub(rb'\n  <meta name="xrbench-site-sha256" content="[a-f0-9]{64}">', b"", data)
        need(sha(data) == expected, "Browser site bytes differ from their frozen identity")
    return value


def initialize(manifest_path, output):
    manifest_path, output = Path(manifest_path), Path(output).resolve()
    release_bytes = read_evidence(manifest_path)
    manifest = decode(release_bytes)
    check_release(manifest)
    need(sha((manifest_path.parent / manifest["apk"]).read_bytes()) == manifest["apk_sha256"],
         "Release APK checksum mismatch")
    need(not output.is_relative_to(ROOT / "dist"), "Evidence cannot live in served files")
    if output.is_relative_to(ROOT):
        ignored = subprocess.run(["git", "check-ignore", "-q", str(output / "attempt.json")],
                                 cwd=ROOT, capture_output=True)
        need(ignored.returncode == 0, "Evidence directory must be ignored by Git")
    site_directory = manifest_path.parent.parent if manifest_path.parent.name == "runner" else manifest_path.parent
    site = site_identity(site_directory)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    output.chmod(0o700)
    for name in (*site["files"], "site-manifest.json"):
        destination = output / "site" / name
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with destination.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(read_evidence(site_directory / name))
    need(_equal(site_identity(output / "site"), site), "Browser files changed while freezing the attempt")
    (output / "release.json").write_bytes(release_bytes)
    (output / "release.json").chmod(0o600)
    provenance = {}
    for key, args in {"commit": ["rev-parse", "HEAD"], "local_changes": ["status", "--short"]}.items():
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        provenance[key] = result.stdout.strip() if result.returncode == 0 else "unavailable"
    write_private(output / "site.json", site)
    value = dict(schema_version=1, evidence_version=2, site_sha256=site["site_sha256"], kind="xrbench.headset-validation",
                 created_at=datetime.now(timezone.utc).isoformat(),
                 origin="http://localhost:8765", release_sha256=sha(release_bytes),
                 provenance=provenance, operator="", host=dict(os="", browser="", user_agent=""),
                 device=dict(model="Quest 3", firmware="", gpu="", installation_reference="", installation_id=""),
                 checks={key: dict(status="blocked", reason="Quest 3 operator unavailable",
                                   next_action=action, observations=[], evidence=[])
                         for key, action in GATES.items()})
    write_private(output / "attempt.json", value)
    (output / "checklist.md").write_text(
        "# Quest 3 campaign\n\nFollow docs/workflows/quest3-validation.md. "
        "Original files stay private here. Edit attempt.json to record observations and "
        "SHA-256 evidence references; a checkbox is not proof.\n\n" +
        "\n".join(f"- [ ] `{key}` — {action}" for key, action in GATES.items()) + "\n")
    (output / "checklist.md").chmod(0o600)
    return output


def write_private(path, value):
    path = Path(path)
    with path.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def artifacts(root, references, inventory, cache=None):
    from engine.native_reports import _real

    values = []
    need(bool(references), "No original evidence attached")
    cache = {} if cache is None else cache
    for reference in references:
        path = contained(root, reference["path"])
        if path not in cache:
            data = read_evidence(path)
            cache[path] = (sha(data), decode(data) if path.suffix == ".json" else None)
        actual, value = cache[path]
        need(actual == reference["sha256"], "Original evidence checksum mismatch")
        inventory[reference["path"]] = actual
        if value is not None:
            _real(value)
            pending = [value]
            while pending:
                item = pending.pop()
                if isinstance(item, dict):
                    need(all(k not in item or item[k] is False for k in ("test_only", "simulated")),
                         "Ambiguous software/simulation evidence marker")
                    pending.extend(item.values())
                elif isinstance(item, list):
                    pending.extend(item)
            values.append(value)
    return values


def reports_from(values, manifest, site_sha=None):
    bundle = Bundle()
    reports = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        if value.get("kind") == "xrbench.delivery-attempt":
            bundle.receipts.append(value)
        if "reports" in value:
            need(value.get("schema_version") == 1 and isinstance(value.get("session"), dict),
                 "Invalid browser export")
            extra = value.get("validation", {})
            need(extra.get("origin") == ORIGIN and extra.get("evidence_version") == 2,
                 "Physical gates require current exports with a verified local origin")
            need(_equal(extra.get("release"), manifest), "Mixed release identities in browser export")
            need(isinstance(extra.get("site_sha256"), str) and len(extra["site_sha256"]) == 64
                 and all(c in "0123456789abcdef" for c in extra["site_sha256"])
                 and (site_sha is None or extra["site_sha256"] == site_sha), "Browser implementation changed or was not identified")
            need(value["session"].get("site_sha256") == extra["site_sha256"]
                 and (site_sha is None or all(e.get("site_sha256") == site_sha for e in extra.get("browser_events", []))),
                 "Session or browser events belong to another website implementation")
            instant(extra["exported_at"])
            phases = value["session"].get("phases", [])
            configs = {phase["run_id"]: phase["configuration"] for phase in phases}
            need(len({raw["run_id"] for raw in value["reports"]}) == len(value["reports"])
                 and all(raw["run_id"] in configs and _equal(configs[raw["run_id"]], raw["configuration"])
                     for raw in value["reports"]), "Export session does not bind its native reports")
            connection = value["session"].get("connection_id")
            need(isinstance(connection, str) and len(connection) == 24
                 and all(c in "0123456789abcdef" for c in connection), "Missing private device connection identity")
            bundle.exports.append(value)
            bundle.diagnostics.extend(extra.get("diagnostics", []))
            bundle.deliveries.extend(extra.get("deliveries", []))
            bundle.events.extend(extra.get("browser_events", []))
            bundle.installations.extend(extra.get("installations", []))
            candidates = value["reports"]
            for raw in candidates:
                profile = value["session"].get("capabilities", {})
                need(all(_equal(profile.get(k), raw["device"].get(k)) for k in (*DEVICE_FIELDS, "installation_id")),
                     "Browser profile and native device identity disagree")
        else:
            candidates = [value] if value.get("schema_version") == 3 else []
        for raw in candidates:
            expected = dict(manifest["configurations"]["gpu"], run_id=raw["run_id"], execution_target="headset")
            validate(raw, expected, manifest["catalog"], partial=True)
            from engine.devices import identify

            need(identify(raw["device"])["device_id"] == "quest-3", "Evidence is not from Quest 3")
            need(all(raw["device"].get(k) not in (None, "", []) for k in DEVICE_FIELDS),
                 "Missing observed device/firmware/GPU identity")
            need(type(raw["device"]["sdk"]) is int and raw["device"]["sdk"] >= 29
                 and isinstance(raw["device"]["abi"], list) and "arm64-v8a" in raw["device"]["abi"],
                 "Observed platform cannot run the Android ARM64 release")
            identity(raw["device"].get("installation_id"))
            need(raw["device"].get("vulkan") is True
                 and raw["device"].get("backends", {}).get("xrgpu_vulkan", {}).get("compatible") is True
                 and ("physical_qualification" not in raw["device"] or raw["device"]["physical_qualification"] is True)
                 and all("physical_qualification" not in row.get("runtime_info", {})
                         or row["runtime_info"]["physical_qualification"] is True for row in raw["results"]),
                 "Observed profile/runtime explicitly lacks physical Vulkan evidence")
            need(all(raw["device"].get(k) == manifest[k] for k in
                     ("runner_revision", "runtime_revision", "native_package_sha256")),
                 "Observed device profile belongs to another release")
            need("physical_qualification" not in raw or raw["physical_qualification"] is True,
                 "Software evidence cannot close a physical gate")
            progress = raw.get("progress", {})
            need(progress.get("run_id") == raw["run_id"] and progress.get("status") == raw["status"]
                 and type(progress.get("sequence")) is int and progress["sequence"] > 0
                 and type(progress.get("completed_cases")) is int
                 and progress.get("completed_cases") == sum(r["status"] != "interrupted" for r in raw["results"]),
                 "Missing or inconsistent native checkpoint progress")
            bundle.snapshots.append(raw)
            previous = reports.get(raw["run_id"])
            if previous and previous["status"] != "running" and raw["status"] != "running":
                need(_equal(previous, raw), "Conflicting final evidence under one run ID")
            if previous is None or raw["status"] != "running":
                reports[raw["run_id"]] = raw
    bundle.reports = list(reports.values())
    return bundle


def verify_database(store, report, receipt):
    from engine.reports import digest, summarize

    expected = summarize(copy.deepcopy(report))
    expected["native_report"] = report
    # One read-only snapshot: independent queries must not see different commits.
    with store.connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        row = connection.execute("""SELECT report, report_sha256, schema_version, suite_revision,
            runtime_revision, backend, mode, preset, status, device, configuration, started_at, finished_at
            FROM xrgpu_benchmarks.benchmark_runs WHERE run_id=%s""", (report["run_id"],)).fetchone()
        need(row is not None and _equal(row[0], expected), "PostgreSQL native JSON does not match original evidence")
        need(digest(row[0]) == row[1] == receipt.get("report_sha256"), "PostgreSQL report digest does not match receipt")
        parent = (report["schema_version"], report["suite_revision"], report["runtime_revision"], report["backend"],
                  report["configuration"]["mode"], report["configuration"]["preset"], report["status"],
                  report["device"], report["configuration"])
        need(_equal(list(row[2:11]), list(parent))
             and row[11].timestamp() == instant(report["started_at"])
             and row[12].timestamp() == instant(report["finished_at"]),
             "PostgreSQL indexed run metadata differs from native JSON")
        stored = connection.execute("""SELECT case_id, phase, trial, operation, status,
            correctness, timings, samples, result FROM xrgpu_benchmarks.benchmark_results
            WHERE run_id=%s""", (report["run_id"],)).fetchall()
    actual = [list(row) for row in stored]
    wanted = [[r["case"], r["phase"], r["trial"], r["operation"], r["status"],
               r.get("correctness"), r.get("timings", {}), r["samples"], r] for r in expected["results"]]
    need(_equal(sorted(actual, key=lambda r: tuple(r[:3])), sorted(wanted, key=lambda r: tuple(r[:3]))),
         "PostgreSQL result rows or indexed columns do not match native evidence")


def verify_gate(key, check, bundle, store, manifest):
    reports = bundle.reports
    need(reports and bundle.exports, "Missing original browser/native evidence")
    need(len(reports) == 1 or key.startswith("thermal."), "Attach exactly one run for this physical gate")
    action = key.split(".")[1]
    details = {"run_ids": [r["run_id"] for r in reports]}
    diagnostics = {}
    for report in reports:
        need(report["status"] != "running", "Recover terminal evidence before passing a gate")
        diagnostic, rows = lifecycle(bundle, report, manifest)
        diagnostics[report["run_id"]] = (diagnostic, rows)
        stages(report)
    # Each attestation names the condition/action actually being qualified.
    observation_action = "headset_removed" if key.startswith("removal.") else "repeat_submission" if action == "idempotency" else action
    matches = observations(check, observation_action)
    by_id = {r["run_id"]: r for r in reports}
    for observation in matches:
        need(observation.get("run_id") in by_id, "Observation belongs to a different run")

    if key.startswith("correctness."):
        report = reports[0]
        passed = complete(report, display=action == "worn")
        diagnostic = diagnostics[report["run_id"]][0]
        need(any(instant(report["started_at"]) <= on_device(o["at"], diagnostic)[0]
                 <= on_device(o["at"], diagnostic)[1] <= instant(report["finished_at"])
                 for o in matches), "Wear-state observation is outside the native run")
        details.update(passed=len(passed), unsupported=[r["case"] for r in report["results"] if r["status"] == "unsupported"])
        need(type(check.get("passed_count", len(passed))) is int
             and check.get("passed_count", len(passed)) == len(passed), "Unsupported operators counted as passes")

    if key.startswith("removal."):
        report = reports[0]
        complete(report, timing=False)
        diagnostic, rows = diagnostics[report["run_id"]]
        stage_rows, _ = stages(report)
        environments = [e for e in rows if e["event"] == "environment"]
        removed = observations(check, "headset_removed", report=report)
        matched = False
        for observation in removed:
            low, high = on_device(observation["at"], diagnostic)
            overlap = any(a["stage"] == action and instant(a["at"]) <= low <= high < instant(b["at"])
                          for a, b in zip(stage_rows, stage_rows[1:]))
            before = [e for e in environments if instant(e["at"]) <= low]
            off = [e for e in environments if high <= instant(e["at"]) <= instant(report["finished_at"])
                   and e.get("details", {}).get("display_interactive") is False]
            was_on = bool(before) and before[-1].get("details", {}).get("display_interactive") is True
            continued = any(s["display_interactive"] is False and instant(s["at"]) >= instant(off[0]["at"])
                            for r in report["results"] for s in r["samples"]) if off else False
            matched |= overlap and was_on and continued
        need(matched, "No real display transition and continued native sampling after stage-specific removal")

    if key.startswith("recovery."):
        report = reports[0]
        events = browser_events(bundle, report["run_id"])
        snapshots = checkpoint_chain([r for r in bundle.snapshots if r["run_id"] == report["run_id"]])
        need(len(snapshots) >= 2 and snapshots[0]["status"] == "running"
             and any(row["samples"] for row in snapshots[0]["results"]),
             "Recovery needs a running checkpoint containing samples and a later terminal checkpoint")
        before_exports = [e for e in bundle.exports if any(_equal(r, snapshots[0]) for r in e["reports"])]
        after_exports = [e for e in bundle.exports if any(_equal(r, snapshots[-1]) for r in e["reports"])]
        launch = next(e for e in events if e["event"] == "launch_intent")
        requests = [e for e in events if e["event"] == "stop_requested"]
        connected = [e for e in events if e["event"] == "connected" and instant(e["at"]) > instant(launch["at"])]
        disconnected = [e for e in events if e["event"] == "disconnected"]
        verified = False
        for observation in matches:
            at = instant(observation["at"])
            for connection in connected:
                end = instant(connection["at"])
                starts = [e for e in events if e["event"] == "connected" and instant(e["at"]) < at]
                if not starts or not (at <= end and
                    any(instant(e["validation"]["exported_at"]) < at for e in before_exports) and
                    any(instant(e["validation"]["exported_at"]) >= end for e in after_exports)):
                    continue
                if action == "refresh":
                    verified |= starts[-1]["page_id"] != connection["page_id"]
                elif action == "usb_reconnect":
                    verified |= any(at <= instant(e["at"]) < end for e in disconnected)
                else:
                    verified |= any(instant(d["at"]) <= instant(s["at"]) < end
                                    for d in disconnected for s in requests)
        need(verified, "Missing ordered interruption/refresh, checkpoint, and reconnection evidence")
        if action == "disconnected_stop":
            need(report["status"] == "canceled" and report.get("error") == "Canceled by user",
                 "Disconnected Stop was not recovered as a user cancellation")
        else:
            complete(report, timing=False)

    if key.startswith("service."):
        report = reports[0]
        diagnostic, rows = diagnostics[report["run_id"]]
        cleanup(diagnostic, rows, manifest)
        finished = instant(next(e["at"] for e in rows if e["event"] == "service_finished"))
        if action in {"browser_stop", "notification_stop"}:
            need(report["status"] == "canceled" and report.get("error") == "Canceled by user",
                 "This was not a user-requested cancellation")
            if action == "browser_stop":
                starts = [e for e in browser_events(bundle, report["run_id"]) if e["event"] == "stop_requested"]
                need(starts, "Missing browser Stop timestamp")
                low, high = on_device(starts[0]["at"], diagnostic)
            else:
                starts = [e for e in rows if e["event"] == "stop_requested"
                          and e.get("details", {}).get("source") == "notification"
                          and e["details"].get("reason") == "Canceled by user"]
                need(starts, "No notification Stop action was recorded")
                low = high = instant(starts[0]["at"])
            need(instant(report["started_at"]) <= low <= high <= instant(report["finished_at"])
                 and 0 <= finished - low <= 60, "Stop timing does not enclose bounded cancellation")
            details["cancellation_seconds"] = finished - low
        if action == "deadline":
            deadlines = [e for e in rows if e["event"] == "deadline_reached"]
            need(len(deadlines) == 1 and 1800000 <= number(deadlines[0]["elapsed_ms"]) <= 1801000,
                 "No actual 30-minute deadline observation")
            need(any(e["event"] == "stop_requested" and e.get("details", {}).get("source") == "deadline"
                     for e in rows) and report["status"] in {"failed", "canceled"}
                 and report.get("error") == "Native benchmark deadline exceeded",
                 "Deadline did not produce terminal deadline-stopped evidence")
            need(0 <= finished - instant(deadlines[0]["at"]) <= 60, "Deadline cleanup exceeded 60 seconds")

    if key.startswith("thermal."):
        intervals, active_seconds = [], 0.
        severe = False
        for report in reports:
            diagnostic, rows = diagnostics[report["run_id"]]
            interval = (instant(report["started_at"]), instant(report["finished_at"]))
            need(0 < interval[1] - interval[0] <= 1860, "Thermal run exceeds the bounded execution window")
            intervals.append(interval)
            env = [e for e in rows if e["event"] == "environment"]
            hot = [e for e in env if type(e.get("details", {}).get("thermal_status")) is int
                   and 3 <= e["details"]["thermal_status"] <= 6]
            cold = [e for e in env if type(e.get("details", {}).get("thermal_status")) is int
                    and 0 <= e["details"]["thermal_status"] < 3]
            _, windows = stages(report)
            sampled = [row for row in report["results"] if row["samples"]]
            need(sampled, "Thermal evidence contains no actual native samples")
            for row in sampled:
                execution = sum(number(s["execution_ms"]) for s in row["samples"])
                warmup = number(row.get("warmup_execution_ms", 0))
                need(execution >= 0 and warmup >= 0, "Invalid measured native work")
                for stage, measured in (("execution", execution), ("warmup", warmup)):
                    spans = [b["elapsed_ms"] - a["elapsed_ms"] for a, b in windows
                             if a["case"] == row["case"] and a["stage"] == stage]
                    need(measured <= sum(spans) + 1, "Measured work exceeds its native stage")
                active_seconds += (execution + warmup) / 1000
            if report.get("error") == "Thermal state severe; measurements stopped":
                need(hot and cold and instant(cold[0]["at"]) < instant(hot[0]["at"])
                     and interval[0] <= instant(hot[0]["at"]) <= interval[1]
                     and report["status"] == "failed", "No in-run OS thermal threshold transition")
                cleanup(diagnostic, rows, manifest)
                finish = instant(next(e["at"] for e in rows if e["event"] == "service_finished"))
                need(0 <= finish - instant(hot[0]["at"]) <= 60, "Thermal stop cleanup exceeded 60 seconds")
                severe = True
            else:
                need(report["status"] == "complete"
                     and all(row["status"] in {"passed", "unsupported"} for row in report["results"]),
                     "A failed/idle run cannot qualify sustained thermal work")
        intervals.sort()
        need(all(0 <= b[0] - a[1] <= 60 for a, b in zip(intervals, intervals[1:])),
             "Thermal runs overlap or contain a cooling gap longer than 60 seconds")
        need(severe if action == "severe_stop" else severe or active_seconds >= 1800,
             "No real thermal stop or 30 minutes of measured native work")
        details["execution_seconds"] = active_seconds

    if key.startswith("delivery."):
        need(store is not None, "Use --check-postgres for independent database verification")
        report = reports[0]
        complete(report)
        matching = [r for r in bundle.deliveries if r.get("run_id") == report["run_id"]]
        need(matching, "Missing acknowledged native delivery evidence")
        row = max(matching, key=lambda r: number(r.get("acknowledged_at", -1)))
        body, receipt = row.get("body"), row.get("receipt", {})
        need(isinstance(body, str) and _equal(decode(body.encode()), report),
             "Missing exact submitted native JSON body")
        site_sha = bundle.exports[0]["validation"]["site_sha256"]
        need(row.get("status") == "sent" and row.get("endpoint") == ENDPOINT and row.get("site_sha256") == site_sha
             and receipt.get("stored") is True and receipt.get("storage") == "postgresql"
             and receipt.get("run_id") == report["run_id"]
             and receipt.get("payload_sha256") == row.get("payload_sha256") == sha(body.encode()),
             "PostgreSQL receipt/payload/endpoint mismatch")
        history = row.get("history", [])
        need(history and all(h.get("payload_sha256") == row["payload_sha256"] and h.get("page_id")
                             and h.get("site_sha256") == site_sha for h in history),
             "Delivery history is not bound to immutable bytes and page identities")
        times = [number(h["at"]) for h in history]
        need(times == sorted(times) and history[0]["event"] == "queued"
             and history[-1]["event"] == "acknowledged"
             and row.get("acknowledged_at") == times[-1], "Invalid delivery chronology")
        need(on_device(datetime.fromtimestamp(times[0] / 1000, timezone.utc).isoformat(),
                       diagnostics[report["run_id"]][0])[0] >= instant(report["finished_at"]),
             "Delivery was queued before native completion")
        for previous in matching:
            need(previous.get("body") == body and previous.get("payload_sha256") == row["payload_sha256"]
                 and _equal(previous.get("history", []), history[:len(previous.get("history", []))]),
                 "Delivery bytes or history changed across exports")
        if action == "online":
            need([h["event"] for h in history] == ["queued", "acknowledged"],
                 "Retry/rejection evidence cannot qualify first-attempt online delivery")
        if action == "offline_retry":
            failures = [h for h in history if h["event"] == "attempt_failed"]
            restores = [h for h in history if h["event"] == "restored"]
            need(any((f.get("failure_kind") == "transport" and f.get("http_status") is None
                      or f.get("failure_kind") == "http" and f.get("http_status") in {502, 503, 504})
                     and isinstance(f.get("error"), str) and f["error"]
                     and f["at"] < s["at"] < times[-1] and f["page_id"] != s["page_id"]
                     and history[-1]["page_id"] == s["page_id"] for f in failures for s in restores),
                 "Missing ordered failure, actual page restoration, and acknowledgment")
        if action == "idempotency":
            observations(check, "repeat_submission", report=report)
            need(any(r.get("endpoint") == ENDPOINT and r.get("status_code") == 200
                     and r.get("payload_sha256") == row["payload_sha256"]
                     and _equal(r.get("receipt"), receipt)
                     and times[-1] / 1000 <= instant(r["requested_at"]) <= instant(r["responded_at"])
                     for r in bundle.receipts),
                 "Missing independent, timestamped repeat-submission exchange")
        try:
            verify_database(store, report, receipt)
        except ValueError:
            raise
        except Exception:
            raise ValueError("PostgreSQL read verification failed; check local database availability") from None
    return details


def verify_installation(key, check, values, manifest, site_sha):
    from engine.native_reports import _real

    action = key.split(".")[1]
    observations(check, {"same_key_upgrade": "upgrade", "same_key_downgrade": "downgrade"}.get(action, action))
    exports = [v for v in values if isinstance(v, dict) and "reports" in v]
    need(exports, "Installation gates need original browser exports, not manifests alone")
    snapshots, releases = [], []
    for value in exports:
        extra = value.get("validation", {})
        need(extra.get("origin") == ORIGIN and extra.get("evidence_version") == 2,
             "Installation evidence lacks verified local browser provenance")
        release = extra["release"]
        check_release(release)
        if _equal(release, manifest):
            need(extra.get("site_sha256") == site_sha, "Installation used a different browser implementation")
            need(value["session"].get("site_sha256") == site_sha
                 and all(e.get("site_sha256") == site_sha for e in extra.get("browser_events", [])),
                 "Installation session/events belong to another website implementation")
        releases.append(release)
        need(all(instant(s["captured_at"]) <= instant(extra["exported_at"])
                 for s in extra.get("installations", [])), "Installation snapshot postdates its export")
        snapshots.extend(extra.get("installations", []))
        _real(value)
    if key == "installation.update_runner":
        need(len(exports) >= 2, "Preserve old and new browser sessions")
        old = [e for e in exports if e["validation"]["release"]["apk_sha256"] != manifest["apk_sha256"]]
        new = [e for e in exports if _equal(e["validation"]["release"], manifest)]
        need(len(old) == len(new) == 1, "Update needs a distinct previous release and final candidate")
        retired, current = old[0], new[0]
        old_bundle = reports_from([retired], retired["validation"]["release"])
        new_bundle = reports_from([current], manifest, site_sha)
        need(len(old_bundle.reports) == len(new_bundle.reports) == 1, "Update needs two distinct native run identities")
        previous, candidate = old_bundle.reports[0], new_bundle.reports[0]
        need(previous["status"] == "running" and candidate["run_id"] != previous["run_id"]
             and retired["session"]["status"] == "canceled", "Old unfinished checkpoint was not preserved/retired")
        complete(candidate, timing=False)
        old_events = browser_events(old_bundle, previous["run_id"])
        new_events = browser_events(new_bundle, candidate["run_id"])
        retirement = [e for e in old_events if e["event"] == "retired_for_update"
                      and e.get("next_apk_sha256") == manifest["apk_sha256"]]
        need(len(retirement) == 1 and instant(next(e["at"] for e in new_events if e["event"] == "launch_intent"))
             > instant(retirement[0]["at"]), "New run started before the previous worker was retired")
        requested = [e for e in old_events if e["event"] == "update_requested"
                     and e.get("next_apk_sha256") == manifest["apk_sha256"]]
        need(len(requested) == 1 and requested[0].get("site_sha256") == site_sha
             and retirement[0].get("site_sha256") == site_sha
             and any(instant(requested[0]["at"]) <= instant(o["at"])
                                        <= instant(retirement[0]["at"]) for o in check["observations"]),
             "Missing ordered Update runner request and operator observation")
        return {"run_ids": [previous["run_id"], candidate["run_id"]]}
    need(snapshots, "No device installation snapshots captured")
    for snapshot in snapshots:
        need(snapshot.get("kind") == "xrbench.installation-snapshot" and not snapshot.get("error"),
             "Invalid installation snapshot")
        instant(snapshot["captured_at"])
    current = [s for s in snapshots if s.get("installed") is True
               and s.get("apk_sha256") == manifest["apk_sha256"] and s.get("package") == manifest["package"]
               and s.get("version_code") == manifest["version_code"]]
    need(current, "Final candidate was not observed installed on the device")
    after = max(current, key=lambda s: instant(s["captured_at"]))
    identity(after.get("installation_id"))
    need(after.get("connection_id"), "Missing device connection identity")
    need(any(v["session"].get("connection_id") == after["connection_id"] for v in exports
             if _equal(v["validation"]["release"], manifest)), "Installation snapshot belongs to another connection")
    for snapshot in snapshots:
        if snapshot.get("installed"):
            import re

            need(snapshot["package"] == manifest["package"] and
                 re.fullmatch(r"[a-f0-9]{64}", snapshot["apk_sha256"])
                 and type(snapshot["version_code"]) is int and snapshot["version_code"] > 0,
                 "Invalid observed installed package identity")
            version = re.search(r"\bversionCode=(\d+)\b", snapshot.get("package_dump", ""))
            need(version and int(version[1]) == snapshot["version_code"], "Missing original installed-version dump")
            files = snapshot.get("reports", [])
            need(len({r["path"] for r in files}) == len(files)
                 and all(re.fullmatch(r"files/runs/run-[a-f0-9]{32}/report\.json", r["path"])
                         and re.fullmatch(r"[a-f0-9]{64}", r["sha256"]) for r in files),
                 "Invalid preserved report inventory")
    if action == "usb_authorization":
        obs = observations(check, "usb_authorization")
        need(any(o.get("usb_authorized") is True and o.get("debugging_authorized") is True for o in obs),
             "Operator must attest both browser USB and headset debugging authorization")
        connections = [e for v in exports for e in v["validation"].get("browser_events", [])
                       if e.get("event") == "connected" and e.get("event_id") and e.get("page_id")]
        need(any(instant(o["at"]) <= instant(e["at"]) <= instant(after["captured_at"])
                 and instant(after["captured_at"]) - instant(e["at"]) <= 240
                 for o in obs for e in connections), "No authenticated browser connection follows the approval observation")
        return {"installation_id": after["installation_id"]}
    before = [s for s in snapshots if s.get("installed") is True
              and s.get("package") == after["package"] and s["apk_sha256"] != after["apk_sha256"]
              and instant(s["captured_at"]) < instant(after["captured_at"])]
    need(before, "Missing before/after installation state")
    preserved = False
    for prior in before:
        if not any(instant(prior["captured_at"]) <= instant(o["at"]) <= instant(after["captured_at"])
                   for o in check["observations"]):
            continue
        if prior.get("installation_id") != after["installation_id"] or prior.get("connection_id") != after["connection_id"]:
            continue
        old_files = prior.get("reports", [])
        new_files = {r["path"]: r["sha256"] for r in after.get("reports", [])}
        if not old_files or not all(new_files.get(r["path"]) == r["sha256"] for r in old_files):
            continue
        if action == "existing_app_preserved":
            preserved = True
        else:
            old_release = next((r for r in releases if r["apk_sha256"] == prior["apk_sha256"]
                                and r["version_code"] == prior["version_code"]), None)
            if old_release is None or old_release["signing_certificate_sha256"] != manifest["signing_certificate_sha256"]:
                continue
            direction = prior["version_code"] < after["version_code"] if action == "same_key_upgrade" else prior["version_code"] > after["version_code"]
            preserved |= direction and prior["apk_sha256"] != after["apk_sha256"]
    need(preserved, "No observed, same-installation transition preserving original app data")
    return {"installation_id": after["installation_id"]}


def assess(directory, *, store=None):
    root = Path(directory).resolve()
    attempt = load(root / "attempt.json")
    need(attempt.get("schema_version") == 1 and attempt.get("kind") == "xrbench.headset-validation",
         "Invalid campaign attempt")
    need(attempt.get("origin") == "http://localhost:8765", "Campaign is scoped to localhost:8765")
    release_bytes = read_evidence(root / "release.json")
    need(sha(release_bytes) == attempt["release_sha256"], "Frozen release changed")
    manifest = decode(release_bytes)
    check_release(manifest)
    site = load(root / "site.json")
    from engine.reports import digest

    need(site.get("schema_version") == 1 and digest(site["files"]) == site.get("site_sha256") == attempt.get("site_sha256"),
         "Frozen browser identity changed")
    need(_equal(site_identity(root / "site"), site), "Frozen browser artifact changed")
    need(set(attempt["checks"]) == set(GATES), "Campaign checklist is incomplete or unknown")
    inventory, results, all_devices, cache, bundles = {}, {}, [], {}, {}
    for key, check in attempt["checks"].items():
        status = check.get("status")
        need(status in STATUSES, "Unknown gate status")
        result = dict(status=status, reason=check.get("reason", ""), next_action=check.get("next_action", GATES[key]),
                      evidence=check.get("evidence", []))
        if status == "passed":
            try:
                need(bool(attempt.get("operator")) and all(isinstance(attempt.get("host", {}).get(k), str)
                     and attempt["host"][k].strip() for k in ("os", "browser", "user_agent"))
                     and all(attempt.get("device", {}).get(k) for k in ("firmware", "gpu", "installation_reference", "installation_id")),
                     "Record operator, host, firmware, GPU, and installation identity first")
                identity(attempt["device"]["installation_id"])
                need(all(o.get("operator") == attempt["operator"] for o in check.get("observations", [])),
                     "Observation operator differs from the attempt operator")
                values = artifacts(root, check.get("evidence", []), inventory, cache)
                need(all(v.get("validation", {}).get("user_agent") == attempt["host"]["user_agent"]
                         for v in values if isinstance(v, dict) and "reports" in v),
                     "Browser identity changed or was not recorded")
                if key.startswith("installation."):
                    result.update(verify_installation(key, check, values, manifest, site["site_sha256"]))
                    current = [v for v in values if isinstance(v, dict) and "reports" in v
                               and _equal(v.get("validation", {}).get("release"), manifest)]
                    need(current, "No final-candidate browser profile")
                    profiles = [v["session"].get("capabilities", {}) for v in current]
                    for profile in profiles:
                        from engine.devices import identify

                        need(identify(profile)["device_id"] == "quest-3"
                             and profile.get("installation_id") == attempt["device"]["installation_id"]
                             and profile.get("os") == attempt["device"]["firmware"]
                             and profile.get("gpu") == attempt["device"]["gpu"],
                             "Installation profile does not match this physical attempt")
                    if result.get("installation_id"):
                        need(result["installation_id"] == attempt["device"]["installation_id"],
                             "Installation snapshots changed installation identity")
                    bundle = reports_from(current, manifest, site["site_sha256"])
                    bundles[key] = bundle
                    result["reason"] = "Installation artifacts and specific operator observations checked"
                    results[key] = result
                    continue
                bundle = reports_from(values, manifest, site["site_sha256"])
                for report in bundle.reports:
                    need(report["device"]["os"] == attempt["device"]["firmware"]
                         and report["device"]["gpu"] == attempt["device"]["gpu"]
                         and report["device"]["installation_id"] == attempt["device"]["installation_id"],
                         "Observed device/installation identity changed")
                result.update(verify_gate(key, check, bundle, store, manifest))
                bundles[key] = bundle
                all_devices.extend({k: report["device"][k] for k in DEVICE_FIELDS} for report in bundle.reports)
                result["reason"] = "Evidence and operator observations checked"
            except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError) as error:
                result.update(status="failed", reason=str(error))
        results[key] = result
    # Compare originals across gates as well as within one evidence bundle.
    # A run ID cannot acquire new display state, samples, or final content in
    # another checklist entry.
    run_claims = {}
    for key, bundle in bundles.items():
        for raw in bundle.snapshots:
            run_claims.setdefault(raw["run_id"], []).append((key, raw))
    for claims in run_claims.values():
        try:
            checkpoint_chain([raw for _, raw in claims])
        except (ValueError, KeyError, TypeError) as error:
            for key, _ in claims:
                results[key].update(status="failed", reason=str(error))
    for first, second in (("correctness.worn", "correctness.unworn"),
                          ("delivery.online", "delivery.offline_retry")):
        if results[first]["status"] == results[second]["status"] == "passed":
            if set(results[first]["run_ids"]) & set(results[second]["run_ids"]):
                results[second].update(status="failed", reason="These conditions require distinct physical runs")
    connections = {value["session"]["connection_id"] for bundle in bundles.values() for value in bundle.exports}
    if len(connections) > 1:
        for key in bundles:
            results[key].update(status="failed", reason="Mixed physical device connections in attempt")
    worn, unworn = results["correctness.worn"], results["correctness.unworn"]
    if worn["status"] == unworn["status"] == "passed":
        if set(worn["unsupported"]) != set(unworn["unsupported"]):
            unworn.update(status="failed", reason="Capabilities changed between display conditions")
    if all_devices and any(not _equal(d, all_devices[0]) for d in all_devices):
        for result in results.values():
            if result["status"] == "passed":
                result.update(status="failed", reason="Mixed device/firmware identities in attempt")
    statuses = {r["status"] for r in results.values()}
    return dict(schema_version=1, evidence_version=2, kind="xrbench.headset-validation.assessment",
                assessed_at=datetime.now(timezone.utc).isoformat(),
                status="ready" if statuses == {"passed"} else "failed" if "failed" in statuses else "incomplete",
                scope="Quest 3 / local browser USB / standard native operators / local PostgreSQL",
                site_sha256=site["site_sha256"],
                device_scope={k: attempt.get("device", {}).get(k) for k in
                              ("model", "firmware", "gpu", "installation_id")},
                host_scope=attempt.get("host", {}), origin=ORIGIN,
                release=release_identity(manifest), checks=results, evidence=inventory,
                database_check_requested=store is not None,
                database_checked=store is not None and any(key.startswith("delivery.") and value["status"] == "passed"
                                                          for key, value in results.items()),
                limitations="Operator attestations and local consistency checks do not authenticate hardware; "
                "unsupported operators, hosted releases, OpenXR timing, and speedup claims are excluded.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--manifest", type=Path, default=ROOT / "dist/runner/manifest.json")
    init.add_argument("--output", type=Path, required=True)
    assessment = commands.add_parser("assess")
    assessment.add_argument("attempt", type=Path)
    assessment.add_argument("--check-postgres", action="store_true")
    assessment.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init":
            print(initialize(args.manifest, args.output))
            return 0
        store = None
        if args.check_postgres:
            from engine.db.config import database

            store = database()
        result = assess(args.attempt, store=store)
        if args.output:
            write_private(args.output, result)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "ready" else 1
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OSError) as error:
        parser.exit(2, f"Campaign evidence error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
