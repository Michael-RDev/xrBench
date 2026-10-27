import {test} from 'node:test';
import assert from 'node:assert/strict';
import {nativeManifest, operatorReport} from '../../src/bench_web/tests/native-fixtures.mjs';
import {MAX_REPORT_BYTES, REPORT_LIMITS, ReportLimitError, prepareSubmission} from '../report-contract.mjs';
import {createAdmissionPolicy, createReportHandler, supabaseStore} from '../report-receiver.mjs';

const manifest = nativeManifest();
const encode = value => new TextEncoder().encode(JSON.stringify(value));
const report = () => ({...operatorReport({...manifest.configurations.gpu,
  run_id: 'run-' + '1'.repeat(32)}, manifest.catalog), status: 'failed', results: []});
const origin = 'https://bench.example';
function request(body = encode(report()), options = {}) {
  return new Request('https://receiver.example/api/reports', {method: 'POST',
    headers: {origin, 'content-type': 'application/json', ...options.headers},
    body, ...(body instanceof ReadableStream ? {duplex: 'half'} : {}), ...options});
}
function receiver(options = {}) {
  const calls = [];
  const handler = createReportHandler({origins: [origin], manifests: [manifest],
    admit: async () => true,
    store: async submission => {calls.push(submission); return {stored: true};}, ...options});
  return {handler, calls};
}

// The raw HTTP Origin header is intentionally spoofed, as in the review PoC.
test('spoofed allowed Origin cannot reach storage without trusted admission', async () => {
  const {handler, calls} = receiver({admit: undefined});
  assert.equal((await handler(request())).status, 503);
  assert.equal(calls.length, 0);
  const denied = receiver({admit: async () => false});
  assert.equal((await denied.handler(request())).status, 403);
  assert.equal(denied.calls.length, 0);
});

test('verified identity and atomic shared quota must both approve before body is read', async () => {
  let authenticated = false, remaining = 1, cancellations = 0;
  const debits = [];
  const admit = createAdmissionPolicy({authenticate: async () => authenticated ? 'verified-user' : null,
    consumeQuota: async ({principal, maxBytes}) => {debits.push({principal, maxBytes}); return remaining-- > 0;}});
  const {handler, calls} = receiver({admit});
  const heldBody = () => new ReadableStream({cancel() {cancellations++;}});
  assert.equal((await handler(request(heldBody()))).status, 401);
  assert.equal(debits.length, 0);
  authenticated = true;
  assert.equal((await handler(request())).status, 200);
  assert.equal((await handler(request(heldBody()))).status, 429);
  assert.deepEqual(debits[0], {principal: 'verified-user', maxBytes: MAX_REPORT_BYTES});
  assert.equal(calls.length, 1);
  assert.equal(cancellations, 2);
});

test('stalled body times out, cancels promptly even when cancel never resolves, and frees capacity', async () => {
  let cancelled = false;
  const {handler, calls} = receiver({maxInFlight: 1, uploadTimeoutMs: 15});
  const stalled = new ReadableStream({start(controller) {controller.enqueue(new Uint8Array([123]));},
    cancel() {cancelled = true; return new Promise(() => {});}});
  assert.equal((await handler(request(stalled))).status, 408);
  assert.equal(cancelled, true);
  assert.equal(calls.length, 0);
  assert.equal((await handler(request())).status, 200);
});

test('active uploads are capped and request abort also frees a receiver slot', async () => {
  let entered;
  const ready = new Promise(resolve => {entered = resolve;});
  const {handler} = receiver({maxInFlight: 1, uploadTimeoutMs: 1000});
  const abort = new AbortController();
  const pending = handler(request(new ReadableStream({pull() {entered();}}), {signal: abort.signal}));
  await ready;
  assert.equal((await handler(request())).status, 429);
  abort.abort();
  assert.equal((await pending).status, 408);
  assert.equal((await handler(request())).status, 200);
});

