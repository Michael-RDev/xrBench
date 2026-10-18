import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from 'jsdom';

const root=new URL('../../../',import.meta.url);
const catalog=JSON.parse(await readFile(new URL('layers/catalog.json',root),'utf8'));
const id='run-'+'a'.repeat(32);
const paths={
  '/shared/session.mjs':'layers/engine/web/session.mjs',
  '/stats/view-model.mjs':'app/stats_for_nerds/web/view-model.mjs',
  '/stats/app.js':'app/stats_for_nerds/web/app.js',
  '/bench/app.js':'app/bench_web/web/app.js',
  '/bench/device-view.mjs':'app/bench_web/web/device-view.mjs',
  '/bench/results-view.mjs':'app/bench_web/web/results-view.mjs',
};
const drain=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));};
async function page(kind,report=null,stateOverrides={},sceneMode='unavailable'){
  const pretty=kind==='bench';
  const html=await readFile(new URL(`app/${pretty?'bench_web':'stats_for_nerds'}/web/index.html`,root),'utf8');
  const dom=new JSDOM(html,{url:'http://localhost:8765/'+(pretty?'':'stats/')+(report?'?run='+id:''),runScripts:'outside-only'});
  const window=dom.window,calls=[],intervals=[],faults={start:false,status:false},downloads=[];
  let hidden=false;Object.defineProperty(window.document,'hidden',{get:()=>hidden});
  window.Blob=Blob;window.URL.createObjectURL=blob=>{downloads.push(blob);return 'blob:fixture';};window.URL.revokeObjectURL=()=>{};
  window.document.addEventListener('click',e=>{if(e.target.closest('a[download]'))e.preventDefault();});
  const state={devices:[{serial:'fixture',model:'Quest 3',state:'device',supported:true}],database:{ready:true,message:'Local PostgreSQL ready'},jobs:[],apk_ready:true,catalog,suite_revision:'fixture-suite',...stateOverrides};
  window.fetch=async(path,options={})=>{
    const body=options.body?JSON.parse(options.body):null;
    calls.push({path,body});
    if((path==='/api/runs'&&body&&faults.start)||(path==='/api/status'&&faults.status))return {ok:false,json:async()=>({error:'Local service unavailable'})};
    let value;
    if(path==='/api/session')value={token:'fixture-token'};
    else if(path==='/api/status')value=structuredClone(state);
    else if(path.startsWith('/api/saved'))value={runs:[],next_offset:null};
    else if(path==='/api/runs'&&body){
      assert.deepEqual(Object.keys(body).filter(k=>!['experiment','profiling'].includes(k)).sort(),pretty?['mode','preset','serial']:['calibration','mode','operations','preset','serial']);
      value={run_id:id,status:'running',configuration:{...body,cases:catalog.cases.filter(c=>!body.operations||body.operations.includes(c.operation)).map(c=>c.id)},message:'Test 1 of 63',storage:'pending'};
      state.jobs=[value];
    }else if(path==='/api/watch'){
      if(faults.watchGate&&body.action==='enable')await faults.watchGate;
      state.watch_status=body.action==='disable'?[]:[{owner:body.owner,serial:body.serial,state:'Watching for changes',run_id:null}];value=state.watch_status;
    }else if(path.endsWith('/stop')){state.jobs[0].cancel_requested=true;value=state.jobs[0];}
    else if(path.endsWith('/report'))value=report;
    else throw Error('Unexpected API request '+path);
    return {ok:true,json:async()=>value};
  };
  window.setInterval=callback=>{intervals.push(callback);return intervals.length;};
  const cache=new Map(),context=dom.getInternalVMContext();
  const viewer={devices:[],progress:[],resets:0,disposed:0};
  let releaseScene;
  const sceneGate=new Promise(resolve=>{releaseScene=resolve;});
  const sceneModule=new vm.SyntheticModule(['createScene'],function(){
    this.setExport('createScene',(container,{onAvailabilityChange})=>{
      viewer.availability=onAvailabilityChange;
      container.querySelector('#scene-fallback').hidden=true;
      return {setDevice:device=>viewer.devices.push(structuredClone(device)),
        update:states=>viewer.progress.push([...states]),resetView:()=>viewer.resets++,dispose:()=>viewer.disposed++};
    });
  },{context});
  async function module(path){
    if(cache.has(path))return cache.get(path);
    const file=paths[path];if(!file)throw Error('Unknown module '+path);
    const source=await readFile(new URL(file,root),'utf8');
    const mod=new vm.SourceTextModule(source,{context,identifier:path,importModuleDynamically:async()=>{
      if(sceneMode==='unavailable')throw Error('WebGL unavailable in DOM test');
      if(sceneMode==='delayed')await sceneGate;
      await sceneModule.link(()=>{});await sceneModule.evaluate();return sceneModule;
    }});
    cache.set(path,mod);
    await mod.link((specifier,ref)=>module(new URL(specifier,'http://fixture'+ref.identifier).pathname));
    return mod;
  }
  await (await module(pretty?'/bench/app.js':'/stats/app.js')).evaluate();await drain();
  return {window,state,calls,faults,viewer,downloads,setHidden:async value=>{hidden=value;window.document.dispatchEvent(new window.Event('visibilitychange'));await drain();},releaseScene:async()=>{releaseScene();await drain();},
    setReport:value=>{report=value;},
    close:()=>window.close(),tick:async()=>{for(const callback of intervals)await callback();await drain();}};
}

