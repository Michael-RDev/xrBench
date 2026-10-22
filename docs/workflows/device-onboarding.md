# Device onboarding workflow for agents

Workflow version: 1.0. Source contracts reviewed: 2026-10-21.

Use this runbook to onboard one device model/revision to the benchmark website.
Completion requires accurate recognition, device-specific artwork, a passing
public website hardware track, and a passing scoped qualification track. A catalog
entry or successful demo alone is not completed onboarding.

Example invocation:

> Onboard DEVICE MODEL / REVISION to xrBench using
> docs/workflows/device-onboarding.md. The runner executes in EXECUTION TARGET.
> Hardware availability is AVAILABLE / UNAVAILABLE, and the intended release is
> RELEASE / TEST URL. Complete independent work, record blocked gates, and return
> the evidence record and handoff. Do not publish a release.

Commands below run from the repository root. Variable-based examples use
PowerShell; replace the uppercase placeholders before running them. Execute
hardware stages individually, inspecting each result before continuing.

## 1. Establish the starting point

- [ ] Read applicable agent instructions, inspect `git status --short`, and record
  the commit plus relevant uncommitted changes. Preserve others' work; isolate
  this task's edits and distinguish existing failures from new regressions.
- [ ] Record the exact model/revision, catalog ID if present, hardware access,
  owner/operator availability, intended release/URL, and actual execution target:
  `headset`, `glasses`, `phone`, `compute_puck`, or `unknown`.
- [ ] Identify available host/browser, Android toolchain, signed browser release,
  developer build, and report receiver. Ask only for missing facts that cannot be
  found locally and materially block a stage.
- [ ] Create a private evidence record from the template below. Keep raw evidence
  under `engine/.state/device-onboarding/<device-id>/<attempt-id>/`, outside the
  served site. Use a fresh attempt directory when the build or device identity
  changes. Confirm generated files are ignored before staging any changes.
- [ ] Recheck the source contracts below. If code has evolved, follow its current
  behavior and update this workflow rather than applying stale commands.

| Concern | Sources of truth |
| --- | --- |
| Identity and catalog | [catalog](../../engine/device-catalog.json), [Python identity](../../engine/devices.py), [browser identity](../../src/bench_web/web/browser/evidence.mjs) |
| Artwork and presentation | [renderer](../../src/bench_web/web/ascii-headset.mjs), [application](../../src/bench_web/web/app.js), [demo](../../src/bench_web/web/smoke-demo.mjs) |
| Browser packaging, delivery, recovery | [web guide](../../src/bench_web/README.md), [release workflow](../../.github/workflows/web-distribution.yml) |
| Developer setup and capabilities | [engine guide](../../engine/README.md), [native setup](../../layers/README.md), [portability guide](../PORTABILITY.md) |
| Measurement and qualification | [measurement definitions](../../engine/MEASUREMENTS.md), [qualification CLI/assessor](../../engine/qualification.py), [profile identity](../../engine/profiles.py) |

Missing hardware blocks physical checks, not research, artwork, catalog changes,
or host tests. Missing signing configuration blocks release packaging. Unsupported
platforms require a recorded blocker; a new backend, transport, or platform port
is a separate project. Do not provision hosting/storage or publish as part of this
workflow unless the task explicitly includes it.

## 2. Research identity and capabilities

- [ ] Find authoritative manufacturer product specifications, processor
  documentation, and product images. Record each URL, access date, supported
  claim, and unresolved uncertainty. Do not copy an unsupported claim from another
  catalog entry or a search snippet.
- [ ] Collect observed model, product, device, SoC model/manufacturer, Android/API,
  ABI, firmware/build, and native GPU/driver properties when hardware is available.
  Use the existing discovery/profile paths; retain original observations locally.
  Marketing names and unobserved aliases remain sourced inferences.
- [ ] Distinguish primary compute from spatial/display processors and companion
  compute. A benchmark executing on a phone is a phone result, even when glasses
  are attached. The form-factor declaration is not measured hardware identity.
- [ ] Check current transport/backend requirements: discovery requires Android
  API 29+ and `arm64-v8a`; a native probe establishes backend compatibility.
  Compatible discovery is not proof that all operators or timestamps work.
