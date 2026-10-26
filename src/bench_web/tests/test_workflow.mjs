import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from 'jsdom';

const root=new URL('../../../',import.meta.url);
const catalog=JSON.parse(await readFile(new URL('engine/workloads/catalog.json',root),'utf8'));
const id='run-'+'a'.repeat(32);
const benchId='bench-'+'b'.repeat(32);
const devicesCatalog=JSON.parse(await readFile(new URL('engine/device-catalog.json',root),'utf8'));
const paths={
  '/shared/session.mjs':'engine/web/session.mjs',
  '/stats/view-model.mjs':'src/stats_for_nerds/web/view-model.mjs',
  '/stats/app.js':'src/stats_for_nerds/web/app.js',
  '/bench/processor-policy.mjs':'src/bench_web/web/processor-policy.mjs',
  '/bench/app.js':'src/bench_web/web/app.js',
  '/bench/device-view.mjs':'src/bench_web/web/device-view.mjs',
  '/bench/results-view.mjs':'src/bench_web/web/results-view.mjs',
  '/bench/chip-view.mjs':'src/bench_web/web/chip-view.mjs',
  '/bench/features.mjs':'src/bench_web/web/features.mjs',
  '/bench/ascii-headset.mjs':'src/bench_web/web/ascii-headset.mjs',
  '/bench/smoke-demo.mjs':'src/bench_web/web/smoke-demo.mjs',
};
const drain=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));};
async function page(kind,report=null,stateOverrides={},options={}){
  const pretty=kind==='bench',fixtureOptions=options;
  const html=await readFile(new URL(`src/${pretty?'bench_web':'stats_for_nerds'}/web/index.html`,root),'utf8');
  const dom=new JSDOM(html,{url:'http://localhost:8765/'+(pretty?'':'stats/')+(options.search??(report?'?run='+id:'')),runScripts:'outside-only'});
  const window=dom.window,calls=[],intervals=new Map(),timeouts=new Map(),faults={start:false,status:false,...options.faults},downloads=[],scrolls=[];
  window.document.documentElement.dataset.transport=options.hosted?'webusb':'local';
  let time=Date.now(),timerId=0,nativeStarts=0,frameTime=0;const frames=new Map();window.Date.now=()=>time;window.structuredClone=structuredClone;
  let hidden=false;Object.defineProperty(window.document,'hidden',{get:()=>hidden});
  window.Blob=Blob;window.URL.createObjectURL=blob=>{downloads.push(blob);return 'blob:fixture';};window.URL.revokeObjectURL=()=>{};
  window.document.addEventListener('click',e=>{if(e.target.closest('a[download]'))e.preventDefault();});
  const state={devices:[{serial:'fixture',model:'Quest 3',state:'device',supported:true}],database:{ready:true,message:'Local PostgreSQL ready'},jobs:[],apk_ready:true,catalog,suite_revision:'fixture-suite',...stateOverrides};
  if(fixtureOptions.mobile){Object.defineProperty(window,'innerWidth',{value:320});window.HTMLElement.prototype.scrollIntoView=function(options){scrolls.push({target:this.id||this.className,options});};}
  if(fixtureOptions.animation){
    const stage=window.document.querySelector('#headset-stage'),art=stage.querySelector('pre');
    stage.getBoundingClientRect=()=>({left:0,top:0,width:620,height:parseFloat(stage.style.height)||430});
    art.getBoundingClientRect=()=>{const rows=art.textContent.split('\n'),font=parseFloat(art.style.fontSize)||7;return {width:rows[0].length*font*.6,height:rows.length*font*1.2};};
    window.requestAnimationFrame=callback=>{const key=++timerId;frames.set(key,callback);return key;};
    window.cancelAnimationFrame=key=>frames.delete(key);
  }
  window.fetch=async(path,options={})=>{
    const body=options.body?JSON.parse(options.body):null;
    calls.push({path,body});
    if((['/api/runs','/api/benchmark-sessions'].includes(path)&&body&&faults.start)||(path==='/api/status'&&faults.status)||(path==='/api/device-catalog'&&faults.catalog))return {ok:false,json:async()=>({error:'Local service unavailable'})};
    let value;
    if(path==='/api/session')value={token:'fixture-token'};
    else if(path==='/api/status'){
      if(state.benchmark_sessions?.length&&state.jobs.length&&fixtureOptions.syncSession!==false){const s=state.benchmark_sessions[0];s.phases[0].run=state.jobs[0];s.status=state.jobs[0].status;s.cancel_requested=state.jobs[0].cancel_requested;s.phases[0].status=s.status;}
      value=structuredClone(state);
    }
    else if(path==='/api/device-catalog')value=fixtureOptions.deviceCatalog||devicesCatalog;
    else if(path==='/api/benchmark-sessions'){
      assert.deepEqual(Object.keys(body).sort(),['processor','request_id','serial']);
      const runId=++nativeStarts===1?id:'run-'+String(nativeStarts).padStart(32,'d');
      const sessionId=nativeStarts===1?benchId:'bench-'+String(nativeStarts).padStart(32,'e');
      const processor=body.processor||'gpu',backend=processor==='cpu'?'executorch_cpu':'executorch_vulkan';
      const job={run_id:runId,status:'running',configuration:{mode:'isolated',preset:'smoke',backend,cases:catalog.cases.map(c=>c.id)},message:'Test 1 of 63',storage:'pending'};
      state.jobs=[job];value={session_id:sessionId,status:'running',created_at:Date.now()/1000,current_phase:0,identity:state.devices[0]?.identity||{device_id:'quest-3'},connection_id:state.devices[0]?.connection_id||body.serial,processor,
        phases:[{processor,backend,status:'running',run_id:runId,run:job,configuration:job.configuration}]};
      state.benchmark_sessions=[value];
    }
    else if(path.startsWith('/api/saved'))value={runs:[],next_offset:null};
    else if(path==='/api/runs'&&body){
      assert.deepEqual(Object.keys(body).filter(k=>!['experiment','profiling','backend','workload_suite','execution_target'].includes(k)).sort(),pretty?['mode','preset','serial']:['calibration','mode','operations','preset','serial']);
      value={run_id:id,status:'running',configuration:{...body,cases:catalog.cases.filter(c=>!body.operations||body.operations.includes(c.operation)).map(c=>c.id)},message:'Test 1 of 63',storage:'pending'};
      state.jobs=[value];
    }else if(path==='/api/devices/probe'){
      state.probes=[{serial:body.serial,status:'running',message:'Inspecting native capabilities'}];value=state.probes[0];
    }else if(path==='/api/watch'){
      if(faults.watchGate&&body.action==='enable')await faults.watchGate;
      state.watch_status=body.action==='disable'?[]:[{owner:body.owner,serial:body.serial,state:'Watching for changes',run_id:null}];value=state.watch_status;
    }else if(path.endsWith('/stop')){if(state.jobs[0])state.jobs[0].cancel_requested=true;if(path.includes('benchmark-sessions')){state.benchmark_sessions[0].cancel_requested=true;value=state.benchmark_sessions[0];}else value=state.jobs[0];}
    else if(path.endsWith('/report'))value=report;
    else throw Error('Unexpected API request '+path);
    return {ok:true,json:async()=>value};
  };
  window.__browserService={
    request:async(path,body)=>(await window.fetch(path,{body:body===undefined?undefined:JSON.stringify(body)})).json(),
    start:async(serial,request_id,processor)=>{
      const s=await (await window.fetch('/api/benchmark-sessions',{body:JSON.stringify({serial,request_id,processor})})).json();
      if(options.preparing){state.jobs=[];s.status='starting';s.message='Downloading headset runner…';for(const p of s.phases){delete p.run;p.status='pending';}}
      return s;
    },
    exportSession:async()=>({reports:[report]}),dispose:()=>{},
    retry:async sessionId=>{
      calls.push({path:'/api/benchmark-sessions/'+sessionId+'/retry'});
      const s=state.benchmark_sessions.find(s=>s.session_id===sessionId);s.connection_state='connecting';s.message='Connecting';
      for(const d of state.devices)d.connection_state='connecting';return structuredClone(s);
    },
  };
  window.setInterval=callback=>{const key=++timerId;intervals.set(key,callback);return key;};
  window.clearInterval=key=>intervals.delete(key);
  window.setTimeout=(callback,delay)=>{const key=++timerId;timeouts.set(key,{callback,at:time+delay});return key;};
  window.clearTimeout=key=>timeouts.delete(key);
  const cache=new Map(),context=dom.getInternalVMContext();
  async function module(path){
    if(cache.has(path))return cache.get(path);
    if(path==='/bench/browser/entry.mjs'){
      const mod=new vm.SourceTextModule('export const createBrowserService=()=>globalThis.__browserService;',{context,identifier:path});cache.set(path,mod);await mod.link(()=>{});return mod;
    }
    const file=paths[path];if(!file)throw Error('Unknown module '+path);
    let source=path==='/bench/features.mjs'&&options.demoEnabled===false?'export const ENABLE_SMOKE_DEMO=false;':await readFile(new URL(file,root),'utf8');
    if(options.rendererFailure&&path==='/bench/ascii-headset.mjs')source=source.replace('createRenderer = createAsciiRenderer', "createRenderer = () => {throw Error('Unavailable graphics');}");
    const mod=new vm.SourceTextModule(source,{context,identifier:path,importModuleDynamically:async(specifier,ref)=>{
      const imported=await module(new URL(specifier,'http://fixture'+ref.identifier).pathname);await imported.evaluate();return imported;
    }});
    cache.set(path,mod);
    await mod.link((specifier,ref)=>module(new URL(specifier,'http://fixture'+ref.identifier).pathname));
    return mod;
  }
  await (await module(pretty?'/bench/app.js':'/stats/app.js')).evaluate();await drain();
  return {window,state,calls,faults,downloads,scrolls,modules:cache,timers:{intervals,timeouts,frames},advanceFrames:ms=>{const end=frameTime+ms;while(frameTime<end){frameTime=Math.min(end,frameTime+1000/60);for(const [id,fn]of [...frames]){frames.delete(id);fn(frameTime);}}},setHidden:async value=>{hidden=value;window.document.dispatchEvent(new window.Event('visibilitychange'));await drain();},
    setReport:value=>{report=value;},
    close:()=>{window.dispatchEvent(new window.Event('pagehide'));window.close();},tick:async(ms=0)=>{
      time+=ms;
      for(const [key,timer] of [...timeouts])if(timer.at<=time){timeouts.delete(key);await timer.callback();}
      for(const [key,callback] of [...intervals])if(intervals.has(key))await callback();await drain();
    }};
}

