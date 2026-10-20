# Benchmark app

- [bench_web](bench_web/README.md): Three.js benchmark interface at `/`.
- [stats_for_nerds](stats_for_nerds/README.md): detailed evidence at `/stats/`.
- [layers](../layers/README.md): workload definitions, catalogs, and setup instructions.
- [engine](../engine/MEASUREMENTS.md): native execution, measurement definitions, and the shared HTTP API.

Use the repository-root uv environment and launch with `uv run python -m src`.
See [setup and architecture](../README.md). Generated bundles, APK, credentials,
signing key, and saved reports live under the root-level `engine/` directory and
are ignored by Git.
