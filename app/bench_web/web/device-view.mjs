// A preview is an illustration of an identified device, not benchmark eligibility.
export function modelKey(model=''){
  const name=model.replaceAll('_',' ').trim().replace(/\s+/g,' ').toLowerCase();
  return ['quest 3','meta quest 3'].includes(name)?'quest-3':null;
}

export function deviceView(status,serviceError=''){
  const view={kind:'searching',serial:null,modelKey:null,visible:false,authorized:false,
    label:'Looking for your headset',help:'Checking the local benchmark service…'};
  if(serviceError)return {...view,kind:'service-error',label:'Local service unavailable',
    help:'Reconnect to the local benchmark service to detect your headset.'};
  if(!status)return view;
  if(status.error)return {...view,kind:'discovery-error',label:'Headset detection unavailable',help:status.error};
  const devices=status.devices || [],quests=devices.filter(device=>modelKey(device.model));
  if(quests.length>1 || (!quests.length&&devices.length>1))return {...view,kind:'multiple',
    label:'Multiple devices detected',help:'Choose a headset in Stats for Nerds.'};
  const device=quests[0] || devices[0];
  if(!device)return {...view,kind:'absent',label:'Connect XR device',help:''};
  const key=modelKey(device.model),base={...view,serial:device.serial,modelKey:key};
  if(device.state==='offline')return {...base,kind:'offline',label:'Headset is offline',
    help:device.reason || 'Reconnect the USB cable and wake your headset.'};
  if(device.state==='unauthorized')return {...base,kind:'unauthorized',visible:!!key,
    label:key?'Meta Quest 3 · Authorization needed':'USB authorization needed',
    help:'Put on your headset and allow USB debugging.'};
  if(device.state!=='device')return {...base,kind:'offline',label:'Headset unavailable',
    help:device.reason || 'Reconnect the USB cable and enable developer mode.'};
  if(!key)return {...base,kind:'unsupported',label:device.model || 'Connected device',
    help:'Model preview unavailable. This version benchmarks Quest 3 only.'};
  return {...base,kind:device.supported?'connected':'unsupported',visible:true,authorized:true,
    label:'Meta Quest 3 · Connected',help:device.supported?'':device.reason || 'Benchmarking is unavailable for this device.'};
}

// Assemble when an identified headset appears, including before USB authorization.
export function deviceTransition(previous,next){
  const identityChanged=previous?.serial!==next.serial || previous?.modelKey!==next.modelKey;
  return {identityChanged,enter:next.visible&&(identityChanged || !previous?.visible)};
}
