import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import yaml

from engine import __main__ as launcher
from engine.db import SchemaNotReady, Store
from engine.db import config
from engine.paths import COMPOSE


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    for key in ('XRGPU_DATABASE_URL', 'XRGPU_DB_PASSWORD', 'XRGPU_DB_PORT'):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / '.env'
    path.write_text('XRGPU_DB_PASSWORD=private-test-password\nXRGPU_DB_PORT=5544\n')
    monkeypatch.setattr(config, 'ENV', path)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('Startup invoked an external process'))
    return path


def test_database_configuration_is_read_only_and_never_starts_docker(local_config):
    from psycopg.conninfo import conninfo_to_dict
    original = local_config.read_bytes()
    params = conninfo_to_dict(config.database().dsn)
    assert params['host'] == '127.0.0.1' and params['port'] == '5544'
    assert params['password'] == 'private-test-password'
    assert local_config.read_bytes() == original
    assert list(local_config.parent.iterdir()) == [local_config]


def test_missing_configuration_requires_manual_setup_without_generating_credentials(local_config):
    local_config.unlink()
    with pytest.raises(config.DatabaseStartupError, match='engine/.env.example'):
        config.database()
    assert not local_config.exists()


def test_external_database_does_not_read_or_create_local_configuration(local_config, monkeypatch):
    monkeypatch.setenv('XRGPU_DATABASE_URL', 'postgresql://external/database')
    local_config.unlink()
    assert config.database().dsn == 'postgresql://external/database'
    assert not local_config.exists()


def test_environment_overrides_match_compose_settings(local_config, monkeypatch):
    from psycopg.conninfo import conninfo_to_dict
    monkeypatch.setenv('XRGPU_DB_PASSWORD', 'another-test-password')
    monkeypatch.setenv('XRGPU_DB_PORT', '5545')
    value = conninfo_to_dict(config.database().dsn)
    assert value['port'] == '5545' and value['password'] == 'another-test-password'


@pytest.mark.parametrize('contents', [
    'XRGPU_DB_PASSWORD=\n', 'XRGPU_DB_PASSWORD=replace-with-your-own-password\n',
    'XRGPU_DB_PASSWORD=private-test-password\nXRGPU_DB_PORT=bad\n',
    'XRGPU_DB_PASSWORD=private-test-password\nXRGPU_DB_PORT=0\n',
    'XRGPU_DB_PASSWORD=private-test-password\nXRGPU_DB_PORT=65536\n',
    'invalid-private-test-password-line\n',
])
def test_invalid_configuration_fails_without_rewriting_or_exposing_credentials(local_config, contents):
    local_config.write_text(contents)
    with pytest.raises(config.DatabaseStartupError) as error:
        config.database()
    assert 'private-test-password' not in str(error.value)
    assert local_config.read_text() == contents


def test_server_start_checks_database_but_never_migrates_or_starts_services(local_config, monkeypatch):
    calls = []
    store = SimpleNamespace(health=lambda: calls.append('health'), migrate=lambda: pytest.fail('Automatic SQL'))
    monkeypatch.setattr(launcher, 'database', lambda: store)
    monkeypatch.setattr(launcher, 'ADB', lambda path: 'adb')
    stopped = []
    controller = SimpleNamespace(stopping=SimpleNamespace(set=lambda: stopped.append(True)))
    monkeypatch.setattr(launcher, 'Controller', lambda *args: calls.append('controller') or controller)
    server = SimpleNamespace(server_port=8766, serve_forever=lambda: calls.append('serve'),
                             server_close=lambda: calls.append('close'))
    monkeypatch.setattr(launcher, 'create_server', lambda *args: server)
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda *args: pytest.fail('Browser opened'))
    monkeypatch.setattr('sys.argv', ['xrbench', '--no-open', '--no-docker', '--port', '8766'])
    launcher.main()
    assert calls == ['health', 'controller', 'serve', 'close'] and stopped == [True]


@pytest.mark.parametrize('failure,expected', [
    (RuntimeError('private-test-password: connection refused'), 'Start the database container manually'),
    (SchemaNotReady('missing'), 'Apply the SQL files'),
])
def test_server_requires_manual_database_and_schema_setup(monkeypatch, failure, expected):
    def health():
        raise failure
    monkeypatch.setattr(launcher, 'database', lambda: SimpleNamespace(health=health))
    monkeypatch.setattr(launcher, 'Controller', lambda *a: pytest.fail('Controller started before setup'))
    monkeypatch.setattr('sys.argv', ['xrbench', '--no-open'])
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert expected in str(error.value)
    assert 'private-test-password' not in str(error.value)


def test_migration_flag_cannot_apply_sql_from_the_server_entry_point(monkeypatch):
    monkeypatch.setattr('sys.argv', ['xrbench', '--migrate-only'])
    monkeypatch.setattr(launcher, 'database', lambda: pytest.fail('Database accessed'))
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2


def test_interrupt_during_readiness_check_exits_cleanly(monkeypatch, capsys):
    def health():
        raise KeyboardInterrupt
    monkeypatch.setattr('sys.argv', ['xrbench', '--no-open'])
    monkeypatch.setattr(launcher, 'database', lambda: SimpleNamespace(health=health))
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 130
    assert 'startup canceled' in capsys.readouterr().err


@pytest.mark.parametrize('versions,ready', [([], False), ([1], False), ([1, 2], False), ([1, 2, 3], True)])
def test_readiness_only_reads_schema_versions_and_tables(monkeypatch, versions, ready):
    calls = []
    def execute(query):
        calls.append(query)
        return [(v,) for v in versions] if 'SELECT version' in query else []
    store = Store('unused')
    monkeypatch.setattr(store, 'connect', lambda: nullcontext(SimpleNamespace(execute=execute)))
    if ready:
        assert store.health()
        assert len(calls) == 3
    else:
        with pytest.raises(SchemaNotReady):
            store.health()
    assert all(query.startswith('SELECT ') for query in calls)


def test_compose_preserves_the_database_volume_and_requires_explicit_start_and_sql():
    compose = yaml.safe_load(COMPOSE.read_text())
    assert compose['name'] == 'xrgpu-benchmarks'
    db = compose['services']['db']
    assert db['restart'] == 'no'
    assert 'benchmark-postgres:/var/lib/postgresql/data' in db['volumes']
    assert './db/migrations:/migrations:ro' in db['volumes']
    assert not any('docker-entrypoint-initdb.d' in path for path in db['volumes'])
