# Bench Web

The homepage explains the 63 GPU tests with connection guidance beside Run,
above the ASCII headset on phones. Device compatibility lists headsets by their
primary processor under Check on device, Planned, and Unavailable; catalog recognition
does not imply hardware qualification. The headset and chip reveal remain the main
visual, with sentence-case interface labels and the ivory, graphite and sage palette.
Run creates an isolated Smoke session containing all 63 standard GPU cases. The active controller
reserves the selected device throughout the run. Browsers display native evidence.
Stats for Nerds retains
single-backend controls, development workloads and custom kernel experiments.

## Browser distribution

The hosted build uses Tango ADB's WebUSB daemon transport directly from desktop
Chrome/Edge. It does not contact a localhost API and does not need Python, Docker,
PostgreSQL, a desktop ADB executable, or compilation tools on a visitor's computer.
The local Python application is retained for development and Stats for Nerds.
Its existing APIs and local build workflow remain available.

On the hosted site, **Run** opens the USB device chooser if needed, authenticates,
downloads and verifies the matched APK, installs it, probes native capabilities,
and executes all 63 GPU cases using the Standard measurement preset: 20 warmups
and 100 measured samples per successful case, automatic calibration, and shader
dispatch tracing. Visitors have no benchmark settings to configure. Installation
is skipped when the headset already has that exact APK. The user must enable
headset developer mode, use a data cable, grant browser USB access, and approve
USB debugging inside the headset. Windows drivers and Linux USB permissions can
still prevent WebUSB access; no universal driver-free claim is made. Another ADB
process or tab holding the USB interface must release it before the browser can
connect. The website never kills other programs or uninstalls an incompatible APK.

The homepage shows the current USB device and **Run**. Completed sessions stay in
collapsed **Previous runs** until selected; explicit report links still open them.
**Back to headset** clears that selection and its URL parameters. USB detection is
shown separately from connection and verified readiness. Connection preparation
does not display benchmark timing or progress. An occupied interface shows
**Retry connection** and **Cancel**; status polling does not repeatedly claim it.
Retry or an observed unplug/reconnect makes a new connection attempt using the
same session. Cancel before launch completes without communicating with the device.

The distributed app ID is `org.xrgpu.benchmarks.web`, separate from the developer
app. Its full activity name remains `org.xrgpu.benchmarks.MainActivity`. It is
intentionally debuggable because the existing authenticated ADB protocol uses
`run-as` for app-private command, progress, cancellation and report files. This is
a sideloaded benchmark runner, not a store-release build. Native numerical kernels
and report schema version 2 are unchanged.

`browser/controller.mjs` owns hosted GPU-default sessions and legacy CPU/GPU
recovery. It persists session
identities and launch intent in IndexedDB before sending commands, holds a Web
Lock while a worker owns the device, and recovers existing reports after refresh or USB loss.
Failures and page exit release the USB handle and lock; recovery reacquires the
lock and reloads the saved session before issuing any command.
It never repeats an uncertain native launch. Closing the tab releases USB; the
current native phase may finish on the headset; reconnecting recovers and validates
its final report. For legacy multi-phase sessions, the next phase waits for this
validation. Stop is durable, including
between phases and while disconnected. Download/install preparation is abortable.
If the native process exits without a final report, the browser marks that run
failed and preserves its validated partial evidence and any completed phase.
It reads the report after checking process liveness so a successful final report
still wins over process exit. A new benchmark requires another explicit Run.
Keep the headset awake with the benchmark app visible during measurement;
headset standby or leaving the app cancels the native activity and saves partial
results, with recovery guidance shown on the page.
Browser storage must remain enabled for recovery. Clearing it removes local run
history, pending deliveries, and authentication credentials. Finished native
reports are sent automatically to the website's report receiver; USB credentials
and browser session/connection identifiers are never included in that request.
**Download results JSON** exports the session’s native reports and state.
New distributions select Standard; saved Smoke sessions retain their original
configuration and recover without restarting or changing sample counts.

The native reports retain input shapes, operation parameters, FP32 precision and
fixture seeds; correctness before/after measurement; every timing sample; GPU
timestamp spans and individual dispatches; shader source paths and SPIR-V hashes;
workgroup sizes; staging/submission costs; calibration; thermal/charging state;
and device, driver and build identities. Missing GPU metrics stay unavailable.
Incorrect or unstable rows have no accepted timing summary, but keep their raw
evidence. Timings overlap: dispatch sums, GPU span, staging and execution latency
must not be added together. See [measurement definitions](../../engine/MEASUREMENTS.md).

### Automatic report delivery

