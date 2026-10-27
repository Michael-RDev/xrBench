import {canonical, distribution, sha256, validateManifest, validateReport} from '../src/bench_web/web/browser/evidence.mjs';

export const MAX_REPORT_BYTES = 32 * 1024 * 1024;
export const REPORT_LIMITS = Object.freeze({depth: 32, nodes: 750000, string: 65536, array: 25000, keys: 256});
export class ReportLimitError extends Error {}
const encoder = new TextEncoder();
const spans = new Set(['single graph, common queue timestamp domain', 'single dispatch sequence, common queue timestamp domain']);
const targets = new Set(['headset', 'glasses', 'compute_puck', 'phone', 'unknown']);

function checkStructure(report) {
  const pending = [[report, 0]]; let nodes = 0;
  while (pending.length) {
    const [item, depth] = pending.pop();
    if (++nodes > REPORT_LIMITS.nodes || depth > REPORT_LIMITS.depth ||
        typeof item === 'string' && item.length > REPORT_LIMITS.string)
      throw new ReportLimitError('Report exceeds the JSON complexity limit.');
    if (!item || typeof item !== 'object') continue;
    if (item.simulated === true || item.source === 'simulated' || item.presentation_source === 'simulated')
      throw Error('Demo results cannot be submitted.');
    const keys = Object.keys(item);
    if (Array.isArray(item) ? item.length > REPORT_LIMITS.array : keys.length > REPORT_LIMITS.keys)
      throw new ReportLimitError('Report collection is too large.');
    for (const key of keys) {
      if (key.length > REPORT_LIMITS.string) throw new ReportLimitError('Report member name is too long.');
      pending.push([item[key], depth + 1]);
    }
  }
}

export async function prepareSubmission(payload, manifests) {
  if (!(payload instanceof Uint8Array) || !payload.byteLength || payload.byteLength > MAX_REPORT_BYTES)
    throw new ReportLimitError('Report is too large or empty.');
  const text = new TextDecoder('utf-8', {fatal: true}).decode(payload);
  // Bound nesting before allocating a deeply nested JSON tree. String escapes
  // must be skipped so braces in diagnostics never count as containers.
  let depth = 0, quoted = false, escaped = false;
  for (const character of text) {
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') quoted = false;
    } else if (character === '"') quoted = true;
    else if (character === '{' || character === '[') {
      if (++depth > REPORT_LIMITS.depth) throw new ReportLimitError('Report nesting is too deep.');
    } else if (character === '}' || character === ']') depth--;
  }
  const report = JSON.parse(text);
  // Run this iterative check before validators and canonicalization recurse over
  // untrusted extra fields. Direct callers receive the same limits as HTTP.
  checkStructure(report);
  const config = report?.configuration;
  const manifest = manifests.find(m => m.apk_sha256 === config?.apk_sha256);
  if (!manifest) throw Error('Unknown runner release.');
  validateManifest(manifest);
  const processor = {xrgpu_vulkan: 'gpu', executorch_vulkan: 'gpu', executorch_cpu: 'cpu'}[report.backend];
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
