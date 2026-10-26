import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createDemoController,demoIdentity} from '../web/smoke-demo.mjs';

const devices=JSON.parse(await readFile(new URL('../../../engine/device-catalog.json',import.meta.url),'utf8'));
const cases=JSON.parse(await readFile(new URL('../../../engine/workloads/catalog.json',import.meta.url),'utf8'));
function clock(){
  let now=100000,id=0;const callbacks=new Map();
  return {now:()=>now,timers:{setInterval(fn){callbacks.set(++id,{fn,repeat:true});return id;},clearInterval(id){callbacks.delete(id);},
    setTimeout(fn){callbacks.set(++id,{fn});return id;},clearTimeout(id){callbacks.delete(id);}},
    tick(ms){now+=ms;for(const [id,task] of [...callbacks]){if(!task.repeat)callbacks.delete(id);if(!task.repeat||callbacks.has(id))task.fn();}},
    get size(){return callbacks.size;}};
}

test('every catalog device can complete an identical synthetic workload without support inference',()=>{
  let baseline;
  for(const device of devices.devices){
    const time=clock(),demo=createDemoController(time);demo.setCatalogs(devices,cases);
    demo.open();assert.equal(demo.snapshot.deviceId,'');demo.connect();assert.equal(demo.snapshot.stage,'choosing');
    demo.choose(device.id);demo.connect();time.tick(500);demo.start();time.tick(12000);
    const state=demo.snapshot;
    assert.equal(state.identity.device_name,device.name);assert.equal(state.identity.identity_source,'simulated');
    assert.equal(state.session.status,'complete');assert.equal(time.size,0);
    for(const report of Object.values(state.reports)){
      assert.equal(report.results.length,cases.cases.length);
      assert.ok(report.results.every(r=>r.samples.length===20&&r.simulated));
      assert.doesNotMatch(report.run_id,/^run-[0-9a-f]{32}$/);
    }
    const samples=JSON.stringify(state.reports['demo-gpu'].results);
    baseline??=samples;assert.equal(samples,baseline);
    demo.destroy();assert.equal(time.size,0);
  }
  assert.match(demoIdentity(devices,devices.devices.find(d=>d.id==='vision-pro')).chip_name,/M2.*M5/);
  assert.equal(demoIdentity(devices,devices.devices.find(d=>d.id==='xreal-aura')).execution_location,'compute_puck');
});

test('exit during connection cancels completion and disposed components remain inert',()=>{
  const time=clock(),demo=createDemoController(time);demo.setCatalogs(devices,cases);demo.open();demo.choose('quest-3');demo.connect();
  demo.exit();time.tick(1000);assert.equal(demo.snapshot.stage,'closed');assert.equal(time.size,0);
  demo.open();demo.choose('quest-3');demo.connect();demo.destroy();time.tick(1000);
  demo.open();demo.start();assert.equal(demo.snapshot.stage,'closed');assert.equal(time.size,0);
});

test('CPU demo requires sourced absence; a normal demo runs exactly one GPU phase',()=>{
  const time=clock(),demo=createDemoController(time);demo.setCatalogs(devices,cases);
  demo.open();demo.choose('quest-3');demo.connect();time.tick(500);
  assert.throws(()=>demo.start('cpu'),/confirmed to have no GPU/);
  demo.start();assert.deepEqual(demo.snapshot.session.phases.map(p=>p.processor),['gpu']);
  time.tick(11999);assert.equal(demo.snapshot.session.status,'running');
  time.tick(1);assert.equal(demo.snapshot.session.status,'complete');demo.destroy();
});