test('Run beneath the description starts the full suite once, preserves selection, and becomes Stop',async()=>{
  const p=await page('bench');
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#run').disabled,false);
    assert.ok(doc.querySelector('.hero-copy .lede + .hero-actions > #run'));
    assert.equal(doc.querySelector('header #run'),null);
    assert.equal(doc.querySelectorAll('header a').length,2);
    assert.equal(doc.querySelector('header nav a').id,'stats-link');
    assert.equal(doc.querySelector('.hero-actions #run-status').hidden,true);
    assert.equal(doc.querySelector('.hero-actions #notice').hidden,true);
    assert.doesNotMatch(doc.body.textContent,/Local workspace|Connect\. Run\. Understand\.|01 \/ NATIVE COMPUTE|A closer look at what’s inside\./);
    assert.equal(doc.querySelector('figcaption'),null);
    assert.equal(doc.querySelector('#results').hidden,true);
    assert.equal(doc.querySelector('footer'),null);
    assert.equal(doc.querySelector('#mode'),null);
    assert.equal(p.calls.filter(c=>c.body).length,0);
    doc.querySelector('#run').click();doc.querySelector('#run').click();await drain();
    const requests=p.calls.filter(c=>c.path==='/api/runs'&&c.body);
    assert.equal(requests.length,1);assert.equal(requests[0].body.mode,'isolated');assert.equal(requests[0].body.preset,'smoke');
    assert.equal(p.state.jobs[0].configuration.cases.length,63);
    assert.equal(new Set(p.state.jobs[0].configuration.cases).size,63);
    assert.equal(doc.querySelector('#stats-link').getAttribute('href'),'/stats/?run='+id);
    assert.equal(p.window.location.search,'?run='+id);
    assert.equal(doc.querySelector('#run-label').textContent,'Stop');
    assert.match(doc.querySelector('#run-status').textContent,/Test 1 of 63/);
    assert.equal(doc.querySelector('#run-status').hidden,false);
    assert.equal(doc.querySelector('#results').hidden,false);
    assert.equal(doc.querySelectorAll('.layer-group').length,25);
    assert.equal(doc.querySelectorAll('.case-table-wrap tbody tr').length,63);
    assert.equal(doc.querySelector('#completion-count').textContent,'0 / 63 cases processed');
    doc.querySelector('#run').click();doc.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);
    assert.equal(doc.querySelector('#run').disabled,true);
    assert.equal(doc.querySelector('#run-label').textContent,'Stopping…');
    doc.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);
    p.state.jobs[0].status='canceled';await p.tick();
    assert.equal(doc.querySelector('#run-phase').textContent,'Run stopped');
    assert.equal(doc.querySelector('#run').disabled,false);
    assert.equal(doc.querySelector('#run-label').textContent,'Run');
  }finally{p.close();}
});

