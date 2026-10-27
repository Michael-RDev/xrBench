# Testing

## Browser USB and native operator runner

The default local launch is `uv run python main.py`. It starts PostgreSQL and
serves the browser USB website at `http://localhost:8765/`; device commands run
through browser ADB. The separate developer dashboard is not needed for Run.

The public runner embeds all 315 verified SPIR-V variants and both Android ARM64
native operator libraries. Its typed suite covers 221 operator identities with
one independent reference fixture per identity. It preserves optional inputs,
multiple outputs, logical/storage types, frozen contract identities and shader
hashes. This does not replace xrGPU's full contract conformance campaign.

Run software regressions with `npm test` and `uv run --group dev python -m pytest
-q`. With the existing local PostgreSQL running, use
`XRGPU_TEST_POSTGRES=1 uv run --group dev python -m pytest -q
engine/tests/test_postgres.py engine/tests/test_native_public.py`. Database tests
create disposable databases and preserve the application database. After release
preparation, run `node --experimental-vm-modules scripts/verify-web.mjs dist` to
exercise the actual bundled website, USB chooser cancellation and report delivery.
Its synthetic reports are protocol fixtures, never device evidence.

Required software checks:

- A previous signing identity cannot block installation. Same-key upgrades and
  supported version downgrades preserve app data; no automatic uninstall occurs.
- Loading a newer site does not replay an unfinished old launch. Update runner
  captures its checkpoint, stops its helper, and starts a distinct new run.
- APK, native package, suite, contract, tensor and shader identities must match.
  Corruption, unsafe paths, CPU fallback and software-runtime reports are rejected.
- All 221 operators appear in completed reports. Unsupported capabilities retain
  explicit reasons and do not count as passing performance observations.
- Preparation, native execution and readback have separate costs. Environment
  polling, correctness comparisons and report writes stay outside execution timing.
  Initial input transfer is included in `prepare_ms` by the current xrGPU API;
  `input_transfer_ms` stays null with an explicit scope rather than an estimate.
- PostgreSQL commits before acknowledging delivery. Retries are idempotent; changed
  evidence under the same run ID conflicts. An outage or file-only receipt leaves
  the exact report queued in IndexedDB.
- Schema migrations preserve historical schema-2 model reports while accepting
  schema-3 typed operator results. No xrGPU database reader is implemented.

Physical Quest 3 checks still required:

- Browser USB authorization and old-app/new-app installation on the real headset.
- Complete 221-operator correctness run while worn, then while unworn/display off.
  Record display state, thermal state, charging, capability skips and original JSON.
- Remove the headset during preparation, warmup and timed execution. Confirm the
  foreground service remains active, with no Activity pause cancellation.
- Disconnect/reconnect USB, refresh the tab, stop from browser/notification, reach
  thermal limits, and confirm partial evidence survives without replaying a launch.
- Verify notification removal, wake-lock release, bounded cancellation and deadline
  behavior; inspect OS-specific suspension policies on each supported device.
- Confirm actual PostgreSQL receipt and matching stored native JSON after a real
  run, including offline delivery retry. Do not substitute synthetic uploads.
- Keep OpenXR frame measurements as a separate worn/focused test. Unworn isolated
  kernel execution does not qualify XR frame timing or establish a speedup.

Software Vulkan artifacts under `artifacts/conformance/public-native-*` retain
setup failures and subsequent numerical checks separately. They cannot establish
physical GPU support or satisfy these headset gates.

### Campaign evidence review — 2026-10-26

The review reproduced false passes from an unrelated installation file, a completed
run without recovery evidence, an idle/empty 30-minute thermal report, and a
deadline/destruction event presented as notification Stop. These paths now fail.

- Installation requires actual before/after snapshots, observed version direction,
  unchanged saved-report hashes and installation identity. Update runner requires
  an unfinished old checkpoint retired before a distinct final-candidate launch.
- Recovery requires original running and terminal exports, ordered interruption/
  reconnection or document refresh, and preserved samples, correctness and provenance.
  Duplicate launch events and conflicting run content are rejected across gates.
