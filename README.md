# xrBench

![xrBench — Native GPU benchmarking for XR devices.](docs/assets/readme-banner.svg)

xrBench runs 63 GPU tests on XR devices to measure execution time and check
numerical correctness. The browser controls the native runner and displays results.

## Getting started

### Run a benchmark

Open a packaged xrBench site in desktop Chrome or Edge. Enable developer mode and
USB debugging on your device, connect a USB data cable, and select **Run**. Approve
USB access in the browser and debugging inside the headset.

Keep the headset awake with the runner visible. Use **Download results JSON** to
save a copy; finished reports are also sent to the site's configured receiver.
See [device requirements](docs/workflows/device-onboarding.md) and [connection help](src/bench_web/README.md#browser-distribution).

### Develop locally

Install Python 3.11–3.13, uv, and Docker with its CLI, then run:

```sh
uv sync --locked --group dev
uv run python main.py --developer
```

This starts PostgreSQL and opens [localhost:8766](http://localhost:8766/).
To run workloads, also prepare ADB and the [native runner](engine/README.md).
Building requires read access to [xrGPU](docs/kernel-development.md).

## Project structure

```text
src/       Browser interfaces
engine/    Native runner, device control, and reports
layers/    Workloads and correctness references
server/    Report receiver
scripts/   Packaging and verification
docs/      Guides and workflows
```

## Documentation

[Browse the guides](docs/) for development, browser releases, measurements,
and device qualification.