test('hosted Run ignores local APK setup and Cancel remains available while downloading',async()=>{
  const p=await page('bench',null,{devices:[],apk_ready:false},{hosted:true,preparing:true});
  try{
    const doc=p.window.document;
    assert.equal(doc.getElementById('stats-link').hidden,true);
    doc.getElementById('run').click();doc.getElementById('run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body).length,1);
    assert.equal(doc.getElementById('run-label').textContent,'Cancel');
    assert.equal(doc.getElementById('chip-dashboard').hidden,true);
    assert.equal(doc.getElementById('results').hidden,true);
    assert.equal(doc.getElementById('run').disabled,false);
    assert.match(doc.getElementById('run-status').textContent,/Downloading headset runner/);
    assert.equal(doc.getElementById('setup-help'),null);
    doc.getElementById('run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);
    assert.equal(doc.getElementById('run-label').textContent,'Stopping…');
  }finally{p.close();}
});

test('hosted reports show automatic sending, retry, and confirmed local capture without an extra action',async()=>{
  const p=await page('bench',null,{}, {hosted:true});
  try{
    const doc=p.window.document,notice=doc.getElementById('report-delivery-status');
    assert.equal(notice.hidden,false);assert.match(notice.textContent,/sent automatically/);
    doc.getElementById('run').click();await drain();
    const location=p.window.location.href;
    p.state.report_delivery={reports:[{run_id:id,status:'sending'}]};await p.tick();
    assert.match(notice.textContent,/Sending benchmark report/);
    p.state.report_delivery.reports[0]={run_id:id,status:'pending',error:'offline'};await p.tick();
    assert.match(notice.textContent,/retry automatically/);
    p.state.report_delivery.reports[0]={run_id:id,status:'sent',receipt:{storage:'local'}};await p.tick();
    assert.equal(notice.textContent,'Report saved locally.');
    assert.equal(doc.getElementById('run-label').textContent,'Stop');
    assert.equal(p.window.location.href,location);
    assert.equal(doc.querySelector('[id*=contribute],[id*=upload]'),null);
  }finally{p.close();}
});

test('native progress and case evidence remain available without visualization components',async()=>{
  const p=await page('bench');
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('.headset-baseline,.headset-hint'),null);
    assert.doesNotMatch(doc.querySelector('#headset-stage').textContent,/Built for another dimension|Move a little closer/);
    assert.equal(doc.querySelector('.headset-topline'),null);
    assert.equal(doc.querySelector('#headset-stage').parentElement,doc.querySelector('.hero'));
    assert.equal(doc.querySelector('#headset-stage pre').getAttribute('aria-hidden'),'true');
    doc.getElementById('run').click();await drain();
    const panel=doc.getElementById('chip-dashboard');
    assert.equal(panel.hidden,false);
    assert.equal(doc.querySelector('#scene,canvas,#viewer-controls,#reset-view,.assembly-order,.chip-region-key'),null);
    assert.match(panel.textContent,/GPU/);
    assert.equal(doc.querySelectorAll('#chip-case-evidence li').length,63);
  }finally{p.close();}
});

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
    const requests=p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body);
    assert.equal(requests.length,1);assert.equal(p.state.jobs[0].configuration.mode,'isolated');assert.equal(p.state.jobs[0].configuration.preset,'smoke');
    assert.equal(p.state.jobs[0].configuration.cases.length,63);
    assert.equal(new Set(p.state.jobs[0].configuration.cases).size,63);
    assert.equal(doc.querySelector('#stats-link').getAttribute('href'),'/stats/?run='+id);
    assert.equal(new URLSearchParams(p.window.location.search).get('run'),id);assert.equal(new URLSearchParams(p.window.location.search).get('session'),benchId);
    assert.equal(doc.querySelector('#run-label').textContent,'Stop');
    assert.match(doc.querySelector('#run-status').textContent,/GPU · native execution/);
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