- Native samples and lifecycle records carry coherent UTC/monotonic timestamps.
  A bounded USB clock calibration correlates operator/browser and device time.
  Removal requires an actual display transition and later display-off GPU samples.
- Notification Stop records its source. Cleanup checks ordered lifecycle events
  plus correctly scoped, post-cleanup OS dumps that show no active resources.
  Exit code zero alone is insufficient. Thermal time counts measured native calls,
  excludes idle preparation, and cannot double-count overlapping runs.
- Correctness requires every expected output's before/after verdict, verified
  shader identities, supported platform/profile and stable conditions. Recovery
  may preserve a charging transition while withholding its timing observations.
- Delivery checks immutable bytes, exact endpoint, ordered page-specific retry
  history, and an independent timestamped repeat exchange. Authentication failures
  and invalid receipts cannot substitute for outage recovery. PostgreSQL checks
  native JSON, indexed run metadata and every result column in one read-only,
  repeatable-read transaction.
- Website identity is independent of APK identity: actual HTML/JS/CSS bytes are
  frozen and matched to sessions, browser events and delivery history. A later
  website cannot reuse a previous site's physical qualification.

Review verification: **74 assessor tests passed**, including adversarial and positive
scenarios; **594 Python tests passed, 52 skipped**; **182 browser tests passed**;
**25 PostgreSQL/native HTTP tests passed, 1 skipped**; **7 native software Vulkan
tests passed**, including all **221 operator fixtures** with new timestamps/warmup
measurements. JVM Stop-source checks, the signed APK build, artifact hashes,
production-bundle verification, Ruff and `git diff --check` passed. Counts include
the concurrent workspace changes present when checked; skipped deployment/role
checks do not establish those deployments.

The reviewed candidate is version **13**, APK SHA-256
`002021874879234c8e05b9eef65de330c97e0ef7cb29aca9da9f679bb9567167`;
website SHA-256 is
`3911c21f7fe888620f6b63deb033445aae99729c20f330d49d0ee174ed036c89`.
Logs and the review summary are in `artifacts/conformance/quest3-assessor-review/`.
The fresh private attempt is
`engine/.state/device-onboarding/quest-3/assessor-review-2026-10-26-final/`.
**All 22 physical gates remain blocked**. Older preparation attempts are retained;
their missing evidence must not be filled in by rewriting originals. The assessor
checks consistency of original exports and named operator attestations; it cannot
authenticate hardware or prevent deliberate fabrication of all evidence.

### Quest 3 campaign preparation — 2026-10-26

The [current hardware campaign](docs/workflows/quest3-validation.md) uses all 221
native operators and schema-3 reports. `python -m engine.headset_validation init`
freezes a signed release and creates a private checklist; `assess` checks original
evidence, operator observations, release/device identity, receipts, and optional
independent PostgreSQL reads. Neither command launches or uploads a benchmark.

The runner now records timestamped preparation, warmup, execution, and readback
transitions. Interrupted checkpoints retain current-case samples without counting
the unfinished case as completed or passed. Optional browser export metadata keeps
service lifecycle/OS diagnostics, launch/recovery events, delivery retry history,
receipts, and the exact submitted body after acknowledgment. Old exports remain
readable; missing evidence cannot be reconstructed into a passing physical gate.

Measured checks on the current shared working tree, including concurrent
database/receiver changes:

- Python regressions: **534 passed, 23 skipped** with local HTTP sockets enabled.
- Browser/receiver regressions: **153 passed**.
- PostgreSQL/native HTTP integration: **20 passed, 1 skipped**. The skipped
  container-recreation check remains opt-in; disposable test databases preserve
  application data. Stored native JSON, receipt digest, and individual result rows
  were independently checked, including tampered-row rejection and input immutability.
- Native software Vulkan: **7 passed**, including **221/221 operator fixtures**,
  stage markers, Stop in preparation/warmup/execution, an accelerated test deadline,
  the thermal-stop control path, and abrupt process loss with an in-flight
  checkpoint. These use SwiftShader, one warmup and one timed sample, and explicit
  software-only flags. The production validator rejected that software report.
- JVM checks exercised the actual service with Android doubles: completion,
  startup failure, cancellation, deadline, destruction, and idempotent cleanup.
  This does not establish Android OS behavior on a headset.
