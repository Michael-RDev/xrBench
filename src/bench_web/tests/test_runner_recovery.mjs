import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {BrowserController} from '../web/browser/controller.mjs';
import {WebUsbTransport, MissingFile} from '../web/browser/transport.mjs';
import {EvidenceError} from '../web/browser/evidence.mjs';
import {MemoryStore} from './browser-fixtures.mjs';
import {nativeManifest} from './native-fixtures.mjs';

function deferred(){let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};}
async function fixture(){
  const manifest=nativeManifest(),store=new MemoryStore(),device={serial:'fixture'};
  const old=structuredClone(manifest);old.apk_sha256='f'.repeat(64);old.apk=old.apk_sha256+'.apk';old.configurations.gpu.apk_sha256=old.apk_sha256;
  await store.save({session_id:'bench-'+'1'.repeat(32),request_id:'old-request',serial:device.serial,status:'disconnected',
    created_at:1,current_phase:0,cancel_requested:false,manifest:old,
    phases:[{processor:'gpu',configuration:old.configurations.gpu,run_id:'run-'+'1'.repeat(32),status:'pending'}]});
  const calls=[];let held=false;
  const transport={supported:true,choose:async()=>device,discover:async()=>[device],
    connect:async(_,signal,message)=>{calls.push('connect');message('Waiting for headset approval');signal.throwIfAborted();},
    close:async()=>{calls.push('close');},installedHash:async()=>null,
    properties:async()=>({model:'Quest 3',sdk:32,abi:'arm64-v8a'}),
    install:async()=>{calls.push('install');},probe:async()=>{throw new EvidenceError('Native probe rejected');}};
  const controller=new BrowserController({transport,store,manifestUrl:'https://fixture.test/runner/manifest.json',
    fetcher:async()=>({ok:true,json:async()=>manifest}),lock:async()=>{assert.equal(held,false);held=true;return()=>{held=false;};}});
  await controller.init();
  return {controller,transport,store,calls,device,session:controller.sessions[0],get held(){return held;},
    async close(){controller.dispose();await Promise.allSettled([...controller.updates.values(),...controller.workers.values()].map(w=>w.task));}};
}

test('runner update returns immediately, exposes progress, and can cancel pending approval',async()=>{
  const f=await fixture(),entered=deferred();
  f.transport.connect=async(_,signal,message)=>{message('Allow USB debugging inside the headset');entered.resolve();await new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}));};
  try{
    const response=await f.controller.upgrade(f.session.session_id);
    assert.equal(response.updating,true);
    const task=f.controller.updates.get(f.session.session_id).task;
    await entered.promise;
    const state=await f.controller.snapshot();
    assert.match(state.benchmark_sessions[0].message,/Allow USB debugging/);
    assert.equal(state.benchmark_sessions[0].updating,true);
    await f.controller.stop(f.session.session_id);await task;
    assert.equal(f.held,false);assert.equal(f.controller.starting,false);
    assert.equal(f.session.cancel_requested,false,'Cancel update must preserve recovery of the original run');
    assert.match(f.session.message,/update canceled/);
    assert.equal(f.store.items.length,1);assert.ok(!f.calls.includes('install'));
    assert.equal((await f.controller.snapshot()).benchmark_sessions[0].updating,false);
  }finally{await f.close();}
});

for(const late of ['selection','rejection'])test(`canceling an update releases a pending chooser before late ${late}`,async()=>{
  const f=await fixture(),chosen=deferred();f.transport.choose=()=>chosen.promise;
  try{
    await f.controller.upgrade(f.session.session_id);const task=f.controller.updates.get(f.session.session_id).task;
    await f.controller.stop(f.session.session_id);await nextTurn();
    assert.equal(f.controller.starting,false,'Cancel must not wait for the native chooser');
    assert.equal(f.controller.view(f.session).updating,false);
    assert.equal(f.session.cancel_requested,false);
    assert.match(f.controller.view(f.session).message,/update canceled/);
    await task;
    if(late==='selection')chosen.resolve(f.device);else chosen.reject(new DOMException('Chooser dismissed','NotFoundError'));
    await nextTurn();
    assert.deepEqual(f.calls,[]);assert.equal(f.held,false);assert.equal(f.store.items.length,1);
  }finally{chosen.resolve(f.device);await f.close();}
});

