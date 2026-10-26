# Qualify an xrGPU inference package

xrGPU owns the compiled replacement shaders, integration hooks, inference core,
and qualification contract. xrBench collects original measurements from that
exact package and exports an eligibility record for xrGPU to embed in a library
build. The existing benchmark-source package and legacy matmul qualification CLI
remain separate.

All evidence stays local. Reports are self-reported measurements, not hardware
attestation. Software tests, CPU fallback, desktop execution, and synthetic test
fixtures cannot establish headset correctness or performance.

## Prepare reproducible workloads

First build an immutable optimized package in xrGPU and note its manifest's
`package_sha256`. Both repositories must use the same pinned ExecuTorch revision.
Use the existing project environment:

```sh
cd /Users/michael/Desktop/xrBench
.venv/bin/python -m engine.qualification_fixtures \
  --package /Users/michael/Desktop/xrGPU/artifacts/packages/quest3-automatic-v2 \
  --xrgpu-source /Users/michael/Desktop/xrGPU \
  --output engine/.build/qualification-fixtures-v2
```

This exports the current 80-case development suite and 13 additional GELU,
grouped-convolution packing, and linear-tail cases. Each static bundle has CPU
and Vulkan programs, hashed FP32/INT64 inputs and reference outputs, and six
fixtures generated using fixed weights. NumPy numerical oracles check the layer
fixtures before export; the shared exporter also executes the portable CPU
program against its reference output. The `--case` option supports an initial
small validation run; partial suites cannot pass full qualification.

Discovery uses seeds `20261025`, `20261026`, `20261027`. Final validation uses
`20261125`, `20261126`, `20261127`. The pretrained MiniLM bundles for lengths
32/128/256 and MobileNetV2 224x224 must contain those six fixtures too; generate
them with xrGPU's `python -m models.export` command.

Obtain the exact physical GPU/driver target from the locally installed benchmark
app's capability probe:

```sh
.venv/bin/python -m engine.qualification campaign probe \
  --serial DEVICE_SERIAL --output engine/.state/quest3-target.json
```

Then prepare an immutable campaign. Pass the actual package digest from its
manifest as `PACKAGE_SHA256`:

```sh
.venv/bin/python -m engine.qualification campaign prepare \
  --package /Users/michael/Desktop/xrGPU/artifacts/packages/quest3-automatic-v2 \
  --package-sha256 PACKAGE_SHA256 \
  --workloads engine/.build/qualification-fixtures-v2/workloads.json \
  --model-bundle /Users/michael/Desktop/xrGPU/artifacts/model-bundles/minilm-32 \
  --model-bundle /Users/michael/Desktop/xrGPU/artifacts/model-bundles/minilm-128 \
  --model-bundle /Users/michael/Desktop/xrGPU/artifacts/model-bundles/minilm-256 \
  --model-bundle /Users/michael/Desktop/xrGPU/artifacts/model-bundles/mobilenetv2 \
  --target engine/.state/quest3-target.json \
  --output engine/.state/quest3-campaign.json
```

The campaign binds package/runtime hashes, each model and fixture inventory,
exact operation descriptors, target identity, measurement protocol, and both
seed sets. Altering a file invalidates the campaign. Model bundles may be
independent of a kernel package; a bundle explicitly requiring a package must
match the campaign package.

## Build and collect native evidence

Build the standalone Android runner and the optional qualification-only OpenXR
APK. The APK shares the same C++ job runner and xrGPU Session library:

```sh
.venv/bin/python -m engine.qualification_build \
  --package /Users/michael/Desktop/xrGPU/artifacts/packages/quest3-automatic-v2 \
  --xrgpu-source /Users/michael/Desktop/xrGPU \
  --output engine/.build/qualification --jobs 2 --xr
```

`--prebuilt-runtime PATH_TO_DEVELOPMENT_LIBXRGPU_RUNTIME_SO` reuses an existing
development core with a matching xrGPU `build.json` inventory. Use the library
in the build's `bin/` directory; a production core, mismatched package, or changed
library bytes are rejected. This avoids a second
ExecuTorch compilation. The default build creates the core from source. Release
libraries reject experiment overrides; qualification requires a development
core.

Use `campaign run` for one workload/variant/fixture/session. Example discovery
measurement:

```sh
.venv/bin/python -m engine.qualification campaign run \
  --campaign engine/.state/quest3-campaign.json \
  --runner engine/.build/qualification/xrgpu-qualification-runner \
  --serial DEVICE_SERIAL --stage screen \
  --workload operator_qualification_linear_tails_bias1 \
  --variant m1_wg8x4_texture2d --seed 20261025 --session discovery-1
```

