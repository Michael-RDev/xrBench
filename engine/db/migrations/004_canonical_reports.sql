BEGIN;
SELECT pg_advisory_xact_lock(871624931);
SET LOCAL search_path TO xrgpu_benchmarks;
DO $migration$
BEGIN
IF NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version = 4) THEN
ALTER TABLE benchmark_runs ADD COLUMN report_canonical TEXT;
ALTER TABLE benchmark_runs ADD CONSTRAINT benchmark_runs_canonical_report_check CHECK (
    report_canonical IS NULL OR (
        report_canonical::jsonb = report
        AND encode(sha256(convert_to(report_canonical, 'UTF8')), 'hex') = report_sha256
    )
);
INSERT INTO schema_migrations(version) VALUES (4);
END IF;
END;
$migration$;
COMMIT;
