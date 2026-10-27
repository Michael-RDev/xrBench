-- Explicit administrator-only setup for a dedicated hosted xrBench database.
-- Apply the migrations first, then run this file with psql. It intentionally
-- fails if xrbench_runtime already exists: inspect existing privileges instead
-- of silently reusing an overprivileged or inherited role.
BEGIN;
CREATE ROLE xrbench_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS NOINHERIT;
REVOKE CREATE, TEMPORARY ON DATABASE :"DBNAME" FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA xrgpu_benchmarks FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA xrgpu_benchmarks FROM PUBLIC;
GRANT CONNECT ON DATABASE :"DBNAME" TO xrbench_runtime;
GRANT USAGE ON SCHEMA xrgpu_benchmarks TO xrbench_runtime;
GRANT SELECT ON xrgpu_benchmarks.schema_migrations TO xrbench_runtime;
GRANT SELECT, INSERT ON xrgpu_benchmarks.benchmark_runs,
    xrgpu_benchmarks.benchmark_results TO xrbench_runtime;
COMMIT;
-- Use psql's interactive password command so the password is not in this file,
-- SQL history, shell arguments or repository. Or provision a secret separately.
\password xrbench_runtime
