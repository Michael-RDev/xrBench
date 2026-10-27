import copy
import os
import uuid

import pytest

from engine.db import SchemaNotReady, Store, migration_files
from engine.reports import summarize


@pytest.fixture
def uninitialized_store():
    psycopg=pytest.importorskip("psycopg")
    if os.environ.get("XRGPU_TEST_POSTGRES")!="1":pytest.skip("Set XRGPU_TEST_POSTGRES=1 for the local PostgreSQL integration tests")
    from engine.db.config import database
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    base=database();name="xrgpu_test_"+uuid.uuid4().hex
    with psycopg.connect(base.dsn,autocommit=True) as connection:connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params=conninfo_to_dict(base.dsn);params["dbname"]=name
    result=Store(make_conninfo(**params))
    try:yield result
    finally:
        with psycopg.connect(base.dsn,autocommit=True) as connection:connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def store(uninitialized_store):
    uninitialized_store.migrate()
    return uninitialized_store


def test_operator_sql_files_initialize_upgrade_and_repeat_without_losing_reports(uninitialized_store, report):
    store = uninitialized_store
    with pytest.raises(SchemaNotReady):
        store.health()
    first, *remaining = migration_files()
    with store.connect() as db:
        db.autocommit = True
        db.execute(first.read_text())
    with pytest.raises(SchemaNotReady):
        store.health()
    receipt = store.put(summarize(report))
    with store.connect() as db:
        db.autocommit = True
        for path in remaining:db.execute(path.read_text())
    assert store.health()
    with store.connect() as db:
        db.autocommit = True
        for path in migration_files():
            db.execute(path.read_text())
    assert store.result(report['run_id'])['receipt'] == receipt
    assert store.health()


def test_external_setup_reopens_prepared_schema_without_losing_reports(uninitialized_store, report, monkeypatch):
    from engine.startup import prepare_database
    uninitialized_store.migrate()
    monkeypatch.setenv('XRGPU_DATABASE_URL', uninitialized_store.dsn)
    store = prepare_database()
    receipt = store.put(summarize(report))
    reopened = prepare_database()
    assert reopened.health()
    assert reopened.result(report['run_id'])['receipt'] == receipt


def test_failed_manual_sql_does_not_record_a_version_or_leave_partial_tables(uninitialized_store):
    from psycopg.errors import DuplicateTable
    store = uninitialized_store
    with store.connect() as db:
        db.execute('CREATE SCHEMA xrgpu_benchmarks')
        db.execute('CREATE TABLE xrgpu_benchmarks.benchmark_results (existing integer)')
    with pytest.raises(DuplicateTable):
        with store.connect() as db:
            db.autocommit = True
            db.execute(migration_files()[0].read_text())
    with store.connect() as db:
        assert db.execute("SELECT to_regclass('xrgpu_benchmarks.benchmark_runs')").fetchone()[0] is None
        assert db.execute("SELECT to_regclass('xrgpu_benchmarks.schema_migrations')").fetchone()[0] is None
        assert db.execute("SELECT to_regclass('xrgpu_benchmarks.benchmark_results')").fetchone()[0]


def test_migrations_persistence_and_conflict(store,report):
    store.migrate();report=summarize(report)
    receipt=store.put(report)
    assert receipt["stored"]
    reopened=Store(store.dsn)
    assert reopened.get(report["run_id"])==report
    assert reopened.put(report)==receipt
    changed=copy.deepcopy(report);changed["device"]["model"]="different"
    with pytest.raises(FileExistsError):reopened.put(changed)
    assert len(reopened.recent())==1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM xrgpu_benchmarks.schema_migrations").fetchone()[0]==len(migration_files())
        assert db.execute("SELECT count(*) FROM xrgpu_benchmarks.benchmark_results").fetchone()[0]==1


def test_native_operator_report_commits_all_rows_and_retries_idempotently(store, tmp_path):
    from engine.tests.test_native_public import native_fixture
    from engine.public_server import PostgresReports
    import json
    report, manifest = native_fixture()
    receiver = PostgresReports(store, [manifest])
    payload = json.dumps(report).encode()
    from engine.public_server import create_server
    import threading
    from urllib.request import Request, urlopen
    server = create_server(store, tmp_path, port=0, manifests=[manifest])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(f'http://localhost:{server.server_port}/api/reports', data=payload,
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=10) as response:
            receipt = json.load(response)
        assert receipt['storage'] == 'postgresql'
    finally:
        server.shutdown();server.server_close();thread.join()
    assert receiver.put(payload) == receipt
    stored = store.get(report['run_id'])
    assert stored['schema_version'] == 3
    assert stored['native_report'] == report
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM xrgpu_benchmarks.benchmark_results').fetchone()[0] == 221


def test_transaction_rolls_back_on_result_failure(store,report):
    from psycopg import errors
    with store.connect() as db:
        db.execute("ALTER TABLE xrgpu_benchmarks.benchmark_results ADD CONSTRAINT injected_failure CHECK (operation <> 'linear')")
    with pytest.raises(errors.CheckViolation):store.put(summarize(report))
    assert store.get(report["run_id"]) is None


