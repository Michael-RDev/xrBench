import os
import runpy
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from engine import __main__ as launcher, startup
from engine.db import config
from engine.db.config import DatabaseStartupError
from engine.paths import ROOT


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for key in ('XRGPU_DATABASE_URL', 'XRGPU_DB_PASSWORD', 'XRGPU_DB_PORT'):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / '.env'
    path.write_text('XRGPU_DB_PASSWORD=private-test-password\nXRGPU_DB_PORT=5544\n')
    monkeypatch.setattr(startup, 'ENV', path)
    monkeypatch.setattr(config, 'ENV', path)
    events, commands = [], []

    def run(args, **kwargs):
        commands.append((args, kwargs))
        events.append(' '.join(args[:2]))
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

    @contextmanager
    def connect():
        events.append('connect')
        yield SimpleNamespace(execute=lambda query: events.append(query))

    def database():
        store.dsn = config.database().dsn
        return store

    store = SimpleNamespace(connect=connect, migrate=lambda: events.append('migrate'),
                            health=lambda: events.append('health'))
    monkeypatch.setattr(startup.subprocess, 'run', run)
    monkeypatch.setattr(startup, 'database', database)
    monkeypatch.setattr(startup.time, 'sleep', lambda seconds: None)
    return SimpleNamespace(path=path, events=events, commands=commands, store=store)


def test_root_command_runs_everything_in_order_and_keeps_options(setup, monkeypatch):
    monkeypatch.setattr(launcher, 'ADB', lambda path: 'adb')
    controller = SimpleNamespace(stopping=SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(launcher, 'Controller', lambda *args: setup.events.append('controller') or controller)
    ports = []
    def create_server(controller, port):
        ports.append(port)
        return SimpleNamespace(server_port=port, serve_forever=lambda: setup.events.append('serve'), server_close=lambda: None)
    monkeypatch.setattr(launcher, 'create_server', create_server)
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda *a: pytest.fail('Browser opened'))
    monkeypatch.setattr('sys.argv', ['main.py', '--developer', '--no-open'])
    runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
    assert setup.events == ['docker info', 'docker compose', 'connect', 'SELECT 1', 'migrate', 'health', 'health', 'controller', 'serve']
    assert ports == [8766]
    args, kwargs = setup.commands[1]
    assert args[-6:] == ['up', '-d', '--wait', '--wait-timeout', '60', 'db']
    assert kwargs['timeout'] == 180
    assert kwargs['env']['XRGPU_DB_PASSWORD'] == 'private-test-password'
    assert kwargs['env']['XRGPU_DB_PORT'] == '5544'
    assert 'private-test-password' not in ' '.join(args)


def test_default_launch_opens_public_site_without_developer_services(monkeypatch):
    from engine import static_site
    calls = []
    monkeypatch.setattr(static_site, 'main', lambda: calls.append('browser'))
    monkeypatch.setattr(startup, 'prepare_database', lambda **kw: pytest.fail('Developer setup started'))
    monkeypatch.setattr('sys.argv', ['main.py', '--no-open'])
    runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
    assert calls == ['browser']


def test_repeat_launch_reuses_credentials_and_volume(setup):
    original = setup.path.read_bytes()
    startup.prepare_database()
    startup.prepare_database()
    assert setup.path.read_bytes() == original
    assert setup.events.count('migrate') == 2
    assert all('down' not in args and 'rm' not in args for args, _ in setup.commands)
    assert not any(args[:2] == ['docker', 'volume'] for args, _ in setup.commands)


def test_first_launch_creates_private_credentials_once(setup):
    setup.path.unlink()
    startup.prepare_database()
    content = setup.path.read_text()
    password = content.splitlines()[0].split('=', 1)[1]
    assert len(password) == 64 and all(c in '0123456789abcdef' for c in password)
    if os.name != 'nt':
        assert setup.path.stat().st_mode & 0o777 == 0o600
    startup.prepare_database()
    assert setup.path.read_text() == content