test('kernel experiment selects matmul, disables unattended runs, and submits the profiling scope',async()=>{
  const p=await page('stats');
  try{
    const doc=p.window.document;
    doc.querySelector('#experiment').value='candidate';doc.querySelector('#experiment').dispatchEvent(new p.window.Event('change'));await drain();
    assert.equal(doc.querySelector('#operations').value,'matmul');
    assert.equal(doc.querySelector('#continuous').disabled,true);
    doc.querySelector('#profiling').value='latency';doc.querySelector('#profiling').dispatchEvent(new p.window.Event('change'));await drain();
    doc.querySelector('#run').click();await drain();
    const request=p.calls.find(c=>c.path==='/api/runs'&&c.body).body;
    assert.equal(request.experiment,'candidate');assert.equal(request.profiling,'latency');
    assert.deepEqual(request.operations,['matmul']);
    assert.equal(p.state.jobs[0].configuration.cases.length,2);
    assert.equal(doc.querySelector('#experiment').disabled,true);
  }finally{p.close();}
});

test('returned kernel evidence shows source-level dispatch detail and paired intervals',async()=>{
  const report={run_id:id,status:'complete',suite_revision:'fixture-suite',configuration:{cases:['matmul_odd'],preset:'smoke'},
    device:{model:'Quest fixture'},experiment:{kind:'candidate'},
    kernel_comparison:{note:'Exploratory',results:[{case:'matmul_odd',available:true,pairs:6,outcome:'improvement_observed',metrics:{execution_ms:{mean_improvement_percent:12,ci95_percent:[8,16],mean_p95_change_percent:-10}}}]},
    results:[{case:'matmul_odd',operation:'matmul',variant:'candidate',phase:'isolated',trial:1,arm:'comparison',status:'passed',samples:[{execution_ms:1}],
      dispatch_summary:[{kernel_name:'xrbench_matmul_buffer_float',dispatch_id:1,local_size:[8,8,1],global_size:[5,4,1],complete:true,timing:{median:.2,p95:.3},shader:{source:'kernels/matmul/xrbench_matmul_scalar.glsl'}}]}]};
  const p=await page('stats',report);
  try{
    const doc=p.window.document;
    assert.match(doc.querySelector('#kernel-results').textContent,/12.00%/);
    assert.match(doc.querySelector('#kernel-results').textContent,/8.00% to 16.00%/);
    const detail=doc.querySelector('#case-matmul_odd');detail.open=true;detail.dispatchEvent(new p.window.Event('toggle'));await drain();
    assert.match(detail.textContent,/xrbench_matmul_buffer_float/);
    assert.match(detail.textContent,/kernels\/matmul\/xrbench_matmul_scalar.glsl/);
  }finally{p.close();}
});

test('live layer evidence advances without another Run and preserves expanded groups and keyboard focus',async()=>{
  const p=await page('bench');
  try{
    const doc=p.window.document;doc.querySelector('#run').click();await drain();
    const [first,second]=catalog.cases;
    const report={run_id:id,status:'running',suite_revision:'fixture-suite',configuration:p.state.jobs[0].configuration,
      results:[{case:first.id,operation:first.operation,phase:'isolated',status:'passed',timing_valid:true,correctness:{passed:true},samples:[{execution_ms:1,gpu_ms:null},{execution_ms:3,gpu_ms:null}]}]};
    p.setReport(report);
    Object.assign(p.state.jobs[0],{report_available:true,report_revision:'one',progress:{current_case:second.id,completed_cases:1,total_cases:63}});
    await p.tick();
    assert.equal(doc.querySelector('#completion-count').textContent,'1 / 63 cases processed');
    assert.match(doc.querySelector('#current-layer').textContent,new RegExp(second.id));
    const firstRow=doc.querySelector('.case-table-wrap tbody tr');
    assert.match(firstRow.textContent,/2\.000 ms/);assert.match(firstRow.textContent,/2\.900 ms/);
    assert.equal(firstRow.lastElementChild.textContent,'—');
    const group=doc.querySelector('.layer-group');group.open=true;group.querySelector('summary').focus();
    await p.tick();
    assert.equal(doc.querySelector('.layer-group').open,true);
    assert.equal(doc.activeElement.tagName,'SUMMARY');
    assert.equal(p.calls.filter(c=>c.path==='/api/runs'&&c.body).length,1);
    report.status='complete';p.state.jobs[0].status='complete';p.state.jobs[0].report_revision='two';
    await p.tick();
    assert.equal(doc.querySelector('#run-phase').textContent,'Run complete');
    assert.equal(doc.querySelector('#run-label').textContent,'Run');
    assert.equal(doc.querySelector('#results-link').getAttribute('href'),'/stats/?run='+id);
    doc.querySelector('#run').click();await drain();
    assert.equal(doc.querySelector('#completion-count').textContent,'0 / 63 cases processed');
    assert.doesNotMatch(doc.querySelector('#layer-results').textContent,/2\.000 ms/);
  }finally{p.close();}
});

