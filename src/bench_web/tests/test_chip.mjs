import {test} from 'node:test';
import assert from 'node:assert/strict';
import {chipEvidence} from '../web/chip-view.mjs';
const now=100000;
function fixture(){return {session_id:'bench-test',status:'running',created_at:90,current_phase:0,phases:[
  {processor:'cpu',run_id:'cpu',configuration:{cases:['a','b']},run:{status:'running',configuration:{cases:['a','b']},progress_received_at:now,progress:{sequence:1,phase:'measurement',current_case:'b',completed_cases:1,current_samples:4}}},
  {processor:'gpu',run_id:'gpu',configuration:{cases:['a','b']},status:'pending'}]};}
test('case count comes from native plans and gauge spans CPU plus GPU',()=>{
  const s=fixture(),reports={cpu:{results:[{case:'a',operation:'matmul',status:'passed',correctness:{passed:true},timing_valid:true,samples:[{execution_ms:1},{execution_ms:3}]}]}};
  const v=chipEvidence(s,reports,[],now);assert.equal(v.total,4);assert.equal(v.completed,1);assert.equal(v.percent,25);
  assert.equal(v.cases[0].state,'passed');assert.equal(v.cases[1].state,'running');assert.equal(v.recent[0].ms,2);assert.equal(v.eta,null);
});
test('stale or disconnected reports freeze activity without fabricating progress',()=>{
  const s=fixture();s.phases[0].run.progress_received_at=now-16000;
  const v=chipEvidence(s,{},[],now);assert.equal(v.active,false);assert.equal(v.stale,true);assert.equal(v.percent,25);assert.equal(v.eta,null);
});
test('failed correctness and invalid timing never become recent performance results',()=>{
  const s=fixture();const reports={cpu:{results:[{case:'a',status:'passed',correctness:{passed:false},timing_valid:true,samples:[{execution_ms:8}]},{case:'b',status:'passed',correctness:{passed:true},timing_valid:false,samples:[{execution_ms:9}]}]}};
  const v=chipEvidence(s,reports,[],now);assert.equal(v.recent.length,0);assert.deepEqual(v.cases.map(c=>c.state),['failed','attention']);
});
test('ETA uses completed native case durations only within the active phase',()=>{
  const s=fixture();s.phases[0].run.configuration.cases=['a','b','c','d','e'];s.phases[0].configuration.cases=['a','b','c','d','e'];s.phases[0].run.progress.completed_cases=3;
  const v=chipEvidence(s,{cpu:{results:[100,200,300].map((n,i)=>({case:String(i),status:'passed',case_wall_ms:n}))}},[],now);
  assert.equal(v.eta,400);s.phases[0].run.progress.phase='warmup';assert.equal(chipEvidence(s,{},[],now).eta,null);
});
test('unavailable backends never inflate measured completion and preparation has no stale sample clock',()=>{
  const s=fixture();s.status='complete';s.partial=true;s.phases[1].status='unsupported';
  assert.equal(chipEvidence(s,{},[],now).completed,1);
  s.status='starting';s.phases[0].run.progress={};s.phases[0].run.progress_received_at=now-60000;
  const v=chipEvidence(s,{},[],now);assert.equal(v.stale,false);assert.equal(v.eta,null);
});
test('both processor phases use their own plans and retain CPU evidence through GPU execution',()=>{
  const s=fixture();s.current_phase=1;s.phases[0].run.status='complete';s.phases[0].run.progress.completed_cases=2;
  s.phases[1].run={status:'running',configuration:{cases:['x','y','z']},progress_received_at:now,progress:{sequence:1,current_case:'x',completed_cases:0}};
  const reports={cpu:{results:[{case:'a',status:'passed',correctness:{passed:true}},{case:'b',status:'failed',correctness:{passed:false}}]}};
  const v=chipEvidence(s,reports,[],now);assert.equal(v.total,5);
  assert.deepEqual(v.phaseCases.cpu,[{id:'a',state:'passed'},{id:'b',state:'failed'}]);
  assert.deepEqual(v.phaseCases.gpu,[{id:'x',state:'running'},{id:'y',state:'pending'},{id:'z',state:'pending'}]);
  s.status='canceled';assert.equal(chipEvidence(s,reports,[],now).phaseCases.gpu[0].state,'interrupted');
  assert.equal(chipEvidence(s,reports,[],now).phaseCases.gpu[1].state,'pending');
});
test('completion counts without result rows never manufacture passed cases; empty plans remain empty',()=>{
  const s=fixture();s.phases[0].run.progress.completed_cases=2;s.phases[0].run.progress.current_case=null;
  s.phases[1].configuration.cases=[];
  const v=chipEvidence(s,{},[{id:'unrelated'}],now);
  assert.equal(v.total,2);assert.deepEqual(v.phaseCases.gpu,[]);assert.ok(v.phaseCases.cpu.every(c=>c.state==='pending'));
});
