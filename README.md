# xrBench

The local website lives in [`src/`](src/README.md), native execution and tooling in
[`engine/`](engine/MEASUREMENTS.md), and workload definitions and catalogs in
[`layers/`](layers/README.md). Custom shaders live in [`kernels/`](kernels/README.md).

From the repository root, launch with `uv run python -m src`. See the
[setup instructions](layers/README.md) for dependencies and device configuration.

Exports and the APK are generated in `engine/.build/`. Local reports, device profiles,
caches, and the signing key live in `engine/.state/`; database configuration is in
`engine/.env`. These generated files stay local and are ignored by Git.

Homepage Run performs the standard CPU suite followed by the GPU suite, with native
progress displayed in an accessible HTML dashboard. See the
[benchmark dashboard and session API](src/bench_web/README.md) and
[device recognition versus execution support](kernels/PORTABILITY.md). Apple Metal
and physical qualification on two Android XR devices remain separate milestones.