def test_headset_assessment_independently_checks_native_json_receipt_and_result_rows(store):
    import json
    from engine.headset_validation import verify_database
    from engine.public_server import PostgresReports
    from engine.tests.test_native_public import native_fixture

    report, manifest = native_fixture()
    receipt = PostgresReports(store, [manifest]).put(json.dumps(report).encode())
    verify_database(store, report, receipt)
    assert "counts" not in report and "timing_valid" not in report["results"][0]
    with pytest.raises(ValueError, match="digest"):
        verify_database(store, report, dict(receipt, report_sha256="0" * 64))
    with store.connect() as connection:
        connection.execute("""UPDATE xrgpu_benchmarks.benchmark_results
            SET result = jsonb_set(result, '{status}', %s::jsonb)
            WHERE run_id=%s AND case_id=%s""",
            (json.dumps("failed"), report["run_id"], report["results"][0]["case"]))
    with pytest.raises(ValueError, match="result rows"):
        verify_database(store, report, receipt)


@pytest.mark.parametrize("change", [
    "UPDATE xrgpu_benchmarks.benchmark_results SET status='failed' WHERE run_id=%s",
    "UPDATE xrgpu_benchmarks.benchmark_results SET trial=1 WHERE run_id=%s",
    "UPDATE xrgpu_benchmarks.benchmark_results SET samples='[]'::jsonb WHERE run_id=%s",
    "UPDATE xrgpu_benchmarks.benchmark_runs SET status='failed' WHERE run_id=%s",
    "UPDATE xrgpu_benchmarks.benchmark_runs SET finished_at=finished_at + interval '1 second' WHERE run_id=%s",
])
def test_headset_database_checks_indexed_columns_as_well_as_native_json(store, change):
    import json
    from engine.headset_validation import verify_database
    from engine.public_server import PostgresReports
    from engine.tests.test_native_public import native_fixture

    report, manifest = native_fixture()
    receipt = PostgresReports(store, [manifest]).put(json.dumps(report).encode())
    with store.connect() as connection:
        connection.execute(change, (report["run_id"],))
    with pytest.raises(ValueError, match="indexed|result rows"):
        verify_database(store, report, receipt)


def test_container_restart_preserves_volume(store,report):
    if os.environ.get("XRGPU_TEST_POSTGRES_RESTART")!="1":pytest.skip("Opt in to restarting the local benchmark database")
    if os.environ.get("XRGPU_DATABASE_URL"):pytest.skip("Restart test only applies to the benchmark-local Compose database")
    import subprocess
    from engine.paths import COMPOSE, ENV
    receipt=store.put(summarize(report))
    subprocess.run(["docker","compose","--project-name","xrgpu-benchmarks","--env-file",str(ENV),"-f",str(COMPOSE),"up","-d","--force-recreate","--wait","db"],check=True,capture_output=True)
    store.migrate()
    assert store.get(report["run_id"])==report
    assert store.put(report)["sha256"]==receipt["sha256"]


def test_history_pagination_and_optional_provenance(store,report):
    records=[]
    for i in range(3):
        r=copy.deepcopy(report);r['run_id']='run-'+str(i)*32
        if i:r['configuration']['apk_sha256']=str(i)*64
        records.append(r);store.put(summarize(r))
    page=store.recent(2,0);tail=store.recent(2,2)
    assert len(page)==2 and len(tail)==1
    assert not {r['run_id'] for r in page}&{r['run_id'] for r in tail}
    assert all('results' not in r and 'receipt' in r for r in page+tail)
    legacy=next(r for r in page+tail if r['run_id']==records[0]['run_id'])
    assert legacy['apk_sha256'] is None
    assert legacy['configuration']['cases']==['linear_odd']


def test_resident_backend_reports_are_stored_without_changing_legacy_rows(store,report):
    # Restore the v1 constraint in this disposable database, then upgrade with data.
    with store.connect() as db:
        db.execute("ALTER TABLE xrgpu_benchmarks.benchmark_runs DROP CONSTRAINT benchmark_runs_backend_name_check")
        db.execute("ALTER TABLE xrgpu_benchmarks.benchmark_runs ADD CONSTRAINT benchmark_runs_backend_check CHECK (backend = 'executorch_vulkan')")
        db.execute("DELETE FROM xrgpu_benchmarks.schema_migrations WHERE version = 2")
    first=store.put(summarize(report))
    store.migrate()
    resident=copy.deepcopy(report)
    resident.update(run_id='run-'+uuid.uuid4().hex,backend='vulkan_resident',execution_location='phone')
    resident['configuration'].update(backend='vulkan_resident',execution_target='phone',cases=['matmul_odd'])
    resident['results'][0].update(case='matmul_odd',operation='matmul')
    receipt=store.put(summarize(resident))
    assert store.get(resident['run_id'])['backend']=='vulkan_resident'
    assert store.result(report['run_id'])['receipt']==first
    assert store.result(resident['run_id'])['receipt']==receipt
