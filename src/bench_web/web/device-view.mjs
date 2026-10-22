export function deviceView(status,serviceError='',selectedSerial=null){
  const view={kind:'searching',serial:null,authorized:false,identity:null,
    label:'Looking for your XR device',help:'Checking the local benchmark service…'};
  if(serviceError)return {...view,kind:'service-error',label:'Local service unavailable',help:'Reconnect to the local benchmark service to detect your device.'};
  if(!status)return view;
  if(status.error)return {...view,kind:'discovery-error',label:'Device detection unavailable',help:status.error};
  const devices=status.devices||[];
  const chosen=devices.find(d=>d.serial===selectedSerial);
  if(devices.length>1&&!chosen)return {...view,kind:'multiple',label:'Choose your XR device',help:'Select the device you want to measure.'};
  const device=chosen||devices[0];
  if(!device)return {...view,kind:'absent',label:'Connect XR device',help:'Connect by USB and allow debugging to discover its processor.'};
  const identity=device.identity||{device_name:device.model,chip_name:device.soc_model||'Processor not identified',identity_source:'unknown',execution_location:'unknown'};
  const base={...view,serial:device.serial,connectionId:device.connection_id,identity};
  if(device.connection_state){
    const kind=device.connection_state;
    return {...base,kind,authorized:kind==='ready',label:identity.device_name||device.model||'USB headset',
      connectionLabel:({detected:'USB detected',connecting:'Connecting',ready:'Ready',blocked:'Connection blocked'})[kind],
      help:kind==='detected'?'Press Run to connect and identify your headset.':device.connection_message||''};
  }
  if(device.state==='offline')return {...base,kind:'offline',label:'Device is offline',help:'Reconnect the cable and wake your device.'};
  if(device.state==='unauthorized')return {...base,kind:'unauthorized',label:identity.device_name||'Connected device',help:'Put on your headset and allow USB debugging.'};
  if(device.state!=='device')return {...base,kind:'offline',label:'Device unavailable',help:device.reason||'Reconnect your device.'};
  return {...base,kind:device.supported?'connected':'unsupported',authorized:!!device.supported,
    label:identity.device_name||device.model||'Connected device',
    help:identity.identity_source==='conflicting'?'Processor identity needs verification.':device.supported?'':device.reason||'Android 10+ and arm64-v8a are required.'};
}