for (const source of ['kernels/matmul/xrbench_matmul_scalar.glsl',
  'https://github.com/Michael-RDev/xrGPU/blob/' + 'a'.repeat(40) + '/kernels/benchmark/matmul/xrbench_matmul_scalar.glsl'])
test('returned kernel evidence shows source-level dispatch detail and paired intervals: ' + source,async()=>{
  const report={run_id:id,status:'complete',suite_revision:'fixture-suite',configuration:{cases:['matmul_odd'],preset:'smoke'},
    device:{model:'Quest fixture'},experiment:{kind:'candidate'},
    kernel_comparison:{note:'Exploratory',results:[{case:'matmul_odd',available:true,pairs:6,outcome:'improvement_observed',metrics:{execution_ms:{mean_improvement_percent:12,ci95_percent:[8,16],mean_p95_change_percent:-10}}}]},
    results:[{case:'matmul_odd',operation:'matmul',variant:'candidate',phase:'isolated',trial:1,arm:'comparison',status:'passed',samples:[{execution_ms:1}],
      dispatch_summary:[{kernel_name:'xrbench_matmul_buffer_float',dispatch_id:1,local_size:[8,8,1],global_size:[5,4,1],complete:true,timing:{median:.2,p95:.3},shader:{source}}]}]};
  const p=await page('stats',report);
  try{
    const doc=p.window.document;
    assert.match(doc.querySelector('#kernel-results').textContent,/12.00%/);
    assert.match(doc.querySelector('#kernel-results').textContent,/8.00% to 16.00%/);
    const detail=doc.querySelector('#case-matmul_odd');detail.open=true;detail.dispatchEvent(new p.window.Event('toggle'));await drain();
    assert.match(detail.textContent,/xrbench_matmul_buffer_float/);
    assert.ok(detail.textContent.includes(source));
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
    assert.equal(p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body).length,1);
    report.status='complete';p.state.jobs[0].status='complete';p.state.jobs[0].report_revision='two';
    await p.tick();
    assert.equal(doc.querySelector('#run-phase').textContent,'Run complete');
    assert.equal(doc.querySelector('#run-label').textContent,'Run');
    assert.equal(doc.querySelector('#results-link').getAttribute('href'),'#layer-results');
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
    assert.equal(p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body).length,1);
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

test('saved evidence hydrates both views without another Run',async()=>{
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
        assert.match(doc.querySelector('.hero-actions #run-status').textContent,/Benchmark canceled/);
        assert.equal(doc.querySelector('#run-status').hidden,false);
        assert.equal(doc.querySelector('#help-link'),null);
      }
    }finally{p.close();}
  }
});

test('Run gives nearby setup guidance without starting an ambiguous or unavailable headset',async()=>{
  const ready={serial:'one',model:'Quest 3',state:'device',supported:true};
  for(const [state,message] of [
    [{devices:[]},/Connect an Android arm64 device/],
    [{devices:[{...ready,state:'unauthorized'}]},/allow USB debugging/],
    [{devices:[ready,{...ready,serial:'two'}]},/Choose one device/],
    [{apk_ready:false},/runner.*unavailable/],
  ]){
    const p=await page('bench',null,state);
    try{
      const doc=p.window.document;
      doc.querySelector('#run').click();await drain();
      assert.equal(p.calls.filter(c=>c.body).length,0);
      assert.equal(doc.querySelector('.hero-actions #notice').hidden,false);
      assert.match(doc.querySelector('#notice-message').textContent,message);
      assert.equal(doc.querySelector('#notice a'),null);
      assert.equal(doc.querySelector('#retry-status').textContent,'Retry');
      assert.equal(p.window.location.pathname,'/');
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

test('device status remains accurate through connection changes',async()=>{
  const p=await page('bench',null,{devices:[]});
  try{
    const doc=p.window.document,device={serial:'quest',model:'Quest 3',state:'unauthorized',supported:false};
    assert.equal(doc.querySelector('#device-name').textContent,'Connect XR device');
    p.state.devices=[device];await p.tick();
    assert.match(doc.querySelector('#device-help').textContent,/allow USB debugging/);
    assert.equal(doc.querySelector('.device-panel').dataset.state,'unauthorized');
    device.state='device';device.supported=true;await p.tick();
    assert.equal(doc.querySelector('.device-panel').dataset.state,'connected');
    p.faults.status=true;await p.tick();
    assert.equal(doc.querySelector('#device-name').textContent,'Local service unavailable');
    p.faults.status=false;await p.tick();
    device.state='offline';await p.tick();
    assert.equal(doc.querySelector('#device-name').textContent,'Device is offline');
    p.state.devices=[];await p.tick();
    assert.equal(doc.querySelector('#device-name').textContent,'Connect XR device');
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.close();}
});

test('homepage keeps completed runs in collapsed history and Back clears selection permanently',async()=>{
  const report={run_id:id,status:'complete',suite_revision:'fixture-suite',configuration:{backend:'executorch_vulkan',cases:catalog.cases.map(c=>c.id)},results:[]};
  const job={...report,report_available:true,created_at:1700000000};
  const parent={session_id:benchId,status:'complete',current_phase:0,created_at:1700000000,identity:{device_name:'Quest 3'},
    phases:[{processor:'gpu',run_id:id,run:job,status:'complete',configuration:job.configuration}]};
  const p=await page('bench',report,{jobs:[job],benchmark_sessions:[parent]},{hosted:true,search:''});
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#run-label').textContent,'Run');
    assert.equal(doc.querySelector('#chip-dashboard').hidden,true);assert.equal(doc.querySelector('#results').hidden,true);
    assert.equal(doc.querySelector('#previous-runs').hidden,false);assert.equal(doc.querySelector('#previous-runs').open,false);
    assert.equal(p.window.location.search,'');assert.equal(p.calls.filter(c=>c.path.endsWith('/report')).length,0);
    doc.querySelector('#previous-runs').open=true;doc.querySelector('#previous-runs-list button').click();await drain();
    assert.equal(doc.querySelector('#results').hidden,false);assert.equal(doc.querySelector('#chip-dashboard').hidden,false);
    assert.equal(new URLSearchParams(p.window.location.search).get('session'),benchId);
    doc.querySelector('#back-headset').click();await p.tick();await p.tick();
    assert.equal(p.window.location.search,'');assert.equal(doc.querySelector('#results').hidden,true);
    assert.equal(doc.querySelector('#chip-dashboard').hidden,true);assert.equal(doc.querySelector('#run-label').textContent,'Run');
    assert.equal(doc.querySelectorAll('#previous-runs-list button').length,1);
    doc.querySelector('#previous-runs-list button').click();await drain();
    assert.equal(doc.querySelector('#results').hidden,false);
  }finally{p.close();}
});

test('a busy connection offers Retry and Cancel without showing benchmark progress',async()=>{
  const parent={session_id:benchId,status:'disconnected',connection_state:'blocked',message:'The USB interface is in use. Close other headset tools, then select Retry connection.',
    current_phase:0,created_at:1700000000,phases:[{processor:'gpu',run_id:id,status:'pending',configuration:{backend:'executorch_vulkan',cases:catalog.cases.map(c=>c.id)}}]};
  const p=await page('bench',null,{benchmark_sessions:[parent],devices:[{serial:'fixture',model:'Quest 3',state:'usb',supported:false,connection_state:'blocked',connection_message:parent.message}]},{hosted:true});
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#run-label').textContent,'Retry connection');
    assert.equal(doc.querySelector('#connection-cancel').hidden,false);
    assert.equal(doc.querySelector('#device-connection').textContent,'Connection blocked');
    assert.match(doc.querySelector('#device-help').textContent,/Close other headset tools/);
    assert.equal(doc.querySelector('#chip-dashboard').hidden,true);assert.equal(doc.querySelector('#chip-details').hidden,true);
    assert.equal(doc.querySelector('#results').hidden,true);
    await p.tick(60000);assert.equal(p.calls.filter(c=>c.path.endsWith('/retry')).length,0);
    doc.querySelector('#run').click();doc.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/retry')).length,1);
    assert.equal(p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body).length,0);
    assert.equal(doc.querySelector('#device-connection').textContent,'Connecting');
    assert.equal(doc.querySelector('#run-label').textContent,'Cancel');
    doc.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);
  }finally{p.close();}
});

