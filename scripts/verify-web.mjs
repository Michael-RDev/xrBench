// Execute the actual production bundle with browser storage and a USB chooser stub.
// This checks packaging and network isolation; it is not physical hardware evidence.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {IDBFactory} from 'fake-indexeddb';
import {BrowserStore} from '../src/bench_web/web/browser/store.mjs';
import {nativeReport} from '../src/bench_web/tests/browser-fixtures.mjs';
import {summarize, sha256} from '../src/bench_web/web/browser/evidence.mjs';

const dir = path.resolve(process.argv[2] || 'dist');
const html = await readFile(path.join(dir, 'index.html'), 'utf8');
assert.match(html, /data-transport="webusb"/);
assert.doesNotMatch(html, /Local runner setup|engine\.build|Checking the local benchmark service/);
const dom = new JSDOM(html, {url: 'https://xrbench.example/site/', runScripts: 'outside-only'});
const w = dom.window, calls = [], intervals = []; let requests = 0;
const manifest = JSON.parse(await readFile(path.join(dir, 'runner/manifest.json'), 'utf8'));
let expectedReport = null, submitted = 0;
Object.assign(w, {structuredClone, TextEncoder, TextDecoder, AbortController, AbortSignal, ReadableStream, WritableStream, TransformStream});
Object.defineProperty(w, 'crypto', {value: webcrypto});
Object.defineProperty(w, 'isSecureContext', {value: true});
Object.defineProperty(w, 'indexedDB', {value: new IDBFactory()});
Object.defineProperty(w.navigator, 'usb', {value: {
  getDevices: async () => [],
  requestDevice: async () => {requests++; throw new w.DOMException('Canceled', 'NotFoundError');},
}});
w.setInterval = f => {intervals.push(f); return intervals.length;};
w.clearInterval = () => {};
w.fetch = async function(value, options) {
  assert.equal(this, w, 'Native fetch must retain its Window receiver');
  const url = new URL(value, w.location.href); calls.push(url.href);
  assert.equal(url.origin, 'https://xrbench.example');
  if (expectedReport && url.pathname === '/site/api/reports') {
    assert.equal(options.method, 'POST'); assert.deepEqual(JSON.parse(options.body), expectedReport); submitted++;
    return {ok: true, json: async () => ({stored: true, storage: 'local', run_id: expectedReport.run_id,
      payload_sha256: await sha256(new TextEncoder().encode(options.body))})};
  }
  assert.equal(url.pathname, '/site/runner/manifest.json', 'Hosted build must only fetch its same-origin distribution; never a local API');
  return {ok: true, json: async () => manifest};
};
const source = await readFile(path.join(dir, 'bench/app.js'), 'utf8');
const module = new vm.SourceTextModule(source, {context: dom.getInternalVMContext(), initializeImportMeta(meta) {meta.url = 'https://xrbench.example/site/bench/app.js';}});
await module.link(name => {throw Error('Unbundled production dependency: ' + name);});
await module.evaluate();
const drain = async () => {for (let i = 0; i < 15; i++) await new Promise(r => setImmediate(r));};
await drain();
try {
  assert.equal(w.document.getElementById('stats-link').hidden, true);
  assert.equal(w.document.getElementById('setup-help'), null);
  assert.equal(w.document.getElementById('run').disabled, false);
  w.document.getElementById('run').click(); await drain();
  assert.equal(requests, 1);
  assert.match(w.document.getElementById('notice-message').textContent, /No headset selected/);
  assert.equal(w.document.getElementById('run').disabled, false);
  assert.equal(calls.length, 1);
  // Recover an explicitly synthetic completed run through the bundled controller.
  // Delivery must use the deployment prefix and never include private session state.
  const store = new BrowserStore(w.indexedDB), run_id = 'run-' + 'd'.repeat(32);
  const configuration = {...manifest.configurations.gpu, run_id, execution_target: 'headset'};
  expectedReport = nativeReport(configuration);
  await store.saveReport(run_id, {native: expectedReport, report: summarize(expectedReport)});
  await store.save({session_id: 'bench-' + 'e'.repeat(32), serial: 'private-fixture-serial', request_id: 'private-request',
    connection_id: 'private-connection', processor: 'gpu', manifest, status: 'complete', current_phase: 0,
    phases: [{processor: 'gpu', configuration, run_id, status: 'complete', run: {run_id, status: 'complete',
      configuration, report_available: true, report_revision: 'fixture', counts: {passed: 63}}}]});
  for (let attempts = 0; attempts < 100 && !w.document.getElementById('report-delivery-status').textContent.includes('Report saved locally.'); attempts++) {
    for (const interval of intervals) await interval();
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.equal(submitted, 1); assert.equal(requests, 1);
  assert.equal(w.document.getElementById('report-delivery-status').textContent, 'Report saved locally.');
  assert.equal((await store.deliveries())[0].status, 'sent');
  assert.equal((await store.report(run_id)).native.results.length, 63);
  (await store.ready).close();
  console.log('Production bundle passed: no localhost/API requests before a run; automatic native report delivery uses the site prefix; Run opens USB chooser; cancellation recovers.');
} finally {w.dispatchEvent(new w.Event('pagehide')); w.close();}