test('result rows distinguish failures, unsupported cases and flagged timings without inventing measurements',async()=>{
  const cases=catalog.cases.slice(0,3),report={run_id:id,status:'complete',suite_revision:'fixture-suite',
    configuration:{mode:'isolated',preset:'smoke',cases:cases.map(c=>c.id)},results:cases.map((c,i)=>({case:c.id,operation:c.operation,phase:'isolated',status:['passed','failed','skipped'][i],timing_valid:i!==0,samples:[{execution_ms:123,gpu_ms:12}],reason:i===2?'<img src=x onerror=alert(1)> unsupported':null}))};
  const p=await page('bench',report);
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#completion-count').textContent,'3 / 3 cases processed');
    assert.match(doc.querySelector('#result-counts').textContent,/1 passed1 failed1 unsupported/);
    assert.match(doc.querySelector('#layer-results').textContent,/Timing flagged/);
    assert.doesNotMatch(doc.querySelector('#layer-results').textContent,/123\.000 ms|12\.000 ms/);
    assert.equal(doc.querySelector('#layer-results img'),null);
    assert.match(doc.querySelector('#layer-results').textContent,/<img src=x/);
  }finally{p.close();}
});

test('headset disconnect retains partial results and resumes the same run',async()=>{
  const p=await page('bench');
  try{
    const doc=p.window.document;doc.querySelector('#run').click();await drain();
    p.state.jobs[0].status='disconnected';p.state.jobs[0].message='Reconnect the headset to recover this run';p.state.devices=[];
    await p.tick();
    assert.equal(doc.querySelector('#run-phase').textContent,'Waiting for connection');
    assert.equal(doc.querySelector('#run-label').textContent,'Stop');
    assert.equal(doc.querySelector('#results').hidden,false);
    p.state.jobs[0].status='running';await p.tick();
    assert.equal(doc.querySelector('#run-phase').textContent,'Benchmarking');
    assert.equal(p.calls.filter(c=>c.path==='/api/runs'&&c.body).length,1);
  }finally{p.close();}
});

test('XR planned duration changes without changing the execution queue',async()=>{
  const p=await page('stats');
  try{
    const doc=p.window.document;doc.querySelector('#mode').value='xr';doc.querySelector('#mode').dispatchEvent(new p.window.Event('change'));
    assert.match(doc.querySelector('#preset-help').textContent,/21 minutes/);
    doc.querySelector('#preset').value='standard';doc.querySelector('#preset').dispatchEvent(new p.window.Event('change'));
    assert.match(doc.querySelector('#preset-help').textContent,/126 minutes/);
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.close();}
});

