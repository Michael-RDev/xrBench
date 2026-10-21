-- Backends are checked by the runner/report registry. Keep storage extensible.
-- Apply after 001_initial.sql; this file is safe to rerun on existing volumes.
BEGIN;
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
COMMIT;
