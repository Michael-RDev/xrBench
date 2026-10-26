import {test} from 'node:test';
import assert from 'node:assert/strict';
import {nativeManifest,operatorReport} from '../../src/bench_web/tests/native-fixtures.mjs';
import {validateManifest,validateReport} from '../../src/bench_web/web/browser/evidence.mjs';
import {prepareSubmission} from '../report-contract.mjs';
const m=nativeManifest(),config={...m.configurations.gpu,run_id:'run-'+'1'.repeat(32)};

test('typed native reports retain all 221 operators and unworn conditions in receiver projections',async()=>{
  validateManifest(m);const report=operatorReport(config,m.catalog);validateReport(report,config,m.catalog);
  const submission=await prepareSubmission(new TextEncoder().encode(JSON.stringify(report)),[m]);
  assert.equal(submission.p_measurements.length,221);
  assert.ok(submission.p_measurements.every(r=>r.timing_valid&&r.result.samples.every(s=>s.display_interactive===false)));
});
test('native receiver rejects altered provenance, types, shader bytes, missing cases and CPU fallback',async()=>{
  for(const mutate of [r=>r.results.pop(),r=>r.results[0].outputs[0].dtype='float32',
    r=>r.results[0].shaders[0].sha256='0'.repeat(64),r=>r.results[0].runtime_info.cpu_compute_fallback=true,
    r=>r.test_only=true,r=>r.results[0].runtime_info.test_only=true,r=>r.native_catalog_sha256='0'.repeat(64),
    r=>r.results[0].actual_outputs[0].sha256='0'.repeat(64),r=>r.results[0].samples[0].thermal_status=null]){
    const report=operatorReport(config,m.catalog);mutate(report);
    await assert.rejects(prepareSubmission(new TextEncoder().encode(JSON.stringify(report)),[m]));
  }
});
test('unsupported operators remain explicit results and never become performance measurements',async()=>{
  const r=operatorReport(config,m.catalog);Object.assign(r.results[0],{status:'unsupported',error:'Required capability unavailable',samples:[]});
  const submission=await prepareSubmission(new TextEncoder().encode(JSON.stringify(r)),[m]);
  assert.equal(submission.p_measurements[0].timing_valid,false);
  assert.equal(submission.p_measurements[0].timings.execution_ms,null);
});
