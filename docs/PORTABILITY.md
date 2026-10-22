# Device support and hardware validation

xrBench now separates workload definitions, execution backends, Android transport,
and report analysis. The standard suite remains 63 cases across 25 operations.
The optional development suite adds 17 cases: 12 matmul shape/seed combinations,
three synthetic residual MLP segments, and two attention fixtures with new seeds.
These are synthetic workloads, not traces captured from a production XR app.

## Devices and capabilities

Any authorized Android 10+ arm64 device can be inspected. Product names do not
establish compute compatibility. In Stats for Nerds, **Inspect capabilities** installs
the current APK and collects a profile without loading the layer suite, calibrating,
or starting an XR session. **Download device profile** exports it without the USB
serial. Running a benchmark independently checks capabilities again on the device.

The stages are:

- `probe_required`: Android/ABI requirements met; native GPU requirements unknown.
- `unsupported`: the selected backend cannot meet its native requirements.
- `compatible_unvalidated`: native requirements met; actual workload correctness,
  measurement behavior, and performance still need testing.
- `validated`: the hardware qualification assessment passed for its recorded
  device installation, build, backend, FP32 matmul case set, and measurement scopes.
  This status is an assessment artifact, not a permanent product-name allowlist.

Profiles include GPU/vendor/device IDs, driver and OS, Vulkan version, extensions,
queue/timestamp support, workgroup and storage limits, and an app-installation ID.
They are self-reported evidence, not hardware attestation. Reinstalling after clearing
app data changes the installation ID. A driver/build change requires new validation.

`engine/device-catalog.json` stores recognized product aliases, chip families,
physical locations and vendor references. It includes Qualcomm XR/AR, Apple Vision
and XREAL families. Android discovery retains product/device and SoC properties;
native CPU and GPU probes establish backend eligibility separately. A catalog match
records inference, not measured compatibility. Conflicts use an unknown schematic.
No catalog entry is marked physically validated by this implementation.

The current probe conservatively rejects multiple Vulkan physical devices rather
than attributing ExecuTorch's chosen device to another runner. Vulkan 1.1 and a
compute queue are required. ExecuTorch additionally checks the FP32 texture format;
individual operators can still fail if their particular requirements are unmet.
Resident matmul checks buffer/workgroup limits and host-coherent allocation support.
Missing GPU timestamps do not prevent latency measurements, but trace qualification
then stays pending.

The optional XR scene retains its Quest 3, stereo rendering, and 72 Hz requirements.
Isolated compute tests do not start OpenXR and do not require a headset feature flag.
Choose **Connected processor is in** to distinguish headset, glasses, companion phone,
and compute puck. The default is unspecified. The device/GPU identity always describes
where the runner executes; the form factor is a user declaration. A phone result must
not be described as execution on its attached glasses.

## Execution backends

| Backend | Implementation | Measured invocation |
| --- | --- | --- |
| `executorch_cpu` | Backend-specific exports and native ExecuTorch portable CPU kernels | JNI staging, CPU forward, output copy |
| `cpu_resident` | xrGPU FP32 scalar / ARM NEON matmul in `kernels/benchmark/cpu/matmul.h` | JNI invocation and CPU matmul; persistent buffers, output copy outside samples |
| `executorch_vulkan` | Existing PyTorch export, ExecuTorch runtime and GLSL templates | JNI staging, native forward, output copy |
| `vulkan_resident` | xrGPU direct Vulkan FP32 matmul in `kernels/benchmark/resident/matmul.comp` | Dispatch encoding, queue submission and completion wait; persistent buffers |

Resident matmul uploads the two inputs and builds pipelines once per model, before
warmup. Output readback is performed for correctness checks outside measured samples.
Buffers remain allocated across calls; host-visible/coherent memory is used, with
device-local memory preferred and the actual choice recorded. This is not a claim of
dedicated VRAM. The candidate computes two output rows per invocation; baseline
computes one. Both are experimental reference implementations, not optimized libraries.
The GPU interval and host invocation interval remain separate. Timing queries can be
disabled. Comparisons across backends or scopes are rejected.

The Vulkan backends and `cpu_resident` support paired matmul experiments. Resident mode currently accepts only
2D FP32 matmul, isolated Smoke/Standard runs, and at most 64 MiB of persistent buffers.
The native per-submission timeout is 15 seconds; safely releasing an in-flight Vulkan
submission still depends on the driver completing it. Normal cancellation is checked
between submissions. No CPU fallback is silently substituted.

