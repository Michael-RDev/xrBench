# Measurement definitions, version 1

Reports remain schema version 2. Optional `diagnostics`, `calibration` and per-row
`validation_observations` extend that envelope; old reports remain readable.
Missing historical fields are unavailable, not zero. The JSON database storage
requires no migration.

New public browser releases use the Standard preset for all 63 GPU cases: 20
warmups and 100 measured samples per successful case, with automatic calibration
and dispatch tracing. Warmups and validation are outside the measured samples.
Saved Smoke sessions preserve their original 5 warmups and 20 samples. The local
development application keeps its existing preset controls.

## Timing boundaries

The Android `TimingLedger` partitions one monotonic `System.nanoTime()` clock into
preparation, calibration, warmup, measurement, validation, pacing, reporting and
other stages. Their sum equals session wall time through the snapshot instant.
Entering a nested stage suspends its parent, preventing double counting. Reporting
includes prior serialization/publishing work; each snapshot excludes its own
remaining serialization and write. This is explicitly labeled in the report.

Preparation includes asset/model loading and loading reference outputs. Independent
NumPy references are generated during export, not recomputed by the Android CPU.
Correctness observations record actual validation wall time (shape lookup and
comparison), pure CPU comparison time, and associated inference time separately.
Associated inference can be the last measured sample or a warmup; it must not be
added to the validation stage. The slowest-check table sorts validation wall time.

