# Developing kernels with headset evidence

The development loop is **computer → headset → report → kernel edit → rebuild → headset comparison**.
You can author and compile kernels without a connected device. USB authorization is
needed to install and execute the benchmark. Performance claims require measurements
on the intended GPU and driver; device registration does not generate kernels.

## What to write where

| Part | Location | Purpose |
| --- | --- | --- |
| Workload definition | `layers/<operation>/layer.py` | PyTorch operation, independent NumPy reference, deterministic inputs |
| Shapes and parameters | `layers/catalog.json` | What the benchmark tests |
| GPU implementation | `kernels/matmul/*.glsl` | Editable GLSL compute shader templates compiled to SPIR-V |
| Matmul tile choice | `kernels/matmul/config.json` | `tile_m` of 1, 2 or 4; drives both shader generation and native dispatch |
| Runtime integration | `layers/engine/kernel_hooks.py` | Exact hooks into the pinned ExecuTorch source |
| Scheduling and validation | `layers/engine/android/.../Runner.java` | Run paired arms and reject incorrect outputs |
| Evidence analysis | `layers/engine/experiments.py` | Per-dispatch summaries and paired-block comparisons |

The Python files describe the layer. They are not the headset GPU implementation.
The native C++ runner launches Vulkan work; Python on the development computer
exports workloads, builds the APK, controls the device and analyzes reports.

## First supported experiment

Stock execution still covers all 63 cases. Kernel experiments currently select
`matmul_rectangular` and `matmul_odd`, using the existing FP32 fixtures and independent
NumPy references. The candidate uses a separate shader namespace, copied from the
pinned upstream matmul templates, with a configurable row tile (initially 2).
This is an experimental implementation, not a claim that tile 2 is faster.
Both variants coexist in one APK. Stock sources remain unchanged except for the
declared tracing and candidate selection hooks; unknown dependency edits fail verification.

1. Prepare the existing toolchain and export bundle following `layers/README.md`.
2. Install the declared hooks and build:

   ```sh
   uv run python -c 'from layers.engine.prepare import install_kernel_hooks, verify; install_kernel_hooks(); verify()'
   uv run --extra export --group dev python -m layers.engine.build
   ```

3. Launch (or restart) the local website, connect the headset by USB, authorize debugging, and
   open **Stats for Nerds**. Run a stock matmul benchmark first.
4. Select **Stock vs stock · noise check**, **Standard**, and **Dispatch trace**.
   Run, save the report, and inspect timing stability.
5. Select **Stock vs candidate · matmul**. The runner preloads both implementations,
   warms each arm, and executes ten pairs per case (six in Smoke). Pair order is
   balanced and randomized using a seed recorded in the report. Each arm uses the
   same inputs and correctness reference.
6. Inspect **Kernel experiment** and expand a case to see individual shaders,
   dispatch timings, workgroup dimensions and source locations. The report contains
   SPIR-V hashes, compiler identity, requested flags, source revision and GPU/driver.
7. Repeat the noise check and candidate comparison with **Latency · GPU timestamps
   off**. This disables GPU query instrumentation; host/runtime overhead remains.
   GPU metrics are unavailable in that mode. This helps assess the effect of tracing.
8. Edit the candidate GLSL or `config.json`, rebuild, and repeat. Shader-only edits
   do not require re-exporting the Python workloads. Changing a Python layer does.

The native runner fails a candidate that never selects its declared shader path.
Correctness failure, missing pairs, interrupted measurements, or thermal/charging
transitions suppress performance claims. All planned pairs are required; unfavorable
pairs are not silently discarded. Continuous mode is disabled for experiments.

## Returning evidence to the development computer

When the headset is attached to your computer, the existing ADB controller retrieves
reports into `layers/.state/runs/` and submits them to your local database. An
app-installation UUID allows reports from the same installation to be correlated;
USB serials are excluded from reports. Reinstalling after clearing app data changes
that UUID, and it is not hardware attestation.

When someone runs on their own computer, they can use **Download diagnostics** and
return that JSON file. Analyze it without a headset or database:

```sh
uv run python -m layers.engine.evidence returned-report.json --output analysis.json
```

This validates the envelope and recomputes summaries from raw samples, preserves
the original, and records its SHA-256. It does not authenticate a third-party
device claim. Reports stay local; this version has no internet collector or automatic
kernel deployment to other users' devices.

## What proves the loop works

- A real headset passes stock correctness and produces interpretable dispatch traces.
- A/A trials establish ordinary noise; they are always labeled as noise controls.
- An intentionally incorrect development kernel fails correctness and cannot receive
  a speedup claim. Automated fixtures exercise this rejection path, but do not replace
  the on-device check.
- A correct deliberately slower kernel produces a slowdown on the headset.
- A candidate improvement is reproduced across at least three separate sessions and
  then in the intended application. A single-session result remains exploratory.

The primary metric is total invocation latency, including JNI staging and output
copy. Analysis resamples whole pair-level median reductions, using 2,000 deterministic
bootstrap draws for an exploratory 95% interval. At least 5% mean improvement with
the interval above zero is labeled **improvement observed**. p95 changes are also
reported. This is not automatic promotion: thermal autocorrelation, repeated tuning,
multiple comparisons and application impact still require investigation.

## Remaining hardware and workload work

No physical headset was connected during implementation. The APK and software checks
can be validated on the computer; device correctness, noise, instrumentation overhead
and speedup remain unmeasured. The first candidate retains a fixed deterministic
fixture per case. More seeds, a kernel-only resident-buffer execution mode, hardware
counters/clock telemetry where supported, FP16/quantized cases, and representative
model segments remain follow-on work. The current synthetic XR scene continues to
measure stock workload interference; paired candidate experiments are isolated only.
