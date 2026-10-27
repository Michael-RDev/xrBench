import {test} from 'node:test';
import assert from 'node:assert/strict';
import {nativeManifest, operatorReport} from '../../src/bench_web/tests/native-fixtures.mjs';
import {createSupabaseReportHandler, hostedConfiguration, allowedOrigins, supabaseProjectUrl,
  MAX_HOSTED_REPORT_BYTES} from '../supabase-adapter.mjs';

const project = 'https://project.supabase.co';
const website = 'https://bench.example';
const serviceKey = 'server-only-key-never-an-upload-token';
// Synthetic fixture capability; never used in a deployment.
const proxySecret = Buffer.from(Array.from({length: 32}, (_, index) => index + 1)).toString('base64url');
const userToken = 'signed.user.access_token';
const principal = '11111111-1111-4111-8111-111111111111';
const reservationId = '22222222-2222-4222-8222-222222222222';
const now = Date.parse('2026-10-26T12:00:00Z');
const manifest = nativeManifest();
const user = () => ({id: principal, role: 'authenticated', is_anonymous: false,
  email: 'person@example.invalid', email_confirmed_at: '2026-10-25T00:00:00Z'});
const report = () => ({...operatorReport({...manifest.configurations.gpu,
  run_id: 'run-' + '1'.repeat(32)}, manifest.catalog), status: 'failed', results: []});
const json = (value, status = 200) => new Response(JSON.stringify(value), {status,
  headers: {'Content-Type': 'application/json'}});
const encode = value => new TextEncoder().encode(JSON.stringify(value));
const request = (body = encode(report()), headers = {}) => new Request('https://receiver.example/functions/v1/submit-report',
  {method: 'POST', headers: {Origin: website, 'Content-Type': 'application/json', Authorization: 'Bearer ' + userToken, ...headers},
    body, ...(body instanceof ReadableStream ? {duplex: 'half'} : {})});

function harness(options = {}) {
  const calls = [];
  const fetcher = async (value, init) => {
    const url = new URL(value);
    assert.equal(url.origin, project);
    assert.equal(init.redirect, 'error');
    assert.equal(init.credentials, 'omit');
    assert.ok(init.signal instanceof AbortSignal);
    assert.equal(init.headers.apikey, serviceKey);
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({path: url.pathname, headers: init.headers, body, signal: init.signal});
    if (options.respond) {
      const response = await options.respond(url.pathname, body, init);
      if (response) return response;
    }
    if (url.pathname === '/auth/v1/user') {
      assert.equal(init.headers.Authorization, 'Bearer ' + userToken);
      return json(user());
    }
    assert.equal(init.headers.Authorization, 'Bearer ' + serviceKey);
    if (url.pathname === '/rest/v1/rpc/reserve_xrbench_upload')
      return json({reservation_id: reservationId, principal, max_bytes: MAX_HOSTED_REPORT_BYTES,
        expires_at: new Date(now + 300000).toISOString()});
    if (url.pathname === '/rest/v1/rpc/ingest_xrbench_report')
      return json({stored: true, run_id: body.p_report.run_id,
        payload_sha256: body.p_payload_sha256, report_sha256: body.p_report_sha256,
        received_at: '2026-10-26T12:00:00Z'});
    throw Error('Unexpected upstream route');
  };
  const {respond, ...configuration} = options;
  return {calls, handler: createSupabaseReportHandler({url: project, serviceKey,
    origins: [website], manifests: [manifest], fetcher, clock: () => now, ...configuration})};
}

test('hosted configuration rejects weak/ambiguous endpoints, origins and missing server secrets', () => {
  const environment = {SUPABASE_URL: project, SUPABASE_SERVICE_ROLE_KEY: serviceKey,
    XRBENCH_ALLOWED_ORIGINS: JSON.stringify([website])};
  assert.deepEqual(hostedConfiguration(environment), {url: project, serviceKey, origins: [website]});
  for (const url of ['http://project.supabase.co', 'https://user:password@project.supabase.co',
    project + '/path', project + '?secret=private', project + '#fragment', ' ' + project])
    assert.throws(() => supabaseProjectUrl(url));
  assert.equal(supabaseProjectUrl('http://127.0.0.1:54321'), 'http://127.0.0.1:54321');
  for (const value of ['*', 'null', JSON.stringify(['*']), JSON.stringify([website + '/']),
    JSON.stringify([website, website]), JSON.stringify(['http://bench.example']), '[]'])
    assert.throws(() => allowedOrigins(value));
  assert.throws(() => hostedConfiguration({...environment, SUPABASE_SERVICE_ROLE_KEY: ''}));
  assert.throws(() => hostedConfiguration({...environment, SUPABASE_SERVICE_ROLE_KEY: 'private secret with spaces'}));
  assert.throws(() => createSupabaseReportHandler({url: project, serviceKey, origins: [website], manifests: []}));
  assert.equal(hostedConfiguration({...environment, XRBENCH_UPLOAD_PROXY_SECRET: proxySecret}).uploadProxySecret, proxySecret);
  for (const secret of ['', 'public-api-key', proxySecret + '=', proxySecret.slice(1), ' '.repeat(43), 'A'.repeat(42) + 'B'])
    assert.throws(() => hostedConfiguration({...environment, XRBENCH_UPLOAD_PROXY_SECRET: secret}));
});

