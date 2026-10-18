# Stats for Nerds

The buildless, dependency-free dashboard at `/stats/` uses xrBench’s light ivory,
graphite and sage palette. It combines device controls, live counters, exclusive
session stages, slowest correctness observations, estimated efficiency, and
searchable expandable operation evidence. History and comparisons start collapsed.
`#benchmark`, `#layers`, and `#runs` scroll to sections; `?run=<id>` carries a saved
or active run between both websites.

The operations field accepts comma-separated exact names and `*` patterns (for
example `linear, conv*`). Every pattern must match. Selection is resolved to known
operation identifiers and cases in catalog order before sending the request.
Result search only filters displayed evidence. The homepage continues to run the
full 25-operation/63-case suite.

Continuous mode starts off. Explicit enabling acquires a server lease for the
selected device; the visible page renews it every 10 seconds and the server expires
it after 30 seconds. Hidden/closed pages release ownership without canceling an
already-running workload. Stop disables the watch and requests native cancellation.
Failed and disconnected attempts do not automatically retry. A manual run satisfies
the same build/configuration key. No benchmark or upload runs in the browser.

Ranking columns sort locally and missing measurements stay last. Selecting a case
opens its detailed evidence. Live updates retain filters, expanded cases and raw
evidence, keyboard focus, and table scroll. Mobile controls wrap and wide tables
scroll within their own regions. Diagnostics downloads include the report,
calibration provenance, controller timings and timing definitions, with USB serials,
lease owners, session tokens, cache identity strings and host paths omitted.

See [measurement definitions](../../layers/engine/MEASUREMENTS.md) for calibration,
timing boundaries and estimate limitations, and [engine documentation](../../layers/README.md)
for setup, API and native execution. Run `npm test` for viewer/workflow tests; the
Stats frontend needs no bundle. `npm run build` regenerates the homepage scene.

Visual QA uses a separate synthetic fixture server with POST execution disabled;
its reports must never be submitted to the benchmark database. Check idle, running,
complete, failed and disconnected states at desktop and phone widths. Actual
calibration and timing validation requires an authorized Quest 3.

Implementation verification: 43 frontend tests and 167 engine/Android/native contract
tests passed (7 optional or platform-specific tests skipped). The homepage bundle
and native arm64 APK build passed. Idle, running, completed, failed and disconnected
synthetic states were inspected at desktop and phone widths with execution disabled.
No Quest 3 was connected, so on-device calibration and the bounded smoke run remain
unverified.

## Kernel experiments

The Kernel experiment selector switches between ordinary stock benchmarking, an A/A
noise control, and stock/candidate matmul. Experiments select matmul and require native
isolated measurements with Smoke or Standard. Results show paired-block intervals;
case details retain per-dispatch timing, workgroup sizes and shader source paths.
Latency mode disables GPU timestamps. See [kernel development](../../kernels/README.md).
