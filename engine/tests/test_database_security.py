from types import SimpleNamespace

import pytest

from engine.db import Store
from engine.db import migrate
from engine.db.security import DatabaseConnectionError, connection_options


@pytest.fixture(autouse=True)
def clean_pg_environment(monkeypatch):
    import os

    for key in os.environ:
        if key.startswith("PG"):
            monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("dsn", [
    "postgresql://db.example/reporting",
    "host=db.example sslmode=disable",
    "host=db.example sslmode=require",
    "host=db.example sslmode=verify-ca",
    "hostaddr=203.0.113.1",
    "host=127.0.0.1 hostaddr=203.0.113.1",
    "host=127.0.0.1,db.example",
    "host=127.0.0.1,",
    "host=localhost",
    "service=production host=127.0.0.1",
    "dbname=reporting",
])
def test_remote_or_ambiguous_connections_force_authenticated_tls(dsn):
    assert connection_options(dsn) == dict(sslmode="verify-full", gssencmode="disable")


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "/tmp/postgres", "@postgres", "127.0.0.1,::1"])
def test_only_explicit_local_addresses_allow_development_transport(host):
    assert connection_options("host=" + host) == dict(host=host, hostaddr="")


def test_environment_hostaddr_cannot_redirect_local_dsn_without_verified_tls(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.1")
    monkeypatch.setenv("PGSSLMODE", "disable")
    assert connection_options("host=127.0.0.1")["sslmode"] == "verify-full"


def test_service_file_cannot_supply_unverified_remote_transport(monkeypatch):
    monkeypatch.setenv("PGSERVICE", "production")
    assert connection_options("host=127.0.0.1")["sslmode"] == "verify-full"


def test_explicit_local_address_overrides_environment_address(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.1")
    assert connection_options("host=127.0.0.1 hostaddr=127.0.0.1") == dict(
        host="127.0.0.1", hostaddr="127.0.0.1"
    )


def test_connection_boundary_overrides_weak_modes_and_preserves_ca_settings(monkeypatch):
    import psycopg

    monkeypatch.setenv("PGSSLROOTCERT", "/private/ca.crt")
    monkeypatch.setenv("PGSSLMODE", "disable")
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kwargs: calls.append((dsn, kwargs)))
    dsn = "host=db.example sslmode=disable sslrootcert=/trusted/ca.crt gssencmode=prefer"
    Store(dsn).connect()
    assert calls == [(dsn, dict(connect_timeout=3, sslmode="verify-full", gssencmode="disable"))]
    assert __import__("os").environ["PGSSLROOTCERT"] == "/private/ca.crt"


def test_malformed_configuration_and_connection_errors_hide_secrets(monkeypatch):
    import psycopg

    with pytest.raises(DatabaseConnectionError) as error:
        connection_options("this is private-password")
    assert "private-password" not in str(error.value)

    def fail(*args, **kwargs):
        raise psycopg.OperationalError("private-password")

    monkeypatch.setattr(psycopg, "connect", fail)
    with pytest.raises(DatabaseConnectionError) as error:
        Store("host=db.example password=private-password").connect()
    assert "private-password" not in str(error.value)
    assert error.value.__suppress_context__


def test_admin_migration_never_falls_back_to_runtime_credentials(monkeypatch):
    monkeypatch.delenv("XRGPU_MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setenv("XRGPU_DATABASE_URL", "runtime-credentials")
    monkeypatch.setattr(migrate, "Store", lambda *args: pytest.fail("Runtime credentials used for DDL"))
    with pytest.raises(SystemExit, match="XRGPU_MIGRATION_DATABASE_URL"):
        migrate.main()


def test_admin_migration_uses_explicit_credentials_then_checks_health(monkeypatch):
    calls = []
    monkeypatch.setenv("XRGPU_MIGRATION_DATABASE_URL", "admin-credentials")
    monkeypatch.setattr(migrate, "Store", lambda dsn: calls.append(dsn) or SimpleNamespace(
        migrate=lambda: calls.append("migrate"), health=lambda: calls.append("health")))
    migrate.main()
    assert calls == ["admin-credentials", "migrate", "health"]


def test_admin_migration_failure_does_not_echo_credentials(monkeypatch):
    monkeypatch.setenv("XRGPU_MIGRATION_DATABASE_URL", "admin-credentials")

    def fail(*args):
        raise RuntimeError("admin-credentials")

    monkeypatch.setattr(migrate, "Store", fail)
    with pytest.raises(SystemExit) as error:
        migrate.main()
    assert "admin-credentials" not in str(error.value)
