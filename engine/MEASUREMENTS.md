# Measurement reference

[Documentation](../docs/README.md) · [Development](README.md) · [Devices](../docs/workflows/device-onboarding.md)

Missing measurements mean unavailable, not zero. Never treat browser progress or
elapsed time as GPU utilization. CPU/GPU results have no combined score.

- [Presets](#presets-and-xr-timing)
- [Timing scopes](#timing-boundaries)
- [Validity](#validity-and-comparisons)
- [Calibration](#calibration)
- [Estimates](#cost-model-and-estimates)

## Presets and XR timing

| Preset | Isolated tests | XR baseline/load pairs per case | Full-suite XR window time |
| --- | --- | --- | --- |
| Smoke | 5 warmups, 20 samples | 2 pairs, 5 seconds/window | 21 minutes |
| Standard | 20 warmups, 100 samples | 4 pairs, 15 seconds/window | 126 minutes |
| Sustained | 20 warmups/case; all cases round-robin at 10 Hz for 10 minutes | 4 pairs, 75 seconds/window | 630 minutes |

XR totals cover 63 cases, excluding setup/warmup. Load runs at 10 Hz with alternating
baseline/load order and no catch-up bursts. The deadline is
`max(30 minutes, 125% of planned XR window time + 5 minutes)`; isolated runs allow
30 minutes. Warmup and correctness checks are outside measured samples.

Hosted releases use Standard with automatic calibration/tracing; the local homepage
uses Smoke. Stats for Nerds allows preset selection; saved sessions keep their settings.

## Timing boundaries

| Field | Definition |
| --- | --- |
| `execution_ms` | Native invocation measured on the device; inclusions depend on backend below |
| `model_ms` | Native forward/compute interval within that invocation |
| `gpu_ms` | Sum of Vulkan operation timestamp intervals, not critical-path latency |
| `gpu_span_ms` | First-to-last GPU timestamps in one verified queue domain |

These timings overlap: don't add them or combine host/GPU clocks. ExecuTorch span
needs one graph execution, Vulkan Context, queue, and query pool. Missing queries,
multiple graphs, reversed timestamps, or wrap make it unavailable. Span uses the
exact device timestamp period; historical `gpu_ms` keeps its definition.

Validation records wall time, CPU comparison, and associated inference separately;
that inference may already be a warmup/sample. XR frame time is
`max(app CPU, app GPU)` from `XR_META_performance_metrics`; dropped frames use
compositor counts. Neither measures motion-to-photon latency.

## Portable execution and resident buffers

| Backend | `execution_ms` | `model_ms` |
| --- | --- | --- |
| `executorch_vulkan` | JNI input staging, native forward, output copy | Forward, including delegate staging and submit/wait |
| `executorch_cpu` | JNI input staging, CPU forward, output copy | CPU forward |
| `vulkan_resident` | JNI dispatch, encoding, submission, completion wait, enabled timestamp extraction | Ends after completion wait |
| `cpu_resident` | JNI invocation and CPU matmul | CPU compute |

Resident matmul prepares inputs/pipelines before warmup and reads outputs outside
samples. Buffers persist, using host-visible/coherent memory with device-local
preferred; reports record properties and bytes. This doesn't imply dedicated VRAM.
Limits: 2D FP32 matmul, isolated Smoke/Standard, 64 MiB.

Vulkan's 15-second submission timeout cannot force safe driver cleanup.
Cancellation is checked between submissions. CPU runs use one calling thread,
without Vulkan initialization, calibration, or GPU metrics.

## Validity and comparisons

Wrong outputs, interrupted samples, thermal/charging changes, or XR focus loss
invalidate measurements. Keep raw samples but withhold accepted summaries and
performance claims. Severe thermal or XR focus/refresh loss stops the run and saves partials.

Compare matching cases, suite, backend, scope, mode, preset, GPU/driver, OS,
precision, stock variant, and fixture seed; XR also needs matching scene settings.
Keep native observations separate from inferred identity. Isolated and XR comparisons
have separate scopes.

## Calibration

Automatic FP32 calibration uses a matching private profile or creates one.
**Recalibrate & run** replaces it. Matching requires GPU/driver, OS, precision, mode,
runner/calibration revisions, and thermal/charging conditions. Failure leaves ordinary
benchmarks available; condition mismatches suppress estimates.

Calibration allows 15 seconds and 64 MiB, using two 16 MiB host-coherent buffers,
three warmups, and nine samples per reference. References are FP32 FMA loops,
a 16 MiB copy, and minimal dispatch, validated between units. Same-queue timestamps
use device period and valid-bit width. A stalled driver cannot be preempted.
These are measured references, not vendor peaks or DRAM bandwidth.

## Cost model and estimates

Cost model v1 covers FP32 linear, matmul/bmm, convolution/depthwise convolution,
add/multiply, sum/mean, transpose-copy, and concatenate. FMA counts as two operations,
bias as one, sum as N−1 per reduction, and mean adds one division. Logical bytes count
inputs (including broadcasts), weights/bias, and outputs once at four bytes per element.
Cache traffic, padding, fusion, and extra backend work are excluded; unsupported models get no estimate.

- Reference duration = max(compute work / calibrated compute rate, logical bytes /
  calibrated copy rate, dispatch count × calibrated dispatch duration).
- Calibration-relative efficiency = reference / verified common-queue GPU span × 100.
- Estimated gap = max(0, observed − reference).
- The largest reference term is the limiting factor; achieved throughput uses the
  same logical work and GPU span.

Keep values above 100% as “exceeds calibration.” Estimates aren't utilization,
guaranteed speedups, or application FPS.

## Kernel evidence extension

Trace reports record dispatch timing, shader hashes, workgroups, and build identity.
Latency profiling disables GPU queries. Comparisons resample whole pair-level
median reductions; wrong outputs, missing arms, or changed conditions suppress results.
`incorrect`/`slower` controls cannot earn improvement labels. See
[comparison rules](README.md#compare-and-interpret) and
[required hardware evidence](../docs/workflows/device-onboarding.md#qualify-hardware).