There is no separate contribution step. After validating and saving a finished
native report in IndexedDB, the browser queues its original schema-2 JSON for
`POST ./api/reports` relative to the website root (including a deployment prefix).
Complete, failed, and canceled native reports are included so failed correctness
checks are not lost. In-progress evidence remains in the browser until a final
native report exists. A process that dies without a final report does not have its
partial data relabeled as a completed measurement. Existing finished native reports
are queued on reopening the site too; legacy CPU/GPU phase plans remain intact.

The request body is **only the native report**, including its raw samples,
correctness results, hardware metadata and build/configuration revisions. It does
not include the browser's session envelope, USB serial, connection hash, or ADB
authentication keys. The independent `browser/report-delivery.mjs` queue retries
with backoff (5 seconds up to 5 minutes) while the page is open, resumes after
refresh, and keeps a browser copy. Failed delivery never restarts a benchmark or
blocks Run/Stop. The page shows sending, retrying, or receipt-confirmed storage.
The public demo does not enter this queue and the receiver rejects simulated tags.

For testing, `uv run python -m src` accepts reports at `POST /api/reports` and
saves each unchanged JSON body to `engine/.state/submitted-reports/<run-id>.json`.
These files are ignored by Git and cannot be read through the website.
`--reports-dir /absolute/private/path` selects another local directory, which must
be outside the served site. This receiver needs neither PostgreSQL nor a headset
to test with fixtures. The developer application continues saving native reports
under `engine/.state/runs/<run-id>/native-report.json` through its existing path.

The receiver accepts JSON up to 32 MiB, validates the existing native schema, and
atomically creates an immutable file. Identical retries return a receipt for the
same run; conflicting contents for an existing ID return HTTP 409. A successful
response must include `stored: true`, matching `run_id`, and `payload_sha256`
(SHA-256 of the exact request bytes). The local receiver also returns
`storage: "local"`, `report_sha256` (canonical JSON digest), and `received_at`.
The browser marks delivery complete only after verifying and saving this receipt.
Files and receipts establish submitted evidence, **not hardware attestation**;
software fixtures must not be treated as physical-device qualification.

Production hosting must route the same-origin report endpoint to durable storage
using this contract. A static-only deployment returns an error and leaves reports
queued in the browser. This change provides the local testing receiver; it does
not provision or upload to a public collection service.
Supabase integration is deferred; local JSON capture remains the default.

### Build and sign (maintainers only)

`.github/workflows/web-distribution.yml` runs on manual dispatch or `web-v*` tags.
It tests the code, prepares pinned Linux build tools, exports and verifies workloads,
builds the signed Android runner, bundles browser dependencies, and uploads a
complete static website artifact. It does not publish a production hostname.

Provision one persistent signing keystore and keep it backed up. Configure these
repository secrets before running the distribution workflow:

- `XRBENCH_WEB_KEYSTORE_BASE64`: base64 of that keystore.
- `XRBENCH_WEB_STORE_PASSWORD`, `XRBENCH_WEB_KEY_ALIAS`, `XRBENCH_WEB_KEY_PASSWORD`.
- Repository variable `XRBENCH_WEB_CERT_SHA256`: the signing certificate's lowercase
  SHA-256 digest, without colons. The build verifies the APK against this identity.

The workflow uses its increasing run number for Android's version code. Never
replace the signing key or reset the version sequence for an existing distribution.
No random key or development key is substituted when signing configuration is
missing. The keystore is only decoded into the CI temporary directory, is removed
after the build, and is not included in the uploaded artifact.

To build locally on a configured maintainer machine, set `XRBENCH_WEB_KEYSTORE` to
the absolute keystore path, the password/alias variables above, the certificate
digest, and a positive `XRBENCH_WEB_VERSION_CODE`. After preparing the native
toolchain and workload exports, run:

```sh
uv run --extra export --group dev python -m engine.build --web
npm ci
npm run build
node --experimental-vm-modules scripts/verify-web.mjs dist
```

The output includes `index.html`, the bundled `bench/` assets, third-party licenses,
and `runner/manifest.json` plus a checksum-named APK. The manifest contains protocol
version 1, package/activity IDs, platform constraints, signing provenance, revisions,
catalogs, and CPU/GPU configurations generated with `Controller.configuration`.
Packaging fails if the signed APK is absent, corrupt, or mismatched with the suite.
`XRBENCH_PYTHON` can select the Python interpreter used by the packaging command.

Serve the entire distribution from one HTTPS origin, at the root or under a path
prefix. Publish the manifest, bundle and APK together; retain immutable APK URLs
while older sessions may need recovery. Do not cache `runner/manifest.json` or
`index.html` indefinitely. `_headers` provides the CSP, USB Permissions Policy and
other response headers for hosts supporting that file; configure equivalent
headers elsewhere. Run the site as a top-level page. No CDN scripts or cross-origin
APK URLs are needed. Hosted navigation omits the local Stats for Nerds application.