Screen every declared variant on all three discovery fixtures. Linear has 18
tile/workgroup/weight-storage configurations. `campaign shortlist --campaign
CAMPAIGN --output SHORTLIST SCREEN_REPORTS...` ranks discovery results and freezes
at most three finalists per exact descriptor. Do not use confirmation fixtures
or sessions to create the shortlist.

For each finalist, collect `confirm`, `noise`, `incorrect`, `slower`, and `trace`
reports on all three held-out seeds, supplying `--shortlist SHORTLIST`. Use fresh
session IDs for confirmation; discovery-session reuse fails the holdout gate.
Use at least three independent hardware sessions for confirmation. The baseline
and comparison both receive the same necessary correctness repairs. Noise uses
identical repaired programs; the slower control executes extra candidate
forwards. The incorrect-output control perturbs a real candidate output and
checks that numerical comparison rejects it; it is explicitly labelled
`candidate_output_perturbation` in the original evidence.

All ordinary measurements use 10 randomized, balanced pairs, 20 warmups and 100
samples per arm, with correctness before and after each arm. Thermal and charging
conditions must remain stable. Dispatch traces identify replacement binary hashes
and retain stock dispatch identities; traces are collected in separate runs from
latency measurements.

After operator confirmation and controls, freeze the exact rules for full-suite
and whole-model validation:

```sh
.venv/bin/python -m engine.qualification campaign candidates \
  --campaign engine/.state/quest3-campaign.json \
  --shortlist engine/.state/quest3-shortlist.json \
  --output engine/.state/quest3-candidate-rules.json REPORTS...
```

Run `correctness` for every regression workload on the held-out fixtures with
`--rules engine/.state/quest3-candidate-rules.json`. The global correctness gate
requires the same frozen rule digest; repaired-stock-only runs do not substitute
for full-suite candidate validation. Then run `model` for each of the four static pretrained model workloads using `--rules
engine/.state/quest3-candidate-rules.json`: this file is the candidate rule array (exact descriptors,
variants, and rule kinds) for the final application evaluation. Development
Session options apply those exact rules; the model bundle cannot inject
production eligibility. Reports preserve the candidate rule-set digest.

`--stage sustained` automatically selects the OpenXR APK. Use it for every model
workload and final candidate rule set, with all held-out seeds across three fresh
sessions. Each randomized baseline/comparison arm lasts at least ten minutes at
focused 72 Hz. The runner records actual frame times, measured arm durations and
compositor missed-frame counters. Headset focus loss, unsupported telemetry,
severe thermal state, or CPU fallback stops qualification. Standalone shell
execution cannot synthesize XR frame evidence.

Jobs and transport metadata are saved before launch. Original terminal report
bytes are immutable. Use `campaign recover TRANSPORT_JSON --output DIRECTORY` to
recover a disconnected or interrupted run without launching it again. Partial
reports remain diagnostic and cannot qualify a kernel.

## Assess and hand records back to xrGPU

```sh
.venv/bin/python -m engine.qualification campaign assess \
  --campaign engine/.state/quest3-campaign.json \
  --shortlist engine/.state/quest3-shortlist.json \
  --output engine/.state/quest3-assessment.json REPORTS...

.venv/bin/python -m engine.qualification campaign export \
  --campaign engine/.state/quest3-campaign.json \
  --shortlist engine/.state/quest3-shortlist.json \
  --output engine/.state/quest3-qualification.json REPORTS...
```

Assessment recomputes statistics from raw paired samples and reports every
missing gate. Confirmation needs at least a 5% invocation-latency reduction, a
95% pair-bootstrap interval above zero, and p95 regression no greater than 5%.
Every model workload needs correctness and median/p95 regression no greater than
5%, with at least one repeatable 10% median model improvement. Sustained XR frame
p95 must remain within 5%, with missed-frame increase at most one percentage
point. Repeated model lengths cannot substitute for each other's evidence.

The shared xrGPU contract seals a version-2 qualification record containing
only eligible rules. Correctness repairs require all base hardware, provenance,
correctness, control and holdout gates. Performance replacements additionally
require invocation, full-model and sustained gates. Pending evidence grants no
selection. Thus repairs can become eligible while performance qualification
remains pending.

Build xrGPU with `XRGPU_KERNEL_PACKAGE_ROOT` and `XRGPU_QUALIFICATION_FILE` pointing
to the verified package and exported record. Its production Session automatically
matches the exact device/GPU/driver/runtime and operation descriptor. Unsupported
requests use eligible stock behavior; a missing required correctness repair
causes whole-model CPU fallback. Changing the target driver or package requires
new matching evidence. Inference does not upload reports or fetch policy.
