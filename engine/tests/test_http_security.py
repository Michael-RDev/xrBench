"""Regression tests for hostile requests against the local-only HTTP services."""

from contextlib import contextmanager
from functools import partial
import http.client
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from engine.local_http import LocalHTTPServer
from engine.public_server import PublicHandler, PostgresReports
from engine.report_delivery import LocalReportSink
from engine.report_json import MAX_REPORT_BYTES, load_report_json
from engine.server import handler
from engine.static_site import StaticHandler


@contextmanager
def running(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.fixture(params=['static', 'public', 'developer'])
def local_server(request, tmp_path):
    for name, value in [('index.html', 'public page'), ('bench/app.js', 'public JS'),
                        ('bench/style.css', 'public CSS'), ('runner/manifest.json', '{}')]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    if request.param == 'developer':
        request_handler = handler(SimpleNamespace(create=lambda body: {'created': True}), 'test-token')
    else:
        cls = StaticHandler if request.param == 'static' else PublicHandler
        request_handler = partial(cls, directory=str(tmp_path))
    server = LocalHTTPServer(('127.0.0.1', 0), request_handler,
                             max_workers=2, header_timeout=.3, body_timeout=.25)
    server.report_sink = SimpleNamespace(put=lambda payload: {'stored': True})
    server.kind = request.param
    server.root = tmp_path
    with running(server):
        yield server


def request(server, path='/', method='GET', headers=None, body=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    connection.request(method, path, body, headers or {})
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def raw_request(server, payload):
    with socket.create_connection(server.server_address, timeout=2) as connection:
        connection.sendall(payload)
        result = connection.recv(8192)
    return int(result.split(b' ', 2)[1])


@pytest.mark.parametrize('name', ['Forwarded', 'X-Forwarded-For', 'X-Forwarded-Host',
                                 'X-Forwarded-Proto', 'X-Real-IP', 'Via'])
def test_local_servers_refuse_forwarded_traffic(local_server, name):
    path = '/api/session' if local_server.kind == 'developer' else '/'
    assert request(local_server, path, headers={name: 'proxy.example'})[0] == 403
    assert request(local_server, path)[0] == 200


@pytest.mark.parametrize('method', ['GET', 'HEAD'])
def test_public_files_exclude_hidden_unlisted_and_symlink_paths(local_server, tmp_path, method):
    if local_server.kind == 'developer':
        pytest.skip('Developer assets already use an explicit route map')
    root = local_server.root
    (root / '.env').write_text('fake-private-value')
    (root / 'secret.txt').write_text('fake-private-value')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'LICENSE').write_text('fake-private-value')
    (root / 'runner/leak.apk').symlink_to(outside / 'LICENSE')
    (root / 'licenses').mkdir()
    (root / 'licenses/private').symlink_to(outside, target_is_directory=True)
    for path in ('/.env', '/%2eenv', '/secret.txt', '/runner/leak.apk',
                 '/licenses/private/LICENSE', '/runner/%2e%2e/.env',
                 '/runner/%2e%2e%2f.env', '/runner/..\\.env', '/runner/', '/_headers'):
        status, body = request(local_server, path, method)
        assert status == 404, path
        assert b'fake-private-value' not in body
    assert request(local_server, '/', method)[0] == 200
    assert request(local_server, '/bench/app.js?version=1', method)[0] == 200
    (root / 'runner/release.apk').write_text('verified APK')
    assert request(local_server, '/runner/release.apk', method)[0] == 200
    (root / 'licenses/public').mkdir()
    (root / 'licenses/public/LICENSE').write_text('public license')
    assert request(local_server, '/licenses/public/LICENSE', method)[0] == 200


def test_header_deadline_applies_to_slow_drip_requests(local_server):
    started = time.monotonic()
    with socket.create_connection(local_server.server_address, timeout=2) as connection:
        connection.sendall(f'GET / HTTP/1.0\r\nHost: localhost:{local_server.server_port}\r\nX-Slow: '.encode())
        stop = threading.Event()

        def drip():
            while not stop.wait(.025):
                try:
                    connection.sendall(b'x')
                except OSError:
                    return

        sender = threading.Thread(target=drip, daemon=True)
        sender.start()
        try:
            try:
                response = connection.recv(8192)
            except ConnectionResetError:
                response = b''
            assert not response or b' 408 ' in response.split(b'\r\n', 1)[0]
            assert time.monotonic() - started < .7
        finally:
            stop.set()
            sender.join()


def test_worker_limit_rejects_extra_clients_and_recovers(local_server):
    peers = [socket.create_connection(local_server.server_address, timeout=2) for _ in range(2)]
    try:
        for peer in peers:
            peer.sendall(b'GET / HTTP/1.0\r\nX-Incomplete: ')
        status = raw_request(local_server, f'GET / HTTP/1.0\r\nHost: localhost:{local_server.server_port}\r\n\r\n'.encode())
        assert status == 503
    finally:
        for peer in peers:
            peer.close()
    deadline = time.monotonic() + 1
    while request(local_server)[0] == 503:
        assert time.monotonic() < deadline
        time.sleep(.01)
    assert request(local_server)[0] == 200


def test_body_deadline_applies_to_slow_drip_requests(local_server):
    path = '/api/runs' if local_server.kind == 'developer' else '/api/reports'
    started = time.monotonic()
    with socket.create_connection(local_server.server_address, timeout=2) as connection:
        connection.sendall((f'POST {path} HTTP/1.0\r\nHost: localhost:{local_server.server_port}\r\n'
                            'X-XRGPU-Session: test-token\r\nContent-Type: application/json\r\n'
                            'Content-Length: 100\r\n\r\n').encode())
        for _ in range(8):
            try:
                connection.sendall(b' ')
            except OSError:
                break
            time.sleep(.05)
        response = connection.recv(8192)
    assert b' 408 ' in response.split(b'\r\n', 1)[0]
    assert time.monotonic() - started < 1


@pytest.mark.parametrize('framing,expected', [
    ('Content-Length: 2\r\nContent-Length: 2', 400),
    ('Content-Length: 2, 2', 400), ('Content-Length: +2', 400),
    ('Transfer-Encoding: chunked\r\nContent-Length: 2', 415),
    ('Content-Length: 2\r\nContent-Encoding: identity\r\nContent-Encoding: gzip', 415),
])
def test_ambiguous_body_framing_is_rejected(local_server, framing, expected):
    path = '/api/runs' if local_server.kind == 'developer' else '/api/reports'
    payload = (f'POST {path} HTTP/1.0\r\nHost: localhost:{local_server.server_port}\r\n'
               'X-XRGPU-Session: test-token\r\nContent-Type: application/json\r\n'
               f'{framing}\r\n\r\n{{}}').encode()
    assert raw_request(local_server, payload) == expected


@pytest.mark.parametrize('payload', [
    b'{"run_id":"first","run_id":"second"}', b'{"padding":' + b'[' * 1000 + b'0' + b']' * 1000 + b'}',
    json.dumps({'padding': 'x' * 65537}).encode(), json.dumps({'padding': [0] * 25001}).encode(),
    json.dumps({str(i): 0 for i in range(257)}).encode(), b'{"x":NaN}', b'{"x":1e999}',
    b'{"x":"\\ud800"}', b' ' * (MAX_REPORT_BYTES + 1),
])
def test_direct_report_sinks_reject_resource_abuse_before_storage(tmp_path, payload):
    calls = []
    postgres = PostgresReports(SimpleNamespace(put=lambda value: calls.append(value)), [])
    for sink in [LocalReportSink(tmp_path / 'reports'), postgres]:
        with pytest.raises(ValueError):
            sink.put(payload)
    assert not calls and not (tmp_path / 'reports').exists()


def test_bounded_parser_preserves_native_operator_fixture():
    import copy
    from engine.tests.test_native_public import native_fixture
    report, _ = native_fixture()
    passed = report['results'][0]
    report['results'] = [dict(copy.deepcopy(passed), case=row['case'], operation=row['operation'])
                         for row in report['results']]
    assert len(report['results']) == 221
    assert sum(len(row['samples']) for row in report['results']) == 22100
    assert load_report_json(json.dumps(report).encode()) == report
