import subprocess
from types import SimpleNamespace

import pytest

from engine import __main__ as launcher
from engine.db import config


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    monkeypatch.delenv("XRGPU_DATABASE_URL", raising=False)
    path = tmp_path / ".env"
    path.write_text("XRGPU_DB_PASSWORD=private-test-password\nXRGPU_DB_PORT=5544\n")
    monkeypatch.setattr(config, "ENV", path)
    monkeypatch.setattr(config, "COMPOSE", tmp_path / "compose.yaml")
    return path


def test_docker_readiness_precedes_bounded_compose_start(local_config, monkeypatch, capsys):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="ready", stderr="")

    monkeypatch.setattr(config.subprocess, "run", run)
    store = config.database()
    assert calls[0][0][:2] == ["docker", "info"]
    assert calls[0][1]["timeout"] == 10
    command, options = calls[1]
    assert command[:2] == ["docker", "compose"]
    assert command[-6:] == ["up", "-d", "--wait", "--wait-timeout", "60", "db"]
    assert command[command.index("--env-file") + 1] == str(local_config)
    assert options["timeout"] == 120
    assert options["check"] and options["capture_output"]
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(store.dsn)["port"] == "5544"
    assert "Starting local PostgreSQL" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["timeout", "unavailable", "missing"])
def test_unavailable_docker_never_launches_compose(local_config, monkeypatch, failure):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "missing":
            raise FileNotFoundError("docker")
        raise subprocess.CalledProcessError(1, command, stderr="Cannot connect to the Docker daemon")

    monkeypatch.setattr(config.subprocess, "run", run)
    with pytest.raises(config.DatabaseStartupError, match="Docker") as error:
        config.database()
    assert len(calls) == 1
    assert "Docker Desktop" in str(error.value)
    if failure == "timeout":
        assert "10 seconds" in str(error.value)


@pytest.mark.parametrize("failure", ["timeout", "container"])
def test_compose_failure_is_actionable_and_redacts_password(local_config, monkeypatch, failure):
    def run(command, **kwargs):
        if command[1] == "info":
            return subprocess.CompletedProcess(command, 0)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        raise subprocess.CalledProcessError(
            1, command, stderr="port is already allocated; private-test-password"
        )

    monkeypatch.setattr(config.subprocess, "run", run)
    with pytest.raises(config.DatabaseStartupError) as error:
        config.database()
    message = str(error.value)
    assert "PostgreSQL container" in message and "container logs" in message
    assert "private-test-password" not in message
    assert "120 seconds" in message if failure == "timeout" else "port is already allocated" in message


def test_existing_database_bypasses_docker(local_config, monkeypatch):
    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: pytest.fail("Docker was invoked"))
    assert config.database(False).dsn
    external = "postgresql://external/database"
    monkeypatch.setenv("XRGPU_DATABASE_URL", external)
    local_config.unlink()
    assert config.database().dsn == external
    assert not local_config.exists()


@pytest.mark.parametrize("failure", ["docker", "interrupt"])
def test_startup_failure_exits_cleanly_before_controller_creation(monkeypatch, capsys, failure):
    monkeypatch.setattr("sys.argv", ["xrbench", "--no-open"])

    def database(start):
        if failure == "interrupt":
            raise KeyboardInterrupt
        raise config.DatabaseStartupError("Docker engine is not responding")

    monkeypatch.setattr(launcher, "database", database)
    monkeypatch.setattr(launcher, "Controller", lambda *a: pytest.fail("Controller was started"))
    with pytest.raises(SystemExit) as error:
        launcher.main()
    if failure == "interrupt":
        assert error.value.code == 130
        assert "startup canceled" in capsys.readouterr().err
    else:
        assert str(error.value) == "Docker engine is not responding"


def test_interrupt_during_migrations_is_also_clean(monkeypatch):
    monkeypatch.setattr("sys.argv", ["xrbench", "--no-open"])

    def migrate():
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher, "database", lambda start: SimpleNamespace(migrate=migrate))
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 130


def test_migrate_only_skips_docker_when_requested_and_does_not_open_browser(monkeypatch, capsys):
    starts, migrations = [], []
    monkeypatch.setattr("sys.argv", ["xrbench", "--migrate-only", "--no-docker"])
    monkeypatch.setattr(launcher, "database", lambda start: (
        starts.append(start) or SimpleNamespace(migrate=lambda: migrations.append(True))
    ))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda *a: pytest.fail("Browser opened"))
    launcher.main()
    assert starts == [False] and migrations == [True]
    assert "migrations applied" in capsys.readouterr().out
