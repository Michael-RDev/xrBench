"""Opt-in local PostgreSQL verification of the shipped runtime-role grants."""

import os
from pathlib import Path
import secrets
import uuid

import pytest

from engine.db import Store
from engine.db.config import database
from engine.db.security import connection_options
from engine.reports import summarize


def test_hosted_runtime_role_can_store_reports_but_cannot_mutate_schema_or_history(report):
    if os.environ.get("XRGPU_TEST_POSTGRES") != "1":
        pytest.skip("Set XRGPU_TEST_POSTGRES=1 for isolated local PostgreSQL integration tests")
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    base = database()
    if "host" not in connection_options(base.dsn):
        pytest.skip("Runtime-role integration tests require an explicitly local database")
    suffix = uuid.uuid4().hex
    database_name = "xrgpu_security_test_" + suffix
    role_name = "xrgpu_security_role_" + suffix
    role_password = secrets.token_urlsafe(32)
    with base.connect() as admin:
        superuser, create_role, create_database = admin.execute(
            "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not (superuser or (create_role and create_database)):
            pytest.skip("Local test administrator needs CREATEDB and CREATEROLE")
        assert admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database_name,)).fetchone() is None
        assert admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role_name,)).fetchone() is None

    created_database = False
    try:
        with base.connect() as admin:
            admin.autocommit = True
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
            created_database = True
        parameters = conninfo_to_dict(base.dsn)
        parameters["dbname"] = database_name
        isolated_admin = Store(make_conninfo(**parameters))
        isolated_admin.migrate()
        with isolated_admin.connect() as admin:
            admin.autocommit = True
            source = (Path(__file__).parents[1] / "db/runtime-role.sql").read_text()
            # Execute the shipped grant contract unchanged, except for isolated
            # identifiers and the interactive psql-only password command.
            statements = "\n".join(
                line for line in source.splitlines() if not line.lstrip().startswith("\\password ")
            ).replace("xrbench_runtime", sql.Identifier(role_name).as_string(admin)).replace(
                ':"DBNAME"', sql.Identifier(database_name).as_string(admin)
            )
            admin.execute(statements)
            admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(role_name), sql.Literal(role_password)
            ))
            attributes = admin.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit "
                "FROM pg_roles WHERE rolname=%s", (role_name,)
            ).fetchone()
            assert attributes == (False,) * 6

        parameters.update(user=role_name, password=role_password)
        runtime = Store(make_conninfo(**parameters))
        with runtime.connect() as connection:
            assert connection.execute("SELECT current_user, session_user").fetchone() == (role_name, role_name)
        assert runtime.health()
        normalized = summarize(report)
        receipt = runtime.put(normalized)
        assert runtime.put(normalized) == receipt
        assert runtime.get(report["run_id"]) == normalized
        assert runtime.result(report["run_id"])["receipt"] == receipt
        assert runtime.recent()[0]["run_id"] == report["run_id"]

        denied = [
            "UPDATE xrgpu_benchmarks.benchmark_runs SET status='failed'",
            "DELETE FROM xrgpu_benchmarks.benchmark_runs",
            "UPDATE xrgpu_benchmarks.benchmark_results SET status='failed'",
            "DELETE FROM xrgpu_benchmarks.benchmark_results",
            "INSERT INTO xrgpu_benchmarks.schema_migrations(version) VALUES (9999)",
            "ALTER TABLE xrgpu_benchmarks.benchmark_runs ADD COLUMN forbidden integer",
            "DROP TABLE xrgpu_benchmarks.benchmark_runs CASCADE",
            "CREATE TABLE xrgpu_benchmarks.forbidden(value integer)",
            "CREATE TABLE public.forbidden(value integer)",
            "CREATE TEMP TABLE forbidden(value integer)",
            "CREATE SCHEMA forbidden",
        ]
        for statement in denied:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with runtime.connect() as connection:
                    connection.execute(statement)
        assert runtime.result(report["run_id"])["receipt"] == receipt
        assert runtime.health()
    finally:
        # Only UUID-named objects created by this test are ever removed. Never
        # restart Docker or change grants/credentials in the configured database.
        with base.connect() as admin:
            admin.autocommit = True
            if created_database:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))