For a local preview of the packaged release, `uv run python -m src` serves
`dist/` on localhost port 8765 and captures submitted reports outside that folder.
It starts neither desktop ADB nor PostgreSQL and exposes no benchmark control APIs.
Chrome permits WebUSB on the localhost secure context; remote hosting requires
HTTPS. `uv run python main.py` opens this same browser preview.
`uv run python main.py --developer` starts Docker, PostgreSQL, and pending SQL, then opens
the existing developer application on port 8766, including Stats for Nerds and its
local APIs. `uv run python -m src --developer` remains the server-only alternative.
See [engine startup](../../engine/README.md).

### Verification and release qualification

Runner builds fetch the immutable xrGPU kernel package pinned in
`dependencies.json`. Configure the CI secret `XRGPU_READ_TOKEN` with read access
to the private xrGPU repository; maintainers can also use their Git credential
helper. Run `uv run python -m engine.kernel_package fetch` before native tests.
The verified sources stay in ignored build dependencies and compiled kernels ship
inside the APK. Visitors need no GitHub access. Reports and runner metadata include
the xrGPU commit and package digest; changing the pin requires rebuilding the APK.
See [kernel development](../../docs/kernel-development.md).

`npm test` covers the browser transport, controller, storage, frontend and demo.
`python -m pytest` includes cross-language configuration, identity, validation and
summary fixtures and a production-bundle execution test with no localhost/API
requests. Tests use explicitly synthetic APK bytes and native evidence; those
artifacts under `artifacts/` are never deployable releases or hardware proof.

Before qualifying a release, use its actual signed APK and hosted bundle on a
clean computer with a Quest 3, then a second supported Android device. Record OS,
browser, headset firmware, driver requirements, release checksum and exported
native reports. Verify first install, repeat runs, updates, permissions, multiple
devices, occupied USB interfaces, Stop during preparation and between phases,
refresh, unplug/reconnect, and cancellation while disconnected. A completed browser
fixture does not qualify physical execution or establish driver-free support.

The signed version-3 distribution was exercised on a real Quest 3 using Windows
build 26200 and desktop Chrome on 2026-10-20. Its static preview completed all
126 cases in about 3 minutes 16 seconds: CPU 63 passed; GPU 61 passed and 2 failed
correctness (`gelu_tokens`, `conv2d_grouped`). Failed cases have no accepted timing
samples. Browser installation, updates, reuse, refresh recovery, exclusive tab
ownership, cancellation and JSON download were observed. Python and browser
validators agreed on the downloaded reports and summaries. This was a configured
maintainer computer using the localhost secure context; clean-computer driver
qualification, public HTTPS testing and a second device remain outstanding.

## Advanced CPU testing

The homepage never runs a CPU comparison or falls back from failed GPU execution.
**Advanced → Run CPU benchmark** appears only for an executing chip confirmed to
have no GPU. The user must click it explicitly; the main Run action remains GPU.
Both session controllers enforce this policy and require native CPU compatibility
before launching a new CPU session. Native reports and the runner ABI are unchanged.
Saved sessions retain their original phase plans, including legacy CPU→GPU runs.
The separate local Stats for Nerds laboratory retains its manual backend controls.

Chip catalog entries may supply `gpu_presence` (`present`, `absent`, or `unknown`)
and `gpu_presence_source` (an authoritative source URL; required for `absent`).
Missing metadata is unknown. Conflicting identity or any positive native GPU
observation prevents CPU eligibility. Vulkan errors and unsupported GPU backends
never prove absence. No current production catalog chip is marked absent; the CPU
path is exercised with explicitly synthetic fixtures, not invented hardware facts.

## Device and processor identity

`engine/device-catalog.json` is the versioned source for detection, chip locations, vendor references and the supported-devices panel. Exact aliases
are matched against Android model, product and device fields. Native and Android
SoC reports are retained separately from catalog inference. Conflicting identities
are flagged for verification. Multiple connected devices require explicit selection;
Quest 3 is never selected preferentially.

Recognition does not imply execution compatibility or physical validation. Android
10+ arm64 devices require USB debugging and a native capability check. Qualcomm XR
families are candidates for the Android runners; Apple CPU/Metal is a separate
milestone. Proprietary spatial processors remain listed with SDK/runner limitations.
For Aura, Reality Elite is located in the compute puck and X1S in the glasses.
The progress panel identifies the component executing the benchmark.

## Live evidence