test('saved evidence hydrates both views without another Run, including unavailable graphics',async()=>{
  const statuses=['passed','failed','skipped','interrupted'];
  const report={run_id:id,status:'canceled',suite_revision:'fixture-suite',device:{model:'Synthetic fixture'},configuration:{mode:'isolated',preset:'smoke',cases:catalog.cases.map(c=>c.id)},results:catalog.cases.slice(0,4).map((c,i)=>({case:c.id,operation:c.operation,phase:'isolated',trial:0,status:statuses[i],correctness:i<2?{passed:i===0}:null,samples:[],reason:i===2?'Unsupported fixture':null}))};
  for(const kind of ['bench','stats']){
    const p=await page(kind,report);
    try{
      const doc=p.window.document;
      assert.equal(p.calls.filter(c=>c.body).length,0);
      if(kind==='stats'){
        assert.equal(doc.querySelector('#download').disabled,false);
        assert.equal(doc.querySelector('#failed').textContent,'1');
        assert.equal(doc.querySelector('#skipped').textContent,'1');
      }
      const href=doc.querySelector(kind==='bench'?'#stats-link':'#bench-link').getAttribute('href');
      assert.ok(href.includes('?run='+id));
      if(kind==='bench'){
        assert.match(doc.querySelector('.hero-actions #run-status').textContent,/Run canceled/);
        assert.equal(doc.querySelector('#run-status').hidden,false);
        assert.equal(doc.querySelector('#help-link').getAttribute('href'),'/stats/?run='+id);
      }
    }finally{p.close();}
  }
});

test('Run gives nearby setup guidance without starting an ambiguous or unavailable headset',async()=>{
  const ready={serial:'one',model:'Quest 3',state:'device',supported:true};
  for(const [state,message] of [
    [{devices:[]},/Connect your Quest 3/],
    [{devices:[{...ready,state:'unauthorized'}]},/approve USB debugging/],
    [{devices:[ready,{...ready,serial:'two'}]},/Choose a headset in Stats for Nerds/],
    [{apk_ready:false},/benchmark app is not ready/],
  ]){
    const p=await page('bench',null,state);
    try{
      const doc=p.window.document;
      doc.querySelector('#run').click();await drain();
      assert.equal(p.calls.filter(c=>c.body).length,0);
      assert.equal(doc.querySelector('.hero-actions #notice').hidden,false);
      assert.match(doc.querySelector('#notice-message').textContent,message);
      assert.equal(doc.querySelector('#help-link').getAttribute('href'),'/stats/');
    }finally{p.close();}
  }
});

test('failed Run and connection loss recover without leaving the hero button stuck',async()=>{
  const p=await page('bench');
  try{
    const doc=p.window.document;
    p.faults.start=true;
    doc.querySelector('#run').click();await drain();
    assert.equal(doc.querySelector('#run').disabled,false);
    assert.equal(doc.querySelector('.hero-actions #notice').hidden,false);
    assert.equal(p.state.jobs.length,0);
    p.faults.status=true;await p.tick();
    assert.equal(doc.querySelector('#run').disabled,true);
    p.faults.status=false;p.faults.start=false;await p.tick();
    assert.equal(doc.querySelector('#run').disabled,false);
    doc.querySelector('#run').click();await drain();
    assert.equal(p.state.jobs.length,1);
    assert.equal(doc.querySelector('#run-label').textContent,'Stop');
    assert.equal(doc.querySelector('#notice').hidden,true);
  }finally{p.close();}
});

test('device status drives the model and controls through connection changes',async()=>{
  const p=await page('bench',null,{devices:[]},'available');
  try{
    const doc=p.window.document,device={serial:'quest',model:'Quest 3',state:'unauthorized',supported:false};
    assert.equal(doc.querySelector('#scene').hidden,true);
    assert.equal(doc.querySelector('#device-name').textContent,'Connect XR device');
    p.state.devices=[device];await p.tick();
    assert.equal(doc.querySelector('#scene').hidden,false);
    assert.match(doc.querySelector('#device-help').textContent,/allow USB debugging/);
    assert.equal(p.viewer.devices.at(-1).authorized,false);
    device.state='device';device.supported=true;await p.tick();
    assert.equal(p.viewer.devices.at(-1).authorized,true);
    assert.equal(doc.querySelector('#viewer-controls').hidden,false);
    doc.querySelector('#reset-view').click();assert.equal(p.viewer.resets,1);
    p.viewer.availability(false);assert.equal(doc.querySelector('#viewer-controls').hidden,true);
    p.viewer.availability(true);assert.equal(doc.querySelector('#viewer-controls').hidden,false);
    p.faults.status=true;await p.tick();
    assert.equal(doc.querySelector('#device-name').textContent,'Local service unavailable');
    assert.equal(doc.querySelector('#scene').hidden,true);
    p.faults.status=false;await p.tick();
    assert.equal(doc.querySelector('#scene').hidden,false);
    device.state='offline';await p.tick();assert.equal(doc.querySelector('#scene').hidden,true);
    p.state.devices=[];await p.tick();
    assert.equal(doc.querySelector('#device-name').textContent,'Connect XR device');
    assert.equal(doc.querySelector('#viewer-controls').hidden,true);
    assert.equal(p.calls.filter(c=>c.body).length,0);
    p.window.dispatchEvent(new p.window.Event('pagehide'));assert.equal(p.viewer.disposed,1);
  }finally{p.close();}
});