test('uncertain launch hides benchmark progress until native evidence arrives',async()=>{
  const job={run_id:id,status:'launching',configuration:{backend:'executorch_vulkan',cases:catalog.cases.map(c=>c.id)}};
  const parent={session_id:benchId,status:'running',current_phase:0,phases:[{processor:'gpu',run_id:id,attempted:true,status:'launching',run:job}]};
  const p=await page('bench',null,{jobs:[job],benchmark_sessions:[parent]},{hosted:true,syncSession:false});
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#chip-dashboard').hidden,true);assert.equal(doc.querySelector('#results').hidden,true);
    assert.equal(doc.querySelector('#run-label').textContent,'Stop');
    job.status='running';await p.tick();
    assert.equal(doc.querySelector('#chip-dashboard').hidden,false);assert.equal(doc.querySelector('#results').hidden,false);
  }finally{p.close();}
});

test('a canceled connection does not claim that benchmark results exist',async()=>{
  const parent={session_id:benchId,status:'canceled',current_phase:0,phases:[{processor:'gpu',run_id:id,status:'pending'}]};
  const p=await page('bench',null,{benchmark_sessions:[parent]},{hosted:true,search:'?session='+benchId});
  try{
    assert.equal(p.window.document.querySelector('#run-status').textContent,'Connection canceled. No benchmark was started.');
    assert.equal(p.window.document.querySelector('#results').hidden,true);
  }finally{p.close();}
});

test('USB detected is distinct from a verified ready connection',async()=>{
  const device={serial:'fixture',model:'Quest 3',state:'usb',supported:false,connection_state:'detected'};
  const p=await page('bench',null,{devices:[device]},{hosted:true});
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#device-connection').textContent,'USB detected');
    assert.equal(doc.querySelector('.device-panel').dataset.state,'detected');
    assert.equal(doc.querySelector('#run').disabled,false);
    assert.doesNotMatch(doc.querySelector('#device-chip').textContent,/XR2/);
    device.connection_state='ready';device.identity={device_name:'Quest 3',chip_name:'Verified processor'};await p.tick();
    assert.equal(doc.querySelector('#device-connection').textContent,'Ready');
    assert.equal(doc.querySelector('#device-chip').textContent,'Verified processor');
  }finally{p.close();}
});

test('an explicitly linked saved Quest report does not imply a currently connected headset',async()=>{
  const report={run_id:id,status:'complete',suite_revision:'fixture-suite',device:{model:'Quest 3'},
    configuration:{mode:'isolated',preset:'smoke',cases:catalog.cases.map(c=>c.id)},results:[]};
  const p=await page('bench',report,{devices:[]});
  try{
    assert.equal(p.window.document.querySelector('#device-name').textContent,'Connect XR device');
    assert.match(p.window.document.querySelector('#run-status').textContent,/Benchmark complete/);
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.close();}
});

test('compatibility groups follow primary processors, including mixed-chip headsets and unknown variants',async()=>{
  const deviceCatalog=structuredClone(devicesCatalog);
  deviceCatalog.devices.push({id:'unknown-variant',name:'Unknown variant',chips:[],primary_chip_options:['xr2-gen2','missing-chip']});
  const p=await page('bench',null,{}, {deviceCatalog});
  try{
    const doc=p.window.document;
    const group=kind=>doc.querySelector(`[data-support="${kind}"]`).textContent;
    assert.match(group('check'),/Meta Quest 3/);
    assert.match(group('check'),/XREAL Aura.*compute puck; the glasses are not benchmarked/);
    assert.doesNotMatch(group('check'),/Apple Vision Pro|XREAL One|Unknown variant/);
    assert.match(group('planned'),/Apple Vision Pro.*Apple M2 or Apple M5/);
    assert.doesNotMatch(group('unavailable'),/Apple Vision Pro|XREAL Aura/);
    assert.match(group('unavailable'),/XREAL One/);
    assert.doesNotMatch(group('unavailable'),/Unknown variant/);
    assert.match(group('unknown'),/Unknown variant/);
    assert.equal(doc.querySelectorAll('#device-catalog li').length,deviceCatalog.devices.length);
  }finally{p.close();}
});