test('missing/malformed user tokens and spoofed Origin never reserve quota or read a body', async () => {
  let read = 0;
  const {handler, calls} = harness();
  for (const token of ['', 'Bearer public-api-key', 'Basic signed.user.token',
    'Bearer a.b.c, Bearer d.e.f', 'Bearer ' + serviceKey, 'Bearer ' + 'a'.repeat(9000)]) {
    const body = new ReadableStream({pull() {read++;}});
    const result = await handler(request(body, {Authorization: token}));
    assert.equal(result.status, 401);
    assert.equal(result.headers.get('www-authenticate'), 'Bearer realm="xrbench-upload"');
    assert.doesNotMatch(await result.text(), /server-only-key|signed\.user/);
  }
  assert.equal(calls.length, 0);
  // ReadableStream eagerly pulls once; no additional read can occur in admission.
  assert.equal(read, 6);
  const denied = await handler(request(encode(report()), {Origin: 'https://evil.example'}));
  assert.equal(denied.status, 403);
  assert.equal(calls.length, 0);
});

test('Auth server rejects expired and wrong-project access tokens without database calls', async () => {
  for (const status of [400, 401, 403]) {
    const {handler, calls} = harness({respond: async path => path === '/auth/v1/user' ?
      json({error: 'private token details'}, status) : null});
    const result = await handler(request());
    assert.equal(result.status, 401);
    assert.doesNotMatch(await result.text(), /private token details/);
    assert.deepEqual(calls.map(call => call.path), ['/auth/v1/user']);
  }
});

test('unverified, anonymous and invalid-role users cannot reserve upload capacity', async () => {
  for (const mutate of [value => value.email_confirmed_at = null,
    value => value.is_anonymous = true, value => value.role = 'service_role',
    value => value.id = 'not-an-auth-uuid', value => value.email = '',
    value => value.email_confirmed_at = 'invalid']) {
    const value = user(); mutate(value);
    const {handler, calls} = harness({respond: async path => path === '/auth/v1/user' ? json(value) : null});
    assert.equal((await handler(request())).status, 403);
    assert.equal(calls.length, 1);
  }
});

test('verified identity and reservation are bound to this request, never forwarded identity headers', async () => {
  const {handler, calls} = harness();
  const result = await handler(request(encode(report()), {'X-User-Id': 'attacker',
    'X-Forwarded-For': '1.2.3.4', 'X-Reservation-Id': 'client-choice'}));
  assert.equal(result.status, 200);
  assert.equal((await result.json()).storage, 'postgresql');
  assert.deepEqual(calls.map(call => call.path), ['/auth/v1/user',
    '/rest/v1/rpc/reserve_xrbench_upload', '/rest/v1/rpc/ingest_xrbench_report']);
  assert.deepEqual(calls[1].body, {p_principal: principal, p_max_bytes: MAX_HOSTED_REPORT_BYTES});
  assert.equal(calls[2].body.p_principal, principal);
  assert.equal(calls[2].body.p_reservation_id, reservationId);
  assert.ok(!JSON.stringify(calls[2].body).includes(userToken));
  assert.ok(!JSON.stringify(calls[2].body).includes(serviceKey));
});

test('quota denial happens before report reading and exposes bounded retry timing', async () => {
  let cancelled = false;
  const {handler, calls} = harness({respond: async path => path.endsWith('reserve_xrbench_upload') ?
    json({code: 'P0001', message: 'private quota details'}, 400) : null});
  const body = new ReadableStream({cancel() {cancelled = true;}});
  const response = await handler(request(body));
  assert.equal(response.status, 429);
  assert.equal(response.headers.get('retry-after'), '43200');
  assert.equal(response.headers.get('access-control-expose-headers'), 'Retry-After');
  assert.equal(response.headers.get('access-control-allow-origin'), website);
  assert.doesNotMatch(await response.text(), /private quota details/);
  assert.equal(cancelled, true);
  assert.equal(calls.length, 2);
});

