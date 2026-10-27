"""Synthetic consistency tests; none of these fixtures qualifies physical hardware."""

import copy
from datetime import datetime, timezone
import json

import pytest

from engine import headset_validation as campaign
from engine.headset_evidence import ENDPOINT
from engine.reports import digest
from engine.tests.test_native_public import native_fixture

BASE = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
INSTALLATION = "11111111-2222-3333-4444-555555555555"
SITE_SOURCE = {"index.html": "<head></head>", "bench/app.js": "// protocol fixture", "bench/style.css": "body{}"}
SITE_FILES = {name: campaign.sha(data.encode()) for name, data in SITE_SOURCE.items()}
SITE_SHA = digest(SITE_FILES)


def at(seconds):
    return datetime.fromtimestamp(BASE + seconds, timezone.utc).isoformat(timespec="milliseconds")


def event(name, seconds, index, page="page-one", **details):
    return dict(event=name, at=at(seconds), run_id="run-" + "1" * 32,
                event_id=f"event-{index}", page_id=page, site_sha256=SITE_SHA, **details)


def build_timeline(report, *, start=0, step=.8, sample_ms=1):
    report["started_at"], report["finished_at"] = at(start), at(start + len(report["results"]) * step)
    stages = [dict(stage="preparation", case=None, at=at(start), elapsed_ms=0)]
    for i, row in enumerate(report["results"]):
        t = start + i * step
        begin = .3 if step < 1 else .003
        readback = begin + sample_ms / 1000 * 100 + .001
        for stage, offset in (("preparation", 0), ("readback", begin / 3),
                              ("warmup", begin * 2 / 3), ("execution", begin), ("readback", readback)):
            stages.append(dict(stage=stage, case=row["case"], at=at(t + offset),
                               elapsed_ms=(t - start + offset) * 1000))
        row["warmup_execution_ms"] = 1
        for j, sample in enumerate(row["samples"]):
            elapsed = i * step + begin + (j + 1) * sample_ms / 1000
            sample.update(at=at(start + elapsed), elapsed_ms=elapsed * 1000, execution_ms=sample_ms)
    end = len(report["results"]) * step
    stages.append(dict(stage="finished", case=None, at=at(start + end), elapsed_ms=end * 1000))
    report["stage_events"] = stages
    report["progress"] = dict(run_id=report["run_id"], status=report["status"], sequence=10000,
                              completed_cases=sum(r["status"] != "interrupted" for r in report["results"]))
    return report


def diagnostic(report, manifest, *, environment=None, stop=None):
    start = campaign.instant(report["started_at"]) - BASE
    end = campaign.instant(report["finished_at"]) - BASE
    began = start - .01

    def entry(name, seconds, **extra):
        return dict(event=name, at=at(seconds), elapsed_ms=(seconds - began) * 1000, **extra)

    rows = [entry("service_started", began), entry("wake_acquired", began + .001),
            entry("environment", began + .005, details=dict(display_interactive=False, thermal_status=0, charging=False))]
    if environment:
        rows[2:] = [entry("environment", seconds, details=details) for seconds, details in environment]
    if stop:
        source, second = stop
        if source == "deadline":
            rows.append(entry("deadline_reached", second))
        rows.append(entry("stop_requested", second + .001,
                          details=dict(source=source, reason="Native benchmark deadline exceeded"
                                       if source == "deadline" else "Canceled by user")))
    rows += [entry("worker_finished", end + .002), entry("wake_released", end + .003),
             entry("service_finished", end + .004), entry("service_destroyed", end + .005)]
    rows.sort(key=lambda e: campaign.instant(e["at"]))
    args = {"service": ["dumpsys", "activity", "services", manifest["package"]],
            "power": ["dumpsys", "power"], "notification": ["dumpsys", "notification", manifest["package"]]}
    outputs = {"service": "ACTIVITY MANAGER SERVICES (dumpsys activity services)\n  (nothing)\n",
               "power": "POWER MANAGER\nWake Locks: size=0\n\nSuspend Blockers: size=1\n",
               "notification": "Notification service state:\nNotification List:\n  (none)\n"}
    return dict(run_id=report["run_id"], package=manifest["package"], apk_sha256=manifest["apk_sha256"],
                clock=dict(host_started_at=at(end + .099), host_finished_at=at(end + .101), device_at=at(end + .1)),
                lifecycle=dict(run_id=report["run_id"], events=rows), captured_at=at(end + .2),
                commands={k: dict(code=0, stdout=outputs[k], stderr="", args=v,
                                  started_at=at(end + .11), finished_at=at(end + .12)) for k, v in args.items()})


