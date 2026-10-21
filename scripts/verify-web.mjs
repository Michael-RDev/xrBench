// Execute the actual production bundle with browser storage and a USB chooser stub.
// This checks packaging and network isolation; it is not physical hardware evidence.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {IDBFactory} from 'fake-indexeddb';

const dir = path.resolve(process.argv[2] || 'dist');
const html = await readFile(path.join(dir, 'index.html'), 'utf8');
assert.match(html, /data-transport="webusb"/);
assert.doesNotMatch(html, /Local runner setup|engine\.build|Checking the local benchmark service/);
const dom = new JSDOM(html, {url: 'https://xrbench.example/site/', runScripts: 'outside-only'});
const w = dom.window, calls = [], intervals = []; let requests = 0;
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
w.fetch = async function(value) {
  assert.equal(this, w, 'Native fetch must retain its Window receiver');
  const url = new URL(value, w.location.href); calls.push(url.href);
  assert.equal(url.origin, 'https://xrbench.example');
  assert.equal(url.pathname, '/site/runner/manifest.json', 'Hosted build must only fetch its same-origin distribution; never a local API');
  return {ok: true, json: async () => JSON.parse(await readFile(path.join(dir, 'runner/manifest.json'), 'utf8'))};
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
  console.log('Production bundle passed: HTTPS assets only, no localhost/API requests, Run opens USB chooser, cancellation recovers.');
} finally {w.dispatchEvent(new w.Event('pagehide')); w.close();}