test('invalid, expired and cross-user reservations never reach report storage', async () => {
  for (const mutation of [value => value.reservation_id = 'invalid', value => value.principal = 'attacker',
    value => value.max_bytes = MAX_HOSTED_REPORT_BYTES + 1, value => value.expires_at = new Date(now - 1).toISOString()]) {
    const value = {reservation_id: reservationId, principal, max_bytes: MAX_HOSTED_REPORT_BYTES,
      expires_at: new Date(now + 300000).toISOString()}; mutation(value);
    const {handler, calls} = harness({respond: async path => path.endsWith('reserve_xrbench_upload') ? json(value) : null});
    assert.equal((await handler(request())).status, 503);
    assert.equal(calls.length, 2);
  }
});

test('upstream authentication and RPC failures remain bounded and redact errors', async () => {
  for (const path of ['/auth/v1/user', '/rest/v1/rpc/reserve_xrbench_upload', '/rest/v1/rpc/ingest_xrbench_report']) {
    const {handler} = harness({authTimeoutMs: 10, quotaTimeoutMs: 10, rpcTimeoutMs: 10,
      respond: async (current, _, init) => current === path ? new Promise((_, reject) => {
        init.signal.addEventListener('abort', () => reject(Error('private upstream secret')), {once: true});
      }) : null});
    const start = Date.now();
    const response = await handler(request());
    assert.equal(response.status, 503);
    assert.ok(Date.now() - start < 2000);
    assert.doesNotMatch(await response.text(), /private upstream secret/);
  }
});

test('oversized, malformed and stalled upstream JSON responses cannot consume unlimited resources', async () => {
  for (const response of [() => new Response('not-json'), () => json({padding: 'x'.repeat(65537)}),
    () => new Response(new ReadableStream({start(controller) {controller.enqueue(encode({}));}}))]) {
    const {handler} = harness({authTimeoutMs: 10, respond: async path => path === '/auth/v1/user' ? response() : null});
    assert.equal((await handler(request())).status, 503);
  }
});

test('transaction conflict/reservation failure map safely and incorrect receipts are rejected', async () => {
  for (const [body, upstreamStatus, expected] of [[{code: '23505'}, 409, 409],
    [{code: '42501', message: 'private reservation'}, 403, 503],
    [{code: 'PGRST202', message: 'private schema'}, 404, 503],
    [{stored: true, run_id: report().run_id, payload_sha256: 'wrong', report_sha256: 'wrong'}, 200, 503]]) {
    const {handler} = harness({respond: async path => path.endsWith('ingest_xrbench_report') ? json(body, upstreamStatus) : null});
    const response = await handler(request());
    assert.equal(response.status, expected);
    assert.doesNotMatch(await response.text(), /private reservation|private schema/);
  }
});

test('complete native report and identical retry have stable authenticated PostgreSQL receipts', async () => {
  const {handler, calls} = harness();
  const complete = operatorReport({...manifest.configurations.gpu, run_id: 'run-' + '3'.repeat(32)}, manifest.catalog);
  const payload = encode(complete);
  const first = await handler(request(payload));
  const second = await handler(request(payload));
  assert.equal(first.status, 200); assert.equal(second.status, 200);
  assert.deepEqual(await first.json(), await second.json());
  assert.equal(calls.filter(call => call.path.endsWith('ingest_xrbench_report'))[0].body.p_measurements.length, 221);
});

test('hosted requests enforce8MiB and one concurrent payload even if generic limits are increased', async () => {
  const {handler, calls} = harness({maxInFlight: 64, maxReportBytes: 32 * 1024 * 1024});
  const tooLarge = new Uint8Array(MAX_HOSTED_REPORT_BYTES + 1);
  assert.equal((await handler(request(tooLarge))).status, 413);
  assert.equal(calls.filter(call => call.path.endsWith('ingest_xrbench_report')).length, 0);
  assert.equal(calls[1].body.p_max_bytes, MAX_HOSTED_REPORT_BYTES);
  const abort = new AbortController();
  const paused = new Request('https://receiver.example/functions/v1/submit-report', {method: 'POST',
    headers: {Origin: website, 'Content-Type': 'application/json', Authorization: 'Bearer ' + userToken},
    body: new ReadableStream({}), duplex: 'half', signal: abort.signal});
  const pending = handler(paused);
  assert.equal((await handler(request())).status, 429);
  abort.abort();
  assert.equal((await pending).status, 408);
  assert.equal((await handler(request())).status, 200);
});