test('admission deadlines and storage failures release receiver capacity', async () => {
  let waiting = true;
  const {handler} = receiver({maxInFlight: 1, admissionTimeoutMs: 15,
    admit: async () => waiting ? new Promise(() => {}) : true});
  assert.equal((await handler(request())).status, 503);
  waiting = false;
  assert.equal((await handler(request())).status, 200);
  let unavailable = true;
  const other = receiver({maxInFlight: 1, store: async () => {
    if (unavailable) throw Error('private storage failure');
    return {stored: true};
  }});
  const failed = await other.handler(request());
  assert.equal(failed.status, 503);
  assert.doesNotMatch(await failed.text(), /private storage failure/);
  unavailable = false;
  assert.equal((await other.handler(request())).status, 200);
});

test('storage deadline aborts its I/O and releases the receiver slot', async () => {
  let stall = true, cancelled = false;
  const {handler} = receiver({maxInFlight: 1, storeTimeoutMs: 15, store: async (_, {signal}) => {
    if (!stall) return {stored: true};
    return new Promise((_, reject) => signal.addEventListener('abort', () => {
      cancelled = true; reject(Error('cancelled private storage operation'));
    }, {once: true}));
  }});
  assert.equal((await handler(request())).status, 503);
  assert.equal(cancelled, true);
  stall = false;
  assert.equal((await handler(request())).status, 200);
});

test('Supabase service credentials cannot be sent to an unencrypted remote endpoint', async () => {
  let contacted = false;
  const store = supabaseStore({url: 'http://database.example', serviceKey: 'private-test-key',
    fetcher: async () => {contacted = true; throw Error('should not connect');}});
  await assert.rejects(store({}), /secure endpoint/);
  assert.equal(contacted, false);
});

test('HTTP and direct contract reject excessive bytes, depth, strings and collections', async () => {
  const {handler, calls} = receiver();
  assert.equal((await handler(request(encode(report()), {headers: {
    origin, 'content-type': 'application/json', 'content-length': String(MAX_REPORT_BYTES + 1)}}))).status, 413);
  await assert.rejects(prepareSubmission(new Uint8Array(MAX_REPORT_BYTES + 1), [manifest]), ReportLimitError);
  const deep = new TextEncoder().encode('['.repeat(10000) + '0' + ']'.repeat(10000));
  assert.equal((await handler(request(deep))).status, 413);
  await assert.rejects(prepareSubmission(deep, [manifest]), ReportLimitError);
  for (const extra of ['x'.repeat(REPORT_LIMITS.string + 1), Array(REPORT_LIMITS.array + 1).fill(0),
    Object.fromEntries(Array.from({length: REPORT_LIMITS.keys + 1}, (_, i) => ['field' + i, 0]))]) {
    const payload = encode({...report(), extra});
    await assert.rejects(prepareSubmission(payload, [manifest]), ReportLimitError);
    assert.equal((await handler(request(payload))).status, 413);
  }
  const tooManyNodes = encode({...report(), extra: Array.from({length: 100}, () => Array(10000).fill(0))});
  await assert.rejects(prepareSubmission(tooManyNodes, [manifest]), ReportLimitError);
  assert.equal((await handler(request(tooManyNodes))).status, 413);
  assert.equal(calls.length, 0);
});

test('streamed oversize reports cancel without relying on content-length', async () => {
  let cancelled = false;
  const body = new ReadableStream({start(controller) {controller.enqueue(new Uint8Array(MAX_REPORT_BYTES + 1));},
    cancel() {cancelled = true;}});
  const {handler, calls} = receiver();
  assert.equal((await handler(request(body))).status, 413);
  assert.equal(cancelled, true);
  assert.equal(calls.length, 0);
});

test('complete 221-operator reports and idempotent retries retain the same content hashes', async () => {
  const complete = operatorReport({...manifest.configurations.gpu, run_id: 'run-' + '2'.repeat(32)}, manifest.catalog);
  // Quotes, slashes and braces in metadata cannot fool the nesting precheck.
  complete.device.note = String.raw`diagnostic "quoted" text \\ { [ ] }`;
  const payload = encode(complete);
  const {handler, calls} = receiver();
  assert.equal((await handler(request(payload))).status, 200);
  assert.equal((await handler(request(payload))).status, 200);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].p_measurements.length, 221);
  assert.equal(calls[0].p_report_sha256, calls[1].p_report_sha256);
  assert.equal(calls[0].p_payload_sha256, calls[1].p_payload_sha256);
});
