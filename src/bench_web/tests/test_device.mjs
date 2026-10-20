import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deviceView} from '../web/device-view.mjs';
const quest={serial:'quest',model:'Quest 3',state:'device',supported:true,identity:{device_name:'Meta Quest 3',chip_name:'Qualcomm Snapdragon XR2 Gen 2'}};
test('device status preserves reported model and processor identity',()=>{
  assert.equal(deviceView({devices:[quest]}).identity.chip_name,'Qualcomm Snapdragon XR2 Gen 2');
  const quest3s={...quest,model:'Quest 3S',identity:{...quest.identity,device_name:'Meta Quest 3S'}};
  assert.equal(deviceView({devices:[quest3s]}).label,'Meta Quest 3S');
});
test('multiple devices require selection, including a Quest alongside a phone',()=>{
  const devices=[quest,{...quest,serial:'phone',model:'Pixel',identity:null}];
  assert.equal(deviceView({devices}).kind,'multiple');
  const chosen=deviceView({devices},'','phone');
  assert.equal(chosen.serial,'phone');assert.equal(chosen.identity.chip_name,'Processor not identified');
});
test('authorization, offline, absent, discovery and service failures remain distinct',()=>{
  assert.equal(deviceView(null).kind,'searching');assert.equal(deviceView({devices:[]}).kind,'absent');
  assert.equal(deviceView({devices:[{...quest,state:'unauthorized'}]}).authorized,false);
  assert.equal(deviceView({devices:[{...quest,state:'offline'}]}).kind,'offline');
  assert.equal(deviceView({devices:[quest]},'Unavailable').kind,'service-error');
  assert.equal(deviceView({error:'ADB unavailable'}).kind,'discovery-error');
  assert.equal(deviceView({devices:[{...quest,supported:false}]}).kind,'unsupported');
  assert.equal(deviceView({devices:[{...quest,identity:{...quest.identity,identity_source:'conflicting'}}]}).help,'Processor identity needs verification.');
});
