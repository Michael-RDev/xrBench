-- xrBench-only bootstrap. Does not alter portfolio/public objects or global grants.
BEGIN;
-- Mirrored from engine/db/migrations/001_initial.sql
SELECT pg_advisory_xact_lock(871624931);
CREATE SCHEMA IF NOT EXISTS xrgpu_benchmarks;
SET LOCAL search_path TO xrgpu_benchmarks;
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
DO $migration$
BEGIN
IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 1) THEN
CREATE TABLE benchmark_runs (
    run_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    suite_revision TEXT NOT NULL,
    runtime_revision TEXT NOT NULL,
    backend TEXT NOT NULL CHECK (backend = 'executorch_vulkan'),
    mode TEXT NOT NULL CHECK (mode IN ('isolated', 'xr')),
    preset TEXT NOT NULL CHECK (preset IN ('smoke', 'standard', 'sustained')),
    status TEXT NOT NULL CHECK (status IN ('complete', 'failed', 'canceled')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    device JSONB NOT NULL,
    configuration JSONB NOT NULL,
    report JSONB NOT NULL,
    report_sha256 TEXT NOT NULL CHECK (length(report_sha256) = 64),
    CHECK (finished_at >= started_at)
);
CREATE TABLE benchmark_results (
    run_id TEXT NOT NULL REFERENCES benchmark_runs(run_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('isolated', 'baseline', 'loaded')),
    trial INTEGER NOT NULL CHECK (trial >= 0),
    operation TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'skipped', 'interrupted')),
    correctness JSONB,
    timings JSONB NOT NULL,
    samples JSONB NOT NULL,
    result JSONB NOT NULL,
    PRIMARY KEY (run_id, case_id, phase, trial)
);
CREATE INDEX benchmark_runs_recent ON benchmark_runs (received_at DESC);
CREATE INDEX benchmark_runs_model ON benchmark_runs ((device->>'model'));
CREATE INDEX benchmark_runs_suite ON benchmark_runs (suite_revision);
CREATE INDEX benchmark_results_operation ON benchmark_results (operation);
INSERT INTO schema_migrations(version) VALUES (1);
END IF;
END;
$migration$;

-- Mirrored from engine/db/migrations/002_execution_backends.sql
SELECT pg_advisory_xact_lock(871624931);
SET LOCAL search_path TO xrgpu_benchmarks;
DO $migration$
BEGIN
IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 2) THEN
ALTER TABLE benchmark_runs DROP CONSTRAINT benchmark_runs_backend_check;
ALTER TABLE benchmark_runs ADD CONSTRAINT benchmark_runs_backend_name_check
    CHECK (backend ~ '^[a-z][a-z0-9_]{0,63}$');
INSERT INTO schema_migrations(version) VALUES (2);
END IF;
END;
$migration$;

-- Mirrored from engine/db/migrations/003_native_operators.sql
SELECT pg_advisory_xact_lock(871624931);
SET LOCAL search_path TO xrgpu_benchmarks;
DO $migration$
BEGIN
IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 3) THEN
ALTER TABLE benchmark_runs DROP CONSTRAINT IF EXISTS benchmark_runs_schema_version_check;
ALTER TABLE benchmark_runs ADD CONSTRAINT benchmark_runs_schema_version_check CHECK (schema_version IN (2,3));
ALTER TABLE benchmark_results DROP CONSTRAINT IF EXISTS benchmark_results_status_check;
ALTER TABLE benchmark_results ADD CONSTRAINT benchmark_results_status_check
    CHECK (status IN ('passed','failed','skipped','interrupted','unsupported'));
INSERT INTO schema_migrations(version) VALUES (3);
END IF;
END;
$migration$;
COMMIT;
