"""Administrative intent and real grant checks for the hosted deployment helper."""

import json

import pytest

from engine import hosted_deployment
from engine.tests.test_hosted_database import hosted_db as _db_fixture
from engine.tests.test_hosted_database import hosted_roles as _roles_fixture

# Reuse the UUID-named local DB/API-role substitutes without creating global
# Supabase role names or modifying existing local/hosted objects.
hosted_db = _db_fixture
hosted_roles = _roles_fixture


def test_default_check_never_uses_runtime_credentials_or_applies(monkeypatch, capsys):
    monkeypatch.delenv('XRBENCH_MIGRATION_DATABASE_URL', raising=False)
    monkeypatch.setenv('XRGPU_DATABASE_URL', 'private-runtime-password')
    monkeypatch.setattr(hosted_deployment, 'Store', lambda *_: pytest.fail('Runtime credentials used'))
    monkeypatch.setattr(hosted_deployment, 'apply_migrations', lambda *_: pytest.fail('Implicit migration'))
    assert hosted_deployment.main([]) == 2
    output = capsys.readouterr().out
    assert 'private-runtime-password' not in output
    assert 'XRBENCH_MIGRATION_DATABASE_URL' in output


def test_apply_is_explicit_and_separate_admin_secret_is_never_printed(monkeypatch, capsys):
    monkeypatch.setenv('XRBENCH_MIGRATION_DATABASE_URL', 'private-admin-password')
    events = []
    monkeypatch.setattr(hosted_deployment, 'Store', lambda value: events.append(('connect', value)) or object())
    monkeypatch.setattr(hosted_deployment, 'audit', lambda _: events.append(('audit',)) or {'ready': True})
    monkeypatch.setattr(hosted_deployment, 'apply_migrations', lambda _: events.append(('apply',)) or ['xrbench.sql'])
    assert hosted_deployment.main([]) == 0
    assert events == [('connect', 'private-admin-password'), ('audit',)]
    assert json.loads(capsys.readouterr().out)['mode'] == 'check'
    events.clear()
    assert hosted_deployment.main(['--apply']) == 0
    output = capsys.readouterr().out
    assert 'private-admin-password' not in output
    assert events == [('connect', 'private-admin-password'), ('apply',), ('audit',)]
    assert json.loads(output)['applied_migrations'] == ['xrbench.sql']


def test_connection_or_sql_failure_cannot_leak_admin_dsn(monkeypatch, capsys):
    monkeypatch.setenv('XRBENCH_MIGRATION_DATABASE_URL', 'private-admin-password')

    def fail(*_):
        raise RuntimeError('postgresql://private-admin-password@private-host')

    monkeypatch.setattr(hosted_deployment, 'Store', fail)
    assert hosted_deployment.main(['--check']) == 2
    output = capsys.readouterr().out
    assert 'private-admin-password' not in output and 'private-host' not in output
    assert json.loads(output)['ready'] is False


def test_read_only_audit_accepts_reviewed_package_and_preserves_state(hosted_db):
    before = hosted_db.scalar('SELECT row_to_json(upload_policy)::text FROM xrbench_private.upload_policy')
    result = hosted_deployment.audit(hosted_db.store, roles=hosted_db.roles)
    assert result['ready'], result['errors']
    assert result['schema_versions'] == [1, 2, 3]
    assert result['quota']['required_reservation_bytes'] == 33 * 1024 * 1024
    assert result['physical_database_bytes'] > 0
    assert result['physical_storage_headroom_verified'] is False
    assert json.loads(json.dumps(result))['ready'] is True
    assert hosted_db.scalar('SELECT row_to_json(upload_policy)::text FROM xrbench_private.upload_policy') == before
    assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_daily') == 0
    assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_reservations') == 0
    assert hosted_db.scalar('SELECT value FROM public.portfolio_sentinel') == 'untouched portfolio'


@pytest.mark.parametrize('target', ['canonical', 'legacy', 'table'])
def test_audit_detects_bad_client_grants_without_repairing_them(hosted_db, target):
    from psycopg import sql

    with hosted_db.connect() as admin:
        if target == 'canonical':
            admin.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.reserve_xrbench_upload(uuid,bigint) TO {}').format(
                sql.Identifier(hosted_db.roles['anon'])))
        elif target == 'legacy':
            admin.execute('GRANT EXECUTE ON FUNCTION public.ingest_xrbench_report(jsonb,jsonb,text,text) TO PUBLIC')
        else:
            admin.execute(sql.SQL('GRANT SELECT ON xrgpu_benchmarks.benchmark_runs TO {}').format(
                sql.Identifier(hosted_db.roles['authenticated'])))
    result = hosted_deployment.audit(hosted_db.store, roles=hosted_db.roles)
    assert not result['ready']
    key = 'api_direct_table_access_denied' if target == 'table' else 'rpc_execute_grants_and_legacy_denials'
    assert result['checks'][key] is False
    # A second check still fails: audit never silently repairs permissions.
    assert not hosted_deployment.audit(hosted_db.store, roles=hosted_db.roles)['ready']


def test_explicit_apply_is_scoped_and_repairs_only_xrbench_grants(hosted_db):
    with hosted_db.connect() as admin:
        admin.execute('GRANT EXECUTE ON FUNCTION public.ingest_xrbench_report(jsonb,jsonb,text,text) TO PUBLIC')
    applied = hosted_deployment.apply_migrations(hosted_db.store, roles=hosted_db.roles)
    assert applied == list(hosted_deployment.MIGRATION_NAMES)
    assert hosted_deployment.audit(hosted_db.store, roles=hosted_db.roles)['ready']
    with hosted_db.connect('anon') as connection:
        assert connection.execute('SELECT value FROM public.portfolio_sentinel').fetchone()[0] == 'untouched portfolio'
        assert connection.execute('SELECT public.portfolio_status()').fetchone()[0] == 'untouched portfolio function'


def test_insufficient_logical_capacity_blocks_readiness(hosted_db):
    hosted_db.policy(storage_limit_bytes=32 * 1024 * 1024)
    result = hosted_deployment.audit(hosted_db.store, roles=hosted_db.roles)
    assert not result['ready'] and result['checks']['quota_policy_and_capacity'] is False


def test_exhausted_global_daily_budget_blocks_readiness(hosted_db):
    hosted_db.policy(global_daily_attempts=1)
    hosted_db.reserve()
    result = hosted_deployment.audit(hosted_db.store, roles=hosted_db.roles)
    assert not result['ready'] and result['checks']['global_daily_capacity'] is False


def test_explicit_package_application_is_atomic_on_failure(hosted_db, tmp_path, monkeypatch):
    import psycopg

    before = hosted_db.scalar('SELECT principal_daily_attempts FROM xrbench_private.upload_policy')
    files = []
    for number, original in enumerate(hosted_deployment.migration_files()):
        source = original.read_text()
        addition = ('UPDATE xrbench_private.upload_policy SET principal_daily_attempts=99;'
                    if number == 0 else 'SELECT 1/0;')
        path = tmp_path / original.name
        path.write_text(source.rsplit('COMMIT;', 1)[0] + addition + '\nCOMMIT;\n')
        files.append(path)
    monkeypatch.setattr(hosted_deployment, 'migration_files', lambda: tuple(files))
    with pytest.raises(psycopg.errors.DivisionByZero):
        hosted_deployment.apply_migrations(hosted_db.store, roles=hosted_db.roles)
    assert hosted_db.scalar('SELECT principal_daily_attempts FROM xrbench_private.upload_policy') == before