test('trusted server proxy derives the anonymous browser principal without an Auth request', async () => {
  const {handler, calls} = harness({uploadProxySecret: proxySecret});
  const headers = {'x-xrbench-proxy-key': proxySecret, 'x-xrbench-principal': principal,
    Authorization: 'Bearer legacy.anon.gateway_jwt'};
  const first = await handler(request(encode(report()), headers));
  const second = await handler(request(encode(report()), headers));
  assert.equal(first.status, 200); assert.equal(second.status, 200);
  assert.deepEqual(await first.json(), await second.json());
  assert.deepEqual(calls.map(call => call.path), ['/rest/v1/rpc/reserve_xrbench_upload',
    '/rest/v1/rpc/ingest_xrbench_report', '/rest/v1/rpc/reserve_xrbench_upload',
    '/rest/v1/rpc/ingest_xrbench_report']);
  assert.equal(calls[0].body.p_principal, principal);
  assert.equal(calls[1].body.p_principal, principal);
  assert.equal(calls[1].body.p_reservation_id, reservationId);
  assert.ok(!JSON.stringify(calls).includes(proxySecret));
});

test('present proxy headers fail closed without both private capability and a valid principal', async () => {
  const wrongFirstByte = Buffer.from(Array.from({length: 32}, (_, index) => index ? index + 1 : 255)).toString('base64url');
  const wrongLastByte = Buffer.from(Array.from({length: 32}, (_, index) => index < 31 ? index + 1 : 255)).toString('base64url');
  for (const headers of [
    {'x-xrbench-principal': principal}, {'x-xrbench-proxy-key': proxySecret},
    {'x-xrbench-proxy-key': '', 'x-xrbench-principal': principal},
    {'x-xrbench-proxy-key': proxySecret, 'x-xrbench-principal': 'attacker'},
    {'x-xrbench-proxy-key': proxySecret, 'x-xrbench-principal': principal + ', ' + principal},
    {'x-xrbench-proxy-key': wrongFirstByte, 'x-xrbench-principal': principal},
    {'x-xrbench-proxy-key': wrongLastByte, 'x-xrbench-principal': principal},
    {'x-xrbench-proxy-key': proxySecret + ', ' + proxySecret, 'x-xrbench-principal': principal},
  ]) {
    const {handler, calls} = harness({uploadProxySecret: proxySecret});
    let cancelled = false;
    const response = await handler(request(new ReadableStream({cancel() {cancelled = true;}}), headers));
    assert.equal(response.status, 401);
    assert.equal(cancelled, true);
    assert.equal(calls.length, 0, 'Invalid proxy assertions must not fall back to even a valid user JWT');
    assert.doesNotMatch(await response.text(), new RegExp(proxySecret));
  }
  const disabled = harness();
  assert.equal((await disabled.handler(request(encode(report()), {
    'x-xrbench-proxy-key': proxySecret, 'x-xrbench-principal': principal}))).status, 401);
  assert.equal(disabled.calls.length, 0);
});

test('verified user compatibility and origin restrictions remain enforced with proxy admission enabled', async () => {
  const {handler, calls} = harness({uploadProxySecret: proxySecret});
  assert.equal((await handler(request())).status, 200);
  assert.equal(calls[0].path, '/auth/v1/user');
  const blocked = await handler(request(encode(report()), {Origin: 'https://evil.example',
    'x-xrbench-proxy-key': proxySecret, 'x-xrbench-principal': principal}));
  assert.equal(blocked.status, 403);
  assert.equal(calls.length, 3);
});

test('proxy uploads still reserve durable quota before reading a body and retain the hosted size cap', async () => {
  const headers = {'x-xrbench-proxy-key': proxySecret, 'x-xrbench-principal': principal, Authorization: ''};
  let cancelled = false;
  const denied = harness({uploadProxySecret: proxySecret,
    respond: async path => path.endsWith('reserve_xrbench_upload') ? json({code: 'P0001'}, 400) : null});
  const result = await denied.handler(request(new ReadableStream({cancel() {cancelled = true;}}), headers));
  assert.equal(result.status, 429); assert.equal(cancelled, true);
  assert.equal(denied.calls.length, 1);
  const oversized = harness({uploadProxySecret: proxySecret});
  assert.equal((await oversized.handler(request(new Uint8Array(MAX_HOSTED_REPORT_BYTES + 1), headers))).status, 413);
  assert.equal(oversized.calls.length, 1);
  assert.equal(oversized.calls[0].body.p_max_bytes, MAX_HOSTED_REPORT_BYTES);
});
