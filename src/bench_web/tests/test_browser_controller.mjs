import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BrowserController} from '../web/browser/controller.mjs';
import {EvidenceError} from '../web/browser/evidence.mjs';
import {MissingFile} from '../web/browser/transport.mjs';
import {ReportDelivery} from '../web/browser/report-delivery.mjs';
import {sha256} from '../web/browser/evidence.mjs';
import {manifest, nativeReport, MemoryStore} from './browser-fixtures.mjs';

const settle = async () => {for (let i = 0; i < 30; i++) await new Promise(r => setImmediate(r));};

test('a previous runner cannot block the next release and its checkpoint is preserved',async()=>{
  const f=fixture();let g;
  try{
    const old=await f.start();await f.step();const previousConfig=structuredClone(f.launches[0]);
    await f.close();
    g=fixture({store:f.store,previousConfig,manifest:m=>{
      m.apk_sha256='f'.repeat(64);m.apk=m.apk_sha256+'.apk';
      for(const c of Object.values(m.configurations))c.apk_sha256=m.apk_sha256;
    }});
    const checkpoint=nativeReport(previousConfig,'running');g.available();g.reports.set(previousConfig.run_id,checkpoint);
    const commands=[];g.transport.shell=async args=>{commands.push(args);return {code:0,stdout:''};};
    const snapshot=await g.controller.snapshot();await g.step();
    assert.equal(snapshot.benchmark_sessions[0].runner_outdated,true);
    assert.equal(g.launches.length,0,'Loading a new website never replays the previous launch');
    const current=await g.controller.upgrade(old.session_id,'new-release-request');
    for(let i=0;i<3&&!g.launches.length;i++)await g.step();
    assert.deepEqual(commands,[['am','force-stop',old.manifest.package]]);
    assert.ok((await g.store.report(previousConfig.run_id)).native);
    assert.equal(g.store.items.find(s=>s.session_id===old.session_id).status,'canceled');
    assert.notEqual(current.session_id,old.session_id);
    assert.equal(g.launches.length,1);
    assert.equal(g.launches[0].apk_sha256,'f'.repeat(64));
  }finally{await f.close();await g?.close();}
});

test('USB detection does not claim authorization or identify the processor', async () => {
  const f = fixture();
  try {
    const snapshot = await f.controller.snapshot();
    assert.equal(snapshot.devices[0].connection_state, 'detected');
    assert.equal(snapshot.devices[0].supported, false); assert.equal(snapshot.devices[0].identity, undefined);
    assert.equal(f.calls.length, 0);
  } finally {await f.close();}
});

test('busy USB stays blocked across polls and refresh; explicit Retry reuses the same run', async () => {
  let occupied = true, g;
  const connect = () => {if (occupied) throw Error('The device is already in used by another program');};
  const f = fixture({connect});
  try {
    const s = await f.start(); await f.step(); const runId = s.phases[0].run_id;
    assert.equal(s.connection_error.code, 'usb_busy'); assert.equal(s.connection_state, 'blocked');
    assert.equal(f.releaseCount, 1); assert.equal(f.launches.length, 0);
    for (let i = 0; i < 4; i++) {
      const snapshot = await f.controller.snapshot(); await settle();
      assert.equal(snapshot.devices[0].connection_state, 'blocked');
    }
    assert.equal(f.calls.filter(c => c === 'connect').length, 1);
    await f.close();
    g = fixture({store: f.store, connect}); await g.controller.snapshot(); await g.step();
    assert.equal(g.calls.length, 0);
    occupied = false;
    await Promise.all([g.controller.retry(s.session_id), g.controller.retry(s.session_id), g.controller.snapshot()]);
    await settle(); await g.step();
    assert.equal(g.store.items.length, 1); assert.equal(g.calls.filter(c => c === 'connect').length, 1);
    assert.equal(g.launches.length, 1); assert.equal(g.launches[0].run_id, runId);
  } finally {await f.close(); await g?.close();}
});

test('a physical reconnect makes one busy retry, and Cancel needs no device connection', async () => {
  const f = fixture({connect: () => {throw Error('The device is already in use by another program');}});
  try {
    const s = await f.start(); await f.step();
    await f.controller.snapshot(); f.disconnect(); await f.controller.snapshot();
    f.connect(); await f.controller.snapshot(); await f.step();
    for (let i = 0; i < 3; i++) await f.controller.snapshot();
    assert.equal(f.calls.filter(c => c === 'connect').length, 2);
    f.disconnect();
    assert.equal((await f.controller.stop(s.session_id)).status, 'canceled');
    await f.controller.snapshot(); f.connect(); await f.controller.snapshot(); await f.step();
    assert.equal(f.calls.filter(c => c === 'connect').length, 2);
    assert.equal(f.launches.length, 0); assert.equal(f.stops.length, 0);
  } finally {await f.close();}
});

