"""Opt-in, isolated PostgreSQL checks for the exact hosted migration/RPC package."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import json
import os
import re
import subprocess
import threading
from types import SimpleNamespace
import uuid

import pytest

from engine.db import Store
from engine.db.config import database
from engine.db.security import connection_options
from engine.paths import ROOT

MIGRATIONS = sorted((ROOT / 'supabase/migrations').glob('*xrbench*.sql'))
MAX_BYTES = 8 * 1024 * 1024  # Hosted adapter limit; SQL also supports the generic 32 MiB ceiling.


@pytest.fixture(scope='module')
def hosted_roles():
    if os.environ.get('XRGPU_TEST_POSTGRES') != '1':
        pytest.skip('Set XRGPU_TEST_POSTGRES=1 for isolated local hosted-database tests')
    pytest.importorskip('psycopg')
    from psycopg import sql

    base = database()
    if 'host' not in connection_options(base.dsn):
        pytest.skip('Hosted integration tests require an explicitly local database')
    with base.connect() as admin:
        if not admin.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0]:
            pytest.skip('An isolated test database and temporary test roles require a local superuser')
    suffix = uuid.uuid4().hex
    roles = {name: 'xrbench_test_' + name + '_' + suffix for name in ('anon', 'authenticated', 'service_role')}
    created = []
    try:
        with base.connect() as admin:
            admin.autocommit = True
            for name in roles.values():
                admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(name)))
                created.append(name)
        yield SimpleNamespace(base=base, roles=roles)
    finally:
        with base.connect() as admin:
            admin.autocommit = True
            for name in reversed(created):
                admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(name)))


@pytest.fixture
def hosted_db(hosted_roles):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = 'xrbench_hosted_test_' + uuid.uuid4().hex
    with hosted_roles.base.connect() as admin:
        admin.autocommit = True
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    parameters = conninfo_to_dict(hosted_roles.base.dsn)
    parameters['dbname'] = name
    store = Store(make_conninfo(**parameters))

    @contextmanager
    def connect(role=None):
        with store.connect() as connection:
            if role:
                connection.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(hosted_roles.roles[role])))
            yield connection

    def apply():
        with connect() as admin:
            admin.autocommit = True
            for file in MIGRATIONS:
                source = file.read_text()
                for role, replacement in hosted_roles.roles.items():
                    source = re.sub(r'\b' + role + r'\b', sql.Identifier(replacement).as_string(admin), source)
                admin.execute(source)

    def policy(**changes):
        with connect() as admin:
            for field, value in changes.items():
                admin.execute(sql.SQL('UPDATE xrbench_private.upload_policy SET {}=%s WHERE singleton').format(
                    sql.Identifier(field)), (value,))

    def scalar(query, values=()):
        with connect() as admin:
            return admin.execute(query, values).fetchone()[0]

    def reserve(principal=None, size=MAX_BYTES, role='service_role'):
        with connect(role) as connection:
            return connection.execute('SELECT public.reserve_xrbench_upload(%s::uuid,%s)',
                                      (principal or uuid.uuid4(), size)).fetchone()[0]

    def ingest(submission, reservation, principal=None, role='service_role'):
        with connect(role) as connection:
            return connection.execute('SELECT public.ingest_xrbench_report(%s::jsonb,%s::jsonb,%s,%s,%s::uuid,%s::uuid)',
                (json.dumps(submission['p_report']), json.dumps(submission['p_measurements']),
                 submission['p_report_sha256'], submission['p_payload_sha256'],
                 principal or reservation['principal'], reservation['reservation_id'])).fetchone()[0]

    try:
        # A shared-portfolio sentinel checks migration scope and preserved grants.
        with connect() as admin:
            admin.execute('CREATE TABLE public.portfolio_sentinel(value text)')
            admin.execute("INSERT INTO public.portfolio_sentinel VALUES ('untouched portfolio')")
            admin.execute(sql.SQL('GRANT SELECT ON public.portfolio_sentinel TO {}').format(
                sql.Identifier(hosted_roles.roles['anon'])))
            admin.execute("CREATE FUNCTION public.portfolio_status() RETURNS text LANGUAGE sql "
                          "AS $$SELECT 'untouched portfolio function'::text$$")
            admin.execute("CREATE FUNCTION public.ingest_xrbench_report(jsonb,jsonb,text,text) "
                          "RETURNS jsonb LANGUAGE sql AS $$SELECT '{\"legacy\":true}'::jsonb$$")
            admin.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.ingest_xrbench_report(jsonb,jsonb,text,text) TO {}').format(
                sql.Identifier(hosted_roles.roles['service_role'])))
        apply()
        yield SimpleNamespace(connect=connect, reserve=reserve, ingest=ingest, scalar=scalar,
                              policy=policy, apply=apply, roles=hosted_roles.roles, store=store)
    finally:
        with hosted_roles.base.connect() as admin:
            admin.autocommit = True
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


@pytest.fixture(scope='module')
def submission(hosted_roles):
    # Use the production JS projection, with a complete synthetic 221x100 report.
    source = """
