import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BrowserController} from '../web/browser/controller.mjs';
import {EvidenceError} from '../web/browser/evidence.mjs';
import {MissingFile} from '../web/browser/transport.mjs';
import {manifest, nativeReport, MemoryStore} from './browser-fixtures.mjs';

const settle = async () => {for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r));};
function fixture(options = {}) {
  const m = manifest(), store = options.store || new MemoryStore(), launches = [], stops = [], calls = [], devices = [{serial: 'one', name: 'Quest 3'}];
  let connected = true, reportAvailable = false, releaseCount = 0, pauses = [], reports = new Map();
  const transport = {
    supported: true, choose: () => Promise.resolve(devices[0]), discover: async () => connected ? devices : [],
    connect: async () => {calls.push('connect'); if (!connected) throw Error('disconnected');}, close: async () => {},
    properties: async () => ({model: 'Quest 3', sdk: 32, abi: 'arm64-v8a', soc_model: 'Qualcomm Snapdragon XR2 Gen 2'}),
    install: async (m, url, signal, message) => {calls.push('install'); message('Installing'); await options.install?.(signal); signal.throwIfAborted();},
    installedHash: async () => m.apk_sha256,
    isRunning: async () => true,
    probe: async () => {calls.push('probe'); return {profile_version: 1, backends: {executorch_cpu: {compatible: true}, executorch_vulkan: {compatible: !options.noGpu}}};},
    launch: async (_, config) => {launches.push(structuredClone(config)); if (options.launchError) throw Error('USB dropped after launch');},
    read: async (_, id, filename) => {
      if (!connected) throw Error('disconnected');
      const config = launches.find(c => c.run_id === id) || options.previousConfig;
      if (!config) throw new MissingFile();
      if (filename === 'progress.json') return {run_id: id, status: reportAvailable ? 'complete' : 'running', completed_cases: reportAvailable ? 63 : 0, sequence: 1};
      if (!reportAvailable) throw new MissingFile();
      return reports.get(id) || nativeReport(config);
    },
    stop: async (_, id) => {stops.push(id); reports.set(id, nativeReport(launches.find(c => c.run_id === id) || options.previousConfig, 'canceled')); reportAvailable = true;},
  };
  const controller = new BrowserController({transport, store, manifestUrl: 'https://fixture.test/runner/manifest.json',
    fetcher: async () => ({ok: true, json: async () => m}), lock: options.lock || (async () => () => releaseCount++),
    sleep: () => new Promise(r => pauses.push(r))});
  return {m, store, transport, controller, launches, stops, calls, reports, get releaseCount() {return releaseCount;},
    available: () => {reportAvailable = true;}, disconnect: () => {connected = false;}, connect: () => {connected = true;},
    async step({wait = true} = {}) {
      const pending = pauses; pauses = []; pending.forEach(r => r()); await settle();
      // WebCrypto hashes complete on a worker thread, not after a fixed number
      // of event-loop turns. Wait for the next controller sleep or worker exit.
      if (wait) {
        const deadline = Date.now() + 3000;
        while (controller.workers.size && !pauses.length && Date.now() < deadline) await new Promise(r => setTimeout(r, 5));
        assert.ok(!controller.workers.size || pauses.length, 'Controller did not settle');
      }
    },
    async start() {await controller.init(); const s = await controller.start('one', 'same-request'); await settle(); return controller.sessions.find(v => v.session_id === s.session_id);},
    async close() {controller.dispose(); await this.step();}};
}
test('one click installs, probes, then runs exactly 63 CPU and 63 GPU cases', async () => {
  const f = fixture(); try {
    const s = await f.start(); assert.deepEqual(f.calls, ['connect', 'install', 'probe']);
    await f.step(); assert.equal(f.launches.length, 1); assert.equal(f.launches[0].backend, 'executorch_cpu');
    assert.equal(f.launches[0].cases.length, 63); f.available();
    await f.step(); assert.equal(s.current_phase, 1); assert.equal(f.launches.length, 1);
    await f.step(); assert.equal(f.launches.length, 2); assert.equal(f.launches[1].backend, 'executorch_vulkan');
    await f.step(); assert.equal(s.status, 'complete'); assert.equal(f.releaseCount, 1);
    assert.equal((await f.controller.exportSession(s.session_id)).reports.length, 2);
  } finally {await f.close();}
});
test('terminal progress alone never advances and stopping between phases never launches GPU', async () => {
  const f = fixture(); try {
    const s = await f.start(); await f.step();
    const read = f.transport.read;
    f.transport.read = async (...args) => args[2] === 'progress.json' ? {run_id: args[1], status: 'complete', completed_cases: 63} : read(...args);
    await f.step(); assert.equal(s.current_phase, 0); assert.equal(f.launches.length, 1);
    f.available(); await f.step(); assert.equal(s.current_phase, 1);
    await f.controller.stop(s.session_id); await f.step(); assert.equal(s.status, 'canceled'); assert.equal(f.launches.length, 1);
  } finally {await f.close();}
});
test('Stop during installation aborts preparation without probing or launching', async () => {
  const f = fixture({install: signal => new Promise(resolve => signal.addEventListener('abort', resolve, {once: true}))});
  try {const s = await f.start(); await f.controller.stop(s.session_id); await settle(); assert.equal(s.status, 'canceled'); assert.deepEqual(f.calls, ['connect', 'install']); assert.equal(f.launches.length, 0);}
  finally {await f.close();}
});
test('refresh after uncertain launch recovers the same run without replaying install, probe, or launch', async () => {
  const f = fixture({launchError: true}); let g;
  try {
    const s = await f.start(); await f.step(); assert.equal(s.status, 'disconnected');
    const config = f.launches[0]; await f.close();
    g = fixture({store: f.store, previousConfig: config}); g.available();
    await g.controller.snapshot(); await settle();
    assert.equal(g.launches.length, 0); assert.deepEqual(g.calls, ['connect']);
    assert.equal(g.controller.sessions[0].phases[0].run_id, config.run_id);
    assert.equal(g.controller.sessions[0].current_phase, 1);
  } finally {await f.close(); await g?.close();}
});
test('unsupported GPU preserves the completed CPU results', async () => {
  const f = fixture({noGpu: true}); try {
    const s = await f.start(); await f.step(); f.available(); await f.step(); await f.step();
    assert.equal(s.status, 'complete'); assert.equal(s.partial, true); assert.equal(s.phases[1].status, 'unsupported'); assert.equal(f.launches.length, 1);
  } finally {await f.close();}
});

