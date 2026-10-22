import {terminal, identify, validateManifest, validateReport, validateProgress, summarize, sha256, EvidenceError} from './evidence.mjs';
import {acquireDeviceLock} from './store.mjs';
import {MissingFile, connectionError, connectionMessage} from './transport.mjs';
import {CPU_RESTRICTION} from '../processor-policy.mjs';

const uuid = prefix => prefix + crypto.randomUUID().replaceAll('-', '');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const now = () => Date.now() / 1000;
export class BrowserController {
  constructor({transport, store, manifestUrl, delivery = null, fetcher = globalThis.fetch.bind(globalThis), lock = acquireDeviceLock, sleep = delay}) {
    Object.assign(this, {transport, store, manifestUrl, delivery, fetcher, lock, sleep});
    this.sessions = []; this.workers = new Map(); this.starting = false; this.disposed = false; this.deviceRows = new Map();
    this.resuming = new Map(); this.lastDevices = null;
  }
  async init() {
    if (this.manifest) return;
    this.initializing ||= (async () => {
      const r = await this.fetcher(this.manifestUrl, {cache: 'no-store'});
      if (!r.ok) throw Error('This website release is missing its headset runner. Retry when the release is available.');
      this.manifest = validateManifest(await r.json());
      this.sessions = await this.store.sessions();
    })().catch(e => {this.initializing = null; this.manifest = null; throw e;});
    await this.initializing;
  }
  async save(s) {s.updated_at = now(); await this.store.save(s);}
  active() {return this.sessions.find(s => !terminal.has(s.status));}
  async start(serial, requestId, processor = 'gpu') {
    if (!['gpu', 'cpu'].includes(processor)) throw Error('Processor must be gpu or cpu');
    const explicitProcessor = arguments.length >= 3;
    const checkRequest = s => {
      if (requestId && s.request_id === requestId && (s.processor ? s.processor !== processor : explicitProcessor))
        throw Error('This request belongs to a different processor plan.');
    };
    if (this.starting) throw Error('A connection request is already open.');
    this.starting = true;
    try {
      // choose() must run synchronously in the click handler, before storage/network I/O.
      const chosen = this.transport.choose(serial);
      const device = await chosen; await this.init();
      if (!device.serial) throw Error('This headset has no stable USB identity and cannot be safely recovered.');
      const existing = this.active();
      if (existing) {checkRequest(existing); if (existing.serial !== device.serial) throw Error('Reconnect the headset with the unfinished run first.'); await this.resume(existing, device); return this.view(existing);}
      const release = await this.lock(device.serial);
      try {
        // Re-read under the cross-tab lock so two tabs cannot start competing sessions.
        this.sessions = await this.store.sessions();
        const duplicate = this.sessions.find(s => s.request_id === requestId || (s.serial === device.serial && !terminal.has(s.status)));
        if (duplicate) {
          checkRequest(duplicate);
          if (duplicate.serial !== device.serial) throw Error('This request belongs to a different headset. Choose a new run.');
          if (terminal.has(duplicate.status)) release(); else this.work(duplicate, device, release);
          return this.view(duplicate);
        }
        const s = {session_id: uuid('bench-'), request_id: requestId || uuid('request-'), serial: device.serial,
          connection_id: (await sha256(new TextEncoder().encode(device.serial))).slice(0, 24),
          status: 'starting', created_at: now(), current_phase: 0, cancel_requested: false, processor,
          message: 'Connecting · put on the headset and allow USB debugging…', manifest: structuredClone(this.manifest),
          phases: [processor].map(processor => ({processor, backend: this.manifest.configurations[processor].backend,
            configuration: structuredClone(this.manifest.configurations[processor]), run_id: uuid('run-'), status: 'pending'}))};
        await this.save(s); this.sessions.push(s); this.work(s, device, release); return this.view(s);
      } catch (e) {release(); throw e;}
    } catch (e) {throw connectionError(e);}
    finally {this.starting = false;}
  }
  view(s) {
    const result = structuredClone(s); delete result.serial; delete result.request_id; delete result.manifest;
    if (result.status === 'disconnected') result.message = connectionMessage({message: result.message});
    return result;
  }
  async snapshot() {
    await this.init();
    await this.syncDelivery();
    const devices = await this.transport.discover();
    const present = new Set(devices.map(d => d.serial));
    for (const serial of this.deviceRows.keys()) if (!present.has(serial)) this.deviceRows.delete(serial);
    const reconnected = new Set([...present].filter(serial => this.lastDevices && !this.lastDevices.has(serial)));
    this.lastDevices = present;
    if (!this.workers.size && !this.resuming.size && !this.starting) {
      const saved = await this.store.sessions();
      if (!this.workers.size && !this.resuming.size && !this.starting) this.sessions = saved;
    }
    const active = this.active();
    if (active && !this.workers.has(active.session_id) && !this.starting && !this.disposed) {
      const device = devices.find(d => d.serial === active.serial);
      // Older saved sessions have only a message. Honor their busy error too.
      const code = active.connection_error?.code || (active.status === 'disconnected' ? connectionError({message: active.message}).code : null);
      const manual = ['usb_busy', 'permission_denied', 'approval_timeout', 'not_selected'].includes(code);
      if (device && (!manual || reconnected.has(active.serial))) {
        try {await this.resume(active, device);} catch (e) {active.message = connectionMessage(e); active.connection_state = 'blocked';}
      }
    }
    const sessions = [...this.sessions].sort((a, b) => b.created_at - a.created_at).map(s => this.view(s));
    return {devices: devices.map(d => ({serial: d.serial, model: d.name || 'USB headset', state: 'usb', supported: false,
        ...this.deviceRows.get(d.serial), connection_state: active?.serial === d.serial ? active.connection_state || (active.status === 'disconnected' ? 'blocked' : 'connecting') : 'detected',
        connection_message: active?.serial === d.serial ? (active.status === 'disconnected' ? connectionMessage({message: active.message}) : active.message) : null})),
      jobs: sessions.flatMap(s => s.phases.flatMap(p => p.run ? [p.run] : [])), benchmark_sessions: sessions,
      catalog: this.manifest.catalog, suite_revision: this.manifest.suite_revision, apk_ready: true,
      browser_usb: true, usb_supported: this.transport.supported, error: null, report_delivery: this.delivery?.snapshot()};
  }
  async syncDelivery() {
    if (!this.delivery || this.disposed) return;
    try {
      await this.delivery.init();
      let issue = '';
      // Recover the gap between saving native evidence and queuing it, including after a refresh.
      for (const s of this.sessions) for (const phase of s.phases) {
        if (!phase.run?.report_available || this.delivery.has(phase.run_id)) continue;
        try {
          const stored = await this.store.report(phase.run_id);
          if (!terminal.has(stored?.native?.status)) continue;
          validateReport(stored.native, phase.configuration, s.manifest.catalog);
          await this.delivery.queue(stored.native);
        } catch (error) {issue = error.message;}
      }
      this.delivery.error = issue;
      this.delivery.kick();
    } catch (error) {this.delivery.error = error.message;}
  }
  async retry(id) {
    const s = this.sessions.find(s => s.session_id === id);
    if (!s || terminal.has(s.status)) throw Error('No unfinished connection to retry. Press Run to start a benchmark.');
    if (this.starting || this.workers.has(id) || this.resuming.has(id)) return this.view(s);
    this.starting = true;
    try {
      // Keep the chooser in the Retry click's user activation, just like Run.
      const device = await this.transport.choose(s.serial);
      if (device.serial !== s.serial) throw Error('Reconnect the headset with the unfinished run first.');
      await this.resume(s, device); return this.view(s);
    } catch (error) {throw connectionError(error);}
    finally {this.starting = false;}
  }
  async resume(s, device) {
    if (this.workers.has(s.session_id)) return;
    if (this.resuming.has(s.session_id)) return this.resuming.get(s.session_id);
    const task = Promise.resolve().then(async () => {
      const release = await this.lock(s.serial);
      try {
        // Another tab may have advanced or finished since this tab's last snapshot.
        const latest = (await this.store.sessions()).find(value => value.session_id === s.session_id);
        if (!latest) throw Error('Session not found');
        Object.assign(s, latest);
        if (terminal.has(s.status) || this.disposed) release(); else this.work(s, device, release);
      } catch (e) {release(); throw e;}
    }).finally(() => this.resuming.delete(s.session_id));
    this.resuming.set(s.session_id, task); return task;
  }
  work(s, device, release) {
    const worker = {abort: new AbortController(), release, task: null};
    this.workers.set(s.session_id, worker);
    worker.task = this.run(s, device, worker).finally(async () => {
      try {await this.transport.close();} finally {release(); this.workers.delete(s.session_id);}
    });
    // Persistence failure must be visible, and must never produce an unhandled task.
    worker.task.catch(e => {s.status = 'disconnected'; s.message = e.message;});
  }
  async run(s, device, worker) {
    const signal = worker.abort.signal;
    try {
      if (s.cancel_requested && !s.phases.some(p => p.attempted)) {await this.finish(s, 'canceled'); return;}
      s.connection_state = 'connecting'; delete s.connection_error;
      s.message = 'Connecting · put on the headset and allow USB debugging…';
      await this.save(s);
      await this.transport.connect(device, signal);
      const properties = await this.transport.properties(signal), identity = identify(properties, s.manifest.device_catalog);
      if (properties.sdk < 29 || !properties.abi.split(',').includes('arm64-v8a')) throw new EvidenceError('This runner requires Android 10+ and arm64-v8a.');
      if (s.processor === 'cpu' && !identity.cpu_eligible) throw new EvidenceError(CPU_RESTRICTION);
      this.deviceRows.set(s.serial, {...properties, identity, serial: s.serial, connection_id: s.connection_id, state: 'device', supported: true});
      s.message = 'Connected · preparing headset runner…';
      if (!s.capabilities_checked) s.status = 'starting';
      if (s.capabilities_checked) {
        if (identity.device_id !== s.identity.device_id || identity.execution_location !== s.identity.execution_location) throw new EvidenceError('Headset identity changed. The previous run will not be replaced.');
        if (await this.transport.installedHash(s.manifest, signal) !== s.manifest.apk_sha256) throw new EvidenceError('The headset runner changed during this session. Existing results are preserved.');
      } else {
        s.identity = identity; await this.save(s);
        await this.transport.install(s.manifest, this.manifestUrl, signal, text => {s.message = text;});
        if (s.cancel_requested) {await this.finish(s, 'canceled'); return;}
        s.message = 'Checking native CPU and GPU capabilities…';
        s.capabilities = await this.transport.probe(s.manifest, signal);
        s.identity = identify({...properties, ...Object.fromEntries(Object.entries(s.capabilities).filter(([, v]) => v !== '' && v != null && v !== 'unknown'))}, s.manifest.device_catalog);
        s.capabilities_checked = true;
        this.deviceRows.set(s.serial, {...this.deviceRows.get(s.serial), identity: s.identity});
        for (const phase of s.phases) phase.configuration.execution_target = s.identity.execution_location;
        await this.save(s);
        await this.sleep(250);
      }
      while (!this.disposed && !terminal.has(s.status)) {
        s.connection_state = 'ready';
        await this.tick(s, signal);
        if (!terminal.has(s.status)) await this.sleep(1000);
      }
    } catch (e) {
      if (this.disposed) return;
      if (s.cancel_requested && !s.phases.some(p => p.attempted)) await this.finish(s, 'canceled');
      else if (e instanceof EvidenceError) {
        const phase = s.phases[s.current_phase];
        if (phase?.attempted && !terminal.has(phase.status)) {
          try {await this.transport.stop(s.manifest, phase.run_id);} catch {}
        }
        s.error = e.message; await this.finish(s, 'failed');
      }
      else {
        const error = connectionError(e);
        console.warn('xrBench device connection interrupted', error);
        s.status = 'disconnected'; s.connection_state = 'blocked';
        s.connection_error = {code: error.code, message: error.message}; s.message = error.message; await this.save(s);
      }
    }
  }
  async finish(s, status) {
    s.status = status; s.finished_at = now();
    s.message = s.error || `Benchmark ${status}. Results are saved in this browser.`;
    // Expose terminal child view models too, so a rejected report cannot leave Stop stuck on.
    for (const p of s.phases) if (p.run && !terminal.has(p.run.status)) {
      p.run.status = p.status = status;
      p.run.message = s.message;
    }
    await this.save(s);
  }
  async tick(s, signal) {
    const phase = s.phases[s.current_phase];
    if (s.cancel_requested && !phase.attempted) {phase.status = 'canceled'; await this.finish(s, 'canceled'); return;}
    if (!phase.attempted) {
      if (s.manifest.apk_sha256 !== this.manifest.apk_sha256) throw new EvidenceError('Website runner changed between phases. Start a new session to use this release.');
      if (s.processor === 'cpu' && !identify({...s.identity.identity_evidence, ...s.capabilities}, s.manifest.device_catalog).cpu_eligible)
        throw new EvidenceError(CPU_RESTRICTION);
      const support = s.capabilities.backends[phase.backend];
      if (support?.compatible !== true) {
        phase.status = 'unsupported'; phase.reason = support?.reason || 'Native backend unavailable';
        if (s.processor) s.error = phase.reason;
        await this.advance(s); return;
      }
      phase.configuration.run_id = phase.run_id;
      phase.run = {run_id: phase.run_id, status: 'launching', configuration: structuredClone(phase.configuration), created_at: now(), message: 'Opening the native runner', storage: 'browser'};
      phase.attempted = true; phase.status = 'launching'; s.status = 'running';
      // Persist intent BEFORE any device command. An uncertain launch is recovered, never replayed.
      await this.save(s);
      signal?.throwIfAborted();
      if (s.cancel_requested) {
        phase.run.status = phase.status = 'canceled';
        await this.finish(s, 'canceled'); return;
      }
      await this.transport.launch(s.manifest, phase.configuration, signal);
      s.started_at ||= now();
      phase.run.status = phase.status = 'running'; s.message = `${phase.processor.toUpperCase()} · native execution`;
      await this.save(s); return;
    }
    if (s.cancel_requested) {
      s.message = 'Stopping · waiting for the current native work unit…';
      await this.transport.stop(s.manifest, phase.run_id);
    }
    let progress;
    try {progress = validateProgress(await this.transport.read(s.manifest, phase.run_id, 'progress.json'), phase.configuration);}
    catch (e) {if (!(e instanceof MissingFile)) throw e;}
    if (progress) {phase.run.progress = progress; phase.run.message = progress.message;}
    // Read the report AFTER checking process liveness. A process can finish and
    // exit between polls; its final validated report still takes precedence.
    const running = await this.transport.isRunning(s.manifest, signal);
    const stoppedMessage = 'The headset benchmark app stopped before finishing. Completed results are saved. Press Run to start a new benchmark.';
    let native;
    try {native = await this.transport.read(s.manifest, phase.run_id, 'report.json');}
    catch (e) {if (!(e instanceof MissingFile)) throw e;}
    if (!native) {
      if (!running) throw new EvidenceError(stoppedMessage);
      s.status = 'disconnected'; s.message = 'Waiting for this run’s native report. Wake the headset; this run will not be launched twice.';
      await this.save(s); return;
    }
    validateReport(native, phase.configuration, s.manifest.catalog, {partial: true});
    const report = summarize(native); report.device_identity = s.identity;
    const revision = await sha256(new TextEncoder().encode(JSON.stringify(native)));
    // Store evidence durably before marking a phase finished or allowing the next launch.
    await this.store.saveReport(phase.run_id, {native, report});
    Object.assign(phase.run, {report_available: true, report_revision: revision, counts: report.counts,
      status: native.status, progress: progress || native.progress, cancel_requested: s.cancel_requested});
    phase.status = native.status;
    if (terminal.has(native.status)) {
      await this.save(s);
      await this.syncDelivery();
      if (s.cancel_requested || native.status === 'canceled') {
        if (!s.cancel_requested && /activity lifecycle/.test(native.error || '')) {
          s.error = 'The headset paused the benchmark. Keep it awake with the benchmark app open, then press Run again. Partial results are saved.';
        }
        await this.finish(s, 'canceled'); return;
      }
      if (native.status === 'failed') {s.error = native.error || 'Native benchmark failed'; await this.finish(s, 'failed'); return;}
      await this.advance(s);
    } else {
      if (!running) throw new EvidenceError(stoppedMessage);
      s.status = 'running'; s.message = native.progress?.message || `${phase.processor.toUpperCase()} · native execution`; await this.save(s);
    }
  }
  async advance(s) {
    if (s.cancel_requested) {await this.finish(s, 'canceled'); return;}
    if (s.current_phase + 1 < s.phases.length) {s.current_phase++; await this.save(s);}
    else {s.partial = s.phases.some(p => p.status === 'unsupported'); await this.finish(s, 'complete');}
  }
  async stop(id) {
    let s = this.sessions.find(s => s.session_id === id), release;
    if (!s) throw Error('Session not found');
    if (terminal.has(s.status)) return this.view(s);
    if (!this.workers.has(id)) {
      // Never overwrite a live owner's state with a stale snapshot from another tab.
      release = await this.lock(s.serial);
    }
    try {
      if (release) {
        s = (await this.store.sessions()).find(s => s.session_id === id);
        if (!s) throw Error('Session not found');
        this.sessions = this.sessions.map(v => v.session_id === id ? s : v);
      }
      if (terminal.has(s.status)) return this.view(s);
      s.cancel_requested = true; s.message = 'Stop requested';
      for (const p of s.phases) if (p.run) p.run.cancel_requested = true;
      await this.save(s);
      // Abort downloads/authentication/preparation, but keep an attempted native run recoverable.
      if (!s.phases.some(p => p.attempted)) {
        this.workers.get(id)?.abort.abort(new DOMException('Benchmark stopped', 'AbortError'));
        await this.finish(s, 'canceled');
      }
      return this.view(s);
    } finally {release?.();}
  }
  async report(id) {return (await this.store.report(id))?.report || null;}
  async exportSession(id) {
    const s = this.sessions.find(s => s.session_id === id); if (!s) throw Error('Session not found');
    const reports = [];
    for (const p of s.phases) {const stored = await this.store.report(p.run_id); if (stored) reports.push(stored.native);}
    return {schema_version: 1, session: this.view(s), reports};
  }
  dispose() {this.disposed = true; this.delivery?.dispose(); for (const w of this.workers.values()) w.abort.abort(); void this.transport.close();}
}
