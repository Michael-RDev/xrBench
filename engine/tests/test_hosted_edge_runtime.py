"""Opt-in production Deno bundle -> local Auth/RPC bridge -> real PostgreSQL.

The bridge stands in for Auth and PostgREST, not Supabase's JWT gateway or email
delivery. SQL and the edge handler are the shipped implementations.
"""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from engine.paths import ROOT
from engine.tests.test_hosted_database import hosted_db as hosted_db
from engine.tests.test_hosted_database import hosted_roles as hosted_roles
from engine.tests.test_hosted_database import submission as submission

ORIGIN = 'https://edge-runtime.example'
PRINCIPAL = '11111111-1111-4111-8111-111111111111'
TOKEN = 'fixture.verified.user'
SERVICE_KEY = 'fixture-server-only-service-key'


@contextmanager
def upstream_bridge(database):
    calls = []

    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            calls.append(self.path)
            if (self.path != '/auth/v1/user' or self.headers.get('apikey') != SERVICE_KEY
                    or self.headers.get('Authorization') != 'Bearer ' + TOKEN):
                self.respond(401, {'error': 'invalid test session'})
                return
            self.respond(200, {'id': PRINCIPAL, 'role': 'authenticated', 'is_anonymous': False,
                              'email': 'fixture@example.invalid',
                              'email_confirmed_at': '2026-10-26T00:00:00Z'})

        def do_POST(self):
            calls.append(self.path)
            if (self.headers.get('apikey') != SERVICE_KEY
                    or self.headers.get('Authorization') != 'Bearer ' + SERVICE_KEY):
                self.respond(401, {'error': 'server credential required'})
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size < 32 * 1024 * 1024:
                self.respond(413, {})
                return
            data = json.loads(self.rfile.read(size))
            try:
                if self.path == '/rest/v1/rpc/reserve_xrbench_upload':
                    receipt = database.reserve(data['p_principal'], data['p_max_bytes'])
                elif self.path == '/rest/v1/rpc/ingest_xrbench_report':
                    receipt = database.ingest(data, {'principal': data['p_principal'],
                                                    'reservation_id': data['p_reservation_id']})
                else:
                    self.respond(404, {})
                    return
            except Exception as error:
                self.respond(400, {'code': getattr(error, 'sqlstate', 'unknown')})
                return
            self.respond(200, receipt)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Bridge)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_real_deno_bundle_auth_quota_rpc_and_full_native_report(hosted_db, submission, tmp_path):
    deno = os.environ.get('XRBENCH_TEST_DENO')
    if not deno or not (Path(deno).is_file() or shutil.which(deno)):
        pytest.skip('Set XRBENCH_TEST_DENO to a Deno executable for edge-runtime verification')
    bundle = ROOT / 'supabase/functions/submit-report/receiver.mjs'
    if not bundle.is_file():
        pytest.fail('Build the hosted receiver with npm run build:receiver before runtime tests')
    result = subprocess.run(['node', '--input-type=module', '-e',
        "import {nativeManifest} from './src/bench_web/tests/native-fixtures.mjs';"
        "process.stdout.write(JSON.stringify(nativeManifest()));"], cwd=ROOT,
        capture_output=True, text=True, check=True, timeout=10)
    manifest = json.loads(result.stdout)
    with socket.socket() as free_port:
        free_port.bind(('127.0.0.1', 0))
        port = free_port.getsockname()[1]
    entry = tmp_path / 'edge.mjs'
    entry.write_text(
        'import {createSupabaseReportHandler,hostedConfiguration} from '
        + json.dumps(bundle.as_uri()) + ';\n'
        + 'Deno.serve({hostname:"127.0.0.1",port:' + str(port) + '},'
        + 'createSupabaseReportHandler({...hostedConfiguration(Deno.env),manifests:['
        + json.dumps(manifest) + ']}));\n'
    )
    hosted_db.policy(principal_daily_attempts=2)
    with upstream_bridge(hosted_db) as (upstream, calls), (tmp_path / 'deno.log').open('w') as log:
        environment = dict(os.environ, SUPABASE_URL=upstream, SUPABASE_SERVICE_ROLE_KEY=SERVICE_KEY,
                           XRBENCH_ALLOWED_ORIGINS=json.dumps([ORIGIN]),
                           DENO_DIR=str(tmp_path / 'deno-cache'))
        process = subprocess.Popen([deno, 'run', '--no-prompt', '--no-lock',
            '--allow-env=SUPABASE_URL,SUPABASE_SERVICE_ROLE_KEY,XRBENCH_ALLOWED_ORIGINS',
            '--allow-net=127.0.0.1', str(entry)], env=environment, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    pytest.fail('The production Deno adapter could not start; inspect the isolated runtime log')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    assert time.monotonic() < deadline, 'Deno listener did not start'
                    time.sleep(.02)
            payload = json.dumps(submission['p_report'], separators=(',', ':')).encode()

            def upload(token=None):
                headers = {'Origin': ORIGIN, 'Content-Type': 'application/json'}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                request = Request(f'http://127.0.0.1:{port}/', data=payload, headers=headers)
                try:
                    response = urlopen(request, timeout=30)
                except HTTPError as error:
                    response = error
                with response:
                    return response.status, json.load(response), response.headers

            assert upload()[0] == 401
            assert calls == []
            assert upload('wrong.project.token')[0] == 401
            assert not any('/rpc/' in call for call in calls)
            first_status, first, _ = upload(TOKEN)
            assert first_status == 200 and first['stored'] and first['storage'] == 'postgresql'
            assert first['report_sha256'] == submission['p_report_sha256']
            used = hosted_db.scalar('SELECT storage_used_bytes FROM xrbench_private.upload_policy')
            second_status, second, _ = upload(TOKEN)
            assert second_status == 200 and first == second
            assert hosted_db.scalar('SELECT storage_used_bytes FROM xrbench_private.upload_policy') == used
            assert hosted_db.scalar('SELECT count(*) FROM xrgpu_benchmarks.benchmark_results') == 221
            assert hosted_db.scalar('SELECT sum(jsonb_array_length(samples)) FROM xrgpu_benchmarks.benchmark_results') == 22100
            limited, _, headers = upload(TOKEN)
            assert limited == 429 and int(headers['Retry-After']) > 0
            assert hosted_db.scalar('SELECT count(*) FROM xrbench_private.upload_reservations') == 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
