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
full 25-operation/63-case suite twice: CPU first, then GPU, in a server-owned session.

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

See [measurement definitions](../../engine/MEASUREMENTS.md) for calibration,
timing boundaries and estimate limitations, and [engine documentation](../../layers/README.md)
for setup, API and native execution. Run `npm test` for frontend/workflow tests. Both web interfaces are buildless.

Visual QA uses a separate synthetic fixture server with hardware execution disabled;
its reports must never be submitted to the benchmark database. Check idle, running,
complete, failed and disconnected states at desktop and phone widths. Actual
calibration and timing validation requires an authorized Quest 3.

Automated tests cover frontend workflows, native
correctness contracts, CPU/GPU sequencing, report recovery and cancellation.
The native arm64 APK builds locally. Browser fixtures are
presentation evidence only. No headset was connected, so on-device calibration,
CPU/GPU correctness and hardware qualification remain unverified.

## Kernel experiments

The Kernel experiment selector switches between ordinary stock benchmarking, an A/A
noise control, and stock/candidate matmul. Experiments select matmul and require native
isolated measurements with Smoke or Standard. Results show paired-block intervals;
case details retain per-dispatch timing, workgroup sizes and shader source paths.
Latency mode disables GPU timestamps. See [kernel development](../../kernels/README.md).


Device inspection and backend controls now support Android 10+ arm64 compute devices
without a product-name allowlist. The UI distinguishes capabilities from hardware
validation, exports profiles without USB serials, and records the execution target.
Resident Vulkan selects matmul and excludes the Quest-only XR scene. The optional
Development suite adds shapes, seeds, and synthetic model segments. See
[kernels/PORTABILITY.md](../../kernels/PORTABILITY.md) for qualification and timing scopes.

The additional `executorch_cpu` backend runs portable CPU exports, while
`cpu_resident` exposes scalar/NEON matmul experiments. CPU controls disable GPU
instrumentation and recalibration; CPU results contain no GPU utilization or timing
claims. Stop also cancels an active homepage CPU/GPU session between phases.