- [ ] Preserve `unknown` or conflicting identity instead of guessing a processor
  generation. A failed Vulkan probe never proves GPU absence and never authorizes
  automatic CPU fallback. Follow the existing sourced GPU-absence policy for any
  explicit Advanced CPU path; it cannot substitute for this GPU qualification.

## 3. Integrate recognition

- [ ] Reuse existing chip definitions when accurate. Add a stable device ID,
  canonical name, sourced aliases, chip locations/roles, visual key, and source
  using the current catalog structure. Keep the primary component first because
  identity resolution currently uses that ordering.
- [ ] Check aliases across the entire catalog after normalization (case,
  underscores, and whitespace). Avoid substring matching and aliases broad enough
  to capture other models. Add only sourced or observed identifiers.
- [ ] Use the existing `primary_chip_options` mechanism for ambiguous generations;
  do not choose a generation from the product name alone. Check both an absent SoC
  report and an explicit reported variant. A conflicting SoC must retain the
  existing conflict/unknown behavior.
- [ ] Verify Python and browser identity parity for `device_id`, `chip_id`,
  `identity_source`, `execution_location`, and `visual_model`. Verify demo labels,
  catalog listings, and chip roles without changing simulated timings into
  supposed device measurements.
- [ ] Extend regression coverage for valid aliases, near-miss names, unknown
  devices, conflicting/ambiguous identity, each supported variant, and companion
  execution. Useful starting points are the [cross-language tests](../../engine/tests/test_web_distribution.py),
  [browser evidence tests](../../src/bench_web/tests/test_browser_evidence.mjs),
  [device view tests](../../src/bench_web/tests/test_device.mjs), and
  [demo tests](../../src/bench_web/tests/test_demo.mjs).

Catalog recognition remains descriptive. Preserve runtime support statuses and
report schemas; do not hardcode `validated` or a product-name compatibility bypass.

## 4. Create device-specific artwork

At this workflow's initial source review, catalog entries expose `visual` and
native identities expose `visual_model`, but the homepage calls a shared renderer
without passing a model selector. `demoIdentity()` also lacks that visual field.
Adding a catalog key alone therefore does not create device-specific artwork.

- [ ] Inspect the current implementation, then add or extend model selection so
  the catalog visual key reaches live, demo, and restored-run presentation.
  Maintain a model-to-geometry mapping in the existing code-generated renderer;
  preserve the existing presentation API's callers when adding the selector.
- [ ] Build a recognizable silhouette and distinguishing features from sourced
  product references, retaining the website's current rendering style and palette.
  Show the actual executing form factor; do not depict companion processing as a
  chip inside attached glasses. Do not imply that the illustrative chip is a
  measured die layout or utilization display.
- [ ] Preserve an honest schematic for unknown/conflicting identities and safe
  rendering failure. A generic illustration is a fallback, not a passed artwork
  gate for the new device.
- [ ] Check selection changes between devices, demo entry/exit, live-device
  takeover, chip reveal, return to device, refresh/restored sessions, desktop and
  narrow screens, reduced motion, and renderer failure. Model changes must not
  reuse stale geometry or repeatedly replay the reveal during progress updates.
- [ ] Preserve readable Run/Stop and evidence controls, animation limits, and
  offscreen/hidden-tab pausing. Extend [renderer tests](../../src/bench_web/tests/test_headset.mjs)
  and [workflow tests](../../src/bench_web/tests/test_workflow.mjs); capture desktop,
  narrow-screen, and chip-reveal screenshots for the evidence record.

## 5. Verify software and the public website on hardware

### Host checks and packaging

Run the existing regression commands after the relevant changes, with dependencies
prepared according to the linked setup guides. Record failures and their origin.

```powershell
npm test
uv run --group dev python -m pytest -q
```

