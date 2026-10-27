# Quest 3 local hardware readiness

This campaign qualifies one Quest 3 installation, firmware/GPU driver, signed
browser release, and local PostgreSQL receiver. It does not qualify a hosted site,
OpenXR frame timing, full xrGPU contract conformance, or a performance improvement.
The separate [matmul qualification campaign](device-onboarding.md#qualify-hardware)
remains independent.

An operator must approve debugging, wear/remove the headset, use the notification,
and reconnect USB. Without that operator, prepare software and leave physical
checks **blocked**. A successful build, fixture upload, or software Vulkan run
cannot close a physical gate. The assessor verifies local consistency and recorded
operator observations; reports are not cryptographic attestations of hardware.
Installation UUIDs and hashes bind records together; they do not authenticate the
headset or make fabricated JSON trustworthy. Preserve original exports and require
the named operator to attest the actual physical actions.

## Prepare and freeze an attempt

Run the software checks in [testing](../../testing.md). Start the local packaged
site with `uv run python main.py`; this prepares the signed runner using the
existing private signing identity and starts PostgreSQL. Finish preparation before
freezing an attempt. Do not rebuild during a campaign.

```sh
uv run python -m engine.headset_validation init \
  --manifest dist/runner/manifest.json \
  --output engine/.state/device-onboarding/quest-3/ATTEMPT_ID
```

Choose a fresh attempt ID. `init` checks the APK bytes and package/signing identity,
copies the release manifest, records Git provenance, and creates `attempt.json`
and `checklist.md` with all 22 physical checks blocked. It refuses an existing
directory, a served destination, or a non-ignored path inside the repository.
Attempt directories have mode 0700; generated evidence metadata has mode 0600.
It also verifies the build's `site-manifest.json` and freezes the actual HTML,
JavaScript and CSS under `site/`, with their identity in `site.json`. Browser assets
use integrity hashes. A website change requires a new attempt even when the APK
is unchanged. Sessions and events retain the website identity that produced them;
downloading an old session from a newer page cannot relabel the original evidence.

Record the operator, host OS and exact Chrome/Edge version, observed firmware
fingerprint, GPU/driver profile, and a private installation reference in
`attempt.json`. The firmware and GPU must match native `device.os` and `device.gpu`.
Copy the export's user-agent string into `host.user_agent`; all qualifying exports
must use the same recorded browser identity.
Set `device.installation_id` to the UUID in the observed native profile. Keep
`installation_reference` as a private human-readable reference. Final-candidate
runs must share that UUID and the same USB connection fingerprint.
Also record API/ABI, charging, thermal and display conditions, cable/USB permissions,
and any OS suspension settings in observations. Do not disable suspension behavior
to manufacture a passing default-policy result. Clearing data, reinstalling,
changing firmware, or changing the runner requires a fresh attempt.

Use `http://localhost:8765`. Keep evidence outside `dist` and served files. The
package is `release.json`'s `package`, including its signing-certificate suffix;
never substitute a fixed package name or the developer runner. Browser WebUSB
owns the connection. Close competing headset tools before testing; do not start a
desktop ADB server while the browser owns USB.

## Collect original evidence

Use **Download results JSON** after each scenario, including failed, canceled,
and partial runs. Keep every original download under the private attempt directory.
Exports retain schema-1 `session`/`reports` and add optional `validation` metadata:

- Frozen release identity and timestamped browser launch, connection, and Stop events
  with durable event IDs and a shared identity for the document lifetime.
- Native `stage_events` for preparation, warmup, execution, readback, and completion.
  Markers include UTC milliseconds, monotonic elapsed time, case, and conditions.
- Terminal service lifecycle history, display/thermal/charging transitions, and
  read-only service/power/thermal/notification OS snapshots obtained through WebUSB.
- Delivery receipts, retry history, and the exact original submitted JSON string,
  retained after PostgreSQL acknowledgment.
- Read-only installation snapshots: observed version/APK, installation UUID,
  connection fingerprint, and original saved-report hashes before/after install.
- Sample UTC/monotonic timestamps, measured warmup work, and device/host clock
  calibration through the same USB connection.

Capture a new download after automatic delivery finishes: an earlier download may
still show a pending receipt. Old exports can be retained, but physical passes require current exports marked
`validation.evidence_version: 2` with the exact local origin and release. Missing
fields must not be added to originals to make old evidence appear current. Diagnostic
errors remain explicit and do not change the native benchmark outcome. OS snapshots
are taken after execution; the service journal records conditions during execution.

Record actual removal time with milliseconds on the same host clock used for the browser. The assessor maps that time through the
captured device clock calibration; it does not assume the two clocks agree. Require it to overlap the requested native stage and a genuine on-to-off display transition followed by native samples while the
display is off. Calibration round trips above 250 ms, missing clock support, or
inconsistent UTC/monotonic histories cannot pass. Retry the capture without editing
timestamps. The read-only device clock command must return epoch milliseconds. If the stage is too brief to
observe, repeat the scenario; do not backdate an observation or add artificial
pauses to the production workload. Rapid display transitions below the service's
one-second environment polling interval may need another run.

For each checklist entry, set `status` to `passed`, `failed`, `blocked`, or
`pending`, record the reason and next action, and attach evidence references:

```json
{
  "status": "passed",
  "reason": "Observed by the headset operator",
  "next_action": "None",
  "observations": [{
    "at": "2026-10-26T20:15:02.125-04:00",
    "operator": "Operator name",
    "action": "headset_removed",
    "observed": "Display switched off and native progress continued",
    "run_id": "run-REPLACE_WITH_ORIGINAL_32_HEX_ID"
  }],
  "evidence": [{"path": "exports/original-download.json", "sha256": "REPLACE_WITH_FILE_SHA256"}]
}
```

Hash original files with `shasum -a 256 PATH`. Evidence paths are relative to the
attempt, and escaping paths are rejected. Never edit a report or original export
to add observations: edit the separate ledger. Attach all relevant before/after
exports and screenshots/logs. These checks are intentionally based on original
native reports, not UI counts. Unsupported operators require reasons and never
count as passes. A supported operator failure fails correctness readiness.

## Execute sequentially

1. **Installation:** observe browser USB and headset authorization. Preserve any
   existing runner/data, exercise same-key upgrade and supported downgrade using
   archived signed artifacts, then Update runner with an unfinished old run.
   Save old/new manifests, hashes, checkpoints, and observations proving preservation
   and one new run identity. Historical releases are installation evidence only;
   correctness reports in the attempt must match the frozen final candidate.
   Manifests alone never establish installation. Attach original before/after
   exports with installation snapshots. Existing saved reports must keep identical
   hashes and the installation UUID must survive the observed APK/version change.
   Same-key tests use action `upgrade` or `downgrade`; the assessor checks observed
   version direction against the two release manifests. Legacy runners without an
   installation UUID leave these gates pending. For authorization, use action
   `usb_authorization` with `usb_authorized: true` and `debugging_authorized: true`
   only after observing both approvals. Update runner requires an old unfinished
   checkpoint, an explicit retirement event, and one later final-candidate launch.
2. **Worn/unworn correctness:** collect one complete worn run and a distinct run
   with the display off throughout measured samples. Both use all 221 operators,
   20 warmups, and 100 samples per supported operator. Require before/after
   correctness, Vulkan with no CPU fallback, explicit stable capability exclusions,
   and matching release/device identity. Attach exactly one final run to each
   correctness check; use action `worn` or `unworn` during that run and record wearing
   separately from the OS interactive state. These conditions require distinct run
   IDs. Conflicting final content under one ID is rejected across checklist entries.
3. **Removal:** collect complete runs proving removal during preparation, warmup,
   and execution. Use `action: headset_removed`; include the exact time/run ID.
   Preserve the native stage markers and service/display transition history.
4. **Recovery:** disconnect/reconnect USB, refresh during execution, and request
   Stop while disconnected. Download a running checkpoint containing samples before
   the interruption and a terminal export after recovery. Record actions `usb_reconnect`, `refresh`, and
   `disconnected_stop` respectively. Recover the same run IDs and durable partial
   evidence. Compare browser launch-intent events before/after; no uncertain launch
   may be replayed. Samples, earlier correctness/provenance, and stage history must
   extend the original checkpoint. A refresh must show a new document identity;
   reconnect/Stop need ordered disconnect and reconnect events. Cancellation retains
   the current case without labeling it passed.
   A charging transition caused by unplugging USB can qualify recovery while its
   performance observations remain invalid. It cannot qualify stable correctness
   or timing gates.
5. **Service:** stop once from the browser and once from the notification. Require
   cancellation, terminal evidence and resource release within 60 seconds of the
   recorded request. Verify the foreground service and notification disappear and
   `xrBench:benchmark` is absent from held wake locks in OS snapshots. Use actions
   `browser_stop`, `notification_stop`, `cleanup`, and `deadline` for their respective
   gates. Notification Stop must have source `notification`; deadline/destruction
   events cannot substitute. Successful dumpsys exit codes alone are insufficient:
   active resources, snapshots taken before cleanup, or unknown dump formats fail. The actual
   30-minute deadline requires a real lifecycle `deadline_reached` event and cleanup.
   Accelerated-clock software tests do not satisfy this gate. If ordinary suites
   finish too soon, leave the deadline observation pending rather than changing
   workload settings or claiming simulated expiry as a physical result.
6. **Thermal:** collect 30 minutes of measured native calls across sequential,
   bounded standard runs, or an earlier genuine severe thermal transition.
   The assessor sums recorded warmup work and timed execution, excludes idle/preparation
   time, and rejects overlapping runs or gaps longer than 60 seconds. Use actions
   `sustained` and `severe_stop` respectively. Empty failed runs and a device already
   severe before execution cannot qualify an in-run thermal transition. Let the device cool between unrelated correctness
   scenarios. A real severe event must stop measurements, preserve partial evidence,
   and release resources. If no severe state occurs, mark `thermal.severe_stop`
   pending; do not force an OS override or heat the device externally.
7. **Delivery:** allow a real complete run to deliver online. For another run,
   make only the local receiver unavailable after the site is loaded; leave the
   PostgreSQL volume intact. Finish the run, record the pending queue, refresh the
   tab, restore the receiver and wait for automatic acknowledgment. Require unchanged
   run ID/body hash and `attempt_failed`, `restored`, `acknowledged` history. Delivery
   backoff for transport outages is bounded at five minutes. Online and offline
   delivery require distinct native runs. Retry history must have ordered timestamps,
   unchanged payload hashes, and a different document ID after refresh. Do not start a new workload to retry delivery.

For the repository's local Compose database, create the outage with
`docker compose --env-file engine/.env -f engine/compose.yaml stop db`. The website
continues serving while report submissions return an error. Restore that same
database and volume with
`docker compose --env-file engine/.env -f engine/compose.yaml up -d --wait db`.
Record both transition times; never remove the volume for this scenario.
Only recorded transport failures or HTTP 502/503/504 followed by restoration can
qualify outage recovery. Authentication errors, rejected evidence, and invalid
HTTP-200 receipts are different failures. Online delivery requires a successful
first attempt.

For idempotency, repeat the exact saved request body and capture a distinct,
timestamped HTTP exchange. A bare copy of the first receipt cannot satisfy this
gate. Record action repeat_submission. The following manual operator command
writes a fresh private exchange record and never starts a native workload:

```sh
uv run python - ORIGINAL_EXPORT.json RUN_ID PRIVATE_EXCHANGE.json <<'PY'
import hashlib, json, os, sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
export_path, run_id, output = sys.argv[1:]
with open(export_path) as stream:
    value = json.load(stream)
row = next(r for r in value["validation"]["deliveries"] if r["run_id"] == run_id)
body = row["body"].encode("utf-8")
endpoint = "http://localhost:8765/api/reports"
record = dict(kind="xrbench.delivery-attempt", endpoint=endpoint,
              payload_sha256=hashlib.sha256(body).hexdigest(),
              requested_at=datetime.now(timezone.utc).isoformat())
request = Request(endpoint, data=body, headers={"Content-Type": "application/json"})
with urlopen(request, timeout=30) as response:
    record.update(status_code=response.status, receipt=json.load(response))
record["responded_at"] = datetime.now(timezone.utc).isoformat()
with open(output, "x") as stream:
    os.fchmod(stream.fileno(), 0o600)
    json.dump(record, stream, indent=2)
PY
```

Attach this exchange and the acknowledged original export to delivery.idempotency.
Both receipts must identify PostgreSQL and the same run, payload hash and stored
report digest. This is an explicit repeat of original physical evidence.

## Assess and hand off

```sh
uv run python -m engine.headset_validation assess \
  engine/.state/device-onboarding/quest-3/ATTEMPT_ID \
  --check-postgres \
  --output engine/.state/device-onboarding/quest-3/ATTEMPT_ID/assessment-01.json
```

Assessment only reads PostgreSQL through the existing connection configuration.
It uses one repeatable-read, read-only transaction to check stored native JSON,
the canonical report digest, indexed run metadata, every result JSON, and all
indexed result columns. Matching JSON alone is insufficient.
PostgreSQL JSONB preserves JSON values, not the original HTTP serialization: exact
request bytes are checked separately against `payload_sha256`. Omitting the database
check cannot pass delivery gates. Assessment does not launch, upload, migrate, or
modify evidence. Output filenames must be fresh. Exit codes: 0 ready, 1 incomplete
or failed gates, 2 invalid campaign input. A database connection failure is a failed
delivery check; correct the prerequisite and reassess without rerunning work.

Use the exact scenario action names above; the operator name on observations must
match the attempt operator. For installation preservation use existing_app_preserved;
for Update runner use update_runner. The claimed action must be bracketed by its
captured before/after states. Generic notes cannot substitute for scenario evidence.

Completion requires every physical check to pass. Keep unsupported operators as
named exclusions and retain every pending/blocked prerequisite. Update
[testing](../../testing.md) with the scoped outcome, commands and results, date,
release identities, sanitized evidence summary, and exact remaining actions.
Private evidence, serials, installation references, and OS snapshots stay local.
