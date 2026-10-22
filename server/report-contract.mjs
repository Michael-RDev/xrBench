import {canonical, distribution, sha256, validateManifest, validateReport} from '../src/bench_web/web/browser/evidence.mjs';

export const MAX_REPORT_BYTES = 32 * 1024 * 1024;
const encoder = new TextEncoder();
const spans = new Set(['single graph, common queue timestamp domain', 'single dispatch sequence, common queue timestamp domain']);
const targets = new Set(['headset', 'glasses', 'compute_puck', 'phone', 'unknown']);

function rejectSimulation(report) {
  const pending = [report];
  while (pending.length) {
    const item = pending.pop();
    if (!item || typeof item !== 'object') continue;
    if (item.simulated === true || item.source === 'simulated' || item.presentation_source === 'simulated')
      throw Error('Demo results cannot be submitted.');
    pending.push(...Object.values(item));
  }
}

export async function prepareSubmission(payload, manifests) {
  const report = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(payload));
  rejectSimulation(report);
  const config = report?.configuration;
  const manifest = manifests.find(m => m.apk_sha256 === config?.apk_sha256);
  if (!manifest) throw Error('Unknown runner release.');
  validateManifest(manifest);
  const processor = {executorch_vulkan: 'gpu', executorch_cpu: 'cpu'}[report.backend];
  if (!processor || !targets.has(report.execution_location)) throw Error('Unsupported execution target.');
  const expected = {...manifest.configurations[processor], run_id: report.run_id, execution_target: report.execution_location};
  validateReport(report, expected, manifest.catalog);
  const cases = new Map(manifest.catalog.cases.map(row => [row.id, row]));
  const measurements = report.results.map(row => {
    const invalid = [];
    if (row.status !== 'passed') invalid.push(row.status);
    if (row.timing_valid === false) invalid.push('native timing invalid');
    if (['correctness', 'correctness_before', 'correctness_after'].some(k => row[k]?.passed === false)) invalid.push('correctness failed');
    if (row.thermal_discontinuity) invalid.push('thermal or charging transition');
    if (row.focus_lost) invalid.push('focus lost');
    // Check samples too: absence of the aggregate flag must not hide a transition.
    const thermal = new Set([row.thermal_start, ...row.samples.map(s => s.thermal_status)].filter(v => v != null));
    const charging = new Set([row.charging_start, ...row.samples.map(s => s.charging)].filter(v => v != null));
    if (thermal.size > 1 || charging.size > 1) invalid.push('sample conditions changed');
    const timings = Object.fromEntries(['execution_ms', 'model_ms', 'gpu_ms', 'gpu_span_ms', 'input_staging_ms', 'output_staging_ms', 'submit_and_wait_ms'].map(key => {
      const values = row.samples.map(s => key === 'gpu_span_ms' && !spans.has(s.gpu_span_scope) ? null : s[key]);
      return [key, invalid.length ? null : distribution(values)];
    }));
    return {case_id: row.case, phase: row.phase, trial: row.trial, operation: row.operation,
      status: row.status, workload: cases.get(row.case), timing_valid: !invalid.length,
      invalid_reasons: invalid, sample_count: row.samples.length, timings,
      thermal_start: row.thermal_start ?? null, charging_start: row.charging_start ?? null,
      shader_names: [...new Set(row.samples.flatMap(s => (s.dispatches || []).map(d => d.kernel_name).filter(Boolean)))],
      result: row};
  });
  return {p_report: report, p_measurements: measurements,
    p_report_sha256: await sha256(encoder.encode(canonical(report))),
    p_payload_sha256: await sha256(payload)};
}
