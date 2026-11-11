"""Exact Python report payloads through real local PostgreSQL migrations."""

import copy

import pytest

from engine.reports import canonical, summarize
from engine.tests.test_hosted_database import hosted_db as _db_fixture
from engine.tests.test_hosted_database import hosted_roles as _roles_fixture

hosted_db = _db_fixture
hosted_roles = _roles_fixture


@pytest.mark.parametrize("value", [1e20, -0.0])
def test_original_canonical_report_survives_jsonb_and_retries(hosted_db, report, value):
    store = hosted_db.store
    report = summarize(copy.deepcopy(report))
    report["device"]["reading"] = value
    original = canonical(report)
    receipt = store.put(report)
    assert canonical(store.get(report["run_id"])) == original
    assert canonical(store.result(report["run_id"])["report"]) == original
    assert store.result(report["run_id"])["receipt"] == receipt
    with store.connect() as connection:
        before = connection.execute(
            "SELECT report,report_canonical,report_sha256,received_at "
            "FROM xrgpu_benchmarks.benchmark_runs WHERE run_id=%s", (report["run_id"],)
        ).fetchone()
    assert before[1].encode("utf-8") == original
    assert canonical(before[0]) != original
    assert before[2] == receipt["sha256"]
    assert store.put(report) == receipt
    store.migrate()
    with store.connect() as connection:
        after = connection.execute(
            "SELECT report,report_canonical,report_sha256,received_at "
            "FROM xrgpu_benchmarks.benchmark_runs WHERE run_id=%s", (report["run_id"],)
        ).fetchone()
    assert after == before


@pytest.mark.parametrize("mutation", [
    "report_canonical='{}'",
    "report_sha256=repeat('0',64)",
    "report=jsonb_set(report,'{device,model}','\"changed\"'::jsonb)",
])
def test_canonical_report_constraint_binds_hash_and_jsonb(hosted_db, report, mutation):
    import psycopg

    store = hosted_db.store
    report = summarize(copy.deepcopy(report))
    receipt = store.put(report)
    with pytest.raises(psycopg.errors.CheckViolation):
        with store.connect() as connection:
            connection.execute("UPDATE xrgpu_benchmarks.benchmark_runs SET " + mutation)
    assert store.result(report["run_id"])["receipt"] == receipt


def test_legacy_upgrade_keeps_hash_and_accepts_only_original_payload(hosted_db, report):
    import psycopg

    store = hosted_db.store
    report = summarize(copy.deepcopy(report))
    report["device"].update(large_counter=1e20, negative_zero=-0.0)
    original = canonical(report)
    receipt = store.put(report)
    with store.connect() as connection:
        connection.execute("ALTER TABLE xrgpu_benchmarks.benchmark_runs DROP COLUMN report_canonical")
        connection.execute("DELETE FROM xrgpu_benchmarks.schema_migrations WHERE version=4")
    store.migrate()
    with store.connect() as connection:
        row = connection.execute(
            "SELECT report_sha256,report_canonical FROM xrgpu_benchmarks.benchmark_runs"
        ).fetchone()
    assert row == (receipt["sha256"], None)
    with pytest.raises(RuntimeError, match="integrity"):
        store.result(report["run_id"])
    with pytest.raises(psycopg.errors.CheckViolation):
        with store.connect() as connection:
            connection.execute("UPDATE xrgpu_benchmarks.benchmark_runs SET report_canonical='{}'")
    with store.connect() as connection:
        connection.execute(
            """UPDATE xrgpu_benchmarks.benchmark_runs SET report_canonical=%s
            WHERE run_id=%s AND report_sha256=%s AND report_canonical IS NULL""",
            (original.decode("utf-8"), report["run_id"], receipt["sha256"]),
        )
    assert canonical(store.result(report["run_id"])["report"]) == original
    assert store.result(report["run_id"])["receipt"] == receipt
