# Native XR benchmarks

- [layers](layers/README.md): 25 layer scripts, 63 cases, shared native engine and HTTP API.
- [bench_web](bench_web/README.md): Three.js benchmark interface at `/`.
- [stats_for_nerds](stats_for_nerds/README.md): detailed evidence at `/stats/`.

Use the repository-root uv environment and launch with `uv run python -m benchmarks`.
See [setup and architecture](../README.md). Generated bundles, APK, credentials,
signing key, and saved reports remain under `layers/` and are ignored by Git.
