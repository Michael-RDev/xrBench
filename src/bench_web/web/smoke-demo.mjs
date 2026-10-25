import {gpuEligibility, CPU_RESTRICTION} from './processor-policy.mjs';

const PHASE_MS = 12000;
const REVISION = 'simulated-ui-v1';

export function demoIdentity(catalog, device) {
  const primary = device.chips.find(chip => chip.role === 'primary');
  const ids = device.primary_chip_options || [primary?.chip_id];
  const chips = ids.map(id => catalog.chips.find(chip => chip.id === id)).filter(Boolean);
  const execution = chips[0]?.execution;
  const support = execution === 'android_probe_required'
    ? 'Real hardware requires Android CPU / GPU capability checks.'
    : execution === 'planned_metal' ? 'Real Apple Metal support is planned.'
    : 'A general compute runner is unavailable for this device.';
  return {device_id: device.id, device_name: device.name, chip_name: chips.map(chip => chip.name).join(' / '),
    execution_location: primary?.location || 'headset', identity_source: 'simulated', support,
    ...gpuEligibility(chips.length === 1 ? chips[0] : null)};
}

function sampleRows(cases, count, processor) {
  return cases.slice(0, count).map((entry, index) => ({
    case: entry.id, operation: entry.operation, inputs: entry.inputs, phase: 'isolated', trial: 0,
    status: 'passed', correctness: {passed: true}, timing_valid: true, simulated: true,
    case_wall_ms: PHASE_MS / cases.length,
    // Illustrative values only: identical across models, never device performance estimates.
    samples: Array.from({length: 20}, (_, sample) => {
      const ms = +((.12 + (index % 11) * .075) * (processor === 'cpu' ? 1.7 : 1) * (1 + (sample % 5 - 2) * .015)).toFixed(4);
      return {execution_ms: ms, ...(processor === 'gpu' ? {gpu_ms: +(ms * .8).toFixed(4)} : {})};
    }),
  }));
}

// This controller owns no network, storage, native IDs, or hardware dependencies.
export function createDemoController({onChange = () => {}, now = () => Date.now(), timers = globalThis} = {}) {
  let deviceCatalog = null, caseCatalog = null, timer = null, connectionTimer = null, disposed = false;
  let state = empty();
  function empty() { return {stage: 'closed', deviceId: '', identity: null, session: null, reports: {}, selected: null}; }
  const emit = () => { if (!disposed) onChange(state); };
  function clear() {
    if (timer !== null) timers.clearInterval(timer);
    if (connectionTimer !== null) timers.clearTimeout(connectionTimer);
    timer = connectionTimer = null;
  }
  function advance() {
    const session = state.session;
    if (!session || session.status !== 'running') return;
    const elapsed = Math.max(0, now() - session.created_at * 1000);
    session.current_phase = Math.min(session.phases.length - 1, Math.floor(elapsed / PHASE_MS));
    for (const [index, phase] of session.phases.entries()) {
      const count = Math.min(phase.cases.length, Math.floor(Math.max(0, elapsed - index * PHASE_MS) / PHASE_MS * phase.cases.length));
      phase.status = count === phase.cases.length ? 'complete' : index === session.current_phase ? 'running' : 'pending';
      const report = state.reports[phase.run_id];
      report.status = phase.status;
      report.results = sampleRows(phase.cases, count, phase.processor);
      report.progress = {phase: 'measurement', completed_cases: count,
        current_case: phase.status === 'running' ? phase.cases[count]?.id : null,
        current_samples: phase.status === 'running' ? 20 : 0};
      report.counts = {passed: count, failed: 0, skipped: 0};
      phase.run = {...report, progress_received_at: now()};
    }
    state.selected = session.phases[session.current_phase].run_id;
    if (elapsed >= PHASE_MS * session.phases.length) {
      session.status = 'complete'; session.finished_at = session.created_at + PHASE_MS * session.phases.length / 1000; clear();
    }
    emit();
  }
  return {
    get snapshot() { return state; },
    get ready() { return !!deviceCatalog?.devices?.length && !!caseCatalog?.cases?.length; },
    setCatalogs(devices, cases) { deviceCatalog = devices; caseCatalog = cases; },
    open() { if (!disposed && state.stage === 'closed') { state = {...empty(), stage: 'choosing'}; emit(); } },
    choose(id) {
      if (disposed || state.stage === 'closed' || state.session?.status === 'running' || state.stage === 'connecting') return;
      clear(); state = {...empty(), stage: 'choosing', deviceId: deviceCatalog?.devices.some(d => d.id === id) ? id : ''}; emit();
    },
    connect() {
      if (disposed || !this.ready || state.stage !== 'choosing') return;
      const device = deviceCatalog.devices.find(d => d.id === state.deviceId); if (!device) return;
      state.identity = demoIdentity(deviceCatalog, device); state.stage = 'connecting'; emit();
      connectionTimer = timers.setTimeout(() => { connectionTimer = null; state.stage = 'connected'; emit(); }, 500);
    },
    start(processor = 'gpu') {
      if (disposed || !this.ready || state.stage !== 'connected' || state.session?.status === 'running') return;
      if (!['gpu', 'cpu'].includes(processor)) throw Error('Processor must be gpu or cpu');
      if (processor === 'cpu' && !state.identity.cpu_eligible) throw Error(CPU_RESTRICTION);
      clear();
      const cases = structuredClone(caseCatalog.cases);
      state.session = {session_id: 'demo-session', simulated: true, status: 'running', created_at: now() / 1000,
        current_phase: 0, identity: state.identity, processor,
        phases: [processor].map(processor => ({processor, run_id: `demo-${processor}`, status: 'pending', cases,
          configuration: {mode: 'isolated', preset: 'smoke', backend: `demo_${processor}`, cases: cases.map(c => c.id)}}))};
      state.reports = Object.fromEntries(state.session.phases.map(phase => [phase.run_id, {
        run_id: phase.run_id, simulated: true, status: 'pending', configuration: phase.configuration,
        device: {model: state.identity.device_name}, backend: `demo_${phase.processor}`,
        suite_revision: REVISION, results: [],
      }]));
      advance(); timer = timers.setInterval(advance, 200);
    },
    stop() {
      if (disposed || state.session?.status !== 'running') return;
      advance(); if (state.session.status !== 'running') return;
      clear(); state.session.status = 'canceled'; state.session.finished_at = now() / 1000;
      const phase = state.session.phases[state.session.current_phase];
      phase.status = phase.run.status = state.reports[phase.run_id].status = 'canceled'; emit();
    },
    selectReport(id) { if (state.session?.status !== 'running' && state.reports[id]) { state.selected = id; emit(); } },
    exit() { clear(); state = empty(); emit(); },
    destroy() { clear(); disposed = true; state = empty(); },
    get displayStatus() { return {catalog: caseCatalog, suite_revision: REVISION}; },
  };
}

