# Development guide

[Documentation](../docs/README.md) · [Browser releases](../src/bench_web/README.md) · [Measurements](MEASUREMENTS.md)

Run commands from the repository root.

- [Setup](#start-the-app)
- [Native build](#build-the-native-app)
- [Dashboard](#stats-for-nerds)
- [Workloads and kernels](#edit-workloads-and-kernels)
- [Reports and API](#api-and-storage)
- [Tests](#tests)

## Start the app

Install Python 3.11–3.13, uv, and Docker with its CLI. On Linux, start Docker first.

```sh
uv sync --locked --group dev
uv run python main.py --developer
```

This starts PostgreSQL, applies migrations, and opens [localhost:8766](http://localhost:8766/).
The launcher starts Docker Desktop on macOS/Windows when needed.
Keep `engine/.env`; existing database credentials must be restored if lost.

| Option | Use |
| --- | --- |
| `--no-open` | Start without opening a browser |
| `--port 9000` | Change the app port |
| `--adb /path/to/adb` | Select platform-tools |
| `XRGPU_DB_PORT` in `engine/.env` | Change PostgreSQL's default port, 5432 |
| `--no-docker` | Use a running database and apply migrations |
| `XRGPU_DATABASE_URL` | Use an external database; skip Docker and local credential creation |

### Manual database setup

For a new database, copy `engine/.env.example` to `engine/.env` and set a password.
Keep existing credentials. With Docker running:

```sh
docker compose --env-file engine/.env -f engine/compose.yaml up -d --wait db
docker compose --env-file engine/.env -f engine/compose.yaml exec -T db psql -U xrgpu -d xrgpu -v ON_ERROR_STOP=1 -f /migrations/001_initial.sql
docker compose --env-file engine/.env -f engine/compose.yaml exec -T db psql -U xrgpu -d xrgpu -v ON_ERROR_STOP=1 -f /migrations/002_execution_backends.sql
uv run python -m src --developer
```

Apply later migrations in numeric order. Direct server commands require a ready database.

### Stop

Ctrl-C stops the server. To stop PostgreSQL while keeping its data:

```sh
docker compose --env-file engine/.env -f engine/compose.yaml stop db
```

`down -v` deletes the database volume. See [browser preview](../src/bench_web/README.md#serve-and-preview)
for the packaged site on port 8765.

## Build the native app

You need JDK 17+ and ADB. Windows also needs Visual Studio 2022's Desktop development
with C++ workload. The commands below prepare the pinned Android/native tools.

### Prepare the kernel package

Give your Git credential helper read access to private xrGPU, or set `XRGPU_READ_TOKEN`.
Keep credentials out of URLs and build artifacts.

```sh
uv run python -m engine.kernel_package fetch
uv run python -m engine.kernel_package verify
```

`dependencies.json` pins the commit and digest. Edit xrGPU itself, not its verified
cache at `artifacts/deps/xrgpu/<commit>/`.

### Compile the APK

```sh
uv sync --extra export --group dev
uv run --extra export --group dev python -m engine.bootstrap_android
uv run --extra export --group dev python -m engine.prepare
uv run --extra export --group dev python -m engine.bootstrap_shaderc
uv run --extra export --group dev python -m layers.export
uv run --extra export --group dev python -m engine.build
```

Keep `engine/.build/xrgpu-benchmarks.apk` and `engine/.build/apk.json` matched.
**Run** installs this developer APK (`org.xrgpu.benchmarks`); hosted releases need
[separate signing](../src/bench_web/README.md#build-and-sign-maintainers-only).
Keep `engine/.state/debug.keystore` when moving the checkout.

### Connect and run

Enable developer mode and USB debugging, connect a data cable, approve debugging,
and select **Run**. Keep the runner visible. Use `--adb /path/to/adb` if discovery
fails; restart after changing it. See [device requirements](../docs/workflows/device-onboarding.md).

After a disconnect, recover the existing run. **Stop** waits for submitted work;
disconnected cancellation takes effect after reconnection.

## Stats for Nerds

Open `/stats/`, inspect capabilities, choose a backend/suite and preset, then
**Run benchmark**. Operation filters accept patterns such as `linear, conv*`;
every pattern must match. XR frame tests require wearing Quest 3.

**Continuous mode** runs once per build/configuration. Closing the page leaves
active work running; **Stop** disables continuous mode and cancels work. Failed
attempts don't auto-retry. **Download diagnostics** saves measurements without
USB serials, tokens, lease owners, cache identities, or host paths.

## Edit workloads and kernels

Standard has 63 cases; Development has 80. Both ship in the APK.
Edit `layers/<operation>/layer.py` and [`layers/catalog.json`](../layers/catalog.json).
Supply a PyTorch `module`, NumPy `reference`, and any `make_weights`/`prepare_inputs`.
Use `rand` for deterministic inputs; references compute in float64, then round to FP32.
Re-export and rebuild after layer changes.

Edit native kernels in xrGPU's `kernels/benchmark/`: `matmul/`, `cpu/matmul.h`,
`resident/`, or `calibration/`. Stock sources allow only declared tracing/selection hooks.

### Kernel experiments

After building the toolchain and exports:

```sh
uv run python -c 'from engine.prepare import install_kernel_hooks, verify; install_kernel_hooks(); verify()'
uv run --extra export --group dev python -m engine.build
```

1. In Stats for Nerds, run stock matmul, then **Stock vs stock · noise check** with
   **Standard** and **Dispatch trace**.
2. Run **Stock vs candidate · matmul**. Check correctness, dispatches, shader hashes,
   and build identity. Standard uses ten balanced randomized pairs; Smoke uses six.
3. Repeat noise and candidate runs with **Latency · GPU timestamps off**.

Keep all planned pairs. Wrong outputs, missing pairs, interruptions, changed
conditions, or wrong shader selection invalidate performance claims.

### Update and rebuild

Commit kernel changes and manifest checksums in xrGPU. Update the full commit and
content digest in `dependencies.json`, then fetch and rebuild. The digest is SHA-256
of sorted-key, compact UTF-8 manifest JSON without a trailing newline.
Shader-only changes don't need new workload exports.

### Compare and interpret

Compare `execution_ms` within matching [timing scopes](MEASUREMENTS.md).
Analysis uses 2,000 pair-bootstrap draws for an exploratory 95% interval.
**Improvement observed** requires at least 5% mean improvement and an interval above zero.
Check p95, thermal effects, repeated tuning, and multiple comparisons. Reproduce
improvement in three distinct sessions, then in the application.
Use the controls and reports in [hardware qualification](../docs/workflows/device-onboarding.md#qualify-hardware).

## API and storage

Local originals are saved as `engine/.state/runs/<run-id>/native-report.json` before
PostgreSQL submission. Database failures queue retries without rerunning workloads;
downloads still work. Reports exclude USB serials, account/email data, camera images,
and pose traces. Hosted uploads use the [browser receiver](../src/bench_web/README.md#automatic-report-delivery).

To inspect an original report without a device or database:

```sh
uv run python -m engine.evidence returned-report.json --output analysis.json
```

For API contracts, see [server routes](server.py), [session recovery](benchmark_sessions.py),
and [watch scheduling](watch.py). Routes require session/origin checks.

## Tests

```sh
npm test
uv run --extra export --group dev python -m pytest engine/tests src/stats_for_nerds/tests -q
uv run --extra export --group dev python -m engine.verify_cpu
```

For database tests, set `XRGPU_TEST_POSTGRES=1`, then run
`uv run --extra export --group dev python -m pytest engine/tests/test_postgres.py -q`.
This needs a running database and permission to create/drop a temporary database.
`XRGPU_TEST_POSTGRES_RESTART=1` also restarts the container, interrupting access.
Keep synthetic UI fixtures out of the database and hardware evidence.
