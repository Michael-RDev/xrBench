# Benchmark app

- [bench_web](bench_web/README.md): native benchmark homepage and browser USB distribution.
- [stats_for_nerds](stats_for_nerds/README.md): detailed evidence at `/stats/`.
- [layers](../layers/README.md): workload definitions, catalogs, and setup instructions.
- [engine](../engine/MEASUREMENTS.md): native execution, measurement definitions, and the shared HTTP API.

For the hosted homepage, build the signed runner and run `npm run build`; deploy
`dist/` over HTTPS. Visitors need only a supported browser and an authorized USB
headset. For the local developer application, use the repository-root uv environment
and launch with `uv run python main.py --developer` on port 8766. This command starts Docker,
PostgreSQL, and pending SQL migrations automatically. See [engine startup](../engine/README.md).
`uv run python main.py` and `uv run python -m src` serve the packaged browser
homepage on port 8765 with local JSON capture, without Docker or database setup.
See [setup and architecture](../README.md). Generated bundles, APK, credentials,
signing key, and saved reports live under the root-level `engine/` directory and
are ignored by Git.
