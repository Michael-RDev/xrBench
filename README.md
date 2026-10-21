# xrBench

The public homepage can connect directly to an Android headset from desktop Chrome
or Edge over USB. Press **Run**, select the headset, and allow USB debugging; the
website installs its verified prebuilt runner and runs the standard CPU then GPU
suite. Visitors do not install a local service or build tools. Headset developer
mode is required, and some computer/device combinations need USB drivers.

`npm run build` produces the deployable HTTPS website in `dist/`, **after** the
signed browser runner has been built. See [browser distribution](src/bench_web/README.md#browser-distribution)
for the CI workflow, signing requirements, and hardware qualification checklist.
The repository does not include a prebuilt production APK.

The local website lives in [`src/`](src/README.md), native execution and tooling in
[`engine/`](engine/MEASUREMENTS.md), and workload definitions and catalogs in
[`layers/`](layers/README.md). Custom shaders live in [`kernels/`](kernels/README.md).

After packaging, `uv run python -m src` serves the browser homepage at
`http://localhost:8765/` without a database or desktop ADB connection. This is a
local preview of the same static files that can be deployed over HTTPS.

For the local developer application, launch with `uv run python -m src --developer`
(port 8766). The existing `python -m engine` entry point and local APIs remain available. See the
[setup instructions](layers/README.md) for dependencies and device configuration.

Exports and the APK are generated in `engine/.build/`. Local reports, device profiles,
caches, and the signing key live in `engine/.state/`; database configuration is in
`engine/.env`. These generated files stay local and are ignored by Git.

Homepage Run performs the standard CPU suite followed by the GPU suite, with native
progress displayed in an accessible HTML dashboard. See the
[benchmark dashboard and session API](src/bench_web/README.md) and
[device recognition versus execution support](kernels/PORTABILITY.md). Apple Metal
and physical qualification on two Android XR devices remain separate milestones.