A compact device-status panel shows the selected device and processor. An accessible
HTML dashboard shows elapsed time, phase ETA, operation, correctness, samples and
recent validated results. Expandable case details retain each CPU and GPU case's
native status. The gauge is **Suite completed**, never utilization. Counts and
results come from native reports; unsupported backends do not inflate completion.
ETA uses recent completed case durations within the current processor phase.
Preparation shows “Estimating…”. Stale progress stops projecting activity and ETA.
Only the elapsed display clock is interpolated.

Finished sessions retain separate CPU/GPU summaries and reports on the homepage.
Run starts the existing native session immediately, then plays a 1.5-second ASCII
reveal: the visor and rear assembly separate and a chip flies forward. Readable
HTML diagnostics sit inside the projected chip outline. CPU/GPU regions follow
the current native phase; they are presentation regions, not a physical die map
or utilization measurement. Expanded evidence and case results stay below.

The scene uses a dependency-free software 3D renderer with depth testing, surface
lighting, curved visor, camera lenses, cooling slots, straps, and a chip package
with pins. The large, borderless artwork shares the ivory and sage hero canvas.
`mountHeadset()` returns `setPresentation({mode, runKey, processor, source, animate})`
and `destroy()`. A run key triggers its reveal once. Repeated progress updates
never replay it; restored runs go straight to the chip. Returning to the headset
preserves results, and a new run reveals its chip again.

Rendering is capped at 30 FPS and pauses offscreen or in hidden tabs. A settled
chip needs no continuous animation. Reduced motion skips the reveal. Touch has
no idle movement or cursor avoidance, but permits the user-triggered reveal.
Rendering failures retain readable diagnostics and Run/Stop controls. On phones,
the action row remains visible while scrolling through the chip.

In the local developer application, Stats for Nerds is available through its named navigation link.
Connection, USB authorization, unsupported hardware, service errors and missing
native APK guidance are handled inline with Retry. The local toolchain and built
APK remain developer-host prerequisites; the local homepage does not invoke builds. The existing
session controller installs the APK, checks capabilities, and runs the selected
processor (GPU by default).

## Optional device demo

Choose **Try a demo**, pick any catalog device, and select **Connect demo device**.
Then **Run demo** previews the standard 63 GPU cases in about 12 seconds.
Stop, rerun, change devices between runs, or use **Exit demo** to restore the live
device view. Apple and specialized glasses retain their actual support notes;
successful simulation does not imply native compatibility.

To disable the entire demo, set `ENABLE_SMOKE_DEMO = false` in
`web/features.mjs` and reload. The demo controls are not mounted and
`web/smoke-demo.mjs` is not imported. No server option or dependency is required.

The component reads the existing device and workload catalogs but keeps all
synthetic connection, progress, and results state in memory. It never sends POST
requests, launches hardware workloads, saves reports, or puts demo IDs in URLs or
browser storage. All models share the same illustrative sample timings. The demo
uses the hosted catalogs or local developer service; no device is needed.
Catalog failures offer retry. Native activity prevents demo entry and takes over
the view if a real run appears while the demo is open.

## Session API and recovery

- `POST /api/benchmark-sessions`: `{serial, request_id, processor?}` with `processor` defaulting to `"gpu"`
  (also accepts `"cpu"` under the Advanced eligibility policy above). Reusing the same
  request ID returns the original session; changing its processor is rejected.
  A device cannot be reserved by two sessions.
- `GET /api/benchmark-sessions/<id>`: phase state and existing child run identities.
- `POST /api/benchmark-sessions/<id>/stop`: durable cancellation, including between
  phases. Fatal failure or cancellation prevents the next phase.
- `GET /api/device-catalog`: shared identity catalog. Existing `/api/runs` routes
  and reports remain compatible.

All routes use the existing local session/origin protection. Session state lives in
`engine/.state/benchmark-sessions/`. `?session=<id>` restores browser selection.
Controller restart recovers persisted child identities; disconnects recover the
same native report. A terminal progress message alone cannot advance the session:
the controller must recover and validate the final report first. Setup failure or
a build/identity change is explicit and does not silently launch a replacement.

## Build and verify

From the repository root:

```sh
npm ci
npm test
uv run --extra export --group dev python -m layers.export
uv run --extra export --group dev python -m engine.verify_cpu
uv run --extra export --group dev python -m engine.build
uv run --group dev python -m pytest -q
uv run python main.py --developer
```

Both local web interfaces are buildless; the hosted distribution uses the bundler described above. Host CPU correctness
covers both the 63 standard and 17 development cases using the shared references.
Browser fixtures use synthetic evidence and must not be submitted as hardware qualification.
The Quest 3 observations above do not qualify a second Android XR device or establish
driver-free support on a clean computer.
See [hardware qualification](../../docs/PORTABILITY.md).
