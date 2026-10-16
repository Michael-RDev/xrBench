# xrBench

Connect Quest 3 → **Run all tests** → native Vulkan execution → local PostgreSQL → xrGPU imports evidence.

Two websites share one host controller and one native runner:

- **Bench Web** at http://localhost:8765: Three.js headset view, full-suite controls, live outcomes.
- **Stats for Nerds** at http://localhost:8765/stats/: shapes, correctness, timing scopes, XR comparisons, and saved runs.
- **Layers**: 25 folders with layer definitions and independent NumPy references; 63 ordered cases.

The Three.js scene only illustrates the headset and progress. Layer benchmarks execute
in the dedicated Android app using stock ExecuTorch/Vulkan. Unity integration and
custom kernel dispatch are separate. No account or public collector is involved.

## Setup and launch

Use this repository's single root uv environment. Docker Desktop and Java 17+ are
required; the pinned Android bootstrap currently targets macOS. Node.js/npm build
the locally served Three.js module; there are no CDN or external font requests.

```sh
uv sync --extra export --group dev
npm ci
npm run build
uv run --locked --extra export --group dev python -m benchmarks.layers.engine.bootstrap_android
uv run --locked --extra export --group dev python -m benchmarks.layers.engine.prepare
uv run --locked --extra export --group dev python -m benchmarks.layers.export
uv run --locked --extra export --group dev python -m benchmarks.layers.engine.build
uv run --locked python -m benchmarks
```

The launcher starts PostgreSQL 17, applies versioned migrations, and opens the
website. It binds only to 127.0.0.1. Use `--no-open`, `--no-docker`, or `--port 8766`
when appropriate. Run only one controller against this state directory.

Enable Quest 3 developer mode, connect USB, and approve debugging in the headset.
Press Run once; every preset includes every case. Missing or unauthorized devices
have setup guidance. Other headsets/glasses remain unsupported. XR mode requires
wearing the headset and keeping the native 72 Hz scene focused.

## Ownership and local state

`benchmarks/layers/engine/` owns ADB, HTTP APIs, native builds, reports, and PostgreSQL.
`benchmarks/bench_web/` and `benchmarks/stats_for_nerds/` own presentation only.
The native scene and tracing sources live in the engine; no xrGPU checkout is needed.

Generated exports/APK are under `benchmarks/layers/.build/`. Credentials live in
`benchmarks/layers/.env`; the signing key and reports are under `.state/` beside it.
These files are ignored and must remain private. Keep the signing key to update an
installed app without erasing its data. The package remains `org.xrgpu.benchmarks`.

For existing installations the Docker project remains `xrgpu-benchmarks`, its volume
is `xrgpu-benchmarks_benchmark-postgres`, and the schema is `xrgpu_benchmarks`.
Changing repositories does not require a database migration or report rewrite.
Reports are saved locally before database submission; database retries never rerun
benchmarks. USB disconnection preserves bounded on-device work and report recovery.

## Read-only evidence API

Get a session token from `GET /api/session` and supply it as `X-XRGPU-Session`.
Control requests retain Host/Origin checks. The token is transient, not a credential
to store in reports or browser files.

- `GET /api/v1/results?limit=20&offset=0`: committed summaries and receipts; limit 1–100, offset 0–100000; `next_offset` is null on the last page.
- `GET /api/v1/results/{run_id}`: `{report, receipt}` containing the unchanged schema-2 report and its stored SHA-256 receipt. Unknown or uncommitted reports return 404; database outages return 503.

The hash is SHA-256 of UTF-8 JSON with sorted keys, compact separators, ASCII escapes,
and no NaN/Infinity. Local pending reports remain downloadable through the website.
APK hashes identify builds, not custom kernel selections. Historical missing fields
are displayed as not recorded.

From the separate xrGPU repository:

```sh
uv run python -m runtime.benchmark_results list
uv run python -m runtime.benchmark_results import --all
uv run python -m runtime.benchmark_results import --run-id run-0123456789abcdef0123456789abcdef
```

Imports verify receipts and cache evidence under xrGPU's `artifacts/benchmarks/`.
They do not run benchmarks, modify kernels, or qualify custom-kernel acceptance.

## Validation

```sh
uv run --locked --extra export --group dev python -m pytest
npm test
XRGPU_TEST_POSTGRES=1 uv run --locked --extra export --group dev python -m pytest benchmarks/layers/engine/tests/test_postgres.py benchmarks/layers/engine/tests/test_results_api.py
```

Set `XRGPU_TEST_POSTGRES_RESTART=1` only when the local database can be restarted.
Integration tests create disposable databases; they do not rewrite saved reports.
See [the layer guide](benchmarks/layers/README.md) for measurement definitions.
Physical Quest validation remains pending until demonstrated on a connected device.
