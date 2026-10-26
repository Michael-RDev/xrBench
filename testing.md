# Testing

## Native inference pipeline verification

This records the completed native-pipeline verification from the xrBench/xrGPU
integration work. These are saved results for the package identified below, not
a claim that subsequent working-tree changes have been tested. Tests were not
rerun for this documentation update.

| Check | Recorded result |
| --- | --- |
| xrBench inference, qualification campaign, and kernel-package tests | 74 passed |
| xrGPU native library, qualification, and integration tests | 28 passed |
| Production native C++ Session model acceptance on the host CPU | 24 fixtures passed |
| Inference shader package compilation | 18 shaders compiled |
| Shader identity in the production Android AAR | All 18 packaged SPIR-V binaries found byte for byte |
| Shader identity in the xrBench qualification APK | All 18 packaged SPIR-V binaries found byte for byte |
| Android production and development native libraries | Built successfully |
| Android release AAR assembly and lint | Passed |
| Native qualification runner and OpenXR qualification APK | Built successfully |
| Ruff checks on changed Python files and `git diff --check` | Passed |

The 102 focused tests include rejection of pending qualification, development
builds in production acceptance, CPU fallback in GPU acceptance, stale package
or policy identities, modified library bytes, and missing expected kernel
selections. Release-handoff tests mock the hardware-assessment/build boundary;
their synthetic fixtures do not qualify a real kernel package.

Full-model and regression qualification now uses the exact final candidate
rules. A claimed model speedup must record selection of a final performance
kernel. Production acceptance preserves the kernel selections observed during
qualification; unaffected regression models may retain stock Vulkan paths.

### Models and numerical checks

The host acceptance run executed six original fixtures for each static bundle:

| Model | Input configuration | Fixtures passed |
| --- | --- | --- |
| `sentence-transformers/all-MiniLM-L6-v2` | Sequence length 32 | 6 |
| `sentence-transformers/all-MiniLM-L6-v2` | Sequence length 128 | 6 |
| `sentence-transformers/all-MiniLM-L6-v2` | Sequence length 256 | 6 |
| `torchvision/mobilenet_v2` | RGB input `[1, 3, 224, 224]` | 6 |

Floating-point outputs were compared with `atol=1e-4` and `rtol=1e-4`; integer
outputs require exact equality. The largest observed absolute output difference
across the 24 model fixtures was `7.152557373046875e-06`. All ran through the
production native `Session` API on the **host CPU**.

The prepared pipeline also contains 93 regression cases and 26 operator
descriptors, for 123 workloads including the four model bundles. Preparation
does not establish that those workloads passed on an XR device.

### Package and evidence

- Kernel package SHA-256:
  `8eac67ee1bc22029d982652ecf12dcbdf1ba01bd3f91be76f004fe9c91bffc1a`.
- Pinned ExecuTorch revision:
  `e4d02f41f7909e8ed5bf4a14ffc520d733453d9f`.
- [Software verification report](engine/.state/native-pipeline/quest3/software-verification.json).
- [Host native acceptance report](engine/.state/native-acceptance/host/validation.json).
- [Prepared pipeline](engine/.state/native-pipeline/quest3/pipeline.json).
- [Saved device inventory](engine/.state/native-pipeline/quest3/adb-devices.txt).
- Build and test logs: `engine/.state/native-pipeline/quest3/verification-logs/`.

These evidence files and generated artifacts live in local, Git-ignored state
and build directories. They may be absent in a fresh checkout. The software
verification report records the tested AAR/APK hashes; rebuilding an artifact
requires checking the new bytes again.

### Rerun the focused tests

From xrBench:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  engine/tests/test_inference.py \
  engine/tests/test_qualification_campaign.py \
  engine/tests/test_kernel_package.py
```

From xrGPU:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_native_library.py \
  tests/test_qualification.py \
  tests/test_integration.py
```

After building the host library against the same package, rerun native model
acceptance from xrBench. Choose a fresh output directory:

```sh
.venv/bin/python -m engine.inference validate \
  --host-runner ../xrGPU/artifacts/build/host-library/xrgpu_run \
  --bundle engine/.state/native-pipeline/quest3/models/minilm-32 \
  --bundle engine/.state/native-pipeline/quest3/models/minilm-128 \
  --bundle engine/.state/native-pipeline/quest3/models/minilm-256 \
  --bundle engine/.state/native-pipeline/quest3/models/mobilenetv2 \
  --output engine/.state/native-acceptance/host-rerun
```

See the [native pipeline guide](docs/native-pipeline.md) for model preparation,
builds, production release assembly, and device acceptance.

### Remaining hardware gates

No authorized Android headset was connected during this verification. Physical
GPU qualification is **blocked**, and the qualified release and production
device acceptance remain **pending**.

The remaining work is to collect real-device discovery/confirmation, numerical
correctness, controls, dispatch traces, complete-model timing, and sustained XR
evidence; export a passing qualification record; then verify the production
library on that device with development overrides disabled. Follow
[model qualification](docs/model-qualification.md).

The recorded host tests, successful builds, and shader-byte checks establish
software behavior and packaging integrity. They do not establish physical-device
support, GPU correctness, or a performance improvement on XR hardware.
