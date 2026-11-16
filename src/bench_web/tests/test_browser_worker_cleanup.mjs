import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {AdbDaemonTransport, AdbFeature} from '@yume-chan/adb';
import {BrowserController} from '../web/browser/controller.mjs';
import {WebUsbTransport} from '../web/browser/transport.mjs';
import {manifest, MemoryStore} from './browser-fixtures.mjs';

function deferred() {
  let resolve;
  const promise = new Promise(yes => {resolve = yes;});
  return {promise, resolve};
}
function headset(serial) {
  const raw = {serialNumber: serial, opened: false, async close() {this.opened = false;}};
  return {serial, raw, opens: 0, async connect() {this.opens++; raw.opened = true; return {device: this};}};
}
function fixture(context) {
  const first = headset('first'), second = headset('second');
  const snapshot = deferred(), snapshotEntered = deferred(), preparation = deferred(), secondInstallEntered = deferred();
  context.mock.method(AdbDaemonTransport, 'authenticate', async ({serial, connection}) => ({
    serial, clientFeatures: [AdbFeature.ShellV2], banner: {features: [AdbFeature.ShellV2]},
    disconnected: new Promise(() => {}), close: () => connection.device.raw.close(),
  }));
  const transport = new WebUsbTransport({credentials: {}});
  context.mock.method(transport, 'properties', async () => ({model: 'Quest 3', sdk: 32,
    abi: 'arm64-v8a', soc_model: 'Qualcomm Snapdragon XR2 Gen 2'}));
  context.mock.method(transport, 'installationSnapshot', async () => {
    if (transport.device === first) {
      // This in-flight snapshot does not observe the worker's cancellation signal.
      snapshotEntered.resolve(); await snapshot.promise;
    }
    return {};
  });
  context.mock.method(transport, 'install', async (_, url, signal) => {
    signal.throwIfAborted();
    if (transport.device === second) {secondInstallEntered.resolve(); await preparation.promise;}
    signal.throwIfAborted();
  });
  context.mock.method(transport, 'probe', () => assert.fail('Preparation is held before probing'));
  const released = [], held = new Set(), distribution = manifest();
  const controller = new BrowserController({transport, store: new MemoryStore(),
    manifestUrl: 'https://fixture.test/runner/manifest.json',
    fetcher: async () => ({ok: true, json: async () => distribution}),
    lock: async serial => {
      assert.equal(held.has(serial), false);
      held.add(serial);
      return () => {assert.equal(held.delete(serial), true); released.push(serial);};
    },
  });
  return {first, second, snapshot, snapshotEntered, preparation, secondInstallEntered,
    transport, controller, released,
    async close() {
      controller.dispose(); snapshot.resolve(); preparation.resolve();
      await Promise.allSettled([...controller.workers.values()].map(worker => worker.task));
    }};
}

test('Stop followed by another headset run waits for the old worker to finish cleanup', {timeout: 5000}, async context => {
  const f = fixture(context);
  try {
    const firstSession = await f.controller.start(f.first.serial, 'first-request', 'gpu', f.first);
    await f.snapshotEntered.promise;
    const oldWorker = f.controller.workers.get(firstSession.session_id);
    await f.controller.stop(firstSession.session_id);
    const secondSession = await f.controller.start(f.second.serial, 'second-request', 'gpu', f.second);
    const newWorker = f.controller.workers.get(secondSession.session_id);
    await nextTurn();
    assert.equal(f.second.opens, 0, 'The next worker must wait while old preparation still uses USB');
    f.snapshot.resolve();
    await oldWorker.task;
    await f.secondInstallEntered.promise;
    assert.deepEqual(f.released, [f.first.serial]);
    assert.equal(f.first.raw.opened, false);
    assert.equal(f.second.opens, 1);
    assert.equal(f.second.raw.opened, true);
    assert.equal(f.transport.device, f.second);
    assert.equal(f.transport.adb.serial, f.second.serial);
    await f.controller.stop(secondSession.session_id);
    f.preparation.resolve();
    await newWorker.task;
    assert.equal(f.second.raw.opened, false, 'The new worker must still close its own connection');
    assert.deepEqual(f.released, [f.first.serial, f.second.serial]);
    assert.equal(f.controller.workers.size, 0);
  } finally {await f.close();}
});

test('Stop cancels a queued headset run without opening its USB connection', {timeout: 5000}, async context => {
  const f = fixture(context);
  try {
    const firstSession = await f.controller.start(f.first.serial, 'first-request', 'gpu', f.first);
    await f.snapshotEntered.promise;
    const oldWorker = f.controller.workers.get(firstSession.session_id);
    await f.controller.stop(firstSession.session_id);
    const secondSession = await f.controller.start(f.second.serial, 'second-request', 'gpu', f.second);
    const newWorker = f.controller.workers.get(secondSession.session_id);
    await nextTurn();
    assert.equal(f.second.opens, 0);
    const stopped = await f.controller.stop(secondSession.session_id);
    assert.equal(stopped.status, 'canceled');
    f.snapshot.resolve(); f.preparation.resolve();
    await Promise.all([oldWorker.task, newWorker.task]);
    assert.equal(f.second.opens, 0);
    assert.equal(f.transport.adb, null);
    assert.equal(f.controller.workers.size, 0);
    assert.deepEqual(f.released, [f.first.serial, f.second.serial]);
  } finally {await f.close();}
});

test('runner updates wait for canceled worker cleanup before using the shared transport', {timeout: 5000}, async context => {
  const f = fixture(context);
  context.mock.method(f.transport, 'choose', async () => f.second);
  context.mock.method(f.transport, 'installedHash', async () => null);
  let updating;
  try {
    const firstSession = await f.controller.start(f.first.serial, 'first-request', 'gpu', f.first);
    await f.snapshotEntered.promise;
    const oldWorker = f.controller.workers.get(firstSession.session_id);
    await f.controller.stop(firstSession.session_id);
    const previous = structuredClone((await f.controller.store.sessions())[0]);
    Object.assign(previous, {session_id: 'bench-' + 'b'.repeat(32), request_id: 'previous-second',
      serial: f.second.serial, status: 'disconnected', cancel_requested: false});
    previous.manifest.apk_sha256 = 'f'.repeat(64);
    previous.manifest.apk = previous.manifest.apk_sha256 + '.apk';
    for (const configuration of Object.values(previous.manifest.configurations))
      configuration.apk_sha256 = previous.manifest.apk_sha256;
    await f.controller.store.save(previous);
    f.controller.sessions.push(previous);
    updating = f.controller.upgrade(previous.session_id, 'updated-second');
    await updating;
    updating = f.controller.updates.get(previous.session_id).task;
    await nextTurn();
    assert.equal(f.second.opens, 0, 'Updating a runner must also wait for old preparation to finish');
    f.snapshot.resolve();
    await oldWorker.task;
    await updating;
    const current = f.controller.active();
    await f.secondInstallEntered.promise;
    assert.notEqual(current.session_id, previous.session_id);
    assert.equal(previous.status, 'canceled');
    assert.deepEqual(f.released, [f.first.serial, f.second.serial]);
    assert.equal(f.second.opens, 2, 'Update and the following run each reserve their own connection');
    assert.equal(f.transport.device, f.second);
    assert.equal(f.second.raw.opened, true);
  } finally {
    await f.close();
    await Promise.allSettled([updating]);
  }
});