test('late scene loading receives current device state',async()=>{
  const p=await page('bench',null,{},'delayed');
  try{
    p.state.devices=[];await p.tick();await p.releaseScene();
    assert.equal(p.viewer.devices.at(-1).kind,'absent');
    assert.equal(p.window.document.querySelector('#scene').hidden,true);
  }finally{p.close();}
});

test('a saved Quest report does not imply a currently connected headset',async()=>{
  const report={run_id:id,status:'complete',suite_revision:'fixture-suite',device:{model:'Quest 3'},
    configuration:{mode:'isolated',preset:'smoke',cases:catalog.cases.map(c=>c.id)},results:[]};
  const p=await page('bench',report,{devices:[]},'available');
  try{
    assert.equal(p.viewer.devices.at(-1).kind,'absent');
    assert.equal(p.window.document.querySelector('#scene').hidden,true);
    assert.match(p.window.document.querySelector('#run-status').textContent,/Run complete/);
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.close();}
});

test('unsupported and ambiguous devices never show the Quest 3 preview; missing graphics retains Run',async()=>{
  for(const devices of [
    [{serial:'3s',model:'Quest 3S',state:'device',supported:false}],
    ['a','b'].map(serial=>({serial,model:'Quest 3',state:'device',supported:true})),
  ]){
    const p=await page('bench',null,{devices},'available');
    try{
      assert.equal(p.window.document.querySelector('#scene').hidden,true);
      assert.equal(p.window.document.querySelector('#device-help-link').hidden,false);
    }finally{p.close();}
  }
  const p=await page('bench');
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#scene').hidden,false);
    assert.equal(doc.querySelector('#scene-fallback').hidden,false);
    assert.equal(doc.querySelector('#viewer-controls').hidden,true);
    doc.querySelector('#run').click();await drain();assert.equal(p.state.jobs.length,1);
  }finally{p.close();}
});


test('stats operation selection rejects unknown patterns and sends exact subsets with refresh policy',async()=>{
  const p=await page('stats');
  try{
    const doc=p.window.document,input=doc.querySelector('#operations');
    input.value='unknown*';input.dispatchEvent(new p.window.Event('input'));
    assert.equal(doc.querySelector('#run').disabled,true);assert.equal(input.getAttribute('aria-invalid'),'true');
    input.value='matmul, linear';input.dispatchEvent(new p.window.Event('input'));
    assert.match(doc.querySelector('#suite-summary').textContent,/2 operations · 12 cases/);
    doc.querySelector('#recalibrate').click();await drain();
    const request=p.calls.find(c=>c.path==='/api/runs'&&c.body).body;
    assert.deepEqual(request.operations,['linear','matmul']);assert.equal(request.calibration,'refresh');
    assert.equal(p.state.jobs[0].configuration.cases.length,12);
  }finally{p.close();}
});

test('continuous mode is explicit, heartbeats while visible, releases on hide, and stops without duplicate run requests',async()=>{
  const p=await page('stats');
  try{
    const doc=p.window.document;
    assert.equal(p.calls.filter(c=>c.path==='/api/watch').length,0);
    doc.querySelector('#continuous').click();await drain();await p.tick();
    assert.deepEqual(p.calls.filter(c=>c.path==='/api/watch').map(c=>c.body.action),['enable','heartbeat']);
    await p.setHidden(true);assert.equal(p.calls.at(-1).body.action,'disable');
    await p.setHidden(false);assert.equal(p.calls.at(-1).body.action,'enable');
    doc.querySelector('#stop').click();await drain();
    assert.equal(doc.querySelector('#continuous').getAttribute('aria-pressed'),'false');
    assert.equal(p.calls.filter(c=>c.path==='/api/runs'&&c.body).length,0);
  }finally{p.close();}
});

