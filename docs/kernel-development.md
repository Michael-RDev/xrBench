# Developing xrGPU kernels with xrBench evidence

The development loop is **computer → headset → report → xrGPU kernel edit → pinned dependency update → rebuild → headset comparison**.
Kernel implementations belong to [xrGPU](https://github.com/Michael-RDev/xrGPU),
under `kernels/benchmark/`. xrBench maintains the benchmark harness and integration.
It fetches the exact commit and content digest in `dependencies.json` during builds.
Sources are cached under ignored `artifacts/deps/xrgpu/<commit>/`; never edit that
cache or add kernel implementations to xrBench. The compiled code ships in the APK.

Prepare or verify the external package independently of the Android toolchain:

```sh
uv run python -m engine.kernel_package fetch
uv run python -m engine.kernel_package verify
```

The private repository requires a Git credential helper with read access or an
`XRGPU_READ_TOKEN` environment variable. CI uses a secret of that name with read
access to xrGPU. Credentials are not placed in URLs, caches, APKs, or browser code.
A verified cache works offline; missing or corrupted packages stop the build.
Starting a benchmark checks APK provenance against the dependency pin without
fetching or requiring the source cache. Existing reports remain readable.
You can author and compile kernels without a connected device. USB authorization is
needed to install and execute the benchmark. Performance claims require measurements
on the intended GPU and driver; device registration does not generate kernels.

## What to write where

| Part | Location | Purpose |
| --- | --- | --- |
| Workload definition | `layers/<operation>/layer.py` | PyTorch operation, independent NumPy reference, deterministic inputs |
| Shapes and parameters | `layers/catalog.json` | What the benchmark tests |
| GPU implementation (xrGPU) | `kernels/benchmark/matmul/*.glsl` | GLSL compute shader templates compiled to SPIR-V |
| CPU implementation (xrGPU) | `kernels/benchmark/cpu/matmul.h` | FP32 scalar baseline and ARM NEON candidate with dimension tails |
| Resident GPU and calibration (xrGPU) | `kernels/benchmark/resident/`, `kernels/benchmark/calibration/` | Direct Vulkan matmul and measurement probes |
| Matmul tile choice (xrGPU) | `kernels/benchmark/matmul/config.json` | `tile_m` of 1, 2 or 4; drives both shader generation and native dispatch |
| Runtime integration | `engine/kernel_hooks.py` | Exact hooks into the pinned ExecuTorch source |
| Scheduling and validation | `engine/android/.../Runner.java` | Run paired arms and reject incorrect outputs |
| Evidence analysis | `engine/experiments.py` | Per-dispatch summaries and paired-block comparisons |

The Python files describe the layer. They are not the headset GPU implementation.
The native C++ runners execute portable CPU, custom CPU matmul, or Vulkan work; Python on the development computer
exports workloads, builds the APK, controls the device and analyzes reports.

## First supported experiment

The standard suite covers 63 cases. Its matmul experiments select
`matmul_rectangular` and `matmul_odd`; the optional development suite adds twelve
matmul shape/seed cases and synthetic model segments. All use FP32 fixtures and independent
NumPy references. The candidate uses a separate shader namespace, copied from the
pinned upstream matmul templates, with a configurable row tile (initially 2).
This is an experimental implementation, not a claim that tile 2 is faster.
Both variants coexist in one APK. Stock sources remain unchanged except for the
declared tracing and candidate selection hooks; unknown dependency edits fail verification.

1. Prepare the existing toolchain and export bundle following `layers/README.md`.
2. Install the declared hooks and build:

   ```sh
   uv run python -c 'from engine.prepare import install_kernel_hooks, verify; install_kernel_hooks(); verify()'
   uv run --extra export --group dev python -m engine.build
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
8. Edit the candidate GLSL or `config.json` in xrGPU and update that package's
   manifest file checksums. Commit the package to a reachable xrGPU branch, then
   update xrBench's full commit and content digest in `dependencies.json` together.
   The digest is SHA-256 of the manifest serialized as sorted-key, compact UTF-8
   JSON without a trailing newline. Fetch, rebuild, and repeat. Shader-only changes
   do not require re-exporting the Python workloads. Changing a Python layer does.

Reports retain the package repository, full commit, identifier, content digest,
compiler, and compiled shader hashes. Kernel source locations point to that exact
xrGPU commit. Dependency updates change the runner identity and require a new APK;
the browser continues to download the matched signed runner without GitHub access.

The native runner fails a candidate that never selects its declared shader path.
Correctness failure, missing pairs, interrupted measurements, or thermal/charging
transitions suppress performance claims. All planned pairs are required; unfavorable
pairs are not silently discarded. Continuous mode is disabled for experiments.

## Returning evidence to the development computer

When the headset is attached to your computer, the existing ADB controller retrieves
reports into `engine/.state/runs/` and submits them to your local database. An
app-installation UUID allows reports from the same installation to be correlated;
USB serials are excluded from reports. Reinstalling after clearing app data changes
that UUID, and it is not hardware attestation.

The public browser homepage automatically saves reports in browser storage and
queues finished native reports to its configured receiver. The local preview
captures those reports in `engine/.state/submitted-reports/`; Supabase integration
is deferred. **Download results JSON** also preserves the original native reports
inside its `reports` array. The developer dashboard provides **Download diagnostics**.
Analyze a native report file without a headset or database:

```sh
uv run python -m engine.evidence returned-report.json --output analysis.json
```

This validates the envelope and recomputes summaries from raw samples, preserves
the original, and records its SHA-256. It does not authenticate a third-party
device claim. The default receiver is local; there is no deployed internet collector
or automatic kernel deployment to other users' devices.

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

No physical headset was connected during implementation. Device correctness, noise,
instrumentation overhead and speedup remain unmeasured. An optional direct Vulkan
runner now uses persistent buffers and a development suite provides additional seeds,
shapes and synthetic MLP/attention segments. Hardware counters, FP16/quantized cases,
production model captures and application validation remain follow-on work.

See [Device support and hardware validation](PORTABILITY.md) for capability discovery,
backend selection, memory/timing scopes, negative controls, and the qualification CLI.

## CPU matmul experiments

Choose **CPU resident matmul (scalar / NEON)** in Stats for Nerds. Stock uses the
scalar implementation; candidate uses four-column NEON vectors and scalar tails.
Both run on one calling thread and use the same fixtures, references, tolerances,
randomized pairs and wrong-output/slower controls as GPU experiments. No GPU
calibration or instrumentation runs. This is a reference implementation, not a
claim of optimized performance. The full standard CPU suite uses ExecuTorch
portable operators through `executorch_cpu`, independently of GPU export readiness.