- The final signed Android release built successfully. APK/manifest, embedded
  asset inventory, all **315 SPIR-V** files, and all four packaged native libraries
  matched. The existing signing certificate was preserved. Production-bundle USB
  chooser cancellation, report retry/delivery, and deployment-prefix checks passed.
- New Python modules passed Ruff; `git diff --check` passed.

The final preparation candidate is version **8**, APK SHA-256
`582a6fe95f604d2bf3331fd8238718454158b8e888254a3e8e3599c3c68ffa54`.
Original logs, software reports, and the verification summary are under
`artifacts/conformance/quest3-readiness-preparation/`. The earlier native harness
timeout is retained separately from the final passing run.

The private attempt is
`engine/.state/device-onboarding/quest-3/readiness-preparation-2026-10-26-current/`.
Its frozen release and `assessment-01.json` record **all 22 physical checks blocked**:
installation, worn/unworn correctness, phase-specific removal, recovery, service
lifecycle/deadline, thermal behavior, and real-run PostgreSQL delivery. No physical
checks were performed because the operator is available later. The assessment is
`incomplete`; no device-support or performance claim has been added.

Next: supply the observed host/device/installation metadata, execute the runbook
with the operator, attach original exports and timestamped observations, and run
`assess --check-postgres` with a fresh output filename. Start a new attempt after
any runner, firmware, or installation change. Named unsupported operators remain
exclusions; they never count as passes. OpenXR and hosted-site qualification stay
separate.

### Recorded software verification — 2026-10-26

- Python regression suite: **434 passed, 13 skipped**. The database and HTTP
  tests were also run with local sockets enabled.
- Browser regression suite: **141 passed**; the final controller/receiver subset
  passed **34 tests**, including recovery from a previous release.
- PostgreSQL/native HTTP integration: **19 passed, 1 skipped**. Container restart
  is a separate opt-in; application database contents were preserved.
- Software Vulkan smoke: **221/221 fixtures passed**, with zero warmups and one
  timed sample, plus correctness checks before and after. This is numerical smoke
  coverage, not benchmark performance or full contract parity.
- Signed Android ARM64 APK built successfully. All **315 embedded SPIR-V** files
  and runtime/license hashes matched the source package inventory.
- Actual production bundle verified USB chooser cancellation, same-origin
  automatic delivery, IndexedDB retention and deployment-prefix handling.
- Chrome loaded the local site and its Run button reached USB selection. No
  headset was selected, so physical installation and unworn behavior remain pending.

The source native package is
`82291e879d9aa5db9c8fb2ae129e7aaebd79deae270815e237e61daec127b605`;
the typed fixture suite is
`749085c3487805211214ce910980a35995628b21a6cffe1278443d143cf4dc39`.
The resolved xrGPU revision is pinned in `dependencies.json`. Local software
reports are in `artifacts/conformance/public-native-v3/` and subsequent directories;
the current APK hash and signing identity are in `dist/runner/manifest.json`.
The final local release summary and successful test logs are preserved in
`artifacts/conformance/browser-native-release/verification.json` and its directory.
The v4 software report also passed typed catalog/shader validation and was rejected
by the production report validator, as required for software-only evidence.

## Native inference pipeline verification

This records the completed native-pipeline verification from the xrBench/xrGPU
integration work. These are saved results for the package identified below, not
a claim that subsequent working-tree changes have been tested. Tests were not
rerun for this documentation update.

| Check | Recorded result |
| --- | --- |
| xrBench inference, qualification campaign, and kernel-package tests | 74 passed |
| xrGPU native library, qualification, and integration tests | 28 passed |
| Production native C++ Session model acceptance on the host CPU | 24 fixtures passed |
| Inference shader package compilation | 18 shaders compiled |
| Shader identity in the production Android AAR | All 18 packaged SPIR-V binaries found byte for byte |
| Shader identity in the xrBench qualification APK | All 18 packaged SPIR-V binaries found byte for byte |
| Android production and development native libraries | Built successfully |
| Android release AAR assembly and lint | Passed |
| Native qualification runner and OpenXR qualification APK | Built successfully |
| Ruff checks on changed Python files and `git diff --check` | Passed |

