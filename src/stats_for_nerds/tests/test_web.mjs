import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {casesForReport,caseState,caseCounts,filterCases,needsAttention,timings} from '../web/view-model.mjs';
const catalog=JSON.parse(readFileSync(new URL('../../../layers/catalog.json',import.meta.url))).cases;
const base={id:'linear_odd',operation:'linear',rows:[]};
test('catalog preview includes every case and operation',()=>{const c=casesForReport(catalog,null,'current');assert.equal(c.length,63);assert.equal(new Set(c.map(c=>c.operation)).size,25);assert.equal(caseCounts(c,null).pending,63);});
test('outcomes distinguish unsupported, failed, interrupted and active evidence',()=>{for(const status of ['passed','failed','skipped','interrupted']){const c={...base,rows:[{status}]};assert.equal(caseState(c,{status:'complete'}),status);assert.equal(needsAttention(c,{status:'complete'}),status!=='passed');}assert.equal(caseState({...base,rows:[{status:'interrupted'}]},{status:'running'}),'running');assert.equal(caseState({...base,rows:[{status:'passed'}]},{status:'running',progress:{current_case:'linear_odd'}}),'running');});
test('filtering never changes the execution catalog and flags invalid timings',()=>{const c=[{...base,rows:[{status:'passed',thermal_discontinuity:true}]},{id:'relu',operation:'relu',rows:[]}];assert.equal(filterCases(c,{status:'complete'},'','attention').length,1);assert.equal(c.length,2);assert.equal(filterCases(c,null,'relu','all').length,1);});
test('historical reports use recorded shapes, not today’s catalog',()=>{const report={suite_revision:'old',configuration:{cases:[catalog[0].id,'removed_case']},results:[{case:catalog[0].id,operation:'linear',inputs:[{shape:[3],dtype:'float32'}]}]};const rows=casesForReport(catalog,report,'new');assert.deepEqual(rows[0].inputs,[{shape:[3],dtype:'float32'}]);assert.equal(rows[1].operation,'Unrecorded operation');assert.equal(rows.length,2);});
test('unavailable timing stays unavailable; valid samples retain median and p95',()=>{assert.equal(timings({samples:[{gpu_ms:1},{}]},'gpu_ms'),null);const t=timings({samples:[{execution_ms:1},{execution_ms:3}]},'execution_ms');assert.equal(t.median,2);assert.equal(t.p95,2.9);});
test('XR windows count as one case and correctness failure outranks timing',()=>{const c={...base,rows:[{status:'passed'},{status:'passed',correctness_after:{passed:false}}]};assert.equal(caseCounts([c],{status:'complete'}).failed,1);});

const {resolveOperations,sortMeasurements,correctnessObservations,efficiencyRows}=await import('../web/view-model.mjs');
test('operation patterns resolve known names in catalog order and reject partial invalid requests',()=>{
  const selected=resolveOperations(catalog,'matmul, linear, conv*');
  assert.deepEqual(selected.operations,['linear','matmul','conv2d']);
  assert.deepEqual(selected.cases,catalog.filter(c=>selected.operations.includes(c.operation)));
  assert.equal(resolveOperations(catalog,'*').cases.length,63);
  for(const text of ['',',','linear,missing','[a-z]','linear,'])assert.throws(()=>resolveOperations(catalog,text));
});
test('numeric rankings keep unavailable values last in both directions without changing evidence',()=>{
  const rows=[{case:'b',gpu:2},{case:'c',gpu:null},{case:'a',gpu:1}];
  assert.deepEqual(sortMeasurements(rows,'gpu',-1).map(r=>r.case),['b','a','c']);
  assert.deepEqual(sortMeasurements(rows,'gpu',1).map(r=>r.case),['a','b','c']);
  assert.equal(rows[0].case,'b');
});
test('correctness and efficiency tables distinguish observed checks, invalid estimates and XR windows',()=>{
  const entries=[{id:'linear',operation:'linear',rows:[{phase:'isolated',status:'passed',trial:0,correctness:{passed:true},samples:[{execution_ms:3}],validation_observations:[{passed:true,total_ms:4,comparison_ms:1,inference_ms:3}],efficiency:{available:true,efficiency_percent:140,gap_ms:0}},{phase:'loaded',validation_observations:[]}]},{id:'gelu',rows:[]}];
  assert.equal(correctnessObservations(entries)[0].total_ms,4);
  const rows=efficiencyRows(entries);assert.equal(rows[0].execution_ms,3);assert.equal(rows[0].efficiency_percent,140);assert.equal(rows[1].available,false);
});
