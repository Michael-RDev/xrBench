import copy
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from engine.analysis import metadata
from engine.controller import Controller
from engine.reports import digest, save
from engine.server import handler


def test_committed_results_api_and_frontend_routes(tmp_path, report):
    receipt = dict(stored=True, run_id=report['run_id'], sha256=digest(report))
    envelope = dict(report=copy.deepcopy(report), receipt=receipt)
    offline = [False]
    def result(run_id):
        if offline[0]: raise OSError('offline')
        return envelope if run_id == report['run_id'] else None
    store = SimpleNamespace(result=result, recent=lambda limit, offset: [dict(metadata(report), receipt=receipt)][offset:offset+limit])
    controller = Controller(None, store, tmp_path/'runs', tmp_path, start_workers=False)
    save(controller.state/report['run_id']/'report.json', report)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(controller, 'test-session'))
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    def request(path, token='test-session', body=None):
        return urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
            headers={'X-XRGPU-Session': token, 'Content-Type': 'application/json'}, data=body), timeout=3)
    try:
        for path, text in [('/', b'id="run"'), ('/stats/', b'Stats for Nerds'), ('/stats', b'/stats/app.js'),
                           ('/bench/app.js', b'/api/runs'), ('/shared/session.mjs', b'rememberRun')]:
            with request(path) as response: assert text in response.read()
        with request('/api/v1/results?limit=1&offset=0') as response:
            page = json.load(response)
            assert page['runs'][0]['receipt'] == receipt and page['next_offset'] is None
        with request('/api/v1/results/'+report['run_id']) as response: assert json.load(response) == envelope
        for path, token, body, code in [('/api/v1/results', '', None, 403),
                ('/api/v1/results?limit=101', 'test-session', None, 400),
                ('/api/v1/results/run-'+'0'*32, 'test-session', None, 404),
                ('/api/v1/results', 'test-session', b'{}', 404),
                ('/bench/../../engine/.env', 'test-session', None, 404)]:
            with pytest.raises(urllib.error.HTTPError) as e: request(path, token, body)
            assert e.value.code == code
        offline[0] = True
        with pytest.raises(urllib.error.HTTPError) as e: request('/api/v1/results/'+report['run_id'])
        assert e.value.code == 503
        with request('/api/runs/'+report['run_id']+'/report') as response: assert json.load(response) == report
    finally: server.shutdown(); server.server_close(); worker.join()


def test_database_result_contains_original_receipt(store, report):
    receipt = store.put(report)
    assert store.result(report['run_id']) == dict(report=report, receipt=receipt)
    assert store.result('run-'+'0'*32) is None


from engine.tests.test_postgres import store, uninitialized_store  # noqa: F401


def test_stored_report_hash_is_verified_before_export(store, report):
    store.put(report)
    with store.connect() as db:
        db.execute("UPDATE xrgpu_benchmarks.benchmark_runs SET report_sha256=%s WHERE run_id=%s", ("0"*64,report["run_id"]))
    with pytest.raises(RuntimeError, match="integrity"):
        store.result(report["run_id"])
