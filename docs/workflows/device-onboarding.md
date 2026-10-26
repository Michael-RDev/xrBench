# Devices and qualification

[Documentation](../README.md) · [Development](../../engine/README.md) · [Measurements](../../engine/MEASUREMENTS.md)

- [Requirements](#requirements)
- [Backends](#execution-backends)
- [Qualification](#qualify-hardware)
- [Onboarding checklist](#onboard-a-device)

## Requirements

| Test | Requirements |
| --- | --- |
| Device inspection | Android 10+, arm64, USB debugging, and an authorized connection |
| Vulkan compute | Vulkan 1.1, a compute queue, and the selected backend's format/buffer capabilities |
| XR frame impact | Quest 3, stereo rendering, 1440×1440 per eye, and 72 Hz |

In Stats for Nerds, use **Inspect capabilities**, then **Download device profile**.
This installs the runner without starting workloads, calibration, or XR. Profiles
omit USB serials but don't authenticate hardware. Declare where processing runs;
companion-phone results belong to the phone. Catalog recognition isn't qualification.

`probe_required` needs a GPU probe; `unsupported` fails backend requirements;
`compatible_unvalidated` passes capabilities; `validated` applies only to the
assessed installation, build, backend, cases, and timing scopes.

Multiple Vulkan physical devices fail the probe. Missing timestamps allow latency
tests but block trace qualification. Revalidate after driver/build changes.
Isolated compute doesn't need OpenXR; Metal/proprietary accelerators aren't implemented.

## Execution backends

`executorch_vulkan` and `executorch_cpu` run the layer suites. `vulkan_resident` and
`cpu_resident` run 2D FP32 matmul with persistent buffers: isolated Smoke/Standard,
64 MiB maximum. CPU runs don't initialize Vulkan; GPU failures don't fall back to CPU.
See [native setup](../../engine/README.md#build-the-native-app) and
[timing scopes](../../engine/MEASUREMENTS.md#portable-execution-and-resident-buffers).

<a id="hardware-proof-using-the-owners-own-computer"></a>

## Qualify hardware

Use the developer runner on the owner's computer. Keep one installation,
firmware/driver, APK, backend, case list, candidate revision, and execution target
per attempt. Changes require fresh evidence. Clearing app data/reinstalling changes
installation identity. Full inference packages use [a separate workflow](../model-qualification.md).

Run one stage at a time with the device awake and thermally stable; inspect each
final report before continuing. Defaults: 14 Development FP32 matmul cases,
three fixture seeds, Standard, and a 30-minute deadline.

```sh
uv run python -m engine.qualification run \
  --serial DEVICE_SERIAL --target headset --backend executorch_vulkan \
  --stage stock --profiling trace --output engine/.state/hardware-evidence/quest
```

Replace the serial/target. Repeat with these combinations, saving every run ID:

| Stage | Profiling / runs | Pass condition |
| --- | --- | --- |
| `stock` | `trace`, 1 | Correct before/after each case; 100 measured samples |
| `noise` | `trace` and `latency`, 1 each | Complete stock/stock noise checks |
| `incorrect` | `trace`, 1 | Every case fails correctness; no performance claim |
| `slower` | `latency`, 1 | Correct output; paired interval shows slowdown beyond 5% |
| `candidate` | `trace`, 1 | Correct output and verified dispatch/shader provenance |
| `candidate` | `latency`, 3 | Three distinct final runs with stable conditions |

Non-stock stages use ten balanced randomized pairs. Wrong-output controls use
distinct shader names/hashes or a resident push constant; slower controls repeat
work eight times. Controls cannot earn improvement labels.

### Recover an interrupted run

Recover before relaunching, reinstalling, or clearing data. Use the full ID from
`engine/.state/qualification-jobs/`:

```sh
uv run python -m engine.qualification recover \
  --serial DEVICE_SERIAL --run-id run-RECORDED_ID \
  --output engine/.state/hardware-evidence/quest
```

Keep partials; the device stays reserved until the final report is recovered.
Both commands accept `--adb /absolute/path/to/adb`.

### Assess the reports

List nine original final reports explicitly—no profiles, partials, analyses, other
attempts, or directory globs:

```sh
uv run python -m engine.qualification assess \
  run-STOCK_ID.json run-NOISE_TRACE_ID.json run-NOISE_LATENCY_ID.json \
  run-INCORRECT_ID.json run-SLOWER_ID.json run-CANDIDATE_TRACE_ID.json \
  run-CANDIDATE_LATENCY_1_ID.json run-CANDIDATE_LATENCY_2_ID.json \
  run-CANDIDATE_LATENCY_3_ID.json --output engine/.state/qualification.json
```

Require `status: "validated"` and no missing gates. Originals/digests are preserved;
`reproduced_improvements` may be empty. Repeat on a second compatible device and
assess separately. Keep evidence private unless its owner shares it.

Qualification covers these FP32 matmul cases and this device/build, not full-suite
support or application speedup. Host tests/demos cannot replace hardware evidence.
See [recorded results and failures](../../src/bench_web/README.md#verification-and-release-qualification).

## Onboard a device

Workflow 1.0: complete all seven steps for one model/revision. Run commands from
the repository root. Save private evidence under
`engine/.state/device-onboarding/<device-id>/<attempt-id>/`, outside Git and served files.
Start a new attempt after build or identity changes.

### 1. Establish the starting point

- [ ] Follow agent instructions; record commit, local changes, and existing failures.
- [ ] Record model/revision, catalog ID, operator/hardware, release/URL, and target:
  `headset`, `glasses`, `phone`, `compute_puck`, or `unknown`.
- [ ] Check host/browser, toolchain, signed release, developer build, and receiver.
- [ ] Start an evidence record; confirm reports are ignored by Git.
- [ ] Check commands against current source; preserve others' work.

Continue independent work without hardware; mark physical checks blocked. Packaging
needs signing credentials. Platform ports, provisioning, and publishing need explicit scope.

### 2. Research identity and capabilities

- [ ] Use original manufacturer/processor sources and images; record URLs, dates,
  claims, and uncertainty.
- [ ] Collect model/product/device, SoC, Android/API, ABI, firmware/build, GPU/driver.
  Keep originals; label unobserved aliases/marketing names as inferred.
- [ ] Separate primary, spatial/display, and companion chips; distinguish declared form factor.
- [ ] Verify API 29+, `arm64-v8a`, backend/operator capabilities, and timestamps.
- [ ] Leave conflicts unresolved. Vulkan failure doesn't prove GPU absence;
  Advanced CPU cannot substitute for GPU qualification.

### 3. Integrate recognition

- [ ] Reuse accurate chips; add stable ID, name, sourced aliases, roles/locations,
  visual key, and source. Put the primary chip first.
- [ ] Normalize case, underscores, and whitespace; avoid broad/substring aliases.
- [ ] Use `primary_chip_options` for ambiguity; test missing SoC, variants, and conflicts.
- [ ] Match Python/browser `device_id`, `chip_id`, `identity_source`,
  `execution_location`, and `visual_model`; check chip roles and label demo timings simulated.
- [ ] Test aliases, near misses, unknowns/conflicts, variants, and companion execution.

Update the [catalog](../../engine/device-catalog.json), [Python identity](../../engine/devices.py),
and [browser identity](../../src/bench_web/web/browser/evidence.mjs).
Preserve schemas/statuses; never hardcode `validated` or bypass capability checks.

### 4. Create device-specific artwork

- [ ] Map `visual`/`visual_model` to [device geometry](../../src/bench_web/web/ascii-headset.mjs)
  in live/demo/restored views; preserve presentation callers.
- [ ] Use sourced references and existing style; show processing location, not a measured die map.
- [ ] Keep generic fallbacks for conflicts/failures; they don't count as device-specific artwork.
- [ ] Check switching, demo entry/exit, live takeover, reveal/return, recovery,
  desktop/phone, reduced motion, and failures; prevent stale/repeated reveals.
- [ ] Preserve readable controls/results, animation limits, and hidden/offscreen pausing.
  Extend tests; save desktop, phone, and reveal screenshots.

### 5. Verify software and the public website on hardware

Prepare [native builds](../../engine/README.md#build-the-native-app) and
[signing](../../src/bench_web/README.md#build-and-sign-maintainers-only):

```sh
npm test
uv run --group dev python -m pytest -q
uv run --extra export --group dev python -m engine.build --web
npm run build
node --experimental-vm-modules scripts/verify-web.mjs dist
```

Stop on failure. Use the real signed runner, not fixtures, random signing keys,
or the developer APK. Verify packaged catalog, build IDs, hashes, certificate, and version.
Local preview isn't the public website check: use the intended authorized HTTPS
origin and receiver, or mark hosting blocked.

- [ ] Use matching `org.xrgpu.benchmarks.web` in Chrome/Edge; record host/browser,
  drivers/permissions, firmware, and clean/configured-host status.
- [ ] Authorize USB/debugging; keep the runner awake/visible and record thermal/charging
  conditions. Release USB owners without killing apps or uninstalling packages.
- [ ] Check recognition, target, artwork, and independent capabilities; don't bypass guards.
- [ ] Run 63 GPU cases: `executorch_vulkan`, isolated, Standard, 20 warmups,
  100 samples per successful case, automatic calibration, trace. Verify report settings.
- [ ] Require all 63 correct with valid samples. Failed, skipped, interrupted, or
  invalid cases fail the suite; missing timing isn't zero.
- [ ] Test install/reuse/update, denied permissions/retry, multiple devices,
  exclusive ownership, and busy-USB recovery. Mark untested checks pending/blocked.
- [ ] Test Stop during preparation/execution, refresh, disconnect/reconnect, and
  disconnected cancellation. Recover the same ID; never repeat an uncertain launch.
  Keep failed, canceled, and partial reports.
- [ ] Export schema-1 `session`/`reports`; validate copies of original schema-2 reports
  with `engine.reports.validate`/`summarize`. Don't alter originals or validate the wrapper.
- [ ] Test delivery/failure recovery against the authorized receiver. Require
  `stored: true`, matching `run_id`, and exact-byte `payload_sha256`.
  Failures must not rerun work or block controls.

### 6. Complete scoped hardware qualification

Release browser USB. Use developer package `org.xrgpu.benchmarks` for all
[nine qualification runs](#qualify-hardware) and assessment. Don't mix browser reports
into this evidence; require `validated` with no missing gates.

### 7. Record evidence and hand off

Mark each check `passed` (with evidence), `failed`, `blocked` (missing prerequisite),
or `pending` (untested). Completion requires every gate to pass, including pre-existing
failures. Keep demos out of evidence and uploads. Storage receipts don't prove execution.

Preserve private originals; separate browser and qualification files. Keep serials,
installation IDs, and credentials outside Git. File-byte SHA-256, canonical
`engine.reports.digest`, and receipt `payload_sha256` are different hashes.

Record these fields and hand off exact remaining actions:

```text
Device: model/revision/catalog ID; target and observed/declared basis; date/operator/hardware.
Sources: claims/aliases/variants, URLs or observations, dates, confirmed/inferred/unresolved.
Identity: model/product/device/SoC, component roles/locations, private profile/installation reference.
Host: OS/browser/USB drivers, clean/configured. Device: API/ABI/firmware/OS build/GPU/driver.
Conditions: thermal/charging/foreground. Capability profile: path/backend/missing capabilities.
Builds: commit/local changes; release/HTTPS origin/private evidence directory.
Browser: package/version/APK SHA-256/certificate/manifest/site artifact/suite-runner-runtime revisions.
Qualification: package/APK SHA-256/suite-runner-runtime-candidate revisions/backend/cases/target.
Each check: status, evidence/failure, next action. Each run: track/stage/session/run ID,
  original path, byte SHA-256, canonical digest, outcome (including partial/failed/canceled).
Receipts: endpoint/run_id/stored/payload_sha256/path. Tests: commands/environment/results/logs.
Artwork: sources and desktop/phone/reveal/fallback screenshots. Record pre-existing failures.
Assessment: path/status/scope/missing gates/report_sha256.
Handoff: completion/scope, changes/review location, sanitized summary, limitations,
  remaining actions/prerequisites, and deployment status.
```
