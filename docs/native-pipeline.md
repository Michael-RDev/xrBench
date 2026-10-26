# Native model pipeline

`python -m engine.inference` connects exported Hugging Face models, the compiled
xrGPU kernels tested by xrBench, and the production C++/Android Session library.
It uses native ExecuTorch execution on the device. All preparation, artifacts,
and evidence stay local.

The first supported Hugging Face acceptance case is
`sentence-transformers/all-MiniLM-L6-v2`, exported at lengths 32, 128 and 256.
The qualification protocol also includes MobileNetV2 as a vision regression.
The generic model exporter accepts compatible static FP32/INT64 PyTorch modules;
this does not promise support for every architecture on the Hub.

## Prepare exactly the package that will be deployed

Build a new immutable inference kernel package in xrGPU, using its project
environment and pinned toolchain:

```sh
.venv/bin/python -m runtime.kernel_package build --output artifacts/packages/quest3-native
```

Then, from xrBench, prepare model and regression bundles:

```sh
.venv/bin/python -m engine.inference prepare \
  --xrgpu-source ../xrGPU \
  --package ../xrGPU/artifacts/packages/quest3-native \
  --output engine/.state/native-pipeline/quest3
```

This uses cached pinned model weights by default. `--allow-download` permits
downloading the example weights. Alternatively, supply one or more `--bundle`
paths to existing exported models. Existing programs, weights and fixtures are
preserved and rebound to this kernel package in a new directory. Every supplied
model is checked against its CPU reference before publishing the pipeline input.
Missing Vulkan lowering stops preparation.

Use `--fixtures PATH_TO_WORKLOADS_JSON` to reuse verified qualification fixtures
without compiling them again. Their bundles are copied and rebound too. Omit
this option to export the regression fixtures with the current implementation.
Existing output directories are never overwritten.

Preparation writes `pipeline.json`, `workloads.json` and the package-bound
models. It grants no hardware eligibility. Connect the headset, obtain its real
capability identity, and create a campaign:

```sh
.venv/bin/python -m engine.qualification campaign probe \
  --serial DEVICE_SERIAL --output engine/.state/target.json
.venv/bin/python -m engine.inference campaign \
  --pipeline engine/.state/native-pipeline/quest3/pipeline.json \
  --target engine/.state/target.json \
  --output engine/.state/native-pipeline/quest3/campaign.json
```

Follow [model qualification](model-qualification.md) to build the development
runner, screen variants, freeze a shortlist, confirm controls and candidate
rules, and collect regression/model/sustained evidence. Full-model and regression
comparisons use only the exact final candidate rules. Blanket development
repairs cannot hide a missing production kernel path. A model speedup must also
record an actual selection of a final performance kernel.

The separate `kernels/benchmark/` matmul package remains useful for standalone
experiments. To affect deployed models, implement the change in xrGPU's
inference kernels and run this compiled-package qualification path.

## Build the production handoff

Once original hardware reports satisfy the applicable gates:

```sh
.venv/bin/python -m engine.inference release \
  --xrgpu-source ../xrGPU \
  --campaign engine/.state/native-pipeline/quest3/campaign.json \
  --shortlist engine/.state/native-pipeline/quest3/shortlist.json \
  --output engine/.state/native-releases/quest3 REPORTS...
```

Pending qualification stops before a build. A successful release includes the
qualified policy, original evidence, assessment, bound models, native runner,
shared libraries, Android AAR, and a hash inventory. It embeds the verified
SPIR-V bytes from the same package. The build disables development overrides.
`runtime.build --output` keeps the native build and installation in an isolated
directory. The release records production-device acceptance as pending until
the separate acceptance command passes.

## Verify the actual production runtime

```sh
.venv/bin/python -m engine.inference validate \
  --release engine/.state/native-releases/quest3 \
  --serial DEVICE_SERIAL --adb artifacts/toolchain/android-sdk/platform-tools/adb \
  --output engine/.state/native-acceptance/quest3
```

This verifies the release inventory, deploys its native runner and exact shared
libraries, executes every original model fixture, compares outputs, and checks
the Session's package, runtime, policy, device, selected variants and production
build mode. CPU fallback or a missing kernel selection observed during model
qualification fails acceptance. Unaffected regression models may retain stock
Vulkan paths; at least one deployed model must actually select an xrGPU kernel.
Partial acceptance remains marked running; it cannot claim success. Original
Session reports and output bytes remain in the acceptance directory. Acceptance
validates deployment; it does not replace the statistical qualification campaign.

Without a headset, run the same model fixtures through a production host library:

```sh
.venv/bin/python -m engine.inference validate \
  --host-runner ../xrGPU/artifacts/build/host-library/xrgpu_run \
  --bundle engine/.state/native-pipeline/quest3/models/minilm-32 \
  --output engine/.state/native-acceptance/host
```

Host results explicitly record `host_cpu` and cannot qualify GPU kernels.
Applications consume the release AAR or CMake library using `Session.load` and
`forward`; model files cannot inject kernel eligibility.
