import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {IDBFactory} from 'fake-indexeddb';
import {BrowserStore} from '../web/browser/store.mjs';
import {nativeManifest, operatorReport} from './native-fixtures.mjs';
import {sha256, summarize} from '../web/browser/evidence.mjs';

const bundled = build({entryPoints: [fileURLToPath(new URL('../web/browser/entry.mjs', import.meta.url))],
  bundle: true, write: false, format: 'esm', platform: 'browser', target: 'chrome120'}).then(result => result.outputFiles[0].text);

async function page({indexedDB, manifest, download = true, automatic = false, endpoint = '/api/reports', upload}) {
  let html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
  if (automatic) html = html.replace('<head>', '<head><meta name="xrbench-report-delivery" content="automatic">' +
    '<meta name="xrbench-report-endpoint" content="' + endpoint + '">');
  else if (download) html = html.replace('<head>', '<head><meta name="xrbench-report-delivery" content="download">' +
    '<meta name="xrbench-report-endpoint" content="http://unused.invalid/reports">' +
    '<meta name="xrbench-auth-url" content="https://unused-auth.invalid"><meta name="xrbench-auth-public-key" content="unused">');
  const dom = new JSDOM(html, {url: 'https://bench.example/site/', runScripts: 'outside-only'});
  const window = dom.window, requests = [];
  Object.assign(window, {structuredClone, TextEncoder, TextDecoder, AbortController, AbortSignal,
    ReadableStream, WritableStream, TransformStream});
  Object.defineProperty(window, 'crypto', {value: webcrypto});
  Object.defineProperty(window, 'isSecureContext', {value: true});
  Object.defineProperty(window, 'indexedDB', {value: indexedDB});
  Object.defineProperty(window.navigator, 'usb', {value: {getDevices: async () => [], requestDevice: async () => {throw Error('No USB chooser expected');}}});
  window.fetch = async (value, options = {}) => {
    const url = new URL(value, window.location.href); requests.push({url: url.href, method: options.method || 'GET', options});
    if (automatic && options.method === 'POST') {
      assert.equal(url.href, 'https://bench.example/api/reports');
      assert.equal(options.credentials, 'same-origin'); assert.equal(options.redirect, 'error');
      assert.equal(options.headers.Authorization, undefined); assert.equal(options.headers.apikey, undefined);
      return upload(options);
    }
    assert.equal(options.method || 'GET', 'GET', 'Download-only mode cannot POST reports or contact an authentication service');
    assert.equal(url.href, 'https://bench.example/site/runner/manifest.json');
    return {ok: true, json: async () => structuredClone(manifest)};
  };
  const module = new vm.SourceTextModule(await bundled, {context: dom.getInternalVMContext(),
    initializeImportMeta(meta) {meta.url = 'https://bench.example/site/bench/app.js';}});
  await module.link(name => {throw Error('Unbundled dependency: ' + name);}); await module.evaluate();
  let service;
  try {service = module.namespace.createBrowserService();} catch (error) {window.close(); throw error;}
  return {service, requests, window, async close() {service.dispose(); (await service.controller.store.ready).close(); window.close();}};
}

test('public download mode captures complete native JSON without initializing uploads or authentication, including after reload', async () => {
  const indexedDB = new IDBFactory(), store = new BrowserStore(indexedDB), manifest = nativeManifest();
  const run_id = 'run-' + 'd'.repeat(32), session_id = 'bench-' + 'e'.repeat(32);
  const configuration = {...manifest.configurations.gpu, run_id}, native = operatorReport(configuration, manifest.catalog);
  const originalOutbox = {run_id: 'run-' + 'f'.repeat(32), status: 'pending', body: '{"preserved":"prior queued evidence"}',
    attempts: 2, next_attempt_at: 0, history: [{event: 'queued', at: 1}]};
  await store.saveDelivery(originalOutbox);
  await store.save({session_id, serial: 'private-synthetic-usb', request_id: 'private-request', status: 'running',
    current_phase: 0, created_at: Date.now() / 1000, manifest, identity: {},
    phases: [{processor: 'gpu', backend: configuration.backend, run_id, configuration, status: 'running', attempted: true,
      run: {run_id, status: 'running', configuration, created_at: Date.now() / 1000}}]});
  let active = await page({indexedDB, manifest});
  try {
    assert.equal(active.service.reportDeliveryMode, 'download'); assert.equal(active.service.controller.delivery, null);
    active.service.controller.transport = {supported: true, discover: async () => [], close: async () => {},
      read: async (_, id, file) => file === 'report.json' ? native : {run_id: id, status: 'complete', completed_cases: 221, sequence: 1},
      isRunning: async () => true, launch: async () => {throw Error('Existing runs cannot be launched again');}};
    await active.service.request('/api/status');
    await active.service.controller.tick(active.service.controller.sessions[0], new AbortController().signal);
    for (let i = 0; i < 5; i++) await active.service.request('/api/status');
    assert.equal(active.service.controller.sessions[0].status, 'complete');
    const exported = await active.service.exportSession(session_id);
    assert.equal(JSON.stringify(exported.reports[0]), JSON.stringify(native));
    assert.equal(exported.reports[0].results.length, 221);
    assert.equal(exported.reports[0].results.reduce((sum, row) => sum + row.samples.length, 0), 22100);
    assert.equal(JSON.stringify((await store.report(run_id)).native), JSON.stringify(native));
    assert.deepEqual(await store.deliveries(), [originalOutbox]);
    assert.equal(active.window.document.querySelector('input[type="email"],#upload-auth'), null);
    assert.doesNotMatch(active.window.document.body.textContent, /email|sign in|github/i);
    await active.service.retryReport(originalOutbox.run_id);
    assert.equal(active.requests.length, 1);
    await active.close(); active = await page({indexedDB, manifest});
    for (let i = 0; i < 5; i++) await active.service.request('/api/status');
    const restored = await active.service.exportSession(session_id);
    assert.equal(JSON.stringify(restored.reports[0]), JSON.stringify(native));
    assert.deepEqual(await store.deliveries(), [originalOutbox]);
    assert.equal(active.requests.length, 1); assert.equal(active.requests[0].method, 'GET');
    assert.equal(active.window.document.querySelector('input[type="email"],#upload-auth'), null);
  } finally {await active.close(); (await store.ready).close();}
});