The 102 focused tests include rejection of pending qualification, development
builds in production acceptance, CPU fallback in GPU acceptance, stale package
or policy identities, modified library bytes, and missing expected kernel
selections. Release-handoff tests mock the hardware-assessment/build boundary;
their synthetic fixtures do not qualify a real kernel package.

Full-model and regression qualification now uses the exact final candidate
rules. A claimed model speedup must record selection of a final performance
kernel. Production acceptance preserves the kernel selections observed during
qualification; unaffected regression models may retain stock Vulkan paths.

### Models and numerical checks

The host acceptance run executed six original fixtures for each static bundle:

| Model | Input configuration | Fixtures passed |
| --- | --- | --- |
| `sentence-transformers/all-MiniLM-L6-v2` | Sequence length 32 | 6 |
| `sentence-transformers/all-MiniLM-L6-v2` | Sequence length 128 | 6 |
| `sentence-transformers/all-MiniLM-L6-v2` | Sequence length 256 | 6 |
| `torchvision/mobilenet_v2` | RGB input `[1, 3, 224, 224]` | 6 |

Floating-point outputs were compared with `atol=1e-4` and `rtol=1e-4`; integer
outputs require exact equality. The largest observed absolute output difference
across the 24 model fixtures was `7.152557373046875e-06`. All ran through the
production native `Session` API on the **host CPU**.

The prepared pipeline also contains 93 regression cases and 26 operator
descriptors, for 123 workloads including the four model bundles. Preparation
does not establish that those workloads passed on an XR device.

### Package and evidence

- Kernel package SHA-256:
  `8eac67ee1bc22029d982652ecf12dcbdf1ba01bd3f91be76f004fe9c91bffc1a`.
- Pinned ExecuTorch revision:
  `e4d02f41f7909e8ed5bf4a14ffc520d733453d9f`.
- [Software verification report](engine/.state/native-pipeline/quest3/software-verification.json).
- [Host native acceptance report](engine/.state/native-acceptance/host/validation.json).
- [Prepared pipeline](engine/.state/native-pipeline/quest3/pipeline.json).
- [Saved device inventory](engine/.state/native-pipeline/quest3/adb-devices.txt).
- Build and test logs: `engine/.state/native-pipeline/quest3/verification-logs/`.

These evidence files and generated artifacts live in local, Git-ignored state
and build directories. They may be absent in a fresh checkout. The software
verification report records the tested AAR/APK hashes; rebuilding an artifact
requires checking the new bytes again.

### Rerun the focused tests

From xrBench:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  engine/tests/test_inference.py \
  engine/tests/test_qualification_campaign.py \
  engine/tests/test_kernel_package.py
```

From xrGPU:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_native_library.py \
  tests/test_qualification.py \
  tests/test_integration.py
```

After building the host library against the same package, rerun native model
acceptance from xrBench. Choose a fresh output directory:

```sh
.venv/bin/python -m engine.inference validate \
  --host-runner ../xrGPU/artifacts/build/host-library/xrgpu_run \
  --bundle engine/.state/native-pipeline/quest3/models/minilm-32 \
  --bundle engine/.state/native-pipeline/quest3/models/minilm-128 \
  --bundle engine/.state/native-pipeline/quest3/models/minilm-256 \
  --bundle engine/.state/native-pipeline/quest3/models/mobilenetv2 \
  --output engine/.state/native-acceptance/host-rerun
```

See the [native pipeline guide](docs/native-pipeline.md) for model preparation,
builds, production release assembly, and device acceptance.

### Remaining hardware gates

No authorized Android headset was connected during this verification. Physical
GPU qualification is **blocked**, and the qualified release and production
device acceptance remain **pending**.

The remaining work is to collect real-device discovery/confirmation, numerical
correctness, controls, dispatch traces, complete-model timing, and sustained XR
evidence; export a passing qualification record; then verify the production
library on that device with development overrides disabled. Follow
[model qualification](docs/model-qualification.md).

The recorded host tests, successful builds, and shader-byte checks establish
software behavior and packaging integrity. They do not establish physical-device
support, GPU correctness, or a performance improvement on XR hardware.