def export(report, manifest, **extra):
    phase = dict(run_id=report["run_id"], configuration=copy.deepcopy(report["configuration"]))
    metadata = dict(release=copy.deepcopy(manifest), evidence_version=2, origin=campaign.ORIGIN,
                    site_sha256=SITE_SHA,
                    user_agent="Test browser user agent",
                    exported_at=at(campaign.instant(report.get("finished_at", at(.34))) - BASE + 1),
                    diagnostics=[diagnostic(report, manifest)] if report["status"] != "running" else [],
                    browser_events=[event("connected", -.03, 1), event("launch_intent", -.02, 2)],
                    deliveries=[], installations=[])
    metadata.update(extra)
    return dict(schema_version=1, session=dict(session_id="bench-one", site_sha256=SITE_SHA, connection_id="e" * 24, status=report["status"],
                capabilities=copy.deepcopy(report["device"]), phases=[phase]), reports=[copy.deepcopy(report)], validation=metadata)


@pytest.fixture
def attempt(tmp_path):
    report, manifest = native_fixture()
    passing = copy.deepcopy(report["results"][0])
    for row in report["results"]:
        row.update({k: copy.deepcopy(v) for k, v in passing.items() if k not in {"case", "operation"}})
        row.pop("error", None)
        for key in ("correctness_before", "correctness_after", "correctness"):
            row[key] = dict(passed=True, outputs=[dict(name=value["name"], passed=True,
                                                      max_abs_error=0) for value in row["outputs"]])
    checksum = campaign.sha(b"test APK bytes, not a signed release")
    manifest.update(protocol_version=2, apk_sha256=checksum, apk=checksum + ".apk", version_code=2,
                    signing_certificate_sha256="c" * 64, package=campaign.signing_package("c" * 64), runner_revision="d" * 64,
                    **{k: report[k] for k in ("runtime_revision", "native_package_sha256", "native_catalog_sha256")})
    manifest["catalog"].update(operator_count=221, source_package_sha256=manifest["native_package_sha256"],
                               native_catalog_sha256=manifest["native_catalog_sha256"])
    manifest["suite_revision"] = digest(manifest["catalog"])
    manifest["catalog"]["suite_revision"] = manifest["suite_revision"]
    config = manifest["configurations"]["gpu"]
    for key in campaign.IDENTITIES:
        if key not in {"package", "signing_certificate_sha256"}:
            config[key] = manifest[key]
    report["configuration"] = dict(config, run_id=report["run_id"])
    report["suite_revision"] = manifest["suite_revision"]
    report["device"] = dict(model="Quest 3", product="eureka", device="eureka", os="fixture-firmware",
                            gpu="fixture-adreno", sdk=32, abi=["arm64-v8a"], installation_id=INSTALLATION,
                            vulkan=True, backends={"xrgpu_vulkan": {"compatible": True}})
    report["device"].update({k: manifest[k] for k in ("runner_revision", "runtime_revision", "native_package_sha256")})
    build_timeline(report)
    release = tmp_path / "release"
    release.mkdir()
    (release / manifest["apk"]).write_bytes(b"test APK bytes, not a signed release")
    (release / "manifest.json").write_text(json.dumps(manifest))
    for name, text in SITE_SOURCE.items():
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == "index.html":
            text = text.replace("<head>", '<head>\n  <meta name="xrbench-site-sha256" content="' + SITE_SHA + '">')
        target.write_text(text)
    (release / "site-manifest.json").write_text(json.dumps(dict(schema_version=1, site_sha256=SITE_SHA, files=SITE_FILES)))
    root = campaign.initialize(release / "manifest.json", tmp_path / "private-attempt")
    ledger = campaign.load(root / "attempt.json")
    ledger.update(created_at=at(-60), operator="Test operator", host=dict(os="test host", browser="test browser", user_agent="Test browser user agent"),
                  device=dict(firmware="fixture-firmware", gpu="fixture-adreno",
                              installation_reference="private-reference", installation_id=INSTALLATION))

    def save():
        (root / "attempt.json").write_text(json.dumps(ledger))

    def attach(key, value=None, *, action=None, seconds=.35, **observation):
        values = value if isinstance(value, list) else [value if value is not None else export(report, manifest)]
        references = []
        for index, item in enumerate(values):
            name = f"{key}-{index}.json"
            (root / name).write_text(json.dumps(item))
            references.append(dict(path=name, sha256=campaign.sha((root / name).read_bytes())))
        action = action or ("headset_removed" if key.startswith("removal.") else
                            "repeat_submission" if key == "delivery.idempotency" else key.split(".")[1])
        ledger["checks"][key].update(status="passed", evidence=references,
            observations=[dict(at=at(seconds), operator="Test operator", action=action,
                               observed="Explicit scenario observation", run_id=report["run_id"], **observation)])
        save()

    return root, ledger, manifest, report, attach


