# Documentation

[Project overview](../README.md)

Choose a guide by what you need to do:

| Guide | Covers |
| --- | --- |
| [Development](../engine/README.md) | Local setup, native builds, dashboard, workloads, kernels, API, and tests |
| [Browser releases](../src/bench_web/README.md) | Connecting, recovery, signing, hosting, and report delivery |
| [Measurements](../engine/MEASUREMENTS.md) | Presets, timing boundaries, calibration, and comparison rules |
| [Testing](../testing.md) | Recorded native pipeline verification, evidence, rerun commands, and pending hardware gates |
| [Devices](workflows/device-onboarding.md) | Requirements, backends, hardware qualification, and onboarding |

## Common tasks

- [Start the local app](../engine/README.md#start-the-app)
- [Build the native runner](../engine/README.md#build-the-native-app)
- [Compare kernels](../engine/README.md#kernel-experiments)
- [Package a browser release](../src/bench_web/README.md#build-and-sign-maintainers-only)
- [Recover a qualification run](workflows/device-onboarding.md#recover-an-interrupted-run)
- [Onboard a device](workflows/device-onboarding.md#onboard-a-device)

## Inference-package qualification

Use the [native model pipeline](native-pipeline.md) to prepare package-bound
Hugging Face models, build a qualified release, and verify production inference.

Use the separate [inference-package workflow](model-qualification.md) to qualify
an xrGPU package across operators, complete models, and sustained XR sessions.
It has its own campaign runner and evidence requirements; the matmul procedure
in the device guide is a separate workflow.