The CPU implementations use one calling thread; no GPU initialization, calibration,
dispatch trace or GPU measurement is substituted. CPU export readiness is independent
of Vulkan operator coverage. The full CPU suite and custom resident experiments use
the same deterministic fixtures, NumPy references and correctness tolerances.

The Python registry lives in `engine/backends/`; export lowering is backend-specific.
Android's `ComputeBackend` interface separates the scheduler from
native execution. A new API/platform needs an adapter, compiler/package integration,
transport, and a native implementation. Adding a registry name alone is insufficient.
A Metal or vendor accelerator backend is not implemented by this change.

## Build and inspect

```sh
uv run --extra export --group dev python -m layers.export
uv run python -c 'from engine.prepare import install_kernel_hooks, verify; install_kernel_hooks(); verify()'
uv run --extra export --group dev python -m engine.build
```

Use the existing local website. Select a backend and workload suite in Stats for
Nerds. Selecting resident matmul chooses the matmul operation. Developer validation
controls below are deliberately separate from ordinary benchmark controls.

## Hardware proof, using the owner's own computer

Run one stage at a time, with the device awake. Replace `DEVICE_SERIAL` with the
locally connected ADB device, and choose the actual execution target:

```sh
uv run python -m engine.qualification run \
  --serial DEVICE_SERIAL --target headset --backend executorch_vulkan \
  --stage stock --output engine/.state/hardware-evidence/quest
```

The command uses the development matmul suite (14 cases), Standard settings, and a
30-minute deadline. It saves the capability profile, partial report, original final
report and a separate analysis locally. A disconnect does not authorize a second run;
recover the existing run's report before starting another. The CLI records its job in
`engine/.state/qualification-jobs`; report files remain in the app's private storage.
After reconnecting, retrieve an interrupted run without launching it again:

```sh
uv run python -m engine.qualification recover \
  --serial DEVICE_SERIAL --run-id run-RECORDED_ID \
  --output engine/.state/hardware-evidence/quest
```

Use the complete run ID from `qualification-jobs`. A partial report is preserved,
but the device remains reserved until recovery obtains a finished report.

Complete these stages with the same build, backend, device, case list and target:

1. `--stage stock`: correctness before/after each case, 100 measured samples.
2. `--stage noise --profiling trace`, then `--stage noise --profiling latency`:
   stock/stock noise checks, ten balanced randomized pairs per case.
3. `--stage incorrect`: the candidate shader deliberately produces wrong GPU
   output. Every case must fail correctness and receive no performance claim.
4. `--stage slower --profiling latency`: the comparison executes eight times. Outputs must remain
   correct and the paired interval must show a slowdown beyond 5%.
5. `--stage candidate --profiling trace`: inspect dispatch provenance and correctness.
6. `--stage candidate --profiling latency`: repeat on three separate app runs.
   Allow consistent thermal conditions between runs; repeated tuning is exploratory.

The wrong-output controls use distinct ExecuTorch shader names/hashes, or an explicit
resident shader push constant. Slower controls are artificial repeated-work controls,
not a meaningful alternative algorithm. They never receive an improvement label.

Assess only original final reports (not the `-profile`, `-partial`, or `-analysis`
files), listing each explicitly:

```sh
uv run python -m engine.qualification assess \
  path/to/run-FIRST.json path/to/run-SECOND.json path/to/remaining-reports.json \
  --output engine/.state/qualification.json
```

The assessor recomputes summaries, rejects mixed identities/duplicate runs, and lists
missing gates. It requires three fixture seeds, verified trace provenance, and actual
timestamps-off samples. `validated` means the measurement workflow passed its gates.
`reproduced_improvements` separately lists cases improving in three latency runs;
it can be empty even when validation passes. Neither establishes application speedup.
Original files are preserved and their digests are recorded.

Repeat on a second compatible Android device with the same workload definitions and
analysis commands. Assess each device separately. Its owner can send the original
JSON files and capability profile manually. No collector, automatic upload, or remote
deployment is added. Everything remains local until a user chooses to share files.

## Remaining proof

No XR hardware was connected during implementation. Successful exports, host tests,
shader compilation and APK builds cannot establish physical device support or a speedup.
Quest 3 validation, second-device validation, thermal stability and application impact
remain pending. FP16/quantized tests, hardware counters, production model captures,
non-Android platforms and proprietary accelerators remain future work. Apple CPU/Metal
is the next platform milestone. Host verification passed all 80 CPU exports (63
standard and 17 development), but this does not establish Android device correctness.