def result(attempt, key, *, store=None):
    return campaign.assess(attempt[0], store=store)["checks"][key]


def canceled(report, end=.38, error="Canceled by user"):
    raw = copy.deepcopy(report)
    raw.update(status="canceled", error=error, finished_at=at(end), results=[copy.deepcopy(report["results"][0])])
    row = raw["results"][0]
    row.update(status="interrupted", error=error, samples=row["samples"][:70])
    raw["stage_events"] = copy.deepcopy(report["stage_events"][:5]) + [dict(stage="finished", case=None, at=at(end), elapsed_ms=end * 1000)]
    raw["progress"].update(status="canceled", completed_cases=0)
    return raw


def recovery_exports(report, manifest, *, action="refresh"):
    before = copy.deepcopy(report)
    before.update(status="running", results=[copy.deepcopy(report["results"][0])])
    before.pop("finished_at")
    before["results"][0].update(status="interrupted", samples=before["results"][0]["samples"][:40])
    before["stage_events"] = copy.deepcopy(report["stage_events"][:5])
    before["progress"].update(status="running", sequence=5, completed_cases=0)
    initial = [event("connected", -.03, 1), event("launch_intent", -.02, 2)]
    new = initial[:]
    if action != "refresh":
        new.append(event("disconnected", .361, 3))
    if action == "disconnected_stop":
        new.append(event("stop_requested", .365, 4))
    new.append(event("connected", .37, 5, page="page-two" if action == "refresh" else "page-one"))
    return [export(before, manifest, exported_at=at(.345), browser_events=initial),
            export(report, manifest, browser_events=new)]


def delivery_export(report, manifest, *, offline=False):
    end = campaign.instant(report["finished_at"]) - BASE
    body = json.dumps(report)
    checksum = campaign.sha(body.encode())
    receipt = dict(stored=True, storage="postgresql", run_id=report["run_id"],
                   payload_sha256=checksum, report_sha256="a" * 64)
    names = ["queued", "attempt_failed", "restored", "acknowledged"] if offline else ["queued", "acknowledged"]
    history = [dict(event=name, at=round((BASE + end + 1 + i) * 1000), payload_sha256=checksum, site_sha256=SITE_SHA,
                    page_id="delivery-page-two" if offline and i >= 2 else "delivery-page-one")
               for i, name in enumerate(names)]
    for item in history:
        if item["event"] == "attempt_failed":
            item.update(error="Receiver unavailable", failure_kind="http", http_status=503)
    row = dict(body=body, run_id=report["run_id"], payload_sha256=checksum, receipt=receipt,
               status="sent", endpoint=ENDPOINT, history=history, site_sha256=SITE_SHA, acknowledged_at=history[-1]["at"])
    return export(report, manifest, deliveries=[row], exported_at=at(end + len(history) + 1))


