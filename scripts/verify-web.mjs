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
import {optimizationReport} from '../src/bench_web/tests/browser-fixtures.mjs';
import {operatorReport} from '../src/bench_web/tests/native-fixtures.mjs';
import {summarize, sha256} from '../src/bench_web/web/browser/evidence.mjs';

const dir = path.resolve(process.argv[2] || 'dist');
const html = await readFile(path.join(dir, 'index.html'), 'utf8');
assert.match(html, /data-transport="webusb"/);
assert.doesNotMatch(html, /Local runner setup|engine\.build|Checking the local benchmark service/);
let hosting = null;
try {hosting = JSON.parse(await readFile(path.join(dir, 'hosting-manifest.json'), 'utf8'));}
catch (error) {if (error.code !== 'ENOENT') throw error;}
const site = new URL(hosting?.site_path || '/site/', hosting?.site_origin || 'https://xrbench.example');
const dom = new JSDOM(html, {url: site.href, runScripts: 'outside-only'});
const w = dom.window, calls = [], intervals = []; let requests = 0;
const downloadOnly = w.document.querySelector('meta[name="xrbench-report-delivery"]')?.content === 'download';
const receiverOutage = !downloadOnly && process.argv.includes('--receiver-outage');
const receiver = downloadOnly ? null : new URL(w.document.querySelector('meta[name="xrbench-report-endpoint"]')?.content || './api/reports', w.document.baseURI);
const scriptUrl = new URL(w.document.querySelector('script[type="module"][src]').src);
const runnerUrl = new URL('../runner/manifest.json', scriptUrl);
const downloads = [];
w.Blob = Blob;
w.URL.createObjectURL = blob => {downloads.push(blob); return 'blob:fixture-download';};
w.URL.revokeObjectURL = () => {};
// Capture generated downloads without navigating the simulated document.
w.HTMLAnchorElement.prototype.click = function() {assert.match(this.download, /\.json$/);};
const manifest = JSON.parse(await readFile(path.join(dir, 'runner/manifest.json'), 'utf8'));
assert.equal(manifest.configurations.gpu.preset, 'standard');
const typed=manifest.protocol_version===2,count=typed?221:63;
assert.equal(manifest.catalog.cases.length,count);
assert.equal(manifest.configurations.gpu.calibration, typed?'none':'auto');
assert.equal(manifest.configurations.gpu.profiling ?? 'trace', typed?'timestamps':'trace');
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
w.fetch = async function(value, options = {}) {
  assert.equal(this, w, 'Native fetch must retain its Window receiver');
  const url = new URL(value, w.location.href); calls.push(url.href);
  if (expectedReport && receiver && url.href === receiver.href) {
    assert.equal(options.headers.Authorization, undefined);
    assert.equal(options.credentials, receiver.origin === site.origin ? 'same-origin' : 'omit');
    assert.equal(options.redirect, 'error');
    assert.equal(options.method, 'POST'); assert.deepEqual(JSON.parse(options.body), expectedReport); submitted++;
    if (receiverOutage) return {ok: false, status: 503};
    return {ok: true, status: 200, json: async () => ({stored: true, storage: 'postgresql', run_id: expectedReport.run_id,
      payload_sha256: await sha256(new TextEncoder().encode(options.body))})};
  }
  assert.equal(url.origin, site.origin);
  assert.equal(url.href, runnerUrl.href, 'Hosted build must only fetch its same-origin distribution before completion.');
  return {ok: true, json: async () => manifest};
};
const source = await readFile(path.join(dir, 'bench/app.js'), 'utf8');
const module = new vm.SourceTextModule(source, {context: dom.getInternalVMContext(), initializeImportMeta(meta) {meta.url = scriptUrl.href;}});
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
  assert.equal(w.document.querySelector('#upload-auth,input[type="email"]'), null, 'Visitors must not need a sign-in or email form.');
  // Recover an explicitly synthetic completed run through the bundled controller.
  // Delivery must use the deployment prefix and never include private session state.
  const store = new BrowserStore(w.indexedDB), run_id = 'run-' + 'd'.repeat(32);
  const configuration = {...manifest.configurations.gpu, run_id, execution_target: 'headset'};
  expectedReport = typed?operatorReport(configuration,manifest.catalog):optimizationReport(configuration);
  await store.saveReport(run_id, {native: expectedReport, report: summarize(expectedReport)});
  await store.save({session_id: 'bench-' + 'e'.repeat(32), serial: 'private-fixture-serial', request_id: 'private-request',
    connection_id: 'private-connection', processor: 'gpu', manifest, status: 'complete', current_phase: 0,
    phases: [{processor: 'gpu', configuration, run_id, status: 'complete', run: {run_id, status: 'complete',
      configuration, report_available: true, report_revision: 'fixture', counts: {passed: count}}}]});
  const deliveryFinished = async () => {
    const row = (await store.deliveries())[0];
    return receiverOutage ? row?.status === 'pending' && row.attempts >= 1 : row?.status === 'sent';
  };
  for (let attempts = 0; attempts < 100 && (downloadOnly ?
    w.document.getElementById('previous-runs').hidden : !await deliveryFinished()); attempts++) {
    for (const interval of intervals) await interval();
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.equal(submitted, downloadOnly ? 0 : 1); assert.equal(requests, 1);
  assert.equal(w.document.getElementById('results').hidden, true);
  assert.equal(w.document.getElementById('previous-runs').hidden, false);
  assert.equal(w.document.getElementById('previous-runs').open, false);
  w.document.querySelector('#previous-runs-list button').click(); await drain();
  if (downloadOnly) {
    assert.equal(calls.length, 1, 'The public website must only fetch its local runner manifest.');
    assert.equal(w.document.querySelector('#upload-auth'), null);
    assert.deepEqual(await store.deliveries(), [], 'No public submission is queued.');
  } else if (receiverOutage) {
    assert.equal((await store.deliveries())[0].status, 'pending');
    assert.match(w.document.getElementById('report-delivery-status').textContent, /saved in this browser.*retry automatically/);
    assert.equal((await store.deliveries())[0].body, JSON.stringify(expectedReport));
  } else {
    assert.equal(w.document.getElementById('report-delivery-status').textContent, 'Report sent. A copy stays in this browser.');
    assert.equal((await store.deliveries())[0].status, 'sent');
  }
  const download = w.document.getElementById('download-session');
  assert.equal(download.hidden, false, 'Exact results must remain downloadable even when automatic uploads fail.');
  download.click(); await drain();
  assert.equal(downloads.length, 1);
  const exported = JSON.parse(await downloads[0].text());
  assert.deepEqual(exported.reports[0], expectedReport, 'Downloaded native evidence must remain exact.');
  assert.deepEqual((await store.report(run_id)).native, expectedReport);
  assert.equal(expectedReport.results.length, count);
  assert.ok(expectedReport.results.every(row => row.samples.length === 100));
  (await store.ready).close();
  console.log(downloadOnly ? 'Hosted production bundle passed: Run and exact results JSON download work without sign-in, email, or upload requests; USB cancellation recovers.' :
    receiverOutage ? 'Production bundle passed: upload failure preserves the exact report outbox and JSON download without rerunning the benchmark.' :
      'Production bundle passed: completed results upload automatically with same-origin credentials where appropriate, exact JSON downloads remain available, and no contact or sign-in form is required.');
} finally {w.dispatchEvent(new w.Event('pagehide')); w.close();}
