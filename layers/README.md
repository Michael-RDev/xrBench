# Shared native layer benchmarks

Connect Quest 3 by USB, open the local website, and press **Run all
tests**. All 63 cases across 25 layer types run automatically, in catalog order.
The website controls a dedicated Android app. Every supported layer executes in
ExecuTorch's Vulkan backend on the headset; the browser uses graphics only for its optional Three.js presentation.
The native app also offers a 72 Hz OpenXR scene for paired rendering-impact tests.

Each layer type has its own `<operation>/layer.py` script. Edit that script to
change its PyTorch export definition (`module`) or independent NumPy correctness
reference (`reference`). Layers with weights or special inputs also define
`make_weights` or `prepare_inputs` there. References receive float64 arrays and
are rounded to FP32 by the shared harness; use the supplied `rand` function to
preserve deterministic fixtures.

`catalog.json` remains the ordered list of all 63 cases, including shapes and
parameters. `__init__.py` dispatches to the layer scripts and handles shared input
generation, correctness checks, and source revisions. `_shared.py` only registers
PyTorch buffers. The scripts define workloads exported to stock ExecuTorch/Vulkan;
they are not custom native kernel implementations.

| Layer folder / script | Cases |
| --- | ---: |
| [linear/layer.py](linear/layer.py) | 10 |
| [matmul/layer.py](matmul/layer.py) | 2 |
| [bmm/layer.py](bmm/layer.py) | 2 |
| [relu/layer.py](relu/layer.py) | 2 |
| [gelu/layer.py](gelu/layer.py) | 2 |
| [silu/layer.py](silu/layer.py) | 2 |
| [sigmoid/layer.py](sigmoid/layer.py) | 2 |
| [tanh/layer.py](tanh/layer.py) | 2 |
| [layer_norm/layer.py](layer_norm/layer.py) | 3 |
| [rms_norm/layer.py](rms_norm/layer.py) | 3 |
| [softmax/layer.py](softmax/layer.py) | 3 |
| [sum/layer.py](sum/layer.py) | 2 |
| [mean/layer.py](mean/layer.py) | 2 |
| [attention/layer.py](attention/layer.py) | 3 |
| [add/layer.py](add/layer.py) | 2 |
| [mul/layer.py](mul/layer.py) | 2 |
| [conv2d/layer.py](conv2d/layer.py) | 4 |
| [depthwise_conv2d/layer.py](depthwise_conv2d/layer.py) | 2 |
| [batch_norm/layer.py](batch_norm/layer.py) | 2 |
| [max_pool2d/layer.py](max_pool2d/layer.py) | 2 |
| [avg_pool2d/layer.py](avg_pool2d/layer.py) | 2 |
| [adaptive_avg_pool2d/layer.py](adaptive_avg_pool2d/layer.py) | 1 |
| [embedding/layer.py](embedding/layer.py) | 2 |
| [transpose_copy/layer.py](transpose_copy/layer.py) | 2 |
| [concat/layer.py](concat/layer.py) | 2 |

`engine/` contains the native Android runner, build tooling, ADB controller,
report analysis, PostgreSQL setup, shared HTTP API, and engine tests. Both websites
use this engine. Pinned toolchains and native dependency checkouts belong to this
repository; the scene and tracing sources are contained in the engine itself.
Unity integration is separate.

After editing a layer script, rerun the exporter and APK build below. Layer script
changes invalidate the bundle revision. Every preset still runs the full catalog.

## Start the website

From the repository root, with Docker running:

```sh
uv sync --extra export --group dev
uv run --extra export --group dev python -m app
```

