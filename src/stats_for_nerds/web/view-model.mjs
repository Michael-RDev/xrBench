export const terminal = new Set(['complete', 'failed', 'canceled']);
export const timingLabels = {
  execution_ms: 'Total execution', model_ms: 'Native forward', gpu_ms: 'GPU operation intervals',
  gpu_span_ms: 'Common-queue GPU span',
  input_staging_ms: 'Delegate input staging', output_staging_ms: 'Delegate output staging',
  submit_and_wait_ms: 'Submit and wait',
};
export const timingScopes = {
  execution_ms: 'Runner invocation latency; see the report timing scope for included transfers',
  model_ms: 'Native backend execution; see the report timing scope',
  gpu_ms: 'Sum of operation timestamps; not critical-path latency',
  gpu_span_ms: 'First to last timestamp in one verified queue domain',
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
  if(rows.some(r=>['skipped','unsupported'].includes(r.status))) return 'skipped';
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

export function resolveOperations(catalog, expression='*') {
  const known=[...new Set(catalog.map(c=>c.operation))];
  const tokens=expression.trim().split(',').map(s=>s.trim());
  if(!expression.trim()||expression.length>1000||tokens.some(t=>!t||!/^[a-zA-Z0-9_*]+$/.test(t)))throw Error('Enter operation names or * patterns, separated by commas.');
  const chosen=new Set();
  for(const token of tokens){
    const pattern=new RegExp('^'+token.replace(/\*+/g,'.*')+'$');
    const matches=known.filter(name=>pattern.test(name));
    if(!matches.length)throw Error('Unknown operation pattern: '+token);
    matches.forEach(name=>chosen.add(name));
  }
  return {operations:known.filter(name=>chosen.has(name)),cases:catalog.filter(c=>chosen.has(c.operation))};
}

export function sortMeasurements(rows,key,direction=-1) {
  return [...rows].sort((a,b)=>{
    const x=a[key],y=b[key];
    if(x===null||x===undefined)return y===null||y===undefined?String(a.case).localeCompare(String(b.case)):1;
    if(y===null||y===undefined)return -1;
    const order=typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y));
    return direction*order||String(a.case).localeCompare(String(b.case));
  });
}

export function correctnessObservations(entries) {
  return entries.flatMap(entry=>entry.rows.flatMap(row=>(row.validation_observations||[]).map((check,index)=>({
    ...check,case:entry.id,operation:entry.operation,phase:row.phase,trial:row.trial,index,
    outcome:check.passed?'Passed':'Failed',flagged:needsAttention(entry,{status:'complete'}),
  }))));
}

export function efficiencyRows(entries) {
  return entries.map(entry=>{
    const row=entry.rows.filter(r=>r.phase==='isolated').at(-1),estimate=row?.efficiency;
    return {case:entry.id,operation:entry.operation,variant:row?.variant||'stock',execution_ms:row?timings(row,'execution_ms')?.median:null,
      gpu_ms:row?timings(row,'gpu_span_ms')?.median:null,dispatches:row?distribution((row.samples||[]).map(s=>s.dispatch_count))?.median:null,
      ...(estimate?.available?estimate:{reason:estimate?.reason||'No calibrated isolated measurement',available:false}),
      gap_ms:estimate?.available?estimate.gap_ms:null};
  });
}
