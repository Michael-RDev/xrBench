export const terminal = new Set(['complete', 'failed', 'canceled']);
export const timingLabels = {
  execution_ms: 'Total execution', model_ms: 'Native forward', gpu_ms: 'GPU operation intervals',
  input_staging_ms: 'Delegate input staging', output_staging_ms: 'Delegate output staging',
  submit_and_wait_ms: 'Submit and wait',
};
export const timingScopes = {
  execution_ms: 'JNI staging + native forward + output copy',
  model_ms: 'Native forward, including delegate staging',
  gpu_ms: 'Sum of operation timestamps; not critical-path latency',
  input_staging_ms: 'Nested host trace; may overlap other timings',
  output_staging_ms: 'Nested host trace; may overlap other timings',
  submit_and_wait_ms: 'Nested host trace; includes waiting, not pure GPU time',
};
export const labels = {passed:'Passed', failed:'Failed', skipped:'Unsupported', interrupted:'Interrupted', running:'Running', pending:'Not measured'};
export function distribution(values) {
  if (!values?.length || values.some(v => typeof v !== 'number' || !Number.isFinite(v) || v < 0)) return null;
  const sorted = [...values].sort((a,b) => a-b);
  const quantile = p => {const k=(sorted.length-1)*p;return sorted[Math.floor(k)]+(sorted[Math.ceil(k)]-sorted[Math.floor(k)])*(k-Math.floor(k));};
  return {count:values.length,median:quantile(.5),p95:quantile(.95)};
}
export function timings(row, key) {return distribution((row.samples || []).map(s => s[key]));}
export function caseState(entry, report) {
  const rows=entry.rows || [];
  if(rows.some(r => r.status==='failed' || ['correctness','correctness_before','correctness_after'].some(k=>r[k]?.passed===false))) return 'failed';
  const active=report && !terminal.has(report.status);
  if(active && (report.progress?.current_case===entry.id || rows.some(r=>r.status==='interrupted'))) return 'running';
  if(rows.some(r=>r.status==='interrupted')) return 'interrupted';
  if(rows.some(r=>r.status==='skipped')) return 'skipped';
  return rows.length && rows.every(r=>r.status==='passed') ? 'passed' : 'pending';
}
export function needsAttention(entry, report) {
  return ['failed','skipped','interrupted'].includes(caseState(entry,report)) || entry.rows.some(r=>r.thermal_discontinuity || r.focus_lost || (r.status==='passed' && r.timing_valid===false));
}
export function casesForReport(catalog, report, suiteRevision) {
  if(!report)return catalog.map(c=>({...c,rows:[]}));
  const known=report.suite_revision===suiteRevision ? new Map(catalog.map(c=>[c.id,c])) : new Map();
  const ids=[...new Set([...(report.configuration?.cases || []),...(report.results || []).map(r=>r.case)])];
  return ids.map(id=>{
    const rows=(report.results || []).filter(r=>r.case===id),saved=rows[0] || {},spec=known.get(id) || {};
    return {...spec,id,operation:saved.operation || spec.operation || 'Unrecorded operation',inputs:saved.inputs || spec.inputs,params:saved.params || spec.params,rows};
  });
}
export function filterCases(entries, report, query, filter) {
  const needle=query.trim().toLowerCase();
  return entries.filter(c=> (!needle || JSON.stringify([c.id,c.operation,c.inputs]).toLowerCase().includes(needle)) &&
    (filter==='all' || (filter==='attention' ? needsAttention(c,report) : caseState(c,report)===filter)));
}
export function caseCounts(entries,report) {
  const counts={passed:0,failed:0,skipped:0,interrupted:0,running:0,pending:0};
  entries.forEach(c=>counts[caseState(c,report)]++);return counts;
}