test('hiding during an in-flight enable releases the resulting lease',async()=>{
  const p=await page('stats');
  try{
    let release;p.faults.watchGate=new Promise(resolve=>{release=resolve;});
    p.window.document.querySelector('#continuous').click();await drain();
    await p.setHidden(true);release();await drain();
    assert.deepEqual(p.calls.filter(c=>c.path==='/api/watch').map(c=>c.body.action),['enable','disable']);
  }finally{p.close();}
});

test('stats live rankings preserve evidence expansion, focus, filters and table scroll; diagnostics omit private identity',async()=>{
  const selected=catalog.cases.slice(0,2),report={run_id:id,status:'running',suite_revision:'fixture-suite',device:{model:'Synthetic fixture'},
    configuration:{mode:'isolated',preset:'smoke',cases:selected.map(c=>c.id)},
    calibration:{version:1,available:true,compute_gflops:100,memory_gbps:10,dispatch_ms:.01,identity:'private-cache-key'},
    diagnostics:{version:1,session_ms:30,stages_ms:{preparation:10,measurement:18,validation:2}},
    results:selected.map((c,i)=>({...c,case:c.id,phase:'isolated',trial:0,status:'passed',timing_valid:true,correctness:{passed:true},
      samples:[{execution_ms:i+1,gpu_ms:.2}],validation_observations:[{total_ms:i+1,inference_ms:.5,comparison_ms:.1,passed:true}],
      efficiency:{available:false,reason:'Common-queue GPU span unavailable'}}))};
  const p=await page('stats',report,{jobs:[{run_id:id,status:'running',report_available:true,report_revision:'one',configuration:report.configuration,diagnostics:{transfer_ms:12,hostname:'private-host'}}]});
  try{
    const doc=p.window.document;
    assert.match(doc.querySelector('#correctness-ranking tbody tr').textContent,new RegExp(selected[1].id));
    const sort=[...doc.querySelectorAll('#correctness-ranking th button')].find(e=>e.textContent.includes('Validation'));
    sort.click();assert.match(doc.querySelector('#correctness-ranking tbody tr').textContent,new RegExp(selected[0].id));
    doc.querySelector('#correctness-ranking .case-link').click();await drain();
    assert.equal(p.window.location.hash,'#layers');assert.equal(doc.querySelector('.case').open,true);assert.equal(doc.querySelector('.operation-group').open,true);
    const raw=doc.querySelector('.raw-details');raw.open=true;await drain();raw.querySelector('summary').focus();
    const scroll=doc.querySelector('.case .table-scroll');scroll.scrollLeft=123;
    p.state.jobs[0].report_revision='two';report.results[0].samples.push({execution_ms:2,gpu_ms:.3});await p.tick();
    assert.equal(doc.querySelector('.operation-group').open,true);assert.equal(doc.querySelector('.case').open,true);assert.equal(doc.querySelector('.raw-details').open,true);
    assert.equal(doc.activeElement.dataset.focusKey,'raw:'+selected[0].id+':isolated:0');
    assert.equal(doc.querySelector('.case .table-scroll').scrollLeft,123);assert.equal(doc.querySelector('#layer-search').value,selected[0].id);
    doc.querySelector('#download').click();await drain();const downloaded=JSON.parse(await p.downloads[0].text());
    assert.equal(downloaded.calibration.identity,undefined);assert.equal(downloaded.controller_diagnostics.hostname,undefined);
    assert.equal(downloaded.controller_diagnostics.transfer_ms,12);assert.ok(downloaded.timing_definitions.gpu_span_ms);
    p.window.location.hash='runs';await drain();assert.equal(doc.querySelector('#runs').open,true);
  }finally{p.close();}
});