def test_init_private_blocked_non_overwriting(attempt):
    root = attempt[0]
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "release.json").stat().st_mode & 0o777 == 0o600
    assert campaign.assess(root)["status"] == "incomplete"
    assert {c["status"] for c in campaign.assess(root)["checks"].values()} == {"blocked"}
    with pytest.raises(FileExistsError):
        campaign.initialize(root.parent / "release/manifest.json", root)


def test_frozen_browser_files_cannot_be_replaced_without_invalidating_attempt(attempt):
    (attempt[0] / "site/bench/app.js").write_text("// another website")
    with pytest.raises(ValueError, match="Browser site bytes"):
        campaign.assess(attempt[0])


def test_supported_count_and_exclusions(attempt):
    root, ledger, manifest, report, attach = attempt
    report["results"][0].update(status="unsupported", error="Missing capability", samples=[])
    attach("correctness.unworn")
    assert result(attempt, "correctness.unworn")["passed"] == 220
    ledger["checks"]["correctness.unworn"]["passed_count"] = 221
    (root / "attempt.json").write_text(json.dumps(ledger))
    assert result(attempt, "correctness.unworn")["status"] == "failed"


@pytest.mark.parametrize("damage", ["software", "simulated_wrapper", "wrong_device", "mixed_release", "failed_operator",
    "wrong_display", "missing_operators", "missing_origin", "installation_id", "sample_time", "clock_skew", "invalid_timing",
    "false_qualification", "profile_flags", "sdk", "abi", "user_agent", "ambiguous_marker",
    "output_failure", "missing_output_verdict", "site_version"])
