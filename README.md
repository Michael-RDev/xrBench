# xrBench

The public homepage can connect directly to an Android headset from desktop Chrome
or Edge over USB. Press **Run**, select the headset, and allow USB debugging; the
website installs its verified prebuilt runner and runs the standard GPU
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
local preview of the same website that can be deployed over HTTPS. Finished native
reports send automatically to the website's `POST /api/reports` endpoint. During
local testing, the preview saves each report to
`engine/.state/submitted-reports/<run-id>.json`. No extra upload button is needed;
failed deliveries retry from browser storage. Demo results are never submitted.
An HTTPS deployment needs a same-origin receiver implementing this endpoint; a
static file host alone cannot collect reports. See the
[report delivery contract](src/bench_web/README.md#automatic-report-delivery).

For the local developer application, run **`uv run python main.py`**. It starts
Docker Desktop if needed on macOS/Windows, starts PostgreSQL, applies pending SQL,
and opens the website at `http://localhost:8766/`. Docker must already be installed;
Linux requires its Docker engine running. A fresh setup gets a private
`engine/.env`; existing credentials and database reports are preserved.
See [engine startup](engine/README.md) for options and the manual alternative,
and [native setup](layers/README.md) for device/build requirements.

Exports and the APK are generated in `engine/.build/`. Local reports, device profiles,
caches, and the signing key live in `engine/.state/`; database configuration is in
`engine/.env`. These generated files stay local and are ignored by Git.

Homepage Run performs the standard 63-case GPU suite, with native
progress displayed in an accessible HTML dashboard. See the
[benchmark dashboard and session API](src/bench_web/README.md) and
[device recognition versus execution support](kernels/PORTABILITY.md). Apple Metal
and physical qualification on two Android XR devices remain separate milestones.