import {nativeManifest,operatorReport} from './src/bench_web/tests/native-fixtures.mjs';
import {prepareSubmission} from './server/report-contract.mjs';
const m=nativeManifest();
const r=operatorReport({...m.configurations.gpu,run_id:'run-'+'9'.repeat(32)},m.catalog);
process.stdout.write(JSON.stringify(await prepareSubmission(new TextEncoder().encode(JSON.stringify(r)),[m])));
"""
    result = subprocess.run(['node', '--input-type=module', '-e', source], cwd=ROOT,
                            capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


def test_rpc_only_access_rls_and_shared_portfolio_preserved(hosted_db):
    import psycopg

    tables = ['xrgpu_benchmarks.schema_migrations', 'xrgpu_benchmarks.benchmark_runs',
              'xrgpu_benchmarks.benchmark_results', 'xrbench_private.upload_policy',
              'xrbench_private.upload_daily', 'xrbench_private.upload_reservations',
              'xrbench_private.upload_owners']
    for role in ('anon', 'authenticated', 'service_role'):
        for table in tables:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with hosted_db.connect(role) as connection:
                    connection.execute('SELECT * FROM ' + table)
    for role in ('anon', 'authenticated'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            hosted_db.reserve(role=role)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with hosted_db.connect(role) as connection:
                connection.execute('SELECT public.ingest_xrbench_report(NULL,NULL,NULL,NULL,NULL,NULL)')
    with hosted_db.connect('anon') as connection:
        assert connection.execute('SELECT value FROM public.portfolio_sentinel').fetchone()[0] == 'untouched portfolio'
        assert connection.execute('SELECT public.portfolio_status()').fetchone()[0] == 'untouched portfolio function'
    for role in ('anon', 'authenticated', 'service_role'):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with hosted_db.connect(role) as connection:
                connection.execute('SELECT public.ingest_xrbench_report(NULL::jsonb,NULL::jsonb,NULL::text,NULL::text)')
    assert hosted_db.scalar("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('xrbench_private','xrgpu_benchmarks') AND c.relkind='r' AND c.relrowsecurity") == 7
    with hosted_db.connect() as connection:
        functions = connection.execute("SELECT prosecdef,proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND ((proname='reserve_xrbench_upload' AND pronargs=2) "
            "OR (proname='ingest_xrbench_report' AND pronargs=6))").fetchall()
    assert len(functions) == 2 and all(row[0] and 'search_path=""' in row[1] for row in functions)


def test_complete_native_report_receipt_retry_and_ownership(hosted_db, submission):
    import psycopg

    principal = uuid.uuid4()
    reservation = hosted_db.reserve(principal)
    receipt = hosted_db.ingest(submission, reservation)
    assert receipt['stored'] and receipt['run_id'] == submission['p_report']['run_id']
    assert receipt['report_sha256'] == submission['p_report_sha256']
    assert receipt['payload_sha256'] == submission['p_payload_sha256']
    assert hosted_db.scalar('SELECT count(*) FROM xrgpu_benchmarks.benchmark_results') == 221
    assert hosted_db.scalar('SELECT sum(jsonb_array_length(samples)) FROM xrgpu_benchmarks.benchmark_results') == 22100
    used = hosted_db.scalar('SELECT storage_used_bytes FROM xrbench_private.upload_policy')
    assert hosted_db.ingest(submission, hosted_db.reserve(principal)) == receipt
    assert hosted_db.scalar('SELECT storage_used_bytes FROM xrbench_private.upload_policy') == used
    assert hosted_db.scalar('SELECT storage_reserved_bytes FROM xrbench_private.upload_policy') == 0
    with pytest.raises(psycopg.errors.UniqueViolation):
        hosted_db.ingest(submission, hosted_db.reserve())
    changed = copy.deepcopy(submission)
    changed['p_report']['device']['model'] = 'different'
    with pytest.raises(psycopg.errors.UniqueViolation):
        hosted_db.ingest(changed, hosted_db.reserve(principal))


def test_reservations_are_principal_bound_expire_and_cannot_be_replayed(hosted_db, submission):
    import psycopg

    reservation = hosted_db.reserve()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        hosted_db.ingest(submission, reservation, uuid.uuid4())
    assert hosted_db.scalar('SELECT count(*) FROM xrgpu_benchmarks.benchmark_runs') == 0
    with hosted_db.connect() as admin:
        admin.execute("UPDATE xrbench_private.upload_reservations SET expires_at=now()-interval '1 second'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        hosted_db.ingest(submission, reservation)
    current = hosted_db.reserve()
    assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_reservations') == 1
    hosted_db.ingest(submission, current)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        hosted_db.ingest(submission, current)


@pytest.mark.parametrize('scope', ['global', 'principal'])
def test_quota_concurrency_is_atomic_across_connections(hosted_db, scope):
    hosted_db.policy(**{scope + '_daily_attempts': 3})
    principal = uuid.uuid4() if scope == 'principal' else None
    barrier = threading.Barrier(8)

    def compete(_):
        import psycopg
        barrier.wait(timeout=10)
        try:
            hosted_db.reserve(principal, size=1024)
            return 'accepted'
        except psycopg.Error as error:
            return error.sqlstate

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(compete, range(8)))
    assert outcomes.count('accepted') == 3 and outcomes.count('P0001') == 5
    assert hosted_db.scalar("SELECT attempts FROM xrbench_private.upload_daily WHERE principal='*'") == 3
    assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_reservations') == 3


@pytest.mark.parametrize('size', [0, -1, 33554433, None])
def test_rpc_rejects_invalid_reservation_caps(hosted_db, size):
    import psycopg

    with pytest.raises(psycopg.errors.InvalidParameterValue):
        hosted_db.reserve(size=size)
    assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_reservations') == 0


def test_individual_and_global_byte_budgets_are_enforced(hosted_db):
    import psycopg

    principal = uuid.uuid4()
    hosted_db.policy(principal_daily_bytes=20, global_daily_bytes=40)
    hosted_db.reserve(principal, size=11)
    with pytest.raises(psycopg.Error) as error:
        hosted_db.reserve(principal, size=11)
    assert error.value.sqlstate == 'P0001'
    hosted_db.reserve(size=20)
    with pytest.raises(psycopg.Error) as error:
        hosted_db.reserve(size=10)
    assert error.value.sqlstate == 'P0001'
    assert hosted_db.scalar("SELECT bytes FROM xrbench_private.upload_daily WHERE principal='*'") == 31


def test_storage_reservations_cleanup_and_old_counter_pruning(hosted_db):
    import psycopg

    size = 1024
    hosted_db.policy(storage_limit_bytes=4 * size + 1048576)
    hosted_db.reserve(size=size)
    with pytest.raises(psycopg.Error) as error:
        hosted_db.reserve(size=size)
    assert error.value.sqlstate == 'P0001'
    with hosted_db.connect() as admin:
        admin.execute("UPDATE xrbench_private.upload_reservations SET expires_at=now()-interval '1 second'")
        admin.execute("INSERT INTO xrbench_private.upload_daily VALUES (current_date-3,'old',1,1)")
    hosted_db.reserve(size=size)
    assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_reservations') == 1
    assert hosted_db.scalar("SELECT count(*) FROM xrbench_private.upload_daily WHERE principal='old'") == 0
    assert hosted_db.scalar('SELECT storage_reserved_bytes FROM xrbench_private.upload_policy') == 4 * size + 1048576


def test_failed_insert_rolls_back_entire_report_and_reservation_consumption(hosted_db, submission):
    import psycopg

    bad = copy.deepcopy(submission)
    bad['p_report']['results'][-1] = copy.deepcopy(bad['p_report']['results'][0])
    bad['p_measurements'][-1] = copy.deepcopy(bad['p_measurements'][0])
    reservation = hosted_db.reserve()
    before = hosted_db.scalar('SELECT storage_reserved_bytes FROM xrbench_private.upload_policy')
    with pytest.raises(psycopg.errors.UniqueViolation):
        hosted_db.ingest(bad, reservation)
    assert hosted_db.scalar('SELECT count(*) FROM xrgpu_benchmarks.benchmark_runs') == 0
    assert hosted_db.scalar('SELECT count(*) FROM xrgpu_benchmarks.benchmark_results') == 0
    assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_owners') == 0
    assert hosted_db.scalar('SELECT storage_used_bytes FROM xrbench_private.upload_policy') == 0
    assert hosted_db.scalar('SELECT storage_reserved_bytes FROM xrbench_private.upload_policy') == before
    assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_reservations') == 1
    assert hosted_db.ingest(submission, reservation)['stored']


def test_ingestion_cannot_exceed_reserved_storage(hosted_db, submission):
    import psycopg

    reservation = hosted_db.reserve(size=1024)
    with pytest.raises(psycopg.Error) as error:
        hosted_db.ingest(submission, reservation)
    assert error.value.sqlstate == 'P0001'
    assert hosted_db.scalar('SELECT count(*) FROM xrgpu_benchmarks.benchmark_runs') == 0


def test_migrations_repeat_without_changing_limits_reports_or_portfolio(hosted_db, submission):
    receipt = hosted_db.ingest(submission, hosted_db.reserve())
    hosted_db.policy(principal_daily_attempts=9)
    before = hosted_db.scalar('SELECT storage_used_bytes FROM xrbench_private.upload_policy')
    hosted_db.apply()
    assert hosted_db.scalar('SELECT principal_daily_attempts FROM xrbench_private.upload_policy') == 9
    assert hosted_db.scalar('SELECT storage_used_bytes FROM xrbench_private.upload_policy') == before
    assert hosted_db.scalar('SELECT count(*) FROM xrgpu_benchmarks.benchmark_runs') == 1
    assert receipt['report_sha256'] == hosted_db.scalar('SELECT report_sha256 FROM xrgpu_benchmarks.benchmark_runs')
    with hosted_db.connect('anon') as connection:
        assert connection.execute('SELECT value FROM public.portfolio_sentinel').fetchone()[0] == 'untouched portfolio'
