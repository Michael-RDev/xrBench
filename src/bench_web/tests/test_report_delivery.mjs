import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ReportDelivery} from '../web/browser/report-delivery.mjs';
import {sha256} from '../web/browser/evidence.mjs';
import {manifest, nativeReport, MemoryStore} from './browser-fixtures.mjs';

const report = status => nativeReport({...manifest().configurations.gpu, run_id: 'run-' + 'a'.repeat(32)}, status);
const acknowledge = async body => ({stored: true, run_id: JSON.parse(body).run_id, storage: 'postgresql', payload_sha256: await sha256(new TextEncoder().encode(body))});
const send = async d => {d.kick(); await d.task;};

test('automatic delivery sends raw evidence once, validates its receipt, and preserves the browser copy', async () => {
  const store = new MemoryStore(), native = report(), calls = [];
  await store.saveReport(native.run_id, {native});
  const d = new ReportDelivery({store, endpoint: 'https://test/site/api/reports', fetcher: async (url, options) => {
    calls.push({url, options}); return {ok: true, json: () => acknowledge(options.body)};
  }});
  await Promise.all([d.queue(native), d.queue(native)]); d.kick(); d.kick(); await d.task;
  await d.queue(native); await send(d);
  assert.equal(calls.length, 1); assert.equal(calls[0].url, 'https://test/site/api/reports');
  assert.deepEqual(JSON.parse(calls[0].options.body), native);
  assert.equal(calls[0].options.credentials, 'omit'); assert.equal(calls[0].options.redirect, 'error');
  assert.equal(d.snapshot().reports[0].status, 'sent');
  assert.equal(d.snapshot().reports[0].receipt.storage, 'postgresql');
  assert.equal((await store.deliveries())[0].body, undefined);
  assert.deepEqual((await store.report(native.run_id)).native, native);
});

test('offline delivery retries after refresh and backoff, without resubmitting acknowledged reports', async () => {
  const store = new MemoryStore(); let time = 0, calls = 0;
  const first = new ReportDelivery({store, clock: () => time, fetcher: async () => {calls++; throw Error('offline');}});
  await first.queue(report()); await send(first); await send(first);
  assert.equal(calls, 1); assert.equal(first.snapshot().reports[0].status, 'pending');
  first.dispose();
  const refreshed = new ReportDelivery({store, clock: () => time, fetcher: async (_, o) => {calls++; return {ok: true, json: () => acknowledge(o.body)};}});
  await send(refreshed); assert.equal(calls, 1);
  time = 5000; await send(refreshed); assert.equal(calls, 2);
  const again = new ReportDelivery({store, fetcher: async () => {throw Error('Already sent');}});
  await send(again); assert.equal(again.snapshot().reports[0].status, 'sent');
});

test('HTTP failures and mismatched receipts retain the exact queued evidence', async () => {
  for (const response of [{ok: false, status: 503}, {ok: false, status: 409},
    {ok: true, json: async () => ({stored: true, run_id: report().run_id, payload_sha256: 'wrong'})}]) {
    const store = new MemoryStore(), d = new ReportDelivery({store, fetcher: async () => response});
    await d.queue(report()); await send(d);
    const [row] = await store.deliveries(); assert.equal(row.status, 'pending');
    assert.deepEqual(JSON.parse(row.body), report()); assert.ok(row.error);
  }
});

test('a file receipt never claims PostgreSQL has stored a report',async()=>{
  const store=new MemoryStore(),d=new ReportDelivery({store,fetcher:async(_,o)=>({ok:true,json:async()=>({...await acknowledge(o.body),storage:'local'})})});
  await d.queue(report());await send(d);
  assert.equal(d.snapshot().reports[0].status,'pending');
  assert.ok((await store.deliveries())[0].body);
});

test('demo identifiers, simulated labels, and unfinished reports never enter the delivery queue', async () => {
  const store = new MemoryStore(), d = new ReportDelivery({store, fetcher: async () => {assert.fail('No simulated upload');}});
  for (const native of [report('running'), {...report(), run_id: 'demo-run-1'}, {...report(), source: 'simulated'},
    {...report(), simulated: true}, {...report(), results: [{simulated: true}]}]) {
    await assert.rejects(d.queue(native), /finished native/);
  }
  await send(d); assert.equal((await store.deliveries()).length, 0);
});

test('timeouts and disposal abort uploads and retain recoverable queue entries', async () => {
  for (const dispose of [false, true]) {
    const store = new MemoryStore(); let started;
    const ready = new Promise(resolve => {started = resolve;});
    const d = new ReportDelivery({store, timeout: dispose ? 30000 : 5, fetcher: async (_, {signal}) => {
      started(); return new Promise((_, reject) => signal.addEventListener('abort', () => reject(Error('aborted')), {once: true}));
    }});
    await d.queue(report()); d.kick(); await ready; if (dispose) d.dispose(); await d.task;
    assert.equal(d.snapshot().reports[0].status, 'pending');
    assert.equal((await store.deliveries())[0].status, 'pending');
  }
});