def test_missing_credentials_never_replace_an_existing_database(setup, monkeypatch):
    setup.path.unlink()
    original = startup.subprocess.run
    def run(args, **kwargs):
        if args[:2] == ['docker', 'volume']:
            return subprocess.CompletedProcess(args, 0, stdout=startup.DATABASE_VOLUME + '\n')
        return original(args, **kwargs)
    monkeypatch.setattr(startup.subprocess, 'run', run)
    with pytest.raises(DatabaseStartupError, match='original credentials'):
        startup.prepare_database()
    assert not setup.path.exists() and 'docker compose' not in setup.events


def test_external_database_skips_docker_and_local_configuration(setup, monkeypatch):
    setup.path.unlink()
    monkeypatch.setenv('XRGPU_DATABASE_URL', 'postgresql://fixture/test')
    startup.prepare_database()
    assert setup.commands == [] and not setup.path.exists()
    assert setup.events == ['connect', 'SELECT 1', 'migrate', 'health']


def test_explicit_environment_password_needs_no_file_and_stays_out_of_arguments(setup, monkeypatch):
    setup.path.unlink()
    monkeypatch.setenv('XRGPU_DB_PASSWORD', 'another-private-password')
    monkeypatch.setenv('XRGPU_DB_PORT', '5545')
    startup.prepare_database()
    args, kwargs = setup.commands[-1]
    assert '--env-file' not in args
    assert kwargs['env']['XRGPU_DB_PASSWORD'] == 'another-private-password'
    assert kwargs['env']['XRGPU_DB_PORT'] == '5545'
    assert 'another-private-password' not in ' '.join(args)


def test_no_docker_option_still_applies_sql(setup):
    startup.prepare_database(start_container=False)
    assert setup.commands == []
    assert setup.events == ['connect', 'SELECT 1', 'migrate', 'health']


def test_mac_cold_start_opens_desktop_once_then_waits(setup, monkeypatch):
    readiness = iter([False, False, True])
    monkeypatch.setattr(startup.sys, 'platform', 'darwin')
    monkeypatch.setattr(startup, 'docker_ready', lambda: next(readiness))
    startup.start_docker()
    assert [args for args, _ in setup.commands] == [['open', '-a', 'Docker']]


def test_windows_cold_start_opens_desktop_then_waits(setup, monkeypatch):
    readiness = iter([False, True])
    opened = []
    monkeypatch.setattr(startup.sys, 'platform', 'win32')
    monkeypatch.setattr(startup, 'docker_ready', lambda: next(readiness))
    monkeypatch.setattr(startup.subprocess, 'Popen', lambda args, **kwargs: opened.append(args))
    startup.start_docker()
    assert len(opened) == 1 and opened[0][0].endswith('Docker Desktop.exe')


def test_docker_wait_is_bounded_and_does_not_start_postgres(setup, monkeypatch):
    clock = iter([0, 0, 121])
    monkeypatch.setattr(startup.sys, 'platform', 'darwin')
    monkeypatch.setattr(startup, 'docker_ready', lambda: False)
    monkeypatch.setattr(startup.time, 'monotonic', lambda: next(clock))
    with pytest.raises(DatabaseStartupError, match='120 seconds'):
        startup.prepare_database()
    assert 'docker compose' not in setup.events and 'migrate' not in setup.events


@pytest.mark.parametrize('kind', ['missing', 'compose', 'sql', 'connection'])
def test_setup_failures_stop_before_server_launch_and_hide_credentials(setup, monkeypatch, kind):
    original = startup.subprocess.run
    def run(args, **kwargs):
        if kind == 'missing':
            raise FileNotFoundError('docker')
        if kind == 'compose' and args[:2] == ['docker', 'compose']:
            raise subprocess.CalledProcessError(1, args, stderr='private-test-password')
        return original(args, **kwargs)
    monkeypatch.setattr(startup.subprocess, 'run', run)
    if kind == 'sql':
        setup.store.migrate = lambda: (_ for _ in ()).throw(RuntimeError('private-test-password'))
    if kind == 'connection':
        setup.store.connect = lambda: (_ for _ in ()).throw(RuntimeError('private-test-password'))
        clock = iter([0, 31])
        monkeypatch.setattr(startup.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(launcher, 'Controller', lambda *a: pytest.fail('Server started after setup failure'))
    monkeypatch.setattr('sys.argv', ['main.py', '--developer', '--no-open'])
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
    assert 'private-test-password' not in str(error.value)
    assert setup.path.read_text().startswith('XRGPU_DB_PASSWORD=private-test-password')