The launcher creates an ignored, mode-0600 `layers/.env` containing a random
database password, starts PostgreSQL 17, applies migrations, and opens
[the local dashboard](http://localhost:8765). PostgreSQL listens only on
127.0.0.1:5432; the controller listens only on 127.0.0.1:8765. Change
`XRGPU_DB_PORT` in the generated `.env` if port 5432 is already occupied. The
controller stays on the host because it needs access to the USB ADB connection.

Use `--no-open` to suppress browser launch, `--port` to change the website port,
and `--adb /path/to/adb` to select platform-tools. To use an existing database,
set `XRGPU_DATABASE_URL` and pass `--no-docker`. The database role needs permission
to create the `xrgpu_benchmarks` schema and its tables. Database configuration is
never sent to the browser or packaged into the app.

## Build the native app

Prepare the repository's pinned Android toolchain and stock native dependencies.
The exporter and controller use only the root uv environment.

```sh
uv run --extra export --group dev python -m layers.engine.bootstrap_android
uv run --extra export --group dev python -m layers.engine.prepare
uv run --extra export --group dev python -m layers.export
uv run --extra export --group dev python -m layers.engine.build
```

The output is `layers/.build/xrgpu-benchmarks.apk`, with checksums and revision
metadata in `layers/.build/apk.json`. The dashboard automatically installs
this matching APK on Run. Android Gradle dependencies may be downloaded on the
first build. The app uses its own package, `org.xrgpu.benchmarks`, and a local
debug signing key. Its debuggable build is necessary for ADB `run-as` access;
this is a local development harness, not a store release.

The dedicated APK packages only synthetic layer fixtures, exported layer programs,
native libraries, and third-party notices. It does not need MiniLM or other model
downloads. Never substitute an APK without regenerating its metadata.

## Connect and run

1. Enable developer mode and USB debugging on the headset.
2. Connect a data-capable USB cable and approve USB debugging inside the headset.
3. The dashboard detects authorized Android 10+ arm64 headsets automatically.
   Select the headset if more than one is connected.
4. Press **Run all tests**. The default runs every native case with 5 warmups and
   20 samples. Results appear as each test runs. No layer selection is needed.
5. Optionally open **Advanced** to change the preset or enable XR impact for the
   entire suite. XR mode requires wearing the headset and keeping its scene focused.

Quest 3 is the first validation target. Other headsets and glasses are unsupported in this version;
the native runtime checks Vulkan support and the XR scene requires stereo views,
1440×1440 render targets per eye, and a reported 72 Hz refresh rate. An absent
device is reported as absent; desktop performance is never substituted.

Stop writes a cancellation request that the native worker checks between work
units. GPU work already submitted cannot be canceled. Disconnection leaves a
bounded run executing on the headset; reconnection retrieves the saved report.
Stopping while disconnected queues the cancellation request until reconnection.
Restarting the local controller recovers its saved jobs instead of rerunning them.

## Workloads and measurements

The source catalog contains 63 cases covering 25 operations: linear, matmul, bmm,
ReLU, GELU, SiLU, sigmoid, tanh, layer normalization, RMS normalization, softmax,
sum, mean, attention, addition, multiplication, convolution, depthwise convolution,
batch normalization, max/average/global-average pooling, embedding, transpose-copy,
and concatenation. Case-local deterministic seeds prevent selection order from
changing inputs. The independent oracle computes in NumPy float64, then rounds
to FP32. Both export and device execution check output correctness.

| Preset | All 63 isolated cases | XR impact, each of 63 cases |
| --- | --- | --- |
| Smoke | 5 warmups, 20 samples per case | 2 baseline/load pairs, 5 seconds per window |
| Standard | 20 warmups, 100 samples per case | 4 pairs, 15 seconds per window |
| Sustained | 20 warmups per case, then all cases round-robin at 10 Hz for 10 minutes | 4 pairs, 75 seconds per window |

Every preset includes the complete catalog. Unsupported cases and case failures
are recorded individually before proceeding to the next case. Cancellation,
severe thermal conditions, XR focus loss, and other session failures stop the run
and preserve completed results and partial evidence.

XR load is paced at 10 Hz; baseline/load order alternates between pairs. Work that
overruns its interval does not trigger catch-up bursts. Full-suite XR window time
is 21 minutes for smoke, 126 minutes for standard, or 630 minutes for sustained,
plus setup and warmups. The dashboard shows this estimate. Native runs use a
host-independent deadline of `max(30 minutes, 125% of planned XR window time +
5 minutes)`, checked between work units; isolation retains its 30-minute limit.

`POST /api/runs` accepts only `serial`, `mode`, and `preset` (all optional when a
single headset is connected). The controller resolves the full ordered `cases`
list internally; public `cases` or `operations` filters are rejected. Saved
reports retain their resolved case lists, including older partial-suite reports.

Progress includes the current case, one-based index, and completed/total case
counts. Reports can be downloaded while running as well as after completion.

`execution_ms` includes JNI input staging, native forward, and output copying.
`model_ms` measures native forward, including delegate staging. `gpu_ms` is the
sum of available Vulkan operation timestamp intervals; it is not critical-path
latency. GPU timing, staging fields and XR compositor metrics are nullable when
not available. The scene's frame metric is `max(app CPU, app GPU)` from
`XR_META_performance_metrics`; dropped-frame rate uses the compositor counter.
Neither is motion-to-photon latency. Warmup and validation are outside measured
samples. Thermal/charging transitions flag timing validity; severe thermal state
or XR focus/refresh loss stops the run and preserves partial evidence.

Native exports must be fully delegated to Vulkan. Unsupported exports are
recorded explicitly and never silently benchmarked on CPU. These synthetic
layer tests characterize this native backend, not Unity scheduling overhead or
end-to-end model performance.

### Website views

- **Benchmark:** automatic device discovery, one-click full-suite execution,
  live case counts, Stop, JSON downloads, and database retry. Advanced contains
  mode/preset controls; a headset selector appears only for multiple devices.
- **Layers:** all cases grouped by operation, with search/status filters that
  never change the run queue. Expand a case for shapes, correctness before/after,
  timing scopes, sample counts, unsupported reasons, and thermal/focus flags.
- **Runs:** local jobs plus paginated PostgreSQL history, including reports whose
  controller job files are no longer present. Compare two finished reports by
  case, phase, and trial. Invalid or incompatible timing differences are withheld.

`GET /api/saved?limit=20&offset=0` returns `runs` and `next_offset` (null on the
last page). Limits are 1–100 and offsets 0–100000. History includes configuration,
hardware/build provenance, and the original PostgreSQL receipt, without samples.
`GET /api/compare?baseline=<run-id>&current=<run-id>` returns compatibility fields,
reasons, and observed median/p95 changes for each native timing scope. Both use
the existing localhost/session protections. Report downloads remain available
from local files during database outages; PostgreSQL-only history requires the DB.

New run configurations record the installed APK SHA-256 from checked build
metadata. This identifies an artifact, not a selected custom kernel. Historical
reports remain unchanged; missing provenance displays as “Not recorded.” No
schema migration or report rewrite is needed when relocating this code. Re-export
and rebuild after source changes so the packaged suite and APK metadata agree.

Comparisons require matching suite, backend, scope, mode/preset, hardware/driver,
OS, precision, and stock variant; XR also requires matching scene settings.
Each measurement must pass correctness, have valid timings, and match recorded
thermal/charging states. Missing metrics remain unavailable. Revision differences
are shown for interpretation, not treated as proof of a kernel speedup. Timings
overlap and cannot be added; hardware metadata cannot identify a physical unit.

## Results and PostgreSQL

Native reports and progress are stored in app-private `files/runs/<run-id>/`.
The host stores retrieved reports, job state and database receipts under
`layers/.state/runs/`. Reports are saved locally before database submission.
The original native envelope is preserved as `native-report.json`; the downloaded
report adds host-computed statistics, XR pair deltas, and the native envelope hash.
Database outages leave reports pending for retry without rerunning GPU work.

The database stores `schema_migrations`, `benchmark_runs`, and
`benchmark_results` in the `xrgpu_benchmarks` schema. Each terminal report and all
case rows commit in one transaction. The run ID and canonical report hash provide
idempotency; conflicting retries cannot overwrite existing data. The Compose
project stays `xrgpu-benchmarks`, preserving its
`xrgpu-benchmarks_benchmark-postgres` volume across this relocation. Compose lives
in `engine/compose.yaml`; `.env` and `.state/debug.keystore` must be retained. Versioned
migrations also run against existing volumes. No volume reset is required.

Reports contain device capabilities, OS/runtime/suite identity, synthetic workload
configuration, correctness evidence, timings, thermal/charging observations and
errors. USB serials remain in local controller job state and are excluded from
submitted reports. No accounts, email addresses, captured camera images, pose
traces, or cloud uploads are used.

## Tests

```sh
uv run --extra export --group dev python -m pytest layers/engine/tests app/stats_for_nerds/tests -q
uv run --extra export --group dev python -m pytest layers/engine/tests/test_layers.py -q
XRGPU_TEST_POSTGRES=1 uv run --extra export --group dev python -m pytest layers/engine/tests/test_postgres.py -q
node --test app/stats_for_nerds/tests/test_web.mjs
```

The NumPy/PyTorch oracle tests use the root environment installed by
`uv sync --extra export --group dev`. PostgreSQL integration tests create and remove a
uniquely named temporary database, leaving collected benchmark data untouched.
They require the local database to be running and a role allowed to create test
databases. Physical Quest validation must still exercise install, isolation,
Stop, reconnect recovery, and focused XR baseline/load runs.

Add `XRGPU_TEST_POSTGRES_RESTART=1` to the PostgreSQL test command to also recreate
the local benchmark database container and verify its persistent volume. This
briefly interrupts database access; reports remain in the controller's local queue.

Native dependencies retain their upstream licenses. See the repository-root
`THIRD_PARTY_NOTICES.md`; notices are included in the APK assets.

## Validation completed in this workspace

Engine and website regression tests use the root environment. PostgreSQL
container recreation has also been validated. Existing dependency pins,
credentials, signing key, and the database volume are preserved. All 63 cases
retain byte-identical fixtures, reference results, and PyTorch outputs after
the per-layer script split.

- All 63 cases match independent NumPy references and export with complete Vulkan
  delegation. Packaged programs and fixtures pass their manifest checksums.
- The signed arm64 APK builds against the repository-owned pinned stock native
  dependencies. Its embedded runtime and runner revisions match `apk.json`.
- Controller, ADB transport, HTTP session/origin protection, cancellation,
  simulated reconnect recovery, report downloads, native tensor validation,
  and database outage retry tests pass.
- PostgreSQL 17 fresh/repeated migrations, atomic transactions, identical retry
  receipts, conflict rejection, and persistence across container recreation pass.
- The website has been checked in Chrome with the real local controller and
  database. No headset was connected, so APK installation and actual Quest 3
  Vulkan/XR execution remain unverified. No headset timings have been fabricated.
- The light redesign, case filtering, keyboard expansion, mobile layout, and
  comparison validity were checked using an isolated synthetic fixture server;
  these fixtures were never submitted to the benchmark database. HTTP report
  download/receipt tests pass independently of the browser.
