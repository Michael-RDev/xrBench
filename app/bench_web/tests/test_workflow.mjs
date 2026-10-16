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
};
const drain=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));};
async function page(kind,report=null,stateOverrides={},sceneMode='unavailable'){
  const pretty=kind==='bench';
  const html=await readFile(new URL(`app/${pretty?'bench_web':'stats_for_nerds'}/web/index.html`,root),'utf8');
  const dom=new JSDOM(html,{url:'http://localhost:8765/'+(pretty?'':'stats/')+(report?'?run='+id:''),runScripts:'outside-only'});
  const window=dom.window,calls=[],intervals=[],faults={start:false,status:false};
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
      assert.deepEqual(Object.keys(body).sort(),['mode','preset','serial']);
      value={run_id:id,status:'running',configuration:{...body,cases:catalog.cases.map(c=>c.id)},message:'Test 1 of 63',storage:'pending'};
      state.jobs=[value];
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
  return {window,state,calls,faults,viewer,releaseScene:async()=>{releaseScene();await drain();},
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
    assert.equal(doc.querySelectorAll('main section').length,1);
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
    doc.querySelector('#run').click();doc.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);
    assert.equal(doc.querySelector('#run').disabled,true);
    assert.equal(doc.querySelector('#run-label').textContent,'Stopping…');
    doc.querySelector('#run').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);
    p.state.jobs[0].status='canceled';await p.tick();
    assert.equal(doc.querySelector('#run').disabled,false);
    assert.equal(doc.querySelector('#run-label').textContent,'Run');
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
    assert.equal(doc.querySelector('#device-name').textContent,'Connect your headset');
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
    assert.equal(doc.querySelector('#device-name').textContent,'Connect your headset');
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