`execution_ms` includes JNI input staging, native forward and output copying.
`model_ms` is native forward; delegate staging and submit/wait traces are nested.
`gpu_ms` retains the sum of Vulkan operation timestamp intervals. It is not
critical-path latency. `gpu_span_ms` spans the first to last operation timestamp
only when the pinned delegate emits exactly one graph execution: that graph owns
one Vulkan Context, queue and query pool. Multiple graph executions, missing
queries, reversed timestamps and timestamp wrap leave the span unavailable. The new span corrects the pinned delegate’s integer-rounded timestamp period to the device’s actual period; the historical interval sum is retained unchanged. No host timestamps
are combined with GPU timestamps. See the [Vulkan timestamp requirements](https://docs.vulkan.org/samples/latest/samples/api/timestamp_queries/README.html).

Controller report transfer and PostgreSQL submission use a separate host monotonic
clock. They are shown separately and never added to headset session stages.
Correctness failures, interrupted samples, thermal/charging transitions and XR
focus loss remain attached to the affected rows. Invalid observations cannot
produce efficiency estimates or performance deltas. XR baseline/load comparisons
remain separate from isolated-workload estimates.
Browser and Python summaries also check recorded sample conditions for transitions
even when a row-level flag is absent. Invalid rows retain raw samples but have no
accepted timing distributions; a GPU span without a verified timestamp domain is
unavailable.

## Calibration

An automatic FP32 calibration runs before a GPU benchmark when no compatible local
profile exists. Recalibrate & run forces replacement. The app-private cache belongs
to one physical headset and matches GPU, driver, OS build, precision, mode, runner
revision, calibration version, thermal state and charging state. Cache failures or
unsupported calibration leave ordinary benchmarking available.

Native calibration has a 15-second work deadline, checks cancellation and thermal/
charging changes between work units, and bounds buffer allocations to 64 MiB.
It uses two 16 MiB host-coherent storage buffers (device-local memory preferred),
three warmups and nine samples per reference. Output is cleared and validated for
each dispatch. Submitted Vulkan work must finish before resources can be released;
a driver stall cannot be preempted by the application, as with ordinary inference.

The references are FP32 vector FMA loops, a 16 MiB streaming copy, and a minimal
single dispatch. Same-queue top/bottom GPU timestamps use the device timestamp
period and valid-bit width. Profiles retain samples, medians, scratch size, elapsed
calibration work, conditions and provenance. These are empirical references, not
vendor peak throughput or measured DRAM bandwidth. A thermal/charging mismatch
suppresses estimates even when ordinary measurements remain valid.

## Cost model and estimates

Cost model v1 supports FP32 linear, matmul/bmm, convolution/depthwise convolution,
add/multiply, sum/mean, transpose-copy and concatenate. FMA counts as two operations;
bias addition counts once. Sum costs N−1 per reduction and mean adds one division.
Logical bytes count input, weight/bias and output elements once at four bytes each;
broadcast inputs are counted once. This deliberately excludes undocumented cache
traffic, padding, fusion and backend-specific extra work. Unsupported models remain
unavailable.

Following the [Roofline approach](https://docs.nersc.gov/tools/performance/roofline/),
the estimated reference duration is the maximum of compute work / calibrated
compute rate, logical bytes / calibrated copy rate, and dispatch count × calibrated
dispatch duration. Calibration-relative efficiency is reference / observed common-
queue GPU span × 100. Estimated gap is max(0, observed − reference). The limiting
factor is the largest reference term. Achieved throughput uses the same logical
work and GPU span. Values above 100% are preserved as “exceeds calibration.” These
are estimates, not hardware utilization, guaranteed speedups or application FPS.

Comparisons require the exact same selected case list, measurement scope, suite,
mode, preset, GPU/driver, OS, precision and stock variant. Individual measurements
also require correctness, valid timings and matching recorded conditions. No new
kernel variants or browser backend are implied by these estimates.

## Kernel evidence extension

Optional per-sample `dispatches` retain corrected start/end timestamps, duration,
shader name, dispatch/operator identity and global/local workgroup dimensions.
Historical `gpu_ms` remains unchanged; new dispatch durations use `timestamp_scale`.
The report `shaders` map resolves used names to source paths and SPIR-V hashes.
`kernel_build` records compiler identity, requested flags and candidate source identity.

Optional `experiment` schedules balanced randomized pairs within one native run.
Rows keep unique trial IDs plus `pair_index`, `position`, `arm` and `variant`.
`kernel_comparison` resamples complete pair-level median percentage reductions,
not individual samples. Failed correctness, missing arms or changed conditions
suppress results. Intervals are exploratory; no automatic deployment or proof of
application speedup follows. Latency profiling disables GPU queries and leaves
GPU metrics unavailable. The measured primary scope still includes JNI staging.


## Portable execution and resident buffers

Reports identify the backend and actual execution target. Device/GPU identity is
measured on the connected Android processor; form factor is user-declared or unknown.
Capability checks do not validate timing or correctness. Unsupported backends fail
explicitly; missing timestamps leave GPU measurements unavailable.

`vulkan_resident` uses persistent host-coherent Vulkan buffers (device-local preferred).
Inputs are uploaded before warmup. Correctness readback occurs outside samples.
`execution_ms` includes JNI dispatch, command encoding, submission, completion wait,
and timestamp extraction when enabled. `model_ms` ends after the completion wait.
`gpu_span_ms` covers one dispatch sequence in a common queue timestamp domain;
`gpu_ms` sums dispatch intervals. The buffers' memory properties and byte count are
recorded. This measures a separate matmul implementation, not an ExecuTorch graph
with transfers subtracted from its timing. Cross-backend comparisons are withheld.

`executorch_cpu` uses backend-specific exports and native portable ExecuTorch kernels.
Its `execution_ms` includes JNI input staging, CPU forward and output copy;
`model_ms` covers CPU forward. `cpu_resident` uses the FP32 scalar baseline or ARM
NEON candidate in xrGPU’s `kernels/benchmark/cpu/matmul.h`, on one calling thread with persistent
buffers. Its invocation includes JNI and CPU matmul; output copying for correctness
is outside measured samples. CPU samples record kernel source/name, variant and
thread policy. GPU timestamps, dispatch counts and GPU efficiency are unavailable,
not zero. CPU tests never initialize or calibrate Vulkan.

The CPU shared library links independently of Vulkan and OpenXR. Its runtime symbols
bind locally so both libraries can be loaded in the same process. Reports retain
native SoC properties; the controller adds a catalog identity with reported/inferred
evidence while preserving the original native report and its digest.

CPU/GPU benchmark sessions persist ordered child IDs and cancellation. Final native
reports must be recovered and validated before advancing. CPU and GPU results are
always separate; there is no aggregate score. Live completion is measured case
progress, not GPU utilization. Frontend elapsed time is a presentation clock and
never replaces device measurement timestamps.

The development suite records fixture seeds. Paired arms must use the same seed;
cross-run comparison also checks it. Intentional `incorrect` and `slower` controls
are tagged and cannot receive an improvement label. The qualification assessor
requires complete matching evidence and reports validation separately from speedup.
See `docs/PORTABILITY.md` for scope and hardware gates.
