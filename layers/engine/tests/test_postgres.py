import copy
import os
import uuid

import pytest

from layers.engine.db import Store
from layers.engine.reports import summarize


@pytest.fixture
def store():
    psycopg=pytest.importorskip("psycopg")
    if os.environ.get("XRGPU_TEST_POSTGRES")!="1":pytest.skip("Set XRGPU_TEST_POSTGRES=1 for the local PostgreSQL integration tests")
    from layers.engine.db.config import database
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    base=database(False);name="xrgpu_test_"+uuid.uuid4().hex
    with psycopg.connect(base.dsn,autocommit=True) as connection:connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    params=conninfo_to_dict(base.dsn);params["dbname"]=name
    result=Store(make_conninfo(**params))
    try:result.migrate();yield result
    finally:
        with psycopg.connect(base.dsn,autocommit=True) as connection:connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


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
        assert db.execute("SELECT count(*) FROM xrgpu_benchmarks.schema_migrations").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM xrgpu_benchmarks.benchmark_results").fetchone()[0]==1


def test_transaction_rolls_back_on_result_failure(store,report):
    from psycopg import errors
    with store.connect() as db:
        db.execute("ALTER TABLE xrgpu_benchmarks.benchmark_results ADD CONSTRAINT injected_failure CHECK (operation <> 'linear')")
    with pytest.raises(errors.CheckViolation):store.put(summarize(report))
    assert store.get(report["run_id"]) is None


def test_container_restart_preserves_volume(store,report):
    if os.environ.get("XRGPU_TEST_POSTGRES_RESTART")!="1":pytest.skip("Opt in to restarting the local benchmark database")
    if os.environ.get("XRGPU_DATABASE_URL"):pytest.skip("Restart test only applies to the benchmark-local Compose database")
    import subprocess
    from layers.engine.paths import COMPOSE, ENV
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
