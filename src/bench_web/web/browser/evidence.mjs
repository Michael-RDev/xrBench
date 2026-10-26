import {gpuEligibility} from '../processor-policy.mjs';
import {validateNativeManifest,validateNativeReport} from './native-contract.mjs';
export const terminal = new Set(['complete', 'failed', 'canceled']);
export const runPattern = /^run-[0-9a-f]{32}$/;
const gpuSpanScopes = new Set(['single graph, common queue timestamp domain', 'single dispatch sequence, common queue timestamp domain']);
export class EvidenceError extends Error {}
const requireValue = (condition, message) => { if (!condition) throw new EvidenceError(message); };
export function canonical(value) {
  if (typeof value === 'number') requireValue(Number.isFinite(value), 'Non-finite native evidence');
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  return JSON.stringify(value);
}
export async function sha256(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(b => b.toString(16).padStart(2, '0')).join('');
}
export function validateManifest(m) {
  requireValue([1,2].includes(m?.protocol_version) && (m.package === 'org.xrgpu.benchmarks.web'||/^org\.xrgpu\.benchmarks\.web\.s[a-f0-9]{64}$/.test(m.package)) && m.activity === 'org.xrgpu.benchmarks.MainActivity', 'Unsupported runner distribution');
  requireValue(/^[a-f0-9]{64}$/.test(m.apk_sha256) && m.apk === m.apk_sha256 + '.apk', 'Invalid runner checksum or location');
  requireValue(Number.isSafeInteger(m.apk_bytes) && m.apk_bytes > 0 && m.apk_bytes <= 1024 ** 3, 'Invalid runner size');
  requireValue(m.min_sdk === 29 && m.abi === 'arm64-v8a' && m.version_code > 0 && /^[a-f0-9]{64}$/.test(m.signing_certificate_sha256), 'Missing runner provenance');
  if(m.protocol_version===2){try{return validateNativeManifest(m,canonical);}catch(e){throw new EvidenceError(e.message,{cause:e});}}
  requireValue(Array.isArray(m.catalog?.cases) && m.catalog.cases.length === 63 && new Set(m.catalog.cases.map(c => c.id)).size === 63, 'Expected the standard 63-case suite');
  requireValue(Array.isArray(m.device_catalog?.devices) && Array.isArray(m.device_catalog?.chips), 'Missing device catalog');
  requireValue(Array.isArray(m.backends), 'Invalid runner backend list');
  for (const [processor, backend] of [['cpu', 'executorch_cpu'], ['gpu', 'executorch_vulkan']]) {
    const c = m.configurations?.[processor];
    // Keep older releases and saved Smoke sessions recoverable. New distributions
    // select Standard (20 warmups / 100 samples) in their immutable configuration.
    requireValue(c && c.backend === backend && c.mode === 'isolated' && ['smoke', 'standard'].includes(c.preset) && c.workload_suite === 'standard' && c.validation_control === 'none' && !c.experiment, 'Invalid standard run configuration');
    requireValue(c.calibration === 'auto' && (c.profiling ?? 'trace') === 'trace', 'Public runs require automatic calibration and dispatch tracing');
    requireValue(m.backends.includes(backend) && canonical(c.cases) === canonical(m.catalog.cases.map(row => row.id)), 'Runner workloads differ from the catalog');
    for (const key of ['suite_revision', 'runner_revision', 'runtime_revision', 'apk_sha256']) requireValue(typeof m[key] === 'string' && m[key] && c[key] === m[key], 'Runner revision mismatch');
  }
  return m;
}
const normalize = v => String(v || '').replaceAll('_', ' ').trim().toLowerCase().replace(/\s+/g, ' ');
export function identify(device, catalog) {
  const ids = ['model', 'product', 'device'].map(k => normalize(device[k])).filter(v => v && v !== 'unknown');
  const matches = catalog.devices.filter(d => d.aliases.some(a => ids.includes(normalize(a))));
  const entry = matches.length === 1 ? matches[0] : null;
  const chip = catalog.chips.find(c => c.aliases.some(a => normalize(a) === normalize(device.soc_model)));
  const allowed = [...(entry?.chips.map(c => c.chip_id) || []), ...(entry?.primary_chip_options || [])];
  const conflict = matches.length > 1 || !!(entry && chip && !allowed.includes(chip.id));
  const chipId = conflict ? null : chip?.id || entry?.chips[0]?.chip_id;
  const known = catalog.chips.find(c => c.id === chipId);
  const component = entry?.chips.find(c => c.chip_id === chipId || (!c.chip_id && entry.primary_chip_options?.includes(chipId)));
  return {device_id: entry?.id || null, device_name: entry?.name || device.model || 'Connected device',
    chip_id: chipId || null, chip_name: known?.name || (conflict ? 'Processor identity needs verification' : device.soc_model || 'Processor not identified'),
    identity_source: conflict ? 'conflicting' : chip ? 'reported' : chipId ? 'catalog' : 'unknown',
    execution_location: conflict ? 'unknown' : component?.location || entry?.chips[0]?.location || 'unknown',
    visual_model: conflict ? 'schematic' : entry?.visual || 'schematic', validation_status: 'unvalidated',
    identity_evidence: Object.fromEntries(['model', 'product', 'device', 'soc_model', 'soc_manufacturer'].map(k => [k, device[k] || null])),
    ...gpuEligibility(known, device, conflict)};
}
export function distribution(values) {
  if (!values.length || values.some(v => typeof v !== 'number' || !Number.isFinite(v) || v < 0)) return null;
  const a = [...values].sort((x, y) => x - y), percentile = p => {const n = (a.length - 1) * p; return a[Math.floor(n)] + (a[Math.ceil(n)] - a[Math.floor(n)]) * (n % 1);};
  return {count: a.length, median: percentile(.5), p95: percentile(.95), min: a[0], max: a.at(-1), mean: a.reduce((n, v) => n + v, 0) / a.length};
}
export function summarize(report) {
  const result = structuredClone(report);
  for (const row of result.results) {
    const stable = [['thermal_start', 'thermal_status'], ['charging_start', 'charging']].every(([start, sample]) =>
      new Set([row[start], ...row.samples.map(s => s[sample])].filter(v => v != null)).size <= 1);
    row.timing_valid = row.timing_valid !== false && row.status === 'passed' && ['correctness', 'correctness_before', 'correctness_after'].every(k => row[k]?.passed !== false) && !row.thermal_discontinuity && !row.focus_lost && stable;
    row.timings = Object.fromEntries(['execution_ms', 'model_ms', 'gpu_ms', 'gpu_span_ms', 'input_staging_ms', 'output_staging_ms', 'submit_and_wait_ms'].map(k => [k,
      row.timing_valid ? distribution(row.samples.map(s => k === 'gpu_span_ms' && !gpuSpanScopes.has(s.gpu_span_scope) ? null : s[k])) : null]));
  }
  result.counts = Object.fromEntries(['passed', 'failed', 'skipped', 'interrupted',...(report.schema_version===3?['unsupported']:[])].map(s => [s, result.status === 'running' && s === 'interrupted' ? 0 : result.results.filter(r => r.status === s).length]));
  return result;
}
export function validateProgress(p, config) {
  requireValue(p?.run_id === config.run_id && ['running', ...terminal].includes(p.status), 'Native progress identity/status mismatch');
  requireValue(Number.isInteger(p.completed_cases) && p.completed_cases >= 0 && p.completed_cases <= config.cases.length, 'Invalid native completion count');
  requireValue(!p.current_case || config.cases.includes(p.current_case), 'Unknown progress case');
  return p;
}
export function validateReport(report, config, catalog, {partial = false} = {}) {
  canonical(report);
  if(report?.schema_version===3){try{return validateNativeReport(report,config,catalog,{partial,canonical});}catch(e){throw new EvidenceError(e.message,{cause:e});}}
  requireValue(report?.schema_version === 2 && runPattern.test(report.run_id) && report.run_id === config.run_id, 'Native report identity mismatch');
  requireValue(canonical(report.configuration) === canonical(config), 'Native report does not match requested configuration');
  requireValue(terminal.has(report.status) || (partial && report.status === 'running'), 'Invalid report status');
  requireValue(report.backend === config.backend && report.execution_location === config.execution_target, 'Native backend/target mismatch');
  for (const key of ['suite_revision', 'runtime_revision']) requireValue(report[key] === config[key], 'Native report revision mismatch');
  requireValue(report.device && typeof report.device === 'object' && !Array.isArray(report.device), 'Missing device metadata');
  const start = Date.parse(report.started_at), end = Date.parse(report.finished_at);
  requireValue(Number.isFinite(start) && /(?:Z|[+-]\d\d:\d\d)$/.test(report.started_at) && (report.status === 'running' || (Number.isFinite(end) && end >= start && /(?:Z|[+-]\d\d:\d\d)$/.test(report.finished_at))), 'Invalid report timestamps');
  requireValue(Array.isArray(report.results) && report.results.length <= config.cases.length, 'Invalid native results');
  const seen = new Set(), cases = new Map(catalog.cases.map(c => [c.id, c]));
  for (const row of report.results) {
    requireValue(row && typeof row === 'object' && !Array.isArray(row), 'Invalid native result');
    requireValue(config.cases.includes(row.case) && !seen.has(row.case) && cases.get(row.case)?.operation === row.operation && row.phase === 'isolated' && row.trial === 0, 'Invalid or duplicate result identity');
    seen.add(row.case);
    requireValue(['passed', 'failed', 'skipped', 'interrupted'].includes(row.status), 'Invalid result status');
    requireValue(Array.isArray(row.samples) && row.samples.length <= 100000, 'Invalid timing samples');
    if (row.status === 'passed') requireValue(row.correctness?.passed === true && row.samples.length > 0, 'Passed result requires correctness and timing evidence');
    for (const s of row.samples) {
      requireValue(s && typeof s === 'object' && !Array.isArray(s), 'Invalid timing sample');
      if (config.backend === 'executorch_cpu') requireValue(s.gpu_ms == null && s.gpu_span_ms == null && !s.dispatches?.length, 'CPU reports cannot contain GPU measurements');
      for (const key of ['execution_ms', 'model_ms', 'gpu_ms', 'gpu_span_ms', 'input_staging_ms', 'output_staging_ms', 'submit_and_wait_ms', 'dispatch_count']) {
        if (key === 'execution_ms' || s[key] != null) requireValue(distribution([s[key]]) !== null, 'Invalid timing');
      }
      requireValue(s.dispatches === undefined || (Array.isArray(s.dispatches) && s.dispatches.length <= 10000), 'Invalid dispatch list');
      for (const d of s.dispatches || []) {
        requireValue(d && typeof d === 'object' && !Array.isArray(d), 'Invalid dispatch');
        requireValue(distribution([d.start_ns, d.end_ns, d.ms]) && d.end_ns >= d.start_ns, 'Invalid dispatch timing');
        for (const key of ['global_size', 'local_size']) if (d[key] !== undefined) requireValue(Array.isArray(d[key]) && d[key].length === 3 && d[key].every(n => Number.isInteger(n) && n > 0), 'Invalid workgroup dimensions');
      }
    }
    requireValue(row.validation_observations === undefined || Array.isArray(row.validation_observations), 'Invalid validation observations');
    for (const o of row.validation_observations || []) requireValue(o && distribution([o.total_ms, o.inference_ms, o.comparison_ms]) && typeof o.passed === 'boolean' && o.comparison_ms <= o.total_ms, 'Invalid validation accounting');
  }
  if (report.diagnostics !== undefined) {
    const d = report.diagnostics;
    requireValue(d && typeof d.stages_ms === 'object' && d.stages_ms && !Array.isArray(d.stages_ms), 'Invalid diagnostics envelope');
    const stages = Object.values(d.stages_ms), sum = stages.reduce((n, v) => n + v, 0);
    requireValue(d.version === 1 && distribution([d.session_ms, ...stages]) && Math.abs(sum - d.session_ms) <= Math.max(.01, Math.abs(d.session_ms) * 1e-6), 'Invalid exclusive stage accounting');
  }
  if (report.calibration !== undefined) {
    const c = report.calibration;
    requireValue(c && c.version === 1 && typeof c.available === 'boolean', 'Invalid calibration envelope');
    if (c.available) requireValue(distribution([c.compute_gflops, c.memory_gbps, c.dispatch_ms, c.scratch_bytes, c.wall_ms]) && [c.compute_gflops, c.memory_gbps, c.dispatch_ms, c.scratch_bytes].every(n => n > 0) && c.scratch_bytes <= 64 * 1024 ** 2 && c.wall_ms <= 15000, 'Invalid calibration values');
  }
  if (report.status === 'complete') requireValue(seen.size === config.cases.length, 'Completed report is missing cases');
  return report;
}