def test_bad_native_evidence_cannot_pass(attempt, damage):
    _, _, manifest, report, attach = attempt
    value = export(report, manifest)
    raw = value["reports"][0]
    if damage == "software":
        raw["test_only"] = True
    elif damage == "ambiguous_marker":
        raw["test_only"] = 1
    elif damage == "simulated_wrapper":
        value["test_only"] = True
    elif damage == "wrong_device":
        raw["device"]["model"] = "Quest 3S"
    elif damage == "mixed_release":
        value["validation"]["release"]["apk_sha256"] = "f" * 64
    elif damage == "failed_operator":
        raw["results"][0].update(status="failed", error="Wrong output")
    elif damage == "wrong_display":
        raw["results"][0]["samples"][0]["display_interactive"] = True
    elif damage == "missing_operators":
        raw["results"].pop()
    elif damage == "missing_origin":
        value["validation"].pop("origin")
    elif damage == "installation_id":
        raw["device"]["installation_id"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    elif damage == "sample_time":
        raw["results"][0]["samples"][0]["at"] = at(999)
    elif damage == "clock_skew":
        value["validation"]["diagnostics"][0]["clock"]["host_finished_at"] = at(999)
    elif damage == "false_qualification":
        raw["results"][0]["runtime_info"]["physical_qualification"] = False
    elif damage == "profile_flags":
        raw["device"]["vulkan"] = False
    elif damage == "sdk":
        raw["device"]["sdk"] = True
        value["session"]["capabilities"]["sdk"] = True
    elif damage == "abi":
        raw["device"]["abi"] = ["x86_64"]
        value["session"]["capabilities"]["abi"] = ["x86_64"]
    elif damage == "user_agent":
        value["validation"]["user_agent"] = "Another browser"
    elif damage == "output_failure":
        raw["results"][0]["correctness_before"]["outputs"][0]["passed"] = False
    elif damage == "missing_output_verdict":
        raw["results"][0]["correctness_after"].pop("outputs")
    elif damage == "site_version":
        value["validation"]["site_sha256"] = "b" * 64
    else:
        raw["results"][0]["timing_valid"] = False
    attach("correctness.unworn", value)
    assert result(attempt, "correctness.unworn")["status"] == "failed"


@pytest.mark.parametrize("key", ["installation.usb_authorization", "installation.existing_app_preserved",
                                "installation.same_key_upgrade", "installation.same_key_downgrade", "installation.update_runner"])
def test_manifests_and_unrelated_files_cannot_pass_installation(attempt, key):
    _, _, manifest, _, attach = attempt
    attach(key, manifest)
    assert result(attempt, key)["status"] == "failed"


def test_same_run_conflicting_across_gates_is_rejected(attempt):
    _, _, manifest, report, attach = attempt
    attach("correctness.unworn")
    raw = copy.deepcopy(report)
    for row in raw["results"]:
        for sample in row["samples"]:
            sample["display_interactive"] = True
    attach("correctness.worn", export(raw, manifest))
    assessment = campaign.assess(attempt[0])
    assert assessment["checks"]["correctness.unworn"]["status"] == "failed"
    assert assessment["checks"]["correctness.worn"]["status"] == "failed"


@pytest.mark.parametrize("action", ["refresh", "usb_reconnect", "disconnected_stop"])
def test_recovery_requires_actual_interruption_and_checkpoint_preservation(attempt, action):
    _, _, manifest, report, attach = attempt
    if action == "disconnected_stop":
        report = canceled(report)
    values = recovery_exports(report, manifest, action=action)
    attach("recovery." + action, values, seconds=.36)
    assert result(attempt, "recovery." + action)["status"] == "passed"
    values.pop(0)
    attach("recovery." + action, values, seconds=.36)
    assert result(attempt, "recovery." + action)["status"] == "failed"


@pytest.mark.parametrize("damage", ["lost_sample", "new_page_missing", "duplicate_launch", "conflicting_event"])
def test_recovery_rejects_replay_and_lost_evidence(attempt, damage):
    _, _, manifest, report, attach = attempt
    values = recovery_exports(report, manifest)
    if damage == "lost_sample":
        values[0]["reports"][0]["results"][0]["samples"][0]["execution_ms"] = 99
    elif damage == "new_page_missing":
        values[1]["validation"]["browser_events"][-1]["page_id"] = "page-one"
    else:
        ev = copy.deepcopy(values[1]["validation"]["browser_events"][1])
        if damage == "duplicate_launch":
            ev["event_id"] = "event-another-launch"  # even the same UTC timestamp is another launch
        else:
            ev["at"] = at(-.01)
        values[1]["validation"]["browser_events"].append(ev)
    attach("recovery.refresh", values, seconds=.36)
    assert result(attempt, "recovery.refresh")["status"] == "failed"


def test_usb_recovery_preserves_charging_transition_without_qualifying_timing(attempt):
    _, _, manifest, report, attach = attempt
    report["results"][0]["samples"][50]["charging"] = True
    report["results"][0]["timing_valid"] = False
    attach("recovery.usb_reconnect", recovery_exports(report, manifest, action="usb_reconnect"), seconds=.36)
    assert result(attempt, "recovery.usb_reconnect")["status"] == "passed"
    attach("correctness.unworn", export(report, manifest))
    assert result(attempt, "correctness.unworn")["status"] == "failed"


def test_removal_requires_transition_sampling_and_clock_alignment(attempt):
    _, _, manifest, report, attach = attempt
    # Remove in warmup and prove later samples ran after the display transition.
    env = [(-.005, dict(display_interactive=True, thermal_status=0)),
           (.26, dict(display_interactive=False, thermal_status=0))]
    value = export(report, manifest, diagnostics=[diagnostic(report, manifest, environment=env)])
    attach("removal.warmup", value, seconds=.25)
    assert result(attempt, "removal.warmup")["status"] == "passed"
    value["validation"]["diagnostics"][0]["lifecycle"]["events"][2]["details"]["display_interactive"] = False
    attach("removal.warmup", value, seconds=.25)
    assert result(attempt, "removal.warmup")["status"] == "failed"


@pytest.mark.parametrize("damage", ["active_service", "held_wake", "active_notification", "wrong_package", "early_snapshot", "destroy_order", "empty_stdout"])
def test_cleanup_needs_real_os_absence_not_exit_zero(attempt, damage):
    _, _, manifest, report, attach = attempt
    value = export(report, manifest)
    d = value["validation"]["diagnostics"][0]
    attach("service.cleanup", value)
    assert result(attempt, "service.cleanup")["status"] == "passed"
    if damage == "active_service":
        d["commands"]["service"]["stdout"] += "ServiceRecord{123 " + manifest["package"] + "/.BenchmarkService}\n"
    elif damage == "held_wake":
        d["commands"]["power"]["stdout"] = "POWER MANAGER\nWake Locks: size=1\n PARTIAL_WAKE_LOCK 'xrBench:benchmark'\n\n"
    elif damage == "active_notification":
        d["commands"]["notification"]["stdout"] += "NotificationRecord{pkg=" + manifest["package"] + "}\n"
    elif damage == "wrong_package":
        d["package"] = "other.application"
    elif damage == "early_snapshot":
        d["commands"]["service"]["started_at"] = at(0)
    elif damage == "destroy_order":
        d["lifecycle"]["events"][-1]["event"] = "worker_finished"
    else:
        d["commands"]["service"]["stdout"] = ""
    attach("service.cleanup", value)
    assert result(attempt, "service.cleanup")["status"] == "failed"


@pytest.mark.parametrize("source", ["notification", "deadline", "service_destroyed"])
def test_notification_stop_does_not_accept_deadline_or_destruction(attempt, source):
    _, _, manifest, report, attach = attempt
    report = canceled(report, error="Canceled by user" if source != "deadline" else "Native benchmark deadline exceeded")
    value = export(report, manifest, diagnostics=[diagnostic(report, manifest, stop=(source, .365))])
    attach("service.notification_stop", value)
    assert result(attempt, "service.notification_stop")["status"] == ("passed" if source == "notification" else "failed")


@pytest.mark.parametrize("damage", ["idle", "empty_failure", "overlap"])
def test_thermal_does_not_count_wall_time_or_overlapping_runs(attempt, damage):
    _, _, manifest, report, attach = attempt
    if damage == "empty_failure":
        report.update(status="failed", error="Startup stalled", results=[])
    report["finished_at"] = at(1800)
    report["stage_events"][-1].update(at=at(1800), elapsed_ms=1800000)
    report["progress"]["status"] = report["status"]
    report["progress"]["completed_cases"] = len(report["results"])
    values = [export(report, manifest)]
    if damage == "overlap":
        other = copy.deepcopy(report)
        other["run_id"] = "run-" + "2" * 32
        other["configuration"]["run_id"] = other["run_id"]
        other["progress"]["run_id"] = other["run_id"]
        values.append(export(other, manifest))
    attach("thermal.sustained", values)
    assert result(attempt, "thermal.sustained")["status"] == "failed"


@pytest.mark.parametrize("damage", ["wrong_payload", "backwards_time", "same_page", "wrong_endpoint", "missing_exchange", "auth_failure", "invalid_receipt"])
def test_delivery_rejects_weak_retry_and_receipt_evidence(attempt, monkeypatch, damage):
    _, _, manifest, report, attach = attempt
    value = delivery_export(report, manifest, offline=True)
    row = value["validation"]["deliveries"][0]
    monkeypatch.setattr(campaign, "verify_database", lambda *args: None)
    attach("delivery.offline_retry", value)
    assert result(attempt, "delivery.offline_retry", store=object())["status"] == "passed"
    key = "delivery.offline_retry"
    if damage == "wrong_payload":
        row["history"][1]["payload_sha256"] = "bad"
    elif damage == "backwards_time":
        row["history"][2]["at"] = 0
    elif damage == "same_page":
        row["history"][2]["page_id"] = row["history"][1]["page_id"]
    elif damage == "wrong_endpoint":
        row["endpoint"] = "https://other.example/api/reports"
    elif damage in {"auth_failure", "invalid_receipt"}:
        row["history"][1]["http_status"] = 403 if damage == "auth_failure" else 200
    else:
        key = "delivery.idempotency"
    attach(key, [value, row["receipt"]])
    assert result(attempt, key, store=object())["status"] == "failed"


def test_repeat_receipt_requires_timestamped_exchange_and_database_read(attempt, monkeypatch):
    _, _, manifest, report, attach = attempt
    value = delivery_export(report, manifest)
    row = value["validation"]["deliveries"][0]
    end = campaign.instant(report["finished_at"]) - BASE
    repeat = dict(kind="xrbench.delivery-attempt", endpoint=ENDPOINT, status_code=200,
                  payload_sha256=row["payload_sha256"], receipt=copy.deepcopy(row["receipt"]),
                  requested_at=at(end + 4), responded_at=at(end + 5))
    attach("delivery.idempotency", [value, repeat])
    assert result(attempt, "delivery.idempotency")["status"] == "failed"
    checked = []
    monkeypatch.setattr(campaign, "verify_database", lambda *args: checked.append(args))
    assert result(attempt, "delivery.idempotency", store=object())["status"] == "passed"
    assert len(checked) == 1


def test_missing_originals_path_escape_frozen_release_and_deep_json(attempt):
    root, ledger, _, _, attach = attempt
    attach("correctness.unworn")
    path = root / ledger["checks"]["correctness.unworn"]["evidence"][0]["path"]
    path.write_text("{}")
    assert "checksum" in result(attempt, "correctness.unworn")["reason"]
    ledger["checks"]["correctness.unworn"]["evidence"][0]["path"] = "../release/manifest.json"
    (root / "attempt.json").write_text(json.dumps(ledger))
    assert "escapes" in result(attempt, "correctness.unworn")["reason"]
    (root / "release.json").write_text("{}")
    with pytest.raises(ValueError, match="Frozen release"):
        campaign.assess(root)
    with pytest.raises(ValueError, match="nesting"):
        campaign.decode(("[" * 100 + "0" + "]" * 100).encode())


def installation_exports(manifest, report, *, downgrade=False):
    previous = copy.deepcopy(manifest)
    previous["version_code"] = 3 if downgrade else 1
    previous["apk_sha256"] = "f" * 64
    previous["apk"] = previous["apk_sha256"] + ".apk"
    previous["configurations"]["gpu"]["apk_sha256"] = previous["apk_sha256"]
    prior = copy.deepcopy(report)
    prior["run_id"] = "run-" + "2" * 32
    prior["configuration"] = dict(previous["configurations"]["gpu"], run_id=prior["run_id"])
    build_timeline(prior, start=-200)
    path = "files/runs/" + prior["run_id"] + "/report.json"

    def snapshot(release, seconds):
        return dict(kind="xrbench.installation-snapshot", captured_at=at(seconds), installed=True,
                    package=release["package"], apk_sha256=release["apk_sha256"], version_code=release["version_code"],
                    installation_id=INSTALLATION, connection_id="e" * 24,
                    package_dump=f"Package [{release['package']}]\nversionCode={release['version_code']}",
                    reports=[dict(path=path, sha256="a" * 64)])

    return [export(prior, previous, exported_at=at(1), installations=[snapshot(previous, -.2)]),
            export(report, manifest, installations=[snapshot(manifest, -.1)],
                   browser_events=[event("connected", -.3, 1), event("launch_intent", -.02, 2)])]


@pytest.mark.parametrize("key,action", [
    ("installation.usb_authorization", "usb_authorization"),
    ("installation.existing_app_preserved", "existing_app_preserved"),
    ("installation.same_key_upgrade", "upgrade"),
    ("installation.same_key_downgrade", "downgrade")])
def test_observed_installation_transitions_can_pass(attempt, key, action):
    _, _, manifest, report, attach = attempt
    values = installation_exports(manifest, report, downgrade=action == "downgrade")
    attach(key, values, action=action, seconds=-.31 if action == "usb_authorization" else -.15,
           usb_authorized=True, debugging_authorized=True)
    assert result(attempt, key)["status"] == "passed"


@pytest.mark.parametrize("damage", ["data_lost", "reinstalled", "different_connection", "wrong_version", "no_transition"])
def test_installation_must_preserve_identity_and_original_data(attempt, damage):
    _, _, manifest, report, attach = attempt
    values = installation_exports(manifest, report)
    after = values[1]["validation"]["installations"][0]
    if damage == "data_lost":
        after["reports"][0]["sha256"] = "b" * 64
    elif damage == "reinstalled":
        after["installation_id"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    elif damage == "different_connection":
        after["connection_id"] = "b" * 24
    elif damage == "wrong_version":
        after["package_dump"] = "versionCode=999"
    else:
        values[0]["validation"]["installations"][0]["apk_sha256"] = manifest["apk_sha256"]
    attach("installation.same_key_upgrade", values, action="upgrade", seconds=-.15)
    assert result(attempt, "installation.same_key_upgrade")["status"] == "failed"


def test_update_requires_old_unfinished_release_retired_before_new_launch(attempt):
    _, _, manifest, report, attach = attempt
    values = installation_exports(manifest, report)
    previous = values[0]["reports"][0]
    before = recovery_exports(previous, values[0]["validation"]["release"])[0]
    before["session"]["status"] = "canceled"
    before["validation"]["exported_at"] = at(1)
    before["validation"]["browser_events"] = [
        dict(event("connected", -200.03, 1), run_id=previous["run_id"]),
        dict(event("launch_intent", -200.02, 2), run_id=previous["run_id"]),
        dict(event("update_requested", -.2, 3, next_apk_sha256=manifest["apk_sha256"]), run_id=previous["run_id"]),
        dict(event("retired_for_update", -.1, 4, next_apk_sha256=manifest["apk_sha256"]), run_id=previous["run_id"])]
    values[0] = before
    attach("installation.update_runner", values, seconds=-.15)
    assert result(attempt, "installation.update_runner")["status"] == "passed"
    values[0]["validation"]["browser_events"][-1]["at"] = at(.1)
    attach("installation.update_runner", values, seconds=-.15)
    assert result(attempt, "installation.update_runner")["status"] == "failed"


def test_deadline_checks_real_elapsed_time_and_its_own_stop_source(attempt):
    _, _, manifest, report, attach = attempt
    report = canceled(report, end=1800.05, error="Native benchmark deadline exceeded")
    value = export(report, manifest, diagnostics=[diagnostic(report, manifest, stop=("deadline", 1799.99))])
    attach("service.deadline", value)
    assert result(attempt, "service.deadline")["status"] == "passed"
    next(e for e in value["validation"]["diagnostics"][0]["lifecycle"]["events"]
         if e["event"] == "deadline_reached")["elapsed_ms"] = 1800000 + 60000
    attach("service.deadline", value)
    assert result(attempt, "service.deadline")["status"] == "failed"


def test_real_thermal_transition_requires_work_then_cleanup(attempt):
    _, _, manifest, report, attach = attempt
    report = canceled(report, error="Thermal state severe; measurements stopped")
    report["status"] = report["progress"]["status"] = "failed"
    report["results"][0]["samples"] = report["results"][0]["samples"][:40]
    env = [(-.005, dict(thermal_status=0, display_interactive=False)),
           (.355, dict(thermal_status=3, display_interactive=False))]
    value = export(report, manifest, diagnostics=[diagnostic(report, manifest, environment=env)])
    attach("thermal.severe_stop", value)
    assert result(attempt, "thermal.severe_stop")["status"] == "passed"
    value["validation"]["diagnostics"][0]["lifecycle"]["events"][2]["details"]["thermal_status"] = 3
    attach("thermal.severe_stop", value)
    assert result(attempt, "thermal.severe_stop")["status"] == "failed"


def test_sustained_thermal_counts_measured_calls_across_sequential_runs(attempt):
    _, _, manifest, report, attach = attempt
    first = build_timeline(copy.deepcopy(report), step=4.08, sample_ms=40.75)
    second = copy.deepcopy(first)
    second["run_id"] = "run-" + "2" * 32
    second["configuration"]["run_id"] = second["run_id"]
    start = campaign.instant(first["finished_at"]) - BASE + 1
    build_timeline(second, start=start, step=4.08, sample_ms=40.75)
    attach("thermal.sustained", [export(first, manifest), export(second, manifest)])
    assert result(attempt, "thermal.sustained")["status"] == "passed"