test('headset lifecycle cancellation preserves results and explains how to rerun', async () => {
  const f = fixture(); try {
    const s = await f.start(); await f.step();
    f.reports.set(s.phases[0].run_id, {...nativeReport(f.launches[0], 'canceled'), error: 'Canceled by user or activity lifecycle'});
    f.available(); await f.step();
    await [...f.controller.workers.values()][0]?.task;
    assert.equal(s.status, 'canceled'); assert.equal(s.cancel_requested, false);
    assert.match(s.message, /Keep it awake/); assert.equal(f.launches.length, 1);
    assert.equal((await f.controller.exportSession(s.session_id)).reports.length, 1);
  } finally {await f.close();}
});

test('a native GPU crash preserves CPU and partial GPU evidence without replaying either phase', async () => {
  const f = fixture(); try {
    const s = await f.start(); await f.step(); f.available(); await f.step(); await f.step();
    assert.equal(s.current_phase, 1); assert.equal(f.launches.length, 2);
    f.reports.set(s.phases[1].run_id, nativeReport(f.launches[1], 'running'));
    f.transport.isRunning = async () => false;
    await f.step();
    assert.equal(s.status, 'failed'); assert.equal(s.phases[0].status, 'complete');
    assert.match(s.message, /app stopped before finishing/);
    const exported = await f.controller.exportSession(s.session_id);
    assert.equal(exported.reports.length, 2); assert.equal(exported.reports[1].status, 'running');
    assert.equal(f.launches.length, 2);
  } finally {await f.close();}
});

test('a missing report and stopped app ends an uncertain launch without a replacement', async () => {
  const f = fixture(); try {
    const s = await f.start(); await f.step(); f.transport.isRunning = async () => false;
    await f.step(); assert.equal(s.status, 'failed'); assert.equal(f.launches.length, 1);
    assert.equal(s.current_phase, 0);
  } finally {await f.close();}
});