The signed browser runner is a prerequisite for packaging. Follow the
[build/sign instructions](../../src/bench_web/README.md#build-and-sign-maintainers-only)
for pinned tools, exports, persistent signing credentials, and version code. Never
substitute a synthetic APK, random key, or developer APK for the public runner.
On a configured maintainer machine, the packaging sequence is:

```powershell
uv run --extra export --group dev python -m engine.build --web
npm run build
node --experimental-vm-modules scripts/verify-web.mjs dist
```

Stop and record a blocker if a prerequisite or command fails. Record the exact
manifest, APK SHA-256, signing certificate digest, version, suite/runner/runtime
revisions, website artifact identity, and test origin. Catalog updates must be in
the packaged manifest, not only in the source checkout.

For a local preview of that packaged release:

```powershell
uv run python -m src --no-open
```

This serves localhost port 8765 and captures reports outside the site without
desktop ADB/PostgreSQL. It is useful preliminary evidence. To pass the public
website gate, exercise the intended HTTPS release/test origin and its receiver;
localhost-only observations do not establish public HTTPS or clean-host behavior.
Use an existing authorized test deployment; record unavailable hosting as blocked
instead of provisioning or publishing without that task scope.

### Physical browser checklist

- [ ] Use the actual signed `org.xrgpu.benchmarks.web` runner from the matched
  distribution in desktop Chrome or Edge. Record host OS/browser, drivers/USB
  permissions, headset firmware, and whether the computer was clean or previously
  configured. Do not generalize beyond the environment tested.
- [ ] Enable developer mode, use a data cable, grant browser USB access, approve
  debugging on the device, and keep it awake with the runner visible. Record
  thermal and charging conditions. Do not compete with another tab or desktop ADB
  owner for the same device; coordinate release of the interface rather than
  killing other applications or uninstalling incompatible packages.
- [ ] Confirm recognition, actual execution target, and device-specific artwork.
  Probe compatibility independently of the catalog match. Keep unsupported
  capability reports and reasons; do not bypass native guards.
- [ ] Run the standard 63 GPU cases using `executorch_vulkan`, isolated mode,
  Standard preset, 20 warmups, 100 measured samples per successful case, automatic
  calibration, and trace profiling. Verify the recorded configuration, not just
  visible progress or a terminal status.
- [ ] Require all 63 expected cases with successful correctness and valid measured
  samples for the full-suite gate. Record failed, skipped, interrupted, or invalid
  rows as failures, preserving raw evidence. Missing GPU timing stays unavailable,
  not zero. A completed run with failed cases is not a passed full-suite gate.
- [ ] Exercise first installation where available, repeat-run APK reuse, and
  update behavior with an available valid earlier release. Record missing update
  prerequisites rather than inventing evidence. Check permission denial/retry,
  multiple-device selection when available, and exclusive ownership/blocked USB
  recovery. Unexercised required checks remain pending or blocked.
- [ ] Check Stop during preparation and execution, refresh while running, USB
  loss/reconnect, and cancellation while disconnected. Recover the same run ID;
  never repeat an uncertain launch. Retain terminal failed/canceled reports and
  partial evidence from process death without calling it a completed measurement.
- [ ] Download the session export. It currently has `schema_version: 1`, `session`,
  and `reports`; each entry in `reports` is an original schema-2 native report.
  Preserve the export and native report values unchanged. Compare browser results
  with Python `engine.reports.validate` / `summarize` using copies of those native
  reports, not the enclosing export. No new report schema is needed.
- [ ] Verify automatic delivery and recovery from receiver failure. The receipt
  must have `stored: true`, the same `run_id`, and `payload_sha256` matching the
  exact submitted bytes. A queued retry or HTTP success alone is not confirmation.
  Check that delivery failure neither relaunches nor blocks benchmark controls.
  Keep deliberate failure/retry tests within the authorized test environment.

The local receiver stores reports under `engine/.state/submitted-reports/` by
default. Static-only hosting cannot satisfy receipt-confirmed storage. Record
delivery limitations separately from native execution. The demo must remain
explicitly simulated and must never enter the report queue or hardware evidence.

## 6. Complete scoped hardware qualification

This is a separate track using the developer package `org.xrgpu.benchmarks` and
the existing [hardware qualification procedure](../PORTABILITY.md#hardware-proof-using-the-owners-own-computer).
Prepare native tools/exports and the developer build per that guide, then release
the browser USB connection before desktop ADB takes ownership.

Keep every stage on the same installation, firmware/driver, build/APK, backend,
workload case set, candidate revision, and execution target. A change requires a
new evidence set. Do not mix public browser reports into this assessment or claim
that one runner's qualification establishes the other's build behavior.

Set these variables for one attempt. Keep the serial private. Set the actual
target rather than retaining `headset` for a phone or compute puck.

```powershell
$deviceSerial = 'DEVICE_SERIAL'
$deviceTarget = 'headset'
$qualificationDir = 'engine/.state/device-onboarding/DEVICE_ID/ATTEMPT_ID/qualification'
$qualificationArgs = @('--serial', $deviceSerial, '--target', $deviceTarget, '--backend', 'executorch_vulkan', '--suite', 'development', '--timeout', '1800', '--output', $qualificationDir)
```

If ADB is not discoverable, append `--adb` and its absolute executable path to
`$qualificationArgs`; also pass it to recovery. These stages use Standard settings
and the development suite's 14 FP32 matmul cases, including multiple fixture seeds.
Non-stock stages use ten balanced randomized pairs per case. Inspect the final
report and record its run ID after **each** command; do not execute the stages as
an unattended batch. A zero exit code only means collection finished, not that
the qualification gate passed.

Stock correctness:

```powershell
uv run python -m engine.qualification run @qualificationArgs --stage stock --profiling trace
```

Stock/stock noise, with and without tracing (separate runs):

```powershell
uv run python -m engine.qualification run @qualificationArgs --stage noise --profiling trace
```

```powershell
uv run python -m engine.qualification run @qualificationArgs --stage noise --profiling latency
```

Deliberately incorrect output must fail correctness for every case and receive no
performance claim. This expected failure is a passing negative-control gate:

```powershell
uv run python -m engine.qualification run @qualificationArgs --stage incorrect --profiling trace
```

The deliberately slower control must remain correct and show slowdown beyond 5%
under the assessor's paired interval rule:

```powershell
uv run python -m engine.qualification run @qualificationArgs --stage slower --profiling latency
```

Candidate trace must preserve correctness and dispatch/shader provenance:

```powershell
uv run python -m engine.qualification run @qualificationArgs --stage candidate --profiling trace
```

Candidate latency requires three distinct app runs with stable conditions.
Run this command three times, checking each final report and allowing conditions
to stabilize between runs; record three different run IDs:

```powershell
uv run python -m engine.qualification run @qualificationArgs --stage candidate --profiling latency
```

If disconnected or timed out, retrieve the original run ID from
`engine/.state/qualification-jobs/`. Recover before another launch, reinstall, or
data reset. This command retrieves evidence without starting a new benchmark:

```powershell
uv run python -m engine.qualification recover --serial $deviceSerial --run-id 'run-RECORDED_ID' --output $qualificationDir
```

A partial report keeps recovery pending. Preserve it and retry recovery after the
native run stops; do not relabel partial output or abandon an uncertain run to
launch another. The CLI attempts cancellation on interruption but still requires
recovery of the final report.

Assess the nine original final report files explicitly. Replace each `run-..._ID`
filename stem below with its recorded full `run-<32 hex characters>` ID. Do not use directory globs:
profiles, partial reports, analyses, and unrelated attempts are not assessor input.

```powershell
$qualificationReports = @(
    "$qualificationDir/run-STOCK_ID.json"
    "$qualificationDir/run-NOISE_TRACE_ID.json"
    "$qualificationDir/run-NOISE_LATENCY_ID.json"
    "$qualificationDir/run-INCORRECT_ID.json"
    "$qualificationDir/run-SLOWER_ID.json"
    "$qualificationDir/run-CANDIDATE_TRACE_ID.json"
    "$qualificationDir/run-CANDIDATE_LATENCY_1_ID.json"
    "$qualificationDir/run-CANDIDATE_LATENCY_2_ID.json"
    "$qualificationDir/run-CANDIDATE_LATENCY_3_ID.json"
)
uv run python -m engine.qualification assess @qualificationReports --output "$qualificationDir/assessment.json"
```

Read the output, not just the process exit status. Require `status: "validated"`
and no missing gates. Keep the assessor's scope, report digests, and all failures.
Missing trace evidence or incompatible/mixed identities cannot be waived by host
tests. Validation applies only to the recorded FP32 matmul cases and device/build;
it is not hardware attestation, full-suite validation, or application speedup.
`reproduced_improvements` may be empty without failing qualification.

## 7. Record evidence and hand off

Use `passed`, `failed`, `blocked`, or `pending` for each checklist gate. `passed`
requires evidence; `failed` means an executed check did not meet its criteria;
`blocked` names a missing prerequisite and the action needed; `pending` means not
yet performed. These are documentation statuses, not new runtime fields.

Mark full onboarding complete only when recognition, artwork, software checks,
public website checks, and scoped qualification all pass. Otherwise hand off the
completed work and exact remaining actions without a blanket support claim. A
known failure remains a failed gate even if it predates onboarding. Unsupported
platforms can receive catalog/artwork improvements while hardware gates stay
blocked. Multi-device or update checks needing unavailable equipment/releases
must be listed, not silently marked passed.

Keep raw reports, private serials, installation identifiers, and credentials out
of Git and public artifacts. Preserve originals unchanged locally; share a
sanitized summary when needed. Record file-byte SHA-256, canonical report digest
(`engine.reports.digest`), and receipt `payload_sha256` with their distinct labels;
reformatting JSON changes byte hashes without necessarily changing its canonical
digest. Reports from browser and qualification runners belong in separate folders.

### Copyable evidence record

```markdown
# Device onboarding evidence
- Workflow version: 1.0
- Device model / hardware revision / catalog ID:
- Actual execution target / basis (observed or declared):
- Date / operator / hardware availability:
- Repository commit / relevant uncommitted changes:
- Intended release / HTTPS test URL:
- Private evidence root (no serials or credentials in shared copies):

## Identity and sources
| Claim / alias / variant | Source URL or local observation | Access/observation date | Confirmed / inferred / unresolved |
| --- | --- | --- | --- |
| | | | |
- Model / product / device / SoC observations:
- Component roles and physical locations:
- Native capability profile path / backend outcome / missing capabilities:
- Remaining conflicts or unknowns:

## Environment and separate build identities
- Host OS / browser / USB drivers / clean or configured host:
- Android API / ABI / firmware / OS build / GPU / driver:
- Thermal / charging / foreground conditions:
- Browser: package / version / APK SHA-256 / certificate digest / manifest path:
- Browser: suite / runner / runtime revisions / website artifact identity:
- Qualification: package / APK SHA-256 / suite / runner / runtime / candidate revisions:
- Qualification: private profile identity reference / backend / cases / target:

## Gate ledger
| Gate | Status | Evidence / result | Blocker or failure / exact next action |
| --- | --- | --- | --- |
| Identity research and unresolved variants | pending | | |
| Catalog and Python/browser parity | pending | | |
| Device-specific artwork and UI states | pending | | |
| Software regressions | pending | | |
| Matched signed distribution and intended HTTPS origin | pending | | |
| Full 63-case native browser run | pending | | |
| First install, reuse, update | pending | | |
| Permissions, multiple devices, exclusive USB ownership | pending | | |
| Stop, refresh, disconnect, recovery, disconnected cancellation | pending | | |
| Export, Python/browser agreement, delivery and retry receipts | pending | | |
| Scoped qualification assessment | pending | | |

## Evidence inventory (add rows for every run/check)
| Track / stage | Session or run ID | Original artifact path | File SHA-256 / canonical digest | Outcome |
| --- | --- | --- | --- | --- |
| Browser full suite | | | | |
| Browser recovery / canceled / failed runs | | | | |
| Qualification stock | | | | |
| Qualification noise trace / latency (one row each) | | | | |
| Qualification incorrect / slower (one row each) | | | | |
| Qualification candidate trace | | | | |
| Qualification candidate latency 1 / 2 / 3 (one row each) | | | | |
- Receipts: endpoint, run_id, stored, payload_sha256, local receipt path:
- Artwork: reference URLs, desktop/narrow/reveal screenshots, fallback check:
- Tests: commands, environment, results, logs, known pre-existing failures:
- Assessment: path, status, scope, missing gates, report_sha256:
- Completion: complete / incomplete; remaining limitations:
```

### Copyable handoff

```markdown
Onboarded device/revision: ...
Outcome: complete / incomplete, with the evidence-supported scope stated plainly.
Changed files and behavior: ...
Identity/source references and unresolved variants: ...
Artwork screenshots and UI checks: ...
Software tests and packaging checks: ...
Public browser evidence: release/origin, run IDs, reports/digests, receipts, failures.
Scoped qualification: developer build, assessment path/status/scope, missing gates.
Private evidence location and sanitized summary: ...
Outstanding gates: status, exact next command/action, required hardware/person/input.
Reviewable changes: local diff or existing authorized PR; deployment status: ...
```

Finish with reviewable changes and evidence. Publishing, new platform backends, and
storage-service provisioning require their own task scope. Never weaken
correctness, discard failing cases, or invent measurements to finish onboarding.
