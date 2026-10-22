# Maintainer and developer startup

CPU/GPU benchmark kernels and calibration shaders are owned by xrGPU. Builds
fetch its verified commit from `dependencies.json`; the local source cache is an
ignored dependency. See [kernel development](../docs/kernel-development.md) for
read credentials, offline caching, provenance, and updating the pin.

Visitors use the hosted website: connect an authorized headset by USB and press
Run. They do not run these commands or install a local database.

`uv run python main.py` previews the packaged browser website on port 8765 and
saves reports locally without Docker or PostgreSQL. The remaining instructions
are for the separate development application and Stats for Nerds.

From the repository root, run:

```sh
uv run python main.py --developer
```

This opens Docker Desktop when needed on macOS/Windows, waits for Docker,
starts the PostgreSQL container, applies pending SQL migrations, and opens the
website at [localhost:8766](http://localhost:8766/). Docker Desktop and its CLI must
be installed first. Linux requires an already-running Docker engine; the launcher
does not request root privileges or install system services.

For a fresh local database, the launcher generates an ignored `engine/.env` with
a random password and restricted file permissions. Existing credentials and the
named database volume are reused. If that volume exists but its credentials have
been lost, restore them; the launcher never resets the database to make it start.
Each SQL file skips versions already applied, so subsequent launches keep saved
reports. Startup stops with a concise error if Docker, the connection, or SQL fails.

`--no-open` suppresses browser opening, `--port 9000` changes the website port,
and `--adb /path/to/adb` selects platform-tools. Set `XRGPU_DB_PORT` in `engine/.env`
to change the database port. `--no-docker` uses an already-running database while
still applying SQL. `XRGPU_DATABASE_URL` also bypasses Docker and local credential
creation; pending migrations are applied to that configured database.

Docker readiness is bounded to about 120 seconds after opening Desktop. Container
startup has a 180-second limit, including a 60-second health wait. Database connection
readiness is bounded to about 30 seconds. SQL has a 15-second lock timeout and a
120-second statement timeout. Ctrl-C cancels startup or stops the Python server;
PostgreSQL stays running with its data intact.

## Manual alternative

The database container, SQL files, and server can still be run separately using
the commands below. The module entry point `python -m src --developer` starts only
the server; automatic setup belongs to `main.py --developer`.

## 1. Configure and start PostgreSQL yourself

For a new installation, copy `.env.example` from this directory to `engine/.env`
and replace the password placeholder. If `engine/.env` already exists, keep it:
an existing database volume still uses its original password. Choose a different
`XRGPU_DB_PORT` if 5432 is occupied. Keep this file private; it is ignored by Git.

Open Docker Desktop, then explicitly start the database container:

```sh
docker compose --env-file engine/.env -f engine/compose.yaml up -d --wait db
```

PostgreSQL listens on `127.0.0.1` only. The Compose project remains
`xrgpu-benchmarks` and reuses `xrgpu-benchmarks_benchmark-postgres`, preserving
existing reports. Container restart is disabled: start it yourself when needed.
The `up` command also applies the new restart policy and read-only SQL mount to
an existing container. Docker initializes the database/user on a new volume;
benchmark tables are created only by the next, manual step.

## 2. Apply the SQL yourself

Run both files in order. Each file includes its transaction, schema search path,
and migration-version record. Rerunning the files skips versions already applied;
existing benchmark rows are retained. No scripts are mounted into PostgreSQL's
automatic initialization directory.

```sh
docker compose --env-file engine/.env -f engine/compose.yaml exec -T db psql -U xrgpu -d xrgpu -v ON_ERROR_STOP=1 -f /migrations/001_initial.sql
docker compose --env-file engine/.env -f engine/compose.yaml exec -T db psql -U xrgpu -d xrgpu -v ON_ERROR_STOP=1 -f /migrations/002_execution_backends.sql
```

You can instead open the SQL files in `engine/db/migrations/` and execute them in
your SQL client connected to the `xrgpu` database. Apply future migration files
explicitly in numeric order too. The schema needs this setup once, then whenever
a new migration is added, rather than on each server start.

## 3. Start the server yourself

With the repository environment installed (`uv sync --locked --group dev`):

```sh
uv run python -m src --developer
```

Open [the developer homepage](http://localhost:8766/) or
[Stats for Nerds](http://localhost:8766/stats/). The browser opens automatically;
add `--no-open` to suppress it. `--port` selects another port and
`--adb /path/to/adb` selects platform-tools. The module entry points
`uv run python -m src --developer` and `uv run python -m engine --port 8766`
remain available.
In this manual mode, missing database setup stops startup with instructions. The
server does not apply SQL. The old `--migrate-only` option is removed; use `main.py --developer`
for automatic setup or execute the SQL files yourself.

To use an existing PostgreSQL server, set `XRGPU_DATABASE_URL`, manually apply the
same SQL there, and start the Python server. SQL setup needs schema/table creation
permissions. Runtime needs access to the existing schema and report tables.

## Stop each process yourself

Ctrl-C stops the Python server. Stop PostgreSQL separately when finished:

```sh
docker compose --env-file engine/.env -f engine/compose.yaml stop db
```

Stopping preserves the named volume. Avoid `down -v` unless you intend to delete
the collected database.

## Browser preview and local report capture

`uv run python -m src --no-open` serves a previously packaged browser/WebUSB
release on port 8765. It saves submitted JSON to
`engine/.state/submitted-reports/` and does not use PostgreSQL. The manual database
steps above govern the developer engine and Stats for Nerds, not this file receiver.
See [browser packaging and report delivery](../src/bench_web/README.md).