test('a terminal report written before process exit wins over the liveness check', async () => {
  const f = fixture(); try {
    const s = await f.start(); await f.step(); f.available(); f.transport.isRunning = async () => false;
    await f.step(); assert.equal(s.phases[0].status, 'complete'); assert.equal(s.current_phase, 1);
  } finally {await f.close();}
});
test('malformed evidence and changed website release cannot advance to GPU', async () => {
  for (const corrupt of [true, false]) {
    const f = fixture(); try {
      const s = await f.start(); await f.step(); f.available();
      if (corrupt) {const r = nativeReport(f.launches[0]); r.configuration.backend = 'executorch_vulkan'; f.reports.set(r.run_id, r);}
      else f.controller.manifest = {...f.m, apk_sha256: 'c'.repeat(64)};
      await f.step(); await f.step(); assert.equal(s.status, 'failed'); assert.equal(f.launches.length, 1);
      assert.ok(s.phases.every(p => !p.run || ['complete', 'canceled', 'failed'].includes(p.run.status)));
    } finally {await f.close();}
  }
});
test('cross-tab reservation prevents all device commands', async () => {
  const f = fixture({lock: async () => {throw Error('another tab');}});
  await assert.rejects(() => f.start(), /another tab/); assert.deepEqual(f.calls, []); await f.close();
});
test('double click opens one chooser and makes one session', async () => {
  const f = fixture(); let choose;
  f.transport.choose = () => new Promise(r => {choose = r;});
  try {
    await f.controller.init(); const first = f.controller.start();
    await assert.rejects(() => f.controller.start(), /already open/);
    choose({serial: 'one'}); await first; await settle(); assert.equal(f.store.items.length, 1);
  } finally {await f.close();}
});
test('storage failure before launch never sends a native command', async () => {
  const f = fixture(); try {
    const s = await f.start(); const save = f.store.save.bind(f.store);
    f.store.save = async value => {if (value.phases.some(p => p.attempted)) throw new EvidenceError('disk full'); return save(value);};
    await f.step(); assert.equal(f.launches.length, 0); assert.notEqual(s.status, 'complete');
  } finally {await f.close();}
});

test('Stop while disconnected survives refresh and is delivered without another launch', async () => {
  const f = fixture(); let g;
  try {
    const s = await f.start(); await f.step(); const config = f.launches[0];
    f.disconnect(); await f.step(); assert.equal(s.status, 'disconnected');
    await f.controller.stop(s.session_id); assert.equal((await f.store.sessions())[0].cancel_requested, true);
    await f.close();
    g = fixture({store: f.store, previousConfig: config});
    await g.controller.snapshot();
    await [...g.controller.workers.values()][0]?.task;
    assert.equal(g.controller.sessions[0].status, 'canceled', g.controller.sessions[0].message);
    assert.deepEqual(g.stops, [config.run_id]); assert.equal(g.launches.length, 0);
  } finally {await f.close(); await g?.close();}
});
test('another tab cannot overwrite an active owner with a stale cancellation snapshot', async () => {
  const f = fixture(); let g;
  try {
    const s = await f.start(); await f.step();
    g = fixture({store: f.store, lock: async () => {throw Error('another tab owns this device');}});
    await g.controller.init();
    await assert.rejects(() => g.controller.stop(s.session_id), /another tab/);
    assert.equal((await f.store.sessions())[0].cancel_requested, false);
  } finally {await f.close(); await g?.close();}
});
test('report storage failure preserves the active run and cannot launch the next phase', async () => {
  const f = fixture(); try {
    const s = await f.start(); await f.step(); f.available();
    f.store.saveReport = async () => {throw Error('Browser storage full');};
    await f.step(); assert.equal(s.status, 'disconnected'); assert.equal(s.current_phase, 0); assert.equal(f.launches.length, 1);
  } finally {await f.close();}
});

test('resuming a stale tab reloads the completed session under the device lock', async () => {
  const f = fixture(); let g;
  try {
    const s = await f.start(); await f.step();
    g = fixture({store: f.store}); await g.controller.init();
    f.available();
    // WebCrypto runs off the event loop; fixed setImmediate counts race its completion.
    const deadline = Date.now() + 3000;
    while (s.status !== 'complete' && Date.now() < deadline) {
      await f.step(); await new Promise(resolve => setTimeout(resolve, 5));
    }
    assert.equal(s.status, 'complete');
    await g.controller.resume(g.controller.sessions[0], {serial: 'one'});
    assert.equal(g.controller.sessions[0].status, 'complete');
    assert.deepEqual(g.calls, []); assert.equal(g.launches.length, 0);
  } finally {await f.close(); await g?.close();}
});

test('Stop accepted while launch intent is being saved prevents native execution', async () => {
  const f = fixture(); let release;
  try {
    const s = await f.start(), save = f.store.save.bind(f.store); let hold = true;
    f.store.save = async value => {
      await save(value);
      if (hold && value.phases.some(p => p.attempted)) {
        hold = false; await new Promise(resolve => {release = resolve;});
      }
    };
    await f.step({wait: false}); assert.ok(release);
    await f.controller.stop(s.session_id); release();
    await [...f.controller.workers.values()][0]?.task;
    assert.equal(s.status, 'canceled'); assert.equal(f.launches.length, 0);
  } finally {release?.(); await f.close();}
});
