-- All changes are scoped to xrBench namespaces and named RPCs. Safe to review
-- alongside an existing portfolio; no shared Auth settings or global grants change.
BEGIN;
SELECT pg_advisory_xact_lock(871624932);
CREATE SCHEMA IF NOT EXISTS xrbench_private;

CREATE TABLE IF NOT EXISTS xrbench_private.upload_policy (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    principal_daily_attempts bigint NOT NULL DEFAULT 20 CHECK (principal_daily_attempts > 0),
    principal_daily_bytes bigint NOT NULL DEFAULT 268435456 CHECK (principal_daily_bytes > 0),
    global_daily_attempts bigint NOT NULL DEFAULT 500 CHECK (global_daily_attempts > 0),
    global_daily_bytes bigint NOT NULL DEFAULT 4294967296 CHECK (global_daily_bytes > 0),
    storage_limit_bytes bigint NOT NULL DEFAULT 268435456 CHECK (storage_limit_bytes > 0),
    reservation_seconds integer NOT NULL DEFAULT 300 CHECK (reservation_seconds BETWEEN 60 AND 3600),
    storage_used_bytes bigint NOT NULL DEFAULT 0 CHECK (storage_used_bytes >= 0),
    storage_reserved_bytes bigint NOT NULL DEFAULT 0 CHECK (storage_reserved_bytes >= 0)
);
CREATE TABLE IF NOT EXISTS xrbench_private.upload_daily (
    day date NOT NULL,
    principal text NOT NULL,
    attempts bigint NOT NULL CHECK (attempts >= 0),
    bytes bigint NOT NULL CHECK (bytes >= 0),
    PRIMARY KEY (day, principal)
);
CREATE TABLE IF NOT EXISTS xrbench_private.upload_reservations (
    reservation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal uuid NOT NULL,
    max_bytes bigint NOT NULL CHECK (max_bytes BETWEEN 1 AND 33554432),
    storage_bytes bigint NOT NULL CHECK (storage_bytes > 0),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS xrbench_reservations_expiry
    ON xrbench_private.upload_reservations(expires_at);
CREATE TABLE IF NOT EXISTS xrbench_private.upload_owners (
    run_id text PRIMARY KEY REFERENCES xrgpu_benchmarks.benchmark_runs(run_id),
    principal uuid NOT NULL,
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[a-f0-9]{64}$'),
    storage_bytes bigint NOT NULL CHECK (storage_bytes > 0)
);

-- Include any preexisting xrBench report data in the initial logical storage
-- ledger. This estimates JSON/text plus conservative per-row overhead, not WAL,
-- indexes, backups, dead tuples or the shared project's physical disk allocation.
INSERT INTO xrbench_private.upload_policy(singleton, storage_used_bytes)
SELECT true,
    COALESCE((SELECT sum(octet_length(report::text) + octet_length(device::text)
        + octet_length(configuration::text) + 8192)
        FROM xrgpu_benchmarks.benchmark_runs), 0)
    + COALESCE((SELECT sum(octet_length(result::text) + octet_length(samples::text)
        + octet_length(timings::text) + COALESCE(octet_length(correctness::text), 0) + 1024)
        FROM xrgpu_benchmarks.benchmark_results), 0)
ON CONFLICT (singleton) DO NOTHING;

ALTER TABLE xrgpu_benchmarks.schema_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE xrgpu_benchmarks.benchmark_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE xrgpu_benchmarks.benchmark_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE xrbench_private.upload_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE xrbench_private.upload_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE xrbench_private.upload_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE xrbench_private.upload_owners ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON SCHEMA xrgpu_benchmarks, xrbench_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL TABLES IN SCHEMA xrgpu_benchmarks, xrbench_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA xrgpu_benchmarks, xrbench_private FROM PUBLIC, anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.reserve_xrbench_upload(p_principal uuid, p_max_bytes bigint)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
SET timezone = 'UTC'
SET lock_timeout = '3s'
AS $function$
DECLARE
    policy xrbench_private.upload_policy%ROWTYPE;
    now_at timestamptz := clock_timestamp();
    quota_day date := (now_at AT TIME ZONE 'UTC')::date;
    released bigint;
    principal_attempts bigint;
    principal_bytes bigint;
    global_attempts bigint;
    global_bytes bigint;
    reserve_bytes bigint;
    reservation xrbench_private.upload_reservations%ROWTYPE;
BEGIN
    IF p_principal IS NULL OR p_max_bytes IS NULL OR p_max_bytes NOT BETWEEN 1 AND 33554432 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid upload reservation';
    END IF;
    -- This single row serializes every receiver instance and the ingestion RPC.
    -- Lock ordering is always policy first, reservation second, report last.
    SELECT * INTO STRICT policy FROM xrbench_private.upload_policy WHERE singleton FOR UPDATE;
    WITH expired AS (
        DELETE FROM xrbench_private.upload_reservations WHERE expires_at <= now_at
        RETURNING storage_bytes
    ) SELECT COALESCE(sum(storage_bytes), 0) INTO released FROM expired;
    UPDATE xrbench_private.upload_policy SET storage_reserved_bytes = storage_reserved_bytes - released
        WHERE singleton RETURNING * INTO policy;
    DELETE FROM xrbench_private.upload_daily WHERE day < quota_day - 2;
    SELECT COALESCE(max(attempts), 0), COALESCE(max(bytes), 0)
        INTO principal_attempts, principal_bytes FROM xrbench_private.upload_daily
        WHERE day = quota_day AND principal = p_principal::text;
    SELECT COALESCE(max(attempts), 0), COALESCE(max(bytes), 0)
        INTO global_attempts, global_bytes FROM xrbench_private.upload_daily
        WHERE day = quota_day AND principal = '*';
    -- Reserve pessimistically before body reading. Actual committed logical size
    -- is checked against this capacity before any report rows are inserted.
    reserve_bytes := p_max_bytes * 4 + 1048576;
    IF principal_attempts >= policy.principal_daily_attempts
        OR principal_bytes > policy.principal_daily_bytes - p_max_bytes
        OR global_attempts >= policy.global_daily_attempts
        OR global_bytes > policy.global_daily_bytes - p_max_bytes
        OR policy.storage_used_bytes + policy.storage_reserved_bytes > policy.storage_limit_bytes - reserve_bytes THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'Upload quota exceeded';
    END IF;
    INSERT INTO xrbench_private.upload_daily(day, principal, attempts, bytes)
        VALUES (quota_day, p_principal::text, 1, p_max_bytes), (quota_day, '*', 1, p_max_bytes)
        ON CONFLICT (day, principal) DO UPDATE SET
            attempts = xrbench_private.upload_daily.attempts + 1,
            bytes = xrbench_private.upload_daily.bytes + EXCLUDED.bytes;
    INSERT INTO xrbench_private.upload_reservations(principal, max_bytes, storage_bytes, expires_at)
        VALUES (p_principal, p_max_bytes, reserve_bytes,
            now_at + pg_catalog.make_interval(secs => policy.reservation_seconds)) RETURNING * INTO reservation;
    UPDATE xrbench_private.upload_policy SET storage_reserved_bytes = storage_reserved_bytes + reserve_bytes
        WHERE singleton;
    RETURN jsonb_build_object('reservation_id', reservation.reservation_id,
        'principal', reservation.principal, 'max_bytes', reservation.max_bytes,
        'expires_at', reservation.expires_at);
END;
$function$;

CREATE OR REPLACE FUNCTION public.ingest_xrbench_report(
    p_report jsonb, p_measurements jsonb, p_report_sha256 text, p_payload_sha256 text,
    p_principal uuid, p_reservation_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
SET timezone = 'UTC'
SET lock_timeout = '3s'
AS $function$
DECLARE
    policy xrbench_private.upload_policy%ROWTYPE;
    reservation xrbench_private.upload_reservations%ROWTYPE;
    old_run xrgpu_benchmarks.benchmark_runs%ROWTYPE;
    owner xrbench_private.upload_owners%ROWTYPE;
    report_run_id text;
    measurement jsonb;
    native_row jsonb;
    position integer := 0;
    actual_bytes bigint;
    received timestamptz;
BEGIN
    SELECT * INTO STRICT policy FROM xrbench_private.upload_policy WHERE singleton FOR UPDATE;
    SELECT * INTO reservation FROM xrbench_private.upload_reservations
        WHERE reservation_id = p_reservation_id AND principal = p_principal
        AND expires_at > clock_timestamp() FOR UPDATE;
    IF NOT FOUND OR p_principal IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'Upload reservation unavailable';
    END IF;
    IF p_report IS NULL OR p_measurements IS NULL OR p_report_sha256 IS NULL OR p_payload_sha256 IS NULL
        OR jsonb_typeof(p_report) <> 'object' OR jsonb_typeof(p_measurements) <> 'array'
        OR p_report_sha256 !~ '^[a-f0-9]{64}$' OR p_payload_sha256 !~ '^[a-f0-9]{64}$'
        OR jsonb_typeof(p_report->'configuration') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_report->'device') IS DISTINCT FROM 'object'
        OR jsonb_typeof(p_report->'results') IS DISTINCT FROM 'array'
        OR p_report->>'status' IS NULL OR p_report->>'schema_version' IS NULL
        OR p_report->>'status' NOT IN ('complete', 'failed', 'canceled')
        OR (p_report->>'schema_version')::integer NOT IN (2, 3) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid native report';
    END IF;
    report_run_id := p_report->>'run_id';
    IF report_run_id IS NULL OR report_run_id !~ '^run-[a-f0-9]{32}$'
        OR (p_report->'configuration'->>'run_id') IS DISTINCT FROM report_run_id
        OR jsonb_array_length(p_measurements) <> jsonb_array_length(p_report->'results')
        OR jsonb_array_length(p_measurements) > 25000 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid native report projection';
    END IF;
    SELECT * INTO old_run FROM xrgpu_benchmarks.benchmark_runs WHERE run_id = report_run_id;
    IF FOUND THEN
        SELECT * INTO owner FROM xrbench_private.upload_owners WHERE run_id = report_run_id;
        IF NOT FOUND OR owner.principal <> p_principal OR old_run.report_sha256 <> p_report_sha256
            OR old_run.report <> p_report THEN
            RAISE EXCEPTION USING ERRCODE = '23505', MESSAGE = 'Run ID already has different report';
        END IF;
        received := old_run.received_at;
    ELSE
        actual_bytes := octet_length(p_report::text) + octet_length((p_report->'device')::text)
            + octet_length((p_report->'configuration')::text) + 8192;
        FOR measurement IN SELECT value FROM jsonb_array_elements(p_measurements) LOOP
            native_row := measurement->'result';
            IF jsonb_typeof(measurement) IS DISTINCT FROM 'object'
                OR jsonb_typeof(native_row) IS DISTINCT FROM 'object'
                OR native_row IS DISTINCT FROM p_report->'results'->position
                OR measurement->>'case_id' IS DISTINCT FROM native_row->>'case'
                OR measurement->>'phase' IS DISTINCT FROM native_row->>'phase'
                OR measurement->>'trial' IS DISTINCT FROM native_row->>'trial'
                OR measurement->>'operation' IS DISTINCT FROM native_row->>'operation'
                OR measurement->>'status' IS DISTINCT FROM native_row->>'status'
                OR jsonb_typeof(native_row->'samples') IS DISTINCT FROM 'array'
                OR jsonb_typeof(measurement->'timings') IS DISTINCT FROM 'object' THEN
                RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Invalid native report projection';
            END IF;
            actual_bytes := actual_bytes + octet_length(native_row::text)
                + octet_length((native_row->'samples')::text)
                + octet_length((measurement->'timings')::text)
                + COALESCE(octet_length((native_row->'correctness')::text), 0) + 1024;
            position := position + 1;
        END LOOP;
        IF actual_bytes > reservation.storage_bytes
            OR policy.storage_used_bytes + actual_bytes > policy.storage_limit_bytes THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'Upload quota exceeded';
        END IF;
        INSERT INTO xrgpu_benchmarks.benchmark_runs
            (run_id,schema_version,suite_revision,runtime_revision,backend,mode,preset,status,
             started_at,finished_at,device,configuration,report,report_sha256)
        VALUES (report_run_id,(p_report->>'schema_version')::integer,p_report->>'suite_revision',
            p_report->>'runtime_revision',p_report->>'backend',p_report->'configuration'->>'mode',
            p_report->'configuration'->>'preset',p_report->>'status',
            (p_report->>'started_at')::timestamptz,(p_report->>'finished_at')::timestamptz,
            p_report->'device',p_report->'configuration',p_report,p_report_sha256)
        RETURNING received_at INTO received;
        INSERT INTO xrgpu_benchmarks.benchmark_results
            (run_id,case_id,phase,trial,operation,status,correctness,timings,samples,result)
        SELECT report_run_id,value->>'case_id',value->>'phase',(value->>'trial')::integer,
            value->>'operation',value->>'status',value->'result'->'correctness',value->'timings',
            value->'result'->'samples',value->'result'
        FROM jsonb_array_elements(p_measurements);
        INSERT INTO xrbench_private.upload_owners(run_id,principal,payload_sha256,storage_bytes)
            VALUES (report_run_id,p_principal,p_payload_sha256,actual_bytes);
        UPDATE xrbench_private.upload_policy SET storage_used_bytes = storage_used_bytes + actual_bytes
            WHERE singleton;
    END IF;
    DELETE FROM xrbench_private.upload_reservations WHERE reservation_id = p_reservation_id;
    UPDATE xrbench_private.upload_policy SET storage_reserved_bytes = storage_reserved_bytes - reservation.storage_bytes
        WHERE singleton;
    RETURN jsonb_build_object('stored',true,'run_id',report_run_id,
        'payload_sha256',p_payload_sha256,'report_sha256',p_report_sha256,
        'received_at',received,'verification','schema-validated; hardware not attested');
END;
$function$;

-- Existing projects may retain the earlier four-argument ingestion overload.
-- Close every overload of these two xrBench names before granting only the
-- reservation-bound canonical signatures. No unrelated public functions change.
DO $revoke_legacy$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT pg_catalog.format('%I.%I(%s)', n.nspname, p.proname,
            pg_catalog.pg_get_function_identity_arguments(p.oid)) AS signature
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname IN ('reserve_xrbench_upload', 'ingest_xrbench_report')
    LOOP
        EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon, authenticated, service_role', target.signature);
    END LOOP;
END;
$revoke_legacy$;
GRANT EXECUTE ON FUNCTION public.reserve_xrbench_upload(uuid,bigint) TO service_role;
GRANT EXECUTE ON FUNCTION public.ingest_xrbench_report(jsonb,jsonb,text,text,uuid,uuid) TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