test('legacy busy sessions without connection metadata do not reconnect on page load', async () => {
  const f = fixture();
  try {
    const m = manifest();
    await f.store.save({session_id: 'bench-' + 'c'.repeat(32), serial: 'one', status: 'disconnected', current_phase: 0,
      message: 'The device is already in used by another program', manifest: m,
      phases: [{processor: 'gpu', configuration: m.configurations.gpu, run_id: 'run-' + 'd'.repeat(32), status: 'pending'}]});
    const snapshot = await f.controller.snapshot(); await f.step();
    assert.equal(snapshot.devices[0].connection_state, 'blocked'); assert.equal(f.calls.length, 0);
    assert.match(snapshot.benchmark_sessions[0].message, /Close other headset tools/);
    assert.match(snapshot.devices[0].connection_message, /Retry connection/);
  } finally {await f.close();}
});

test('concurrent recovery reserves one worker and one native launch', async () => {
  const f = fixture(); let g;
  try {
    const s = await f.start(); await f.close();
    g = fixture({store: f.store}); await g.controller.init();
    await Promise.all([g.controller.resume(g.controller.sessions[0], {serial: 'one'}),
      g.controller.resume(g.controller.sessions[0], {serial: 'one'}), g.controller.snapshot()]);
    await g.step();
    assert.equal(g.calls.filter(c => c === 'connect').length, 1);
    assert.equal(g.launches.length, 1); assert.equal(g.launches[0].run_id, s.phases[0].run_id);
  } finally {await f.close(); await g?.close();}
});
function fixture(options = {}) {
  const m = structuredClone(manifest()); options.manifest?.(m);
  const store = options.store || new MemoryStore(), launches = [], stops = [], calls = [], devices = [{serial: 'one', name: 'Quest 3'}];
  let connected = true, reportAvailable = false, releaseCount = 0, pauses = [], reports = new Map();
  const transport = {
    supported: true, choose: () => Promise.resolve(devices[0]), discover: async () => connected ? devices : [],
    connect: async (_, signal, message, trace) => {calls.push('connect'); if (!connected) throw Error('disconnected'); await options.connect?.(signal, trace); signal?.throwIfAborted();}, close: async () => {},
    properties: async () => ({model: 'Quest 3', sdk: 32, abi: 'arm64-v8a', soc_model: 'Qualcomm Snapdragon XR2 Gen 2', ...options.properties}),
    install: async (m, url, signal, message) => {calls.push('install'); message('Installing'); await options.install?.(signal); signal.throwIfAborted();},
    installedHash: async () => m.apk_sha256,
    isRunning: async () => true,
    probe: async () => {calls.push('probe'); return {profile_version: 1, backends: {executorch_cpu: {compatible: true}, executorch_vulkan: {compatible: !options.noGpu}}, ...options.capabilities};},
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
    delivery: options.delivery?.(store),
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
    async start(processor) {
      if(options.legacy) {
        const saved = structuredClone(m);
        for (const config of Object.values(saved.configurations)) config.preset = 'smoke';
        await store.save({session_id:'bench-'+'a'.repeat(32),request_id:'same-request',serial:'one',status:'starting',created_at:Date.now()/1000,current_phase:0,cancel_requested:false,manifest:saved,
          phases:['cpu','gpu'].map((p,i)=>({processor:p,backend:saved.configurations[p].backend,configuration:structuredClone(saved.configurations[p]),run_id:'run-'+String(i+1).repeat(32),status:'pending'}))});
      }
      await controller.init(); const s = await controller.start('one', 'same-request', ...(processor ? [processor] : [])); await settle(); return controller.sessions.find(v => v.session_id === s.session_id);},
    async close() {controller.dispose(); await this.step();}};
}
test('finished native runs send their reports automatically without sending USB/session state', async () => {
  for (const status of ['complete', 'failed', 'canceled']) {
    const sent = [], f = fixture({connect: (signal, trace) => trace({attempt: 1, event: 'connected', stage: 'authenticating', elapsed_ms: 1}),
      delivery: store => new ReportDelivery({store, endpoint: '/api/reports', fetcher: async (url, {body}) => {
      sent.push(JSON.parse(body)); return {ok: true, json: async () => ({stored: true, storage: 'postgresql', run_id: sent.at(-1).run_id,
        payload_sha256: await sha256(new TextEncoder().encode(body))})};
    }})});
    try {
      const s = await f.start(); await f.step(); assert.equal(sent.length, 0);
      const native = nativeReport(f.launches[0], status); f.reports.set(native.run_id, native); f.available();
      await f.step(); await f.controller.delivery.task;
      assert.equal(s.status, status); assert.equal(sent.length, 1); assert.deepEqual(sent[0], native);
      assert.equal(sent[0].results.length, 63); assert.equal(sent[0].backend, 'executorch_vulkan');
      assert.equal(sent[0].serial, undefined); assert.equal(sent[0].connection_id, undefined); assert.equal(sent[0].manifest, undefined);
      assert.equal(sent[0].connection_trace, undefined);
      assert.equal((await f.controller.exportSession(s.session_id)).validation.connection_trace.length, 1);
      await f.controller.snapshot(); await f.controller.delivery.task; assert.equal(sent.length, 1);
    } finally {await f.close();}
  }
});