export function mountSmokeDemo({document, onChange, onRetry, canEnter}) {
  const trigger = document.getElementById('demo-trigger'), mount = document.getElementById('demo-mount');
  trigger.innerHTML = '<button id="demo-toggle" class="text-action demo-toggle" type="button" aria-expanded="false" aria-controls="demo-panel">Try a demo</button>';
  mount.innerHTML = `<section id="demo-panel" class="demo-panel" aria-label="Device demo" hidden>
    <div class="demo-heading"><span class="demo-tag">Simulation</span><button id="demo-exit" class="text-action" type="button">Exit demo <span aria-hidden="true">×</span></button></div>
    <label for="demo-device">Choose a device to explore</label>
    <select id="demo-device"><option value="">Choose device…</option></select>
    <p id="demo-support" class="demo-support"></p>
    <p id="demo-message" class="demo-message" role="status"></p>
    <button id="demo-connect" class="secondary" type="button" disabled>Connect demo device</button>
    <button id="demo-retry" class="secondary" type="button" hidden>Retry catalog</button>
    <p class="demo-disclaimer">A preview with sample data. Nothing runs on hardware or gets saved.</p>
  </section>`;
  const $ = id => document.getElementById(id);
  let catalog = null, blocked = false, catalogKey = '', wasConnecting = false;
  const controller = createDemoController({timers: document.defaultView, onChange(state) {
    const justConnected = wasConnecting && state.stage === 'connected';
    const focus = document.activeElement;
    wasConnecting = state.stage === 'connecting';
    draw(); onChange();
    if (justConnected && (focus === document.body || focus === $('demo-connect'))) $('run').focus({preventScroll:true});
  }});
  function draw() {
    const state = controller.snapshot, opened = state.stage !== 'closed';
    $('demo-toggle').setAttribute('aria-expanded', String(opened));
    $('demo-toggle').disabled = blocked; $('demo-toggle').textContent = opened ? 'Demo open' : 'Try a demo';
    $('demo-panel').hidden = !opened;
    $('demo-device').value = state.deviceId;
    $('demo-device').disabled = state.stage === 'connecting' || state.session?.status === 'running' || !controller.ready;
    $('demo-connect').hidden = state.stage === 'connected';
    $('demo-connect').disabled = !controller.ready || !state.deviceId || state.stage !== 'choosing';
    $('demo-connect').textContent = state.stage === 'connecting' ? 'Connecting…' : 'Connect demo device';
    $('demo-retry').hidden = controller.ready;
    const device = catalog?.devices.find(d => d.id === state.deviceId);
    $('demo-support').textContent = device ? demoIdentity(catalog, device).support : 'Explore any catalog device, including planned and unsupported hardware.';
    $('demo-message').textContent = !controller.ready ? 'Catalog unavailable. Retry when the local service is ready.'
      : state.stage === 'connecting' ? 'Simulating a device connection…'
      : state.stage === 'connected' ? `${state.identity.device_name} is connected in simulation.` : '';
  }
  $('demo-toggle').onclick = () => { if (canEnter()) { controller.open(); $('demo-device').focus(); } };
  $('demo-exit').onclick = () => { controller.exit(); $('demo-toggle').focus(); };
  $('demo-device').onchange = () => controller.choose($('demo-device').value);
  $('demo-connect').onclick = () => controller.connect();
  $('demo-retry').onclick = async () => { $('demo-retry').disabled = true; try { await onRetry(); } finally { $('demo-retry').disabled = false; } };
  return {
    controller,
    update(devices, cases, nativeBusy) {
      catalog = devices; blocked = nativeBusy; controller.setCatalogs(devices, cases);
      const key = JSON.stringify(devices?.devices.map(d => [d.id, d.name]));
      if (key !== catalogKey) {
        catalogKey = key;
        const option = (value, label) => { const el = document.createElement('option'); el.value = value; el.textContent = label; return el; };
        $('demo-device').replaceChildren(option('', 'Choose device…'), ...(devices?.devices || []).map(d => option(d.id, d.name)));
      }
      draw();
    },
    destroy() { controller.destroy(); trigger.replaceChildren(); mount.replaceChildren(); },
  };
}
