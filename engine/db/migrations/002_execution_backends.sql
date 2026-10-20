-- Backends are checked by the runner/report registry. Keep storage extensible.
ALTER TABLE benchmark_runs DROP CONSTRAINT benchmark_runs_backend_check;
ALTER TABLE benchmark_runs ADD CONSTRAINT benchmark_runs_backend_name_check
    CHECK (backend ~ '^[a-z][a-z0-9_]{0,63}$');