test('hosted automatic mode uploads exact completed results through the same-origin proxy and preserves JSON downloads during outages', async () => {
  for (const outage of [false, true]) {
    const indexedDB = new IDBFactory(), store = new BrowserStore(indexedDB), manifest = nativeManifest();
    const run_id = 'run-' + 'b'.repeat(32), session_id = 'bench-' + 'c'.repeat(32), configuration = {...manifest.configurations.gpu, run_id};
    const native = operatorReport(configuration, manifest.catalog); let unavailable = outage;
    await store.saveReport(run_id, {native, report: summarize(native)});
    await store.save({session_id, serial: 'private-device', request_id: 'private-request', status: 'complete',
      current_phase: 0, created_at: Date.now() / 1000, manifest, identity: {}, phases: [{processor: 'gpu', backend: configuration.backend,
        run_id, configuration, status: 'complete', attempted: true, run: {run_id, status: 'complete', configuration, report_available: true}}]});
    const upload = async options => {
      assert.equal(options.body, JSON.stringify(native));
      return unavailable ? {ok: false, status: 503} : {ok: true, status: 200, json: async () => ({stored: true, storage: 'postgresql', run_id,
        payload_sha256: await sha256(new TextEncoder().encode(options.body))})};
    };
    let p = await page({indexedDB, manifest, automatic: true, upload});
    try {
      assert.equal(p.service.reportDeliveryMode, 'automatic'); assert.ok(p.service.controller.delivery);
      await p.service.request('/api/status'); await p.service.controller.delivery.task;
      const exported = await p.service.exportSession(session_id);
      assert.equal(JSON.stringify(exported.reports[0]), JSON.stringify(native));
      assert.equal((await store.deliveries())[0].status, outage ? 'pending' : 'sent');
      assert.equal((await store.deliveries())[0].body, JSON.stringify(native));
      assert.equal(p.requests.filter(request => request.method === 'POST').length, 1);
      assert.equal(p.window.document.querySelector('input[type="email"],#upload-auth'), null);
      assert.doesNotMatch(p.window.document.body.textContent, /email|sign in|github/i);
      for (let i = 0; i < 5; i++) await p.service.request('/api/status');
      assert.equal(p.requests.filter(request => request.method === 'POST').length, 1, 'Receipt or backoff prevents duplicate uploads');
      if (outage) {
        unavailable = false; p.service.controller.delivery.clock = () => Date.now() + 10000;
        p.service.controller.delivery.kick(); await p.service.controller.delivery.task;
        assert.equal((await store.deliveries())[0].status, 'sent');
        assert.equal(p.requests.filter(request => request.method === 'POST').length, 2);
      }
      await p.close(); p = await page({indexedDB, manifest, automatic: true, upload});
      await p.service.request('/api/status'); await p.service.controller.delivery.task;
      assert.equal(p.requests.filter(request => request.method === 'POST').length, 0, 'Reload never resubmits acknowledged evidence');
      assert.equal(JSON.stringify((await p.service.exportSession(session_id)).reports[0]), JSON.stringify(native));
      assert.equal(JSON.stringify((await store.report(run_id)).native), JSON.stringify(native));
    } finally {await p.close(); (await store.ready).close();}
  }
});

test('explicit hosted automatic metadata cannot route uploads outside the root same-origin proxy', async () => {
  for (const endpoint of ['https://other.example/api/reports', '/site/api/reports', 'https://bench.example/other'])
    await assert.rejects(page({indexedDB: new IDBFactory(), manifest: nativeManifest(), automatic: true, endpoint}), /same-origin/);
});

test('no delivery metadata preserves the existing local automatic PostgreSQL flow', async () => {
  const indexedDB = new IDBFactory(), p = await page({indexedDB, manifest: nativeManifest(), download: false});
  try {
    assert.equal(p.service.reportDeliveryMode, 'local'); assert.ok(p.service.controller.delivery);
    await p.service.request('/api/status'); assert.equal(p.requests.length, 1);
    assert.equal(p.window.document.querySelector('input[type="email"],#upload-auth'), null);
  } finally {await p.close();}
});