test('unsupported and ambiguous devices require setup or explicit selection',async()=>{
  for(const devices of [
    [{serial:'3s',model:'Quest 3S',state:'device',supported:false}],
    ['a','b'].map(serial=>({serial,model:'Quest 3',state:'device',supported:true})),
  ]){
    const p=await page('bench',null,{devices});
    try{
      const doc=p.window.document;
      assert.equal(doc.querySelector('#device-picker').hidden,devices.length<2);
      assert.equal(doc.querySelector('.device-panel').dataset.state,devices.length>1?'multiple':'unsupported');
      doc.querySelector('#run').click();await drain();
      assert.equal(p.calls.filter(c=>c.body).length,0);
      assert.equal(doc.querySelector('#notice').hidden,false);
    }finally{p.close();}
  }
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

test('new Android devices use compute controls without pretending to be a Quest',async()=>{
  const p=await page('stats',null,{devices:[{serial:'phone',model:'Companion phone',state:'device',supported:true,support_label:'Capability check required'}]});
  try{
    const doc=p.window.document;
    assert.match(doc.querySelector('#device').textContent,/Capability check required/);
    doc.querySelector('#backend').value='vulkan_resident';doc.querySelector('#backend').dispatchEvent(new p.window.Event('change'));await drain();
    assert.equal(doc.querySelector('#operations').value,'matmul');
    doc.querySelector('#execution-target').value='phone';doc.querySelector('#execution-target').dispatchEvent(new p.window.Event('change'));
    doc.querySelector('#mode').value='xr';doc.querySelector('#mode').dispatchEvent(new p.window.Event('change'));
    assert.equal(doc.querySelector('#run').disabled,true);
    doc.querySelector('#mode').value='isolated';doc.querySelector('#mode').dispatchEvent(new p.window.Event('change'));
    doc.querySelector('#run').click();await drain();
    const request=p.calls.find(c=>c.path==='/api/runs').body;
    assert.equal(request.backend,'vulkan_resident');assert.equal(request.execution_target,'phone');assert.deepEqual(request.operations,['matmul']);
  }finally{p.close();}
});

test('capability inspection is independent of benchmark execution and profile downloads exclude serials',async()=>{
  const p=await page('stats',null,{device_profiles:[{serial:'fixture',profile_id:'a'.repeat(64),profile:{model:'Quest 3',gpu:'Synthetic GPU',serial:'must-not-share',backends:{executorch_vulkan:{compatible:true,status:'compatible_unvalidated',reason:'Needs hardware validation'}}}}]});
  try{
    const doc=p.window.document;
    assert.match(doc.querySelector('#profile-status').textContent,/Compatible · not yet validated/);
    doc.querySelector('#download-profile').click();await drain();
    const profile=JSON.parse(await p.downloads[0].text());assert.equal(profile.gpu,'Synthetic GPU');assert.equal(profile.serial,undefined);
    doc.querySelector('#probe-device').click();await drain();
    assert.equal(p.calls.filter(c=>c.path==='/api/runs').length,0);
    assert.equal(doc.querySelector('#run').disabled,true);
    assert.match(doc.querySelector('#profile-status').textContent,/Inspecting/);
  }finally{p.close();}
});

test('development suite changes selected workloads but keeps the standard suite intact',async()=>{
  const development=JSON.parse(await readFile(new URL('engine/workloads/development.json',root),'utf8'));
  const p=await page('stats',null,{development_catalog:{...catalog,cases:[...catalog.cases,...development.cases]}});
  try{
    const doc=p.window.document;
    doc.querySelector('#workload-suite').value='development';doc.querySelector('#workload-suite').dispatchEvent(new p.window.Event('change'));
    doc.querySelector('#operations').value='matmul';doc.querySelector('#operations').dispatchEvent(new p.window.Event('input'));
    assert.match(doc.querySelector('#suite-summary').textContent,/14 cases/);
    doc.querySelector('#workload-suite').value='standard';doc.querySelector('#workload-suite').dispatchEvent(new p.window.Event('change'));
    assert.match(doc.querySelector('#suite-summary').textContent,/2 cases/);
  }finally{p.close();}
});

test('CPU controls keep native execution available without Vulkan and expose resident experiments',async()=>{
  const p=await page('stats',null,{device_profiles:[{serial:'fixture',profile:{soc_model:'Snapdragon XR2 Gen 2',backends:{executorch_cpu:{compatible:true,status:'compatible_unvalidated',reason:'Portable CPU'},executorch_vulkan:{compatible:false,status:'unsupported',reason:'Vulkan unavailable'}}}}]});
  try{
    const doc=p.window.document,backend=doc.getElementById('backend');
    backend.value='executorch_cpu';backend.dispatchEvent(new p.window.Event('change'));await drain();
    assert.equal(doc.getElementById('run').disabled,false);
    assert.equal(doc.getElementById('recalibrate').disabled,true);
    assert.equal(doc.getElementById('profiling').disabled,true);
    assert.equal(doc.getElementById('experiment').disabled,true);
    assert.match(doc.getElementById('profile-status').textContent,/Snapdragon XR2/);
    backend.value='cpu_resident';backend.dispatchEvent(new p.window.Event('change'));await drain();
    assert.equal(doc.getElementById('operations').value,'matmul');
    assert.equal(doc.getElementById('experiment').disabled,false);
    doc.getElementById('experiment').value='candidate';doc.getElementById('experiment').dispatchEvent(new p.window.Event('change'));await drain();
    assert.match(doc.getElementById('experiment-help').textContent,/NEON/);
    doc.getElementById('run').click();await drain();
    const request=p.calls.find(c=>c.path==='/api/runs'&&c.body);
    assert.equal(request.body.backend,'cpu_resident');assert.equal(request.body.experiment,'candidate');
  }finally{p.close();}
});

async function connectDemo(p,device='quest-3'){
  const doc=p.window.document;
  if(doc.querySelector('#demo-panel').hidden)doc.querySelector('#demo-toggle').click();
  doc.querySelector('#demo-device').value=device;
  doc.querySelector('#demo-device').dispatchEvent(new p.window.Event('change'));
  doc.querySelector('#demo-connect').focus();
  doc.querySelector('#demo-connect').click();
  assert.equal(doc.querySelector('#demo-connect').textContent,'Connecting…');
  assert.equal(doc.querySelector('#run').disabled,true);
  await p.tick(500);
  assert.equal(doc.activeElement.id,'run');
}

test('demo smoke connects a chosen device and completes GPU only without native mutations or URL changes',async()=>{
  const p=await page('bench',null,{devices:[],apk_ready:false});
  try{
    const doc=p.window.document,url=p.window.location.href;
    doc.querySelector('#demo-toggle').click();
    assert.equal(doc.querySelector('#demo-device').value,'');
    assert.equal(doc.querySelector('#demo-connect').disabled,true);
    assert.equal(doc.querySelector('#demo-device').options.length,devicesCatalog.devices.length+1);
    await connectDemo(p,'vision-pro');
    assert.match(doc.querySelector('#demo-support').textContent,/Metal support is planned/);
    assert.equal(doc.querySelector('#device-name').textContent,'Apple Vision Pro');
    assert.equal(doc.querySelector('#device-demo-badge').hidden,false);
    assert.equal(doc.querySelector('#run-label').textContent,'Run demo');
    doc.querySelector('#run').click();
    assert.equal(doc.querySelector('#run-label').textContent,'Stop demo');
    assert.equal(doc.querySelector('#demo-device').disabled,true);
    assert.equal(doc.querySelector('#headset-stage').hidden,false);
    assert.equal(doc.querySelector('#headset-stage').dataset.mode,'chip');
    await p.tick(3000);
    assert.match(doc.querySelector('#chip-kicker').textContent,/GPU · simulated/);
    assert.equal(doc.querySelector('#chip-percent').textContent,'24%');
    assert.equal(doc.querySelector('#chip-timer').textContent,'00:03');
    await p.tick(3000);
    assert.match(doc.querySelector('#chip-kicker').textContent,/GPU · simulated/);
    assert.equal(doc.querySelector('#chip-percent').textContent,'49%');
    await p.tick(6000);
    assert.equal(doc.querySelector('#chip-percent').textContent,'100%');
    assert.equal(doc.querySelector('#chip-timer').textContent,'00:12');
    assert.equal(doc.querySelector('#chip-kicker').textContent,'Demo complete');
    assert.equal(doc.querySelectorAll('.case-table-wrap tbody tr').length,63);
    assert.match(doc.querySelector('.results-heading .eyebrow').textContent,/Simulated/);
    assert.match(doc.querySelector('#current-layer').textContent,/simulated passing outcomes/);
    assert.match(doc.querySelector('#timing-note').textContent,/Simulated timings and correctness/);
    assert.equal(doc.querySelector('#results-link').hidden,true);
    assert.doesNotMatch(doc.querySelector('#chip-dashboard').textContent,/NATIVE|on device|validated/);
    assert.doesNotMatch(doc.querySelector('#results').textContent,/Native CPU|Native GPU|Measured on your device|passed correctness checks/);
    const gpu=doc.querySelector('#result-processors button');gpu.focus();gpu.click();
    assert.equal(doc.activeElement.textContent,'GPU');
    assert.match(doc.querySelector('#run-context').textContent,/Simulated GPU/);
    assert.equal(doc.querySelectorAll('.layer-latency')[0].textContent.includes('ms'),true);
    assert.equal(p.calls.filter(c=>c.body).length,0);
    assert.equal(p.calls.some(c=>c.path.includes('demo-')),false);
    assert.equal(p.window.location.href,url);
    assert.equal(p.window.localStorage.length,0);assert.equal(p.window.sessionStorage.length,0);
    assert.equal(doc.querySelector('#stats-link').getAttribute('href'),'/stats/');
    doc.querySelector('#demo-exit').click();
    assert.equal(doc.querySelector('#results').hidden,true);
    assert.equal(doc.querySelector('#headset-stage').hidden,false);
    assert.equal(doc.querySelector('#device-demo-badge').hidden,true);
    assert.equal(doc.querySelector('#device-name').textContent,'Connect XR device');
    assert.equal(doc.querySelector('#run-label').textContent,'Run');
    assert.equal(p.timers.intervals.size,2);
  }finally{p.close();}
});

test('demo stop, restart, device change and exit keep native selections intact',async()=>{
  const report={run_id:id,status:'complete',suite_revision:'fixture-suite',configuration:{cases:[]},device:{model:'Saved device'},results:[]};
  const p=await page('bench',report,{devices:[{serial:'one',model:'Real one',state:'device',supported:true},{serial:'two',model:'Real two',state:'device',supported:true}]});
  try{
    const doc=p.window.document;
    doc.querySelector('#device-picker').value='two';doc.querySelector('#device-picker').dispatchEvent(new p.window.Event('change'));
    const url=p.window.location.href,link=doc.querySelector('#stats-link').getAttribute('href');
    await connectDemo(p);
    doc.querySelector('#run').click();await p.tick(2000);doc.querySelector('#run').click();
    const count=doc.querySelector('#chip-count').textContent;
    await p.tick(3000);assert.equal(doc.querySelector('#chip-count').textContent,count);
    assert.equal(doc.querySelector('#chip-kicker').textContent,'Demo stopped');
    doc.querySelector('#run').click();assert.equal(doc.querySelector('#chip-percent').textContent,'0%');
    doc.querySelector('#run').click();
    await connectDemo(p,'xreal-one');
    assert.match(doc.querySelector('#demo-support').textContent,/runner is unavailable/);
    assert.equal(doc.querySelector('#results').hidden,true);
    doc.querySelector('#run').click();
    doc.querySelector('#demo-exit').click();await p.tick(15000);
    assert.equal(doc.querySelector('#device-picker').value,'two');
    assert.equal(doc.querySelector('#device-name').textContent,'Real two');
    assert.equal(doc.querySelector('#results').dataset.source,'native');
    assert.equal(doc.querySelector('#results-link').hidden,false);
    assert.equal(p.window.location.href,url);assert.equal(doc.querySelector('#stats-link').getAttribute('href'),link);
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.close();}
});

test('demo feature flag prevents loading and mounting the component',async()=>{
  const p=await page('bench',null,{}, {demoEnabled:false});
  try{
    assert.equal(p.window.document.querySelector('#demo-toggle'),null);
    assert.equal(p.window.document.querySelector('#demo-panel'),null);
    assert.equal(p.modules.has('/bench/smoke-demo.mjs'),false);
    p.window.document.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body).length,1);
  }finally{p.close();}
});