test('an updated runner that fails immediately retains its previous session identity',async()=>{
  const f=await fixture();
  try{
    const response=await f.controller.upgrade(f.session.session_id);
    await f.controller.updates.get(f.session.session_id).task;
    await Promise.all([...f.controller.workers.values()].map(w=>w.task));
    const replacement=(await f.controller.snapshot()).benchmark_sessions.find(s=>s.session_id!==response.session_id);
    assert.equal(replacement.status,'failed');assert.equal(replacement.error,'Native probe rejected');
    assert.equal(replacement.replaces_session_id,response.session_id);
    assert.equal((await f.store.sessions()).find(s=>s.session_id===replacement.session_id).replaces_session_id,response.session_id);
  }finally{await f.close();}
});

test('failed update never overwrites another tab\'s completed evidence or live progress',async()=>{
  for(const status of ['complete','running']){
    const f=await fixture(),chosen=deferred();f.transport.choose=()=>chosen.promise;
    try{
      await f.controller.upgrade(f.session.session_id);
      const task=f.controller.updates.get(f.session.session_id).task;
      const newer=structuredClone(f.session);newer.status=status;
      newer.phases[0].status=status;newer.phases[0].run={report_available:true,report_revision:'other-tab'};
      await f.store.save(newer);
      if(status==='running')f.controller.lock=async()=>{throw Error('Device is locked by another tab');};
      chosen.resolve(f.device);await task;
      assert.deepEqual(await f.store.sessions(),[newer]);
      assert.equal(f.session.status,status);assert.equal(f.session.phases[0].run.report_revision,'other-tab');
      assert.equal(f.controller.starting,false);assert.deepEqual(f.calls,[]);
      if(status==='running')assert.match(f.controller.view(f.session).message,/another tab/);
    }finally{chosen.resolve(f.device);await f.close();}
  }
});

test('failure starting the updated runner stays visible after retirement and polling',async()=>{
  const f=await fixture();let locks=0;
  f.controller.lock=async()=>{if(++locks===2)throw Error('Second tab claimed headset');return ()=>{};};
  try{
    await f.controller.upgrade(f.session.session_id);
    await f.controller.updates.get(f.session.session_id).task;
    for(let i=0;i<2;i++){
      const s=(await f.controller.snapshot()).benchmark_sessions[0];
      assert.equal(s.status,'canceled');assert.match(s.error,/new run could not start.*USB interface is in use.*Press Run/);
    }
    assert.equal(f.controller.starting,false);assert.equal(f.store.items.length,1);
    assert.ok(!f.calls.includes('install'));
  }finally{await f.close();}
});

test('persistent native probe failures do not launch or reconnect on every poll',async()=>{
  for(const error of [new EvidenceError('Another benchmark is still running on this headset'),Error('Native capability check did not respond')]){
    const f=await fixture();let probes=0;
    f.session.manifest=structuredClone(f.controller.manifest);
    f.session.phases[0].configuration=structuredClone(f.controller.manifest.configurations.gpu);
    await f.store.save(f.session);
    f.transport.probe=async()=>{probes++;throw error;};
    try{
      await f.controller.retry(f.session.session_id);
      await Promise.all([...f.controller.workers.values()].map(w=>w.task));
      for(let i=0;i<5;i++){await f.controller.snapshot();await nextTurn();}
      assert.equal(probes,1);assert.equal(f.calls.filter(v=>v==='connect').length,1);
      assert.equal(f.session.phases[0].attempted,undefined);
      assert.equal(f.session.status,error instanceof EvidenceError?'failed':'disconnected');
    }finally{await f.close();}
  }
});

test('legacy cached runner probe timeout is a native failure with recovery guidance',async context=>{
  context.mock.timers.enable({apis:['Date','setTimeout']});
  const transport=new WebUsbTransport();transport.launchActivity=async()=>{};
  transport.readJson=async()=>{throw new MissingFile();};
  const result=transport.probe(nativeManifest(),new AbortController().signal);
  const rejected=assert.rejects(result,error=>error instanceof EvidenceError&&/notification/.test(error.message));
  await nextTurn();context.mock.timers.tick(30001);await rejected;
});

test('native busy diagnostics are surfaced immediately without waiting for probe timeout',async()=>{
  const transport=new WebUsbTransport();let id,reads=0;
  transport.launchActivity=async(_,key,value)=>{id=value;};
  transport.readJson=async()=>{reads++;return {probe_id:id,code:'native_busy',error:'Existing benchmark is still running'};};
  await assert.rejects(transport.probe(nativeManifest()),error=>error instanceof EvidenceError&&/Existing benchmark/.test(error.message));
  assert.equal(reads,1);
});
