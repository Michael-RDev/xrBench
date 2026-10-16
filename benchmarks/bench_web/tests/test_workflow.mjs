import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from 'jsdom';

const root=new URL('../../../',import.meta.url);
const catalog=JSON.parse(await readFile(new URL('benchmarks/layers/catalog.json',root),'utf8'));
const id='run-'+'a'.repeat(32);
const paths={
  '/shared/session.mjs':'benchmarks/layers/engine/web/session.mjs',
  '/stats/view-model.mjs':'benchmarks/stats_for_nerds/web/view-model.mjs',
  '/stats/app.js':'benchmarks/stats_for_nerds/web/app.js',
  '/bench/app.js':'benchmarks/bench_web/web/app.js',
};
const drain=async()=>{for(let i=0;i<10;i++)await new Promise(resolve=>setImmediate(resolve));};
async function page(kind,report=null){
  const pretty=kind==='bench';
  const html=await readFile(new URL(`benchmarks/${pretty?'bench_web':'stats_for_nerds'}/web/index.html`,root),'utf8');
  const dom=new JSDOM(html,{url:'http://localhost:8765/'+(pretty?'':'stats/')+(report?'?run='+id:''),runScripts:'outside-only'});
  const window=dom.window,calls=[],intervals=[];
  const state={devices:[{serial:'fixture',model:'Quest 3',state:'device',supported:true}],database:{ready:true,message:'Local PostgreSQL ready'},jobs:[],apk_ready:true,catalog,suite_revision:'fixture-suite'};
  window.fetch=async(path,options={})=>{
    const body=options.body?JSON.parse(options.body):null;
    calls.push({path,body});
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
  async function module(path){
    if(cache.has(path))return cache.get(path);
    const file=paths[path];if(!file)throw Error('Unknown module '+path);
    const source=await readFile(new URL(file,root),'utf8');
    const mod=new vm.SourceTextModule(source,{context,identifier:path,importModuleDynamically:async()=>{throw Error('WebGL unavailable in DOM test');}});
    cache.set(path,mod);
    await mod.link((specifier,ref)=>module(new URL(specifier,'http://fixture'+ref.identifier).pathname));
    return mod;
  }
  await (await module(pretty?'/bench/app.js':'/stats/app.js')).evaluate();await drain();
  return {window,state,calls,close:()=>window.close(),tick:async()=>{for(const callback of intervals)await callback();await drain();}};
}

test('one click runs the entire catalog; both frontends preserve selection and Stop',async()=>{
  const p=await page('bench');
  try{
    const doc=p.window.document;
    assert.equal(doc.querySelector('#run').disabled,false);
    assert.equal(doc.querySelectorAll('.layer-tile').length,25);
    assert.equal(p.calls.filter(c=>c.body).length,0);
    doc.querySelector('#run').click();await drain();
    const requests=p.calls.filter(c=>c.path==='/api/runs'&&c.body);
    assert.equal(requests.length,1);assert.equal(requests[0].body.mode,'isolated');assert.equal(requests[0].body.preset,'smoke');
    assert.equal(p.state.jobs[0].configuration.cases.length,63);
    assert.equal(new Set(p.state.jobs[0].configuration.cases).size,63);
    assert.equal(doc.querySelector('#stats-link').getAttribute('href'),'/stats/?run='+id);
    assert.equal(p.window.location.search,'?run='+id);
    doc.querySelector('#stop').click();await drain();
    assert.equal(p.calls.filter(c=>c.path.endsWith('/stop')).length,1);
    assert.equal(doc.querySelector('#stop').disabled,true);
  }finally{p.close();}
});

test('XR planned duration changes without changing the execution queue',async()=>{
  const p=await page('bench');
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
      assert.equal(doc.querySelector('#download').disabled,false);
      assert.equal(doc.querySelector('#failed').textContent,'1');
      assert.equal(doc.querySelector('#skipped').textContent,'1');
      const href=doc.querySelector(kind==='bench'?'#stats-link':'#bench-link').getAttribute('href');
      assert.ok(href.includes('?run='+id));
      if(kind==='bench')assert.match(doc.querySelector('#scene-caption').textContent,/unavailable|tests|inside/);
    }finally{p.close();}
  }
});