test('catalog failure is retryable and native activity takes precedence over a demo',async()=>{
  const p=await page('bench',null,{}, {faults:{catalog:true}});
  try{
    const doc=p.window.document;
    doc.querySelector('#demo-toggle').click();
    assert.equal(doc.querySelector('#demo-retry').hidden,false);
    assert.equal(doc.querySelector('#demo-connect').disabled,true);
    p.faults.catalog=false;doc.querySelector('#demo-retry').click();await drain();
    assert.equal(doc.querySelector('#demo-retry').hidden,true);
    await connectDemo(p);doc.querySelector('#run').click();
    p.state.jobs=[{run_id:id,status:'running',configuration:{cases:catalog.cases.map(c=>c.id),backend:'executorch_cpu'}}];
    await p.tick(1000);
    assert.equal(doc.querySelector('#demo-panel').hidden,true);
    assert.equal(doc.querySelector('#demo-toggle').disabled,true);
    assert.equal(doc.querySelector('#run-label').textContent,'Stop');
    assert.equal(doc.querySelector('#headset-stage').hidden,false);
    assert.equal(doc.querySelector('#headset-stage').dataset.mode,'chip');
    assert.equal(doc.querySelector('#device-demo-badge').hidden,true);
    assert.equal(p.calls.filter(c=>c.body).length,0);
    assert.equal(p.timers.intervals.size,2);
  }finally{p.close();}
});