test('a receiver outage cannot fail, rerun, or remove a completed native benchmark', async () => {
  const f = fixture({delivery: store => new ReportDelivery({store, fetcher: async () => {throw Error('offline');}})});
  try {
    const s = await f.start(); await f.step(); f.available(); await f.step(); await f.controller.delivery.task;
    assert.equal(s.status, 'complete'); assert.equal(f.launches.length, 1);
    assert.equal((await f.controller.exportSession(s.session_id)).reports.length, 1);
    assert.equal((await f.controller.snapshot()).report_delivery.reports[0].status, 'pending');
  } finally {await f.close();}
});

test('refresh recovers native evidence saved before delivery was queued, without headset commands', async () => {
  const f = fixture(); let g, sent = 0;
  try {
    await f.start(); await f.step(); f.available(); await f.step(); await f.close();
    // A damaged historical record must not prevent newer evidence being delivered.
    const invalid = structuredClone(f.store.items[0]); invalid.session_id = 'bench-' + 'f'.repeat(32);
    invalid.phases[0].run_id = 'run-' + 'f'.repeat(32);
    await f.store.saveReport(invalid.phases[0].run_id, {native: {status: 'complete', schema_version: 0}});
    f.store.items.unshift(invalid);
    g = fixture({store: f.store, delivery: store => new ReportDelivery({store, fetcher: async (_, {body}) => {
      sent++; return {ok: true, json: async () => ({stored: true, run_id: JSON.parse(body).run_id, payload_sha256: await sha256(new TextEncoder().encode(body))})};
    }})});
    await g.controller.snapshot(); await g.controller.delivery.task;
    assert.equal(sent, 1); assert.deepEqual(g.calls, []); assert.deepEqual(g.launches, []);
  } finally {await f.close(); await g?.close();}
});
test('a saved legacy session installs, probes, then preserves its original CPU/GPU plan', async () => {
  const f = fixture({legacy: true}); try {
    const s = await f.start(); assert.deepEqual(f.calls, ['connect', 'install', 'probe']);
    await f.step(); assert.equal(f.launches.length, 1); assert.equal(f.launches[0].backend, 'executorch_cpu');
    assert.equal(f.launches[0].preset, 'smoke'); assert.equal(f.m.configurations.gpu.preset, 'standard');
    assert.equal(f.launches[0].cases.length, 63); f.available();
    await f.step(); assert.equal(s.current_phase, 1); assert.equal(f.launches.length, 1);
    await f.step(); assert.equal(f.launches.length, 2); assert.equal(f.launches[1].backend, 'executorch_vulkan');
    assert.equal(f.launches[1].preset, 'smoke');
    await f.step(); assert.equal(s.status, 'complete'); assert.equal(f.releaseCount, 1);
    assert.equal((await f.controller.exportSession(s.session_id)).reports.length, 2);
  } finally {await f.close();}
});
test('terminal progress alone never advances and stopping between phases never launches GPU', async () => {
  const f = fixture({legacy: true}); try {
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
  const f = fixture({launchError: true, legacy: true}); let g;
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
  const f = fixture({noGpu: true, legacy: true}); try {
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
    assert.match(s.message, /older runner stopped/); assert.equal(f.launches.length, 1);
    assert.equal((await f.controller.exportSession(s.session_id)).reports.length, 1);
  } finally {await f.close();}
});

test('a native GPU crash preserves CPU and partial GPU evidence without replaying either phase', async () => {
  const f = fixture({legacy: true}); try {
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
  const f = fixture({legacy: true}); try {
    const s = await f.start(); await f.step(); f.available(); f.transport.isRunning = async () => false;
    await f.step(); assert.equal(s.phases[0].status, 'complete'); assert.equal(s.current_phase, 1);
  } finally {await f.close();}
});
test('malformed evidence and changed website release cannot advance to GPU', async () => {
  for (const corrupt of [true, false]) {
    const f = fixture({legacy: true}); try {
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

const absentChip = m => Object.assign(m.device_catalog.chips.find(c => c.id === 'xr2-gen2'),
  {gpu_presence: 'absent', gpu_presence_source: 'https://fixture.invalid/no-gpu-spec'});
test('default Run launches the full Standard GPU suite with automatic calibration and tracing', async () => {
  const f = fixture(); try {
    const s = await f.start(); await f.step();
    assert.equal(s.processor, 'gpu'); assert.equal(s.phases.length, 1);
    assert.equal(f.launches[0].backend, 'executorch_vulkan'); assert.equal(f.launches[0].cases.length, 63);
    assert.equal(f.launches[0].preset, 'standard'); assert.equal(f.launches[0].calibration, 'auto');
    assert.equal(f.launches[0].profiling ?? 'trace', 'trace');
    f.available(); await f.step(); assert.equal(s.status, 'complete');
    assert.equal(f.launches.length, 1); assert.equal((await f.controller.exportSession(s.session_id)).reports.length, 1);
    assert.equal((await f.controller.start('one', 'same-request')).session_id, s.session_id);
    await assert.rejects(() => f.controller.start('one', 'same-request', 'cpu'), /different processor plan/);
  } finally {await f.close();}
});
test('unsupported GPU ends explicitly without a CPU launch', async () => {
  const f = fixture({noGpu: true}); try {
    const s = await f.start(); await f.step();
    assert.equal(s.status, 'complete'); assert.equal(s.partial, true); assert.equal(s.phases.length, 1);
    assert.equal(s.phases[0].status, 'unsupported'); assert.ok(s.error); assert.equal(f.launches.length, 0);
  } finally {await f.close();}
});
test('CPU requests require sourced absence and a compatible native CPU, never just failed Vulkan', async () => {
  for (const options of [
    {noGpu: true},
    {manifest: absentChip, noGpu: true, eligible: true},
    {manifest: absentChip, noGpu: true, capabilities: {gpu: 'Native GPU'}},
    {manifest: absentChip, noGpu: true, capabilities: {vulkan: true}},
    {manifest: absentChip, noGpu: false},
    {manifest: absentChip, noGpu: true, capabilities: {backends: {executorch_cpu: {compatible: false}}}},
    {manifest: absentChip, noGpu: true, properties: {soc_model: 'Apple M5'}},
  ]) {
    const f = fixture(options); try {
      const s = await f.start('cpu'); await f.step();
      assert.equal(f.launches.length, options.eligible ? 1 : 0);
      if (options.eligible) {
        assert.equal(s.phases.length, 1); assert.equal(f.launches[0].backend, 'executorch_cpu');
        await assert.rejects(() => f.controller.start('one', 'same-request', 'gpu'), /different processor plan/);
        f.available(); await f.step(); assert.equal(s.status, 'complete');
      } else assert.ok(['failed', 'complete'].includes(s.status));
    } finally {await f.close();}
  }
});
test('GPU launch interrupted by disconnect recovers its single phase without replay', async () => {
  const f = fixture({launchError: true}); let g;
  try {
    const s = await f.start(); await f.step(); const config = f.launches[0];
    assert.equal(s.status, 'disconnected'); await f.close();
    g = fixture({store: f.store, previousConfig: config}); g.available();
    await g.controller.snapshot(); await g.step();
    const restored = g.controller.sessions[0];
    assert.equal(restored.status, 'complete'); assert.equal(restored.phases.length, 1);
    assert.equal(restored.phases[0].run_id, config.run_id); assert.equal(g.launches.length, 0);
  } finally {await f.close(); await g?.close();}
});


test('failed authentication traces are bounded, persisted and downloadable without entering native reports', async () => {
  const f = fixture({connect: (signal, trace) => {
    for (let i = 0; i < 200; i++) trace({attempt: 1, event: 'packet_received', stage: 'handshake', command: 'AUTH', auth_type: 1, elapsed_ms: i});
    throw Error('handshake failed');
  }});
  try {
    const s = await f.start(); await f.step();
    const saved = f.store.items.find(item => item.session_id === s.session_id);
    assert.equal(saved.connection_trace.length, 128);
    assert.equal(f.controller.view(saved).connection_trace, undefined);
    const exported = await f.controller.exportSession(s.session_id);
    assert.deepEqual(exported.validation.connection_trace, saved.connection_trace);
    assert.equal(exported.validation.connection_trace[0].elapsed_ms, 72);
    assert.deepEqual(exported.reports, []);
    assert.equal((await f.store.deliveries()).length, 0);
    assert.equal(f.launches.length, 0);
  } finally {await f.close();}
});
