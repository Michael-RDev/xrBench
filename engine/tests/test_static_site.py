import threading
import json
import shutil
import subprocess
import urllib.error
import urllib.request

import pytest

from engine.static_site import create_server
from engine.paths import ROOT
from engine.report_delivery import MAX_REPORT_BYTES


def test_browser_server_serves_packaged_assets_without_local_apis(tmp_path):
    for name, contents in [('index.html', '<html data-transport="webusb">Run</html>'),
                           ('bench/app.js', '/* bundled browser */'), ('runner/manifest.json', '{}'),
                           ('runner/example.apk', 'verified-native-runner')]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    server = create_server(tmp_path, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://localhost:{server.server_port}'
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            assert b'data-transport="webusb"' in response.read()
            assert response.headers['Permissions-Policy'] == 'usb=(self)'
        with urllib.request.urlopen(url + '/runner/example.apk', timeout=3) as response:
            assert response.read() == b'verified-native-runner'
        for route in ('/api/status', '/runner/', '/../engine/.env'):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url + route, timeout=3)
            assert error.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url, headers={'Host': 'untrusted.example'}), timeout=3)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_browser_server_requires_a_packaged_release(tmp_path):
    with pytest.raises(FileNotFoundError, match='not been packaged'):
        create_server(tmp_path, 0)


@pytest.fixture
def receiver(tmp_path):
    site = tmp_path / 'site'
    for name in ('index.html', 'bench/app.js', 'runner/manifest.json'):
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')
    reports = tmp_path / 'reports'
    server = create_server(site, 0, reports=reports)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://localhost:{server.server_port}', reports
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_report_endpoint_writes_locally_and_never_serves_reports(receiver, report):
    url, destination = receiver
    payload = json.dumps(report).encode()
    headers = {'Content-Type': 'application/json', 'Origin': url, 'Sec-Fetch-Site': 'same-origin'}
    for _ in range(2):
        with urllib.request.urlopen(urllib.request.Request(url + '/api/reports', data=payload, headers=headers), timeout=3) as response:
            receipt = json.loads(response.read())
        assert receipt['stored'] is True and receipt['storage'] == 'local'
    assert (destination / (report['run_id'] + '.json')).read_bytes() == payload
    for path in ['/api/reports', '/api/reports/' + report['run_id'], '/reports/' + report['run_id'] + '.json']:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url + path, timeout=3)
        assert error.value.code == 404


@pytest.mark.parametrize('extra,expected', [({'Origin': 'https://another-site.example'}, 403),
    ({'Sec-Fetch-Site': 'same-site'}, 403), ({'Host': 'untrusted.example'}, 403),
    ({'Content-Type': 'text/plain'}, 415), ({'Content-Encoding': 'gzip'}, 415),
    ({'Content-Length': str(MAX_REPORT_BYTES + 1)}, 413)])
def test_report_endpoint_rejects_cross_origin_and_unsafe_uploads(receiver, report, extra, expected):
    url, destination = receiver
    request = urllib.request.Request(url + '/api/reports', data=json.dumps(report).encode(),
                                    headers={'Content-Type': 'application/json', **extra})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=3)
    assert error.value.code == expected
    assert not destination.exists()


def test_report_endpoint_validates_and_conflicts(receiver, report):
    url, destination = receiver
    def post(value):
        return urllib.request.urlopen(urllib.request.Request(url + '/api/reports', data=json.dumps(value).encode(),
                                      headers={'Content-Type': 'application/json'}), timeout=3)
    for value in [{'simulated': True}, [], {'schema_version': 2}, {**report, 'results': [None]}]:
        with pytest.raises(urllib.error.HTTPError) as error:
            post(value)
        assert error.value.code == 400
    with post(report):
        pass
    with pytest.raises(urllib.error.HTTPError) as error:
        post({**report, 'device': {'model': 'different'}})
    assert error.value.code == 409


def test_capture_directory_cannot_be_inside_public_site(tmp_path):
    with pytest.raises(ValueError, match='outside'):
        create_server(tmp_path, 0, reports=tmp_path / 'reports')


def test_file_capture_preserves_evidence_without_claiming_postgresql_delivery(receiver):
    """Software-only end-to-end capture; the fixture is not a hardware measurement."""
    if not shutil.which('node'):
        pytest.skip('Node required for browser delivery integration')
    url, destination = receiver
    script = """
import assert from 'node:assert/strict';
import {BrowserController} from './src/bench_web/web/browser/controller.mjs';
import {ReportDelivery} from './src/bench_web/web/browser/report-delivery.mjs';
import {manifest, optimizationReport, MemoryStore} from './src/bench_web/tests/browser-fixtures.mjs';
const m = manifest(), store = new MemoryStore(); let config, launches = 0;
const delivery = new ReportDelivery({store, endpoint: process.argv[2] + '/api/reports'});
const transport = {supported: true, choose: async () => ({serial:'fixture-only'}),
  discover: async () => [], connect: async () => {}, close: async () => {},
  properties: async () => ({model:'Quest 3', sdk:32, abi:'arm64-v8a', soc_model:'Qualcomm Snapdragon XR2 Gen 2'}),
  install: async () => {}, probe: async () => ({backends:{executorch_vulkan:{compatible:true}}}),
  launch: async (_, c) => {config=c; launches++;}, isRunning: async () => false,
  read: async (_, id, file) => file === 'report.json' ? optimizationReport(config) : {run_id:id,status:'complete',completed_cases:63},
};
const controller = new BrowserController({transport, store, delivery, manifestUrl:'https://fixture/runner/manifest.json',
  fetcher: async () => ({ok:true,json:async () => m}), lock:async () => () => {}, sleep:async () => {}});
await controller.start('fixture-only','test-request');
await [...controller.workers.values()][0]?.task; await delivery.task;
assert.equal(controller.sessions[0].status,'complete'); assert.equal(launches,1);
assert.equal(delivery.snapshot().reports[0].status,'pending');
assert.ok((await store.deliveries())[0].body, 'Keep the exact report until PostgreSQL confirms');
const exported = await controller.exportSession(controller.sessions[0].session_id);
assert.equal(exported.reports[0].results.length,63);
assert.deepEqual(exported.reports[0], optimizationReport(config));
assert.equal(config.preset, 'standard');
controller.dispose(); console.log(config.run_id);
"""
    result = subprocess.run(['node', '--input-type=module', '-', url], input=script, cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    saved = json.loads((destination / (result.stdout.strip() + '.json')).read_text())
    assert saved['status'] == 'complete' and saved['backend'] == 'executorch_vulkan'
    assert len(saved['results']) == 63 and saved['device']['model'] == 'synthetic'
    assert saved['configuration']['preset'] == 'standard'
    assert all(len(row['samples']) == 100 for row in saved['results'])
    assert saved['results'][0]['fixture_seed'] == 2026
    assert saved['results'][0]['inputs'] and saved['results'][0]['correctness_after']['passed']
    assert saved['results'][0]['samples'][0]['dispatches'][0]['local_size'] == [4, 4, 1]
    assert saved['shaders']['fixture_shader']['spirv_sha256'] == 'c' * 64
    assert saved['kernel_build']['compiler'] == 'fixture-compiler'
    assert saved['calibration']['available']
    assert 'serial' not in saved and 'connection_id' not in saved
