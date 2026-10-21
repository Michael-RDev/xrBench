import {test} from 'node:test';
import assert from 'node:assert/strict';
import {IDBFactory} from 'fake-indexeddb';
import {BrowserStore, acquireDeviceLock} from '../web/browser/store.mjs';
test('IndexedDB saves run identities and native evidence across controller instances', async () => {
  const indexedDB = new IDBFactory(), a = new BrowserStore(indexedDB), b = new BrowserStore(indexedDB);
  const s = {session_id: 'bench-test', status: 'disconnected', cancel_requested: true, phases: [{run_id: 'run-test', attempted: true}]};
  await a.save(s); await a.saveReport('run-test', {native: {status: 'complete'}, report: {counts: {passed: 63}}});
  assert.deepEqual(await b.sessions(), [s]); assert.deepEqual(await b.report('run-test'), await a.report('run-test'));
  (await a.ready).close(); (await b.ready).close();
});
test('version-one storage upgrades without losing native sessions or reports and keeps a durable delivery queue', async () => {
  const indexedDB = new IDBFactory();
  const old = await new Promise((resolve, reject) => {
    const open = indexedDB.open('xrbench-browser-v1', 1);
    open.onupgradeneeded = () => {for (const name of ['sessions', 'reports']) open.result.createObjectStore(name);};
    open.onsuccess = () => resolve(open.result); open.onerror = () => reject(open.error);
  });
  await new Promise(resolve => {
    const tx = old.transaction(['sessions', 'reports'], 'readwrite');
    tx.objectStore('sessions').put({session_id: 'legacy', phases: ['cpu', 'gpu']}, 'legacy');
    tx.objectStore('reports').put({native: {run_id: 'existing'}}, 'existing'); tx.oncomplete = resolve;
  });
  old.close();
  const a = new BrowserStore(indexedDB), b = new BrowserStore(indexedDB);
  assert.equal((await a.sessions())[0].session_id, 'legacy');
  assert.equal((await a.report('existing')).native.run_id, 'existing');
  const row = {run_id: 'existing', body: '{"evidence":"fixture"}', status: 'pending', attempts: 1};
  await a.saveDelivery(row); assert.deepEqual(await b.deliveries(), [row]);
  (await a.ready).close(); (await b.ready).close();
});
test('Web Lock remains held through disconnect until explicitly released', async () => {
  const held = new Set(), locks = {request: async (name, options, work) => {
    assert.equal(options.ifAvailable, true);
    if (held.has(name)) return work(null);
    held.add(name); try {return await work({name});} finally {held.delete(name);}
  }};
  const release = await acquireDeviceLock('one', locks);
  await assert.rejects(() => acquireDeviceLock('one', locks), /another xrBench tab/);
  release(); await new Promise(r => setImmediate(r));
  const again = await acquireDeviceLock('one', locks); again();
});
