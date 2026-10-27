# Documentation

[Project overview](../README.md)

Choose a guide by what you need to do:

| Guide | Covers |
| --- | --- |
| [Development](../engine/README.md) | Local setup, native builds, dashboard, workloads, kernels, API, and tests |
| [Quest 3 validation](workflows/quest3-validation.md) | Current browser release, physical checks, recovery, and PostgreSQL evidence |
| [Measurements](../engine/MEASUREMENTS.md) | Presets, timing boundaries, calibration, and comparison rules |
| [Testing](../testing.md) | Recorded native pipeline verification, evidence, rerun commands, and pending hardware gates |
| [Devices](workflows/device-onboarding.md) | Requirements, backends, hardware qualification, and onboarding |
| [Security review](security-review.md) | Confirmed hosting risks, fixes, validation, and remaining deployment gates |
| [Deployment readiness](deployment-readiness.md) | Activated standalone relay, live HTTP/PostgreSQL evidence, operational checks, and retained portfolio history |
| [Standalone hosting](standalone-hosting.md) | Canonical xrBench address, private Site identity, root deployment, and retained portfolio history |
| [Receiver hosting](report-receiver-hosting.md) | Trusted admission, upload limits, and hosted report storage |
| [Database hosting](database-hosting.md) | Verified TLS, administrative migrations, and restricted runtime credentials |
| [Local server security](local-server-security.md) | Local HTTP boundaries and safe use of developer/device-control services |

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