test('page teardown clears demo connection and run timers',async()=>{
  const p=await page('bench');
  await connectDemo(p);p.window.document.querySelector('#run').click();
  assert.equal(p.timers.intervals.size,3);p.close();
  assert.equal(p.timers.intervals.size,0);assert.equal(p.timers.timeouts.size,0);
});

test('service outage during a demo never falls through to native actions',async()=>{
  const p=await page('bench',null,{devices:[],apk_ready:false});
  try{
    const doc=p.window.document;await connectDemo(p);doc.querySelector('#run').click();
    p.faults.status=true;await p.tick(3000);
    assert.equal(doc.querySelector('#run').disabled,false);
    assert.equal(doc.querySelector('#chip-percent').textContent,'24%');
    doc.querySelector('#run').click();assert.equal(doc.querySelector('#chip-kicker').textContent,'Demo stopped');
    doc.querySelector('#demo-exit').click();
    assert.equal(doc.querySelector('#run').disabled,true);
    assert.match(doc.querySelector('#device-name').textContent,/Local service unavailable/);
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.close();}
});


test('homepage recovery and native GPU diagnostics never require Stats navigation',async()=>{
  const p=await page('bench',null,{}, {syncSession:false});
  try{
    const doc=p.window.document;
    const links=[...doc.querySelectorAll('a[href^="/stats"]')];
    assert.equal(links.length,1);assert.equal(links[0].id,'stats-link');
    doc.querySelector('#run').click();await drain();
    const parent=p.state.benchmark_sessions[0],gpu=parent.phases[0];
    assert.equal(parent.phases.length,1);assert.equal(parent.processor,'gpu');
    assert.equal(doc.querySelector('#headset-stage').dataset.mode,'chip');
    assert.equal(doc.querySelector('#chip-dashboard').parentElement.id,'headset-stage');
    assert.equal(doc.querySelector('#back-headset').hidden,true);
    gpu.run.progress={sequence:1,current_case:catalog.cases[0].id,completed_cases:7,current_samples:4};
    await p.tick(1000);
    assert.match(doc.querySelector('#chip-count').textContent,/7 \/ 63/);
    assert.match(doc.querySelector('#chip-samples').textContent,/4 samples/);
    assert.equal(doc.querySelector('.chip-region[data-processor="gpu"]').dataset.active,'true');
    assert.equal(doc.querySelector('.chip-region[data-processor="cpu"]').hidden,true);
    assert.equal(doc.querySelector('#advanced-cpu').hidden,true);
    parent.status='disconnected';await p.tick(1000);assert.equal(doc.querySelector('#chip-kicker').textContent,'Waiting for device');
    parent.status='complete';parent.finished_at=Date.now()/1000;gpu.run.status=gpu.status='complete';gpu.run.progress.completed_cases=63;
    await p.tick(1000);assert.equal(doc.querySelector('#chip-percent').textContent,'100%');
    assert.equal(doc.querySelector('#back-headset').hidden,false);
    doc.querySelector('#back-headset').click();await p.tick(1000);
    assert.equal(doc.querySelector('#headset-stage').dataset.mode,'headset');assert.equal(doc.querySelector('#results').hidden,true);
    assert.equal(p.window.location.search,'');
    assert.equal(doc.activeElement.id,'run');assert.equal(p.window.location.pathname,'/');
    assert.equal(doc.querySelector('#results-link').getAttribute('href'),'#layer-results');
    assert.equal(p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body).length,1);
  }finally{p.close();}
});

test('setup retry stays inline and a rendering failure leaves the real run controllable',async()=>{
  const p=await page('bench',null,{apk_ready:false},{rendererFailure:true});
  try{
    const doc=p.window.document;doc.querySelector('#run').click();await drain();
    assert.equal(doc.querySelector('#setup-help'),null);
    assert.match(doc.querySelector('#notice').textContent,/unavailable/i);
    assert.doesNotMatch(doc.querySelector('#notice').textContent,/Stats for Nerds/);
    assert.equal(p.calls.filter(c=>c.body).length,0);
    p.state.apk_ready=true;doc.querySelector('#retry-status').click();await drain();
    assert.equal(doc.querySelector('#notice').hidden,true);
    doc.querySelector('#run').click();await drain();
    assert.equal(doc.querySelector('#headset-stage').dataset.graphics,'fallback');
    assert.equal(doc.querySelector('#chip-dashboard').hidden,false);assert.match(doc.querySelector('#chip-kicker').textContent,/GPU/);
    assert.equal(doc.querySelector('#run-label').textContent,'Stop');
    doc.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);
    assert.equal(p.window.location.pathname,'/');
  }finally{p.close();}
});

test('demo return, rerun and exit retain native state and diagnostics on the same page',async()=>{
  const p=await page('bench');
  try{
    const doc=p.window.document,url=p.window.location.href;await connectDemo(p);
    doc.querySelector('#run').click();const first=doc.querySelector('#headset-stage').dataset.runKey;
    doc.querySelector('#run').click();assert.equal(doc.querySelector('#back-headset').hidden,false);
    doc.querySelector('#back-headset').click();await p.tick(2000);
    assert.equal(doc.querySelector('#headset-stage').dataset.mode,'headset');assert.equal(doc.querySelector('#results').hidden,false);
    doc.querySelector('#run').click();assert.equal(doc.querySelector('#headset-stage').dataset.mode,'chip');
    assert.notEqual(doc.querySelector('#headset-stage').dataset.runKey,first);
    await p.tick(6000);assert.equal(doc.querySelector('.chip-region[data-processor="gpu"]').dataset.active,'true');
    assert.match(doc.querySelector('.chip-regions').textContent,/Simulated/);
    doc.querySelector('#demo-exit').click();assert.equal(doc.querySelector('#headset-stage').dataset.mode,'headset');
    assert.equal(doc.querySelector('#results').hidden,true);assert.equal(p.window.location.href,url);
    assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.close();}
});


