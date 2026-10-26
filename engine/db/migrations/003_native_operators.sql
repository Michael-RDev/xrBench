BEGIN;
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
