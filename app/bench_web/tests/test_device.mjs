import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deviceView,deviceTransition,modelKey} from '../web/device-view.mjs';

const quest={serial:'quest',model:'Quest 3',state:'device',supported:true};
const view=(devices,error)=>deviceView({devices},error);

test('Quest 3 identity uses exact normalized names, never Quest 3S or unknown devices',()=>{
  for(const name of ['Quest 3','Quest_3','Meta Quest 3',' quest   3 '])assert.equal(modelKey(name),'quest-3');
  for(const name of ['Quest 3S','Quest_3S','Quest 2','Quest Pro','Unknown','Quest 30',''])assert.equal(modelKey(name),null);
  assert.equal(view([{...quest,model:'Quest 3S'}]).visible,false);
  assert.equal(view([quest,{...quest,serial:'phone',model:'Pixel'}]).serial,'quest');
});

test('device state distinguishes connection, setup, ambiguity, and detection failures',()=>{
  assert.equal(deviceView(null).kind,'searching');
  assert.equal(view([]).kind,'absent');
  assert.equal(view([quest]).kind,'connected');
  const unauthorized=view([{...quest,state:'unauthorized',supported:false}]);
  assert.equal(unauthorized.visible,true);assert.equal(unauthorized.authorized,false);
  assert.equal(view([{...quest,state:'offline'}]).visible,false);
  assert.equal(view([{...quest,state:'unauthorized',model:'Connected headset'}]).visible,false);
  assert.equal(view([quest,{...quest,serial:'second'}]).kind,'multiple');
  assert.equal(view([quest],'Service unavailable').kind,'service-error');
  assert.equal(view([quest],'Service unavailable').visible,false);
  assert.equal(deviceView({devices:[quest],error:'ADB unavailable'}).kind,'discovery-error');
  assert.equal(view([{...quest,supported:false}]).kind,'unsupported');
});

test('entrance plays on initial authorization and reconnection, not repeated polls or model-only previews',()=>{
  const absent=view([]),connected=view([quest]),unauthorized=view([{...quest,state:'unauthorized'}]);
  assert.equal(deviceTransition(null,connected).enter,true);
  assert.equal(deviceTransition(null,unauthorized).enter,false);
  assert.equal(deviceTransition(unauthorized,connected).enter,true);
  assert.equal(deviceTransition(connected,structuredClone(connected)).enter,false);
  assert.equal(deviceTransition(connected,structuredClone(connected)).identityChanged,false);
  assert.equal(deviceTransition(connected,absent).enter,false);
  assert.equal(deviceTransition(absent,connected).enter,true);
  assert.equal(deviceTransition(connected,view([{...quest,serial:'replacement'}])).enter,true);
});