test('native Run starts immediately, Stop works during reveal, and rerun gets a fresh transition',async()=>{
  const p=await page('bench',null,{}, {animation:true});
  try{
    const doc=p.window.document,stage=doc.querySelector('#headset-stage');
    doc.querySelector('#run').click();await drain();
    const first=stage.dataset.runKey;assert.equal(p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body).length,1);
    assert.equal(stage.dataset.transition,'opening');assert.equal(doc.querySelector('#run').disabled,false);
    p.advanceFrames(400);doc.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);assert.equal(stage.dataset.transition,'opening');
    p.state.jobs[0].status='canceled';await p.tick(1000);p.advanceFrames(1300);
    assert.equal(stage.dataset.transition,'settled');assert.equal(p.timers.frames.size,0);
    assert.equal(doc.querySelector('#chip-kicker').textContent,'Benchmark stopped');
    doc.querySelector('#back-headset').click();await p.tick(1000);assert.equal(stage.dataset.mode,'headset');
    doc.querySelector('#run').click();await drain();assert.notEqual(stage.dataset.runKey,first);
    assert.equal(stage.dataset.transition,'opening');assert.equal(p.window.location.pathname,'/');
  }finally{p.close();assert.equal(p.timers.frames.size,0);}
});

test('an existing native run restores directly to its chip without an introduction or another start',async()=>{
  const run={run_id:id,status:'running',configuration:{backend:'executorch_cpu',cases:catalog.cases.map(c=>c.id)},progress:{current_case:catalog.cases[0].id,completed_cases:2,sequence:4}};
  const p=await page('bench',null,{jobs:[run]},{animation:true});
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#headset-stage').dataset.mode,'chip');
    assert.equal(doc.querySelector('#headset-stage').dataset.transition,'settled');
    assert.equal(p.timers.frames.size,0);assert.match(doc.querySelector('#chip-count').textContent,/2 \/ 63/);
    assert.equal(doc.querySelector('#run-label').textContent,'Stop');assert.equal(p.calls.filter(c=>c.body).length,0);
  }finally{p.close();}
});


test('phone Run brings the reveal into view once; progress polling never steals the scroll position',async()=>{
  const p=await page('bench',null,{}, {mobile:true});
  try{
    const doc=p.window.document;await connectDemo(p);doc.querySelector('#run').click();
    assert.equal(p.scrolls.length,1);assert.equal(p.scrolls[0].target,'headset-stage');
    await p.tick(6000);await p.tick(6000);assert.equal(p.scrolls.length,1);
    doc.querySelector('#back-headset').click();assert.match(p.scrolls[1].target,/hero/);
    assert.equal(p.window.location.pathname,'/');
  }finally{p.close();}
});

test('Advanced CPU is explicit, restricted, and never changes the primary GPU default',async()=>{
  for(const hosted of [false,true]){
    const identity={device_name:'Synthetic CPU-only device',chip_name:'Synthetic processor',cpu_eligible:true,gpu_presence:'absent',gpu_presence_source:'https://fixture.invalid/spec'};
    const p=await page('bench',null,{devices:[{serial:'fixture',model:'Synthetic device',state:'device',supported:true,identity,...(hosted?{connection_state:'detected'}:{})}]},{hosted});
    try{
      const doc=p.window.document;
      assert.equal(doc.querySelector('#advanced-cpu').hidden,false);
      assert.equal(doc.querySelector('#advanced-cpu').open,false);
      assert.equal(p.calls.filter(c=>c.body).length,0);
      doc.querySelector('#advanced-cpu').open=true;doc.querySelector('#run-cpu').click();await drain();
      assert.equal(p.calls.find(c=>c.path==='/api/benchmark-sessions'&&c.body).body.processor,'cpu');
      assert.equal(doc.querySelector('#run-cpu').disabled,true);
      assert.match(doc.querySelector('#chip-kicker').textContent,/CPU/);
      assert.equal(doc.querySelector('.chip-region[data-processor="gpu"]').hidden,true);
      assert.equal(doc.querySelector('#run-label').textContent,'Stop');
      doc.querySelector('#run').click();await drain();
      p.state.jobs[0].status='canceled';await p.tick();
      doc.querySelector('#run').click();await drain();
      assert.equal(p.calls.filter(c=>c.path==='/api/benchmark-sessions'&&c.body).at(-1).body.processor,'gpu');
      assert.equal(p.window.location.pathname,'/');
    }finally{p.close();}
  }
  const p=await page('bench');try{
    assert.equal(p.window.document.querySelector('#advanced-cpu').hidden,true);
    p.window.document.querySelector('#run-cpu').click();await drain();
    assert.equal(p.calls.filter(c=>c.body).length,0);
    assert.match(p.window.document.querySelector('#notice-message').textContent,/confirmed to have no GPU/);
  }finally{p.close();}
});

test('test-only GPU-absent demo exercises Advanced CPU without API or storage writes',async()=>{
  const deviceCatalog=structuredClone(devicesCatalog);
  Object.assign(deviceCatalog.chips.find(c=>c.id==='xr2-gen2'),{gpu_presence:'absent',gpu_presence_source:'https://fixture.invalid/spec'});
  const p=await page('bench',null,{devices:[],apk_ready:false},{deviceCatalog});
  try{
    const doc=p.window.document,url=p.window.location.href;await connectDemo(p,'quest-3');
    assert.equal(doc.querySelector('#advanced-cpu').hidden,false);
    doc.querySelector('#advanced-cpu').open=true;doc.querySelector('#run-cpu').click();
    assert.match(doc.querySelector('#chip-kicker').textContent,/CPU · simulated/);
    assert.equal(doc.querySelector('#run-cpu').disabled,true);
    await p.tick(12000);assert.equal(doc.querySelector('#chip-percent').textContent,'100%');
    assert.equal(doc.querySelectorAll('#chip-case-evidence li').length,63);
    assert.equal(doc.querySelector('.chip-region[data-processor="gpu"]').hidden,true);
    assert.equal(p.calls.filter(c=>c.body).length,0);assert.equal(p.window.location.href,url);
    assert.equal(p.window.localStorage.length,0);assert.equal(p.window.sessionStorage.length,0);
    doc.querySelector('#demo-exit').click();assert.equal(doc.querySelector('#advanced-cpu').hidden,true);
  }finally{p.close();}
});

test('unsupported GPU reason and Retry stay inline with no CPU phase or navigation',async()=>{
  const p=await page('bench',null,{}, {syncSession:false});
  try{
    const doc=p.window.document;doc.querySelector('#run').click();await drain();
    const s=p.state.benchmark_sessions[0];s.status='complete';s.partial=true;s.phases[0].status='unsupported';
    s.phases[0].reason='Vulkan driver unavailable';delete s.phases[0].run;p.state.jobs=[];
    await p.tick();assert.equal(doc.querySelector('#notice').hidden,false);
    assert.match(doc.querySelector('#notice-message').textContent,/Vulkan driver unavailable/);
    assert.equal(doc.querySelector('#advanced-cpu').hidden,true);
    doc.querySelector('#retry-status').click();await drain();
    assert.equal(p.calls.filter(c=>c.body).length,1);assert.equal(p.window.location.pathname,'/');
  }finally{p.close();}
});
