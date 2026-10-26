import {selectedRunId,rememberRun,frontendLink} from '/shared/session.mjs';
import {terminal} from '/stats/view-model.mjs';
import {deviceView} from '/bench/device-view.mjs';
import {renderResults} from '/bench/results-view.mjs';
import {chipEvidence,standaloneChipEvidence,renderChip,clockLabel} from '/bench/chip-view.mjs';
import {mountHeadset} from '/bench/ascii-headset.mjs';
import {CPU_RESTRICTION} from '/bench/processor-policy.mjs';
import {ENABLE_SMOKE_DEMO} from '/bench/features.mjs';

const hosted=document.documentElement.dataset.transport!=='local';
const browserService=hosted?(await import('/bench/browser/entry.mjs')).createBrowserService():null;

const $=id=>document.getElementById(id);
let token='',status=null,selected=selectedRunId(),report=null,selectedSerial=null;
let selectedSession=new URLSearchParams(location.search).get('session'),reports={},reportKeys={};
let refreshing=false,busy=false,connected=false,mutation=0,actionError='',serviceError='',catalog=null;
let progressView=null,pendingRequest=null,animateNext=false,demoGeneration=0,presentationKey='';
const dismissedPresentations=new Set();
const observedProgress=new Map();
const headset=mountHeadset($('headset-stage'));
const demo=ENABLE_SMOKE_DEMO?(await import('/bench/smoke-demo.mjs')).mountSmokeDemo({
  document,onChange:()=>render(),onRetry:()=>poll(),canEnter:()=>!busy&&!activeSession()&&!active(),
}):null;
const sessionPattern=/^bench-[0-9a-f]{32}$/;
if(!sessionPattern.test(selectedSession||''))selectedSession=null;
function session(){return status?.benchmark_sessions?.find(s=>s.session_id===selectedSession);}
function activeSession(){return status?.benchmark_sessions?.find(s=>!terminal.has(s.status));}
function job(){return status?.jobs.find(run=>run.run_id===selected);}
function active(){return status?.jobs.find(run=>!terminal.has(run.status));}
function hasNativeRun(parent){return !!parent?.phases.some(p=>p.attempted||p.run);}
function hasNativeProgress(parent){return !!parent?.started_at||!!parent?.phases.some(p=>reports[p.run_id]||p.run?.progress||p.run?.status==='running');}
function blockedConnection(){const parent=activeSession();return hosted&&parent?.status==='disconnected'&&parent.connection_state!=='connecting';}
async function api(path,body){
  if(browserService)return browserService.request(path,body);
  const response=await fetch(path,{headers:{'X-XRGPU-Session':token,...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
  const data=await response.json();if(!response.ok)throw Error(data.error||'Local service unavailable');return data;
}
function rememberSession(){
  const url=new URL(location.href);
  if(selectedSession)url.searchParams.set('session',selectedSession);else url.searchParams.delete('session');
  if(!selected)url.searchParams.delete('run');
  if(url.href!==location.href)history.replaceState(null,'',url);
}
function renderHistory(){
  const parents=(status?.benchmark_sessions||[]).filter(s=>terminal.has(s.status));
  const childIds=new Set((status?.benchmark_sessions||[]).flatMap(s=>s.phases.map(p=>p.run_id)));
  const entries=[...parents,...(status?.jobs||[]).filter(r=>terminal.has(r.status)&&!childIds.has(r.run_id))]
    .sort((a,b)=>(b.created_at||0)-(a.created_at||0));
  $('previous-runs').hidden=!entries.length;
  const list=$('previous-runs-list'),key=JSON.stringify(entries.map(s=>[s.session_id||s.run_id,s.status,s.created_at]));
  if(list.dataset.entries===key)return;list.dataset.entries=key;
  list.replaceChildren(...entries.map(s=>{
    const item=document.createElement('li'),button=document.createElement('button');button.type='button';button.className='text-action';
    const date=s.created_at?new Date(s.created_at*1000).toLocaleString():'Saved run';
    const processors=s.phases?s.phases.map(p=>p.processor.toUpperCase()).join(' + '):(s.configuration?.backend||'').includes('cpu')?'CPU':'GPU';
    button.textContent=`${date} · ${s.identity?.device_name||s.device_identity?.device_name||'Headset'} · ${processors} · ${s.status}`;
    button.onclick=()=>{selectedSession=s.session_id||null;selected=s.phases?.[s.current_phase]?.run_id||s.run_id;report=null;mutation++;void poll();};
    item.append(button);return item;
  }));
}
function renderDevice(){
  const devices=status?.devices||[],picker=$('device-picker');
  if(picker.dataset.identities!==JSON.stringify(devices.map(d=>[d.serial,d.model]))){
    const option=(value,text)=>{const el=document.createElement('option');el.value=value;el.textContent=text;return el;};
    picker.replaceChildren(option('','Choose device…'),...devices.map(d=>option(d.serial,d.identity?.device_name||d.model)));
    picker.dataset.identities=JSON.stringify(devices.map(d=>[d.serial,d.model]));
  }
  picker.hidden=devices.length<2;picker.value=selectedSerial||'';picker.disabled=!!activeSession()||!!active();
  const view=deviceView(status,serviceError,selectedSerial),parent=session();
  if(parent?.capabilities_checked&&parent.connection_id===view.connectionId&&parent.identity){view.identity=parent.identity;}
  $('device-name').textContent=view.label;$('device-chip').textContent=view.identity?.chip_name||'';
  $('device-connection').textContent=view.connectionLabel||'';$('device-connection').hidden=!view.connectionLabel;
  $('device-help').textContent=view.help;$('device-help').hidden=!view.help;
  $('device-retry').hidden=hosted?!blockedConnection():['connected','searching'].includes(view.kind)&&activeSession()?.status!=='disconnected';
  if(hosted&&!activeSession()){
    $('device-help').hidden=false;
    $('device-help').textContent=status?.usb_supported===false?'Use desktop Chrome or Edge on the HTTPS website.':view.kind==='multiple'?'Choose your headset, then press Run.':'Press Run to connect and install the runner automatically. Allow USB debugging in your headset.';
  }else if(hosted&&activeSession()){
    const current=activeSession();
    $('device-help').hidden=false;$('device-help').textContent=current.status==='disconnected'||!hasNativeRun(current)?current.message:'You can take off the headset. Keep USB connected until results are saved.';
  }else if(view.kind==='connected'&&!status?.apk_ready){$('device-help').textContent='The runner for this website release is unavailable. Please retry later.';$('device-help').hidden=false;}
  document.querySelector('.device-panel').dataset.state=view.kind;
}
function renderCatalog(){
  if(!catalog)return;const container=$('device-catalog');container.replaceChildren();
  const chips=new Map(catalog.chips.map(chip=>[chip.id,chip]));
  const groups=[
    {id:'check',title:'Check on device',note:'Android devices with a native runner. GPU compatibility is checked when you run.',devices:[]},
    {id:'planned',title:'Planned',note:'These devices cannot run the benchmark yet.',devices:[]},
    {id:'unavailable',title:'Unavailable',note:'No compute runner is available for these devices.',devices:[]},
    {id:'unknown',title:'Needs verification',note:'Processor support is not confirmed for these devices.',devices:[]},
  ];
  for(const device of catalog.devices){
    const primary=device.chips.filter(chip=>chip.role==='primary');
    const ids=[...new Set([...primary.map(chip=>chip.chip_id).filter(Boolean),...(device.primary_chip_options||[])])];
    const processors=ids.map(id=>chips.get(id));
    // Classify only the processor that executes the benchmark, not spatial coprocessors.
    const execution=processors.length&&processors.every(chip=>chip?.execution==='android_probe_required')?'check':
      processors.length&&processors.every(chip=>chip?.execution==='planned_metal')?'planned':
      processors.length&&processors.every(chip=>['sdk_access_unverified','no_general_compute_runner'].includes(chip?.execution))?'unavailable':'unknown';
    groups.find(group=>group.id===execution).devices.push({device,primary,processors});
  }
  for(const group of groups){
    if(!group.devices.length)continue;
    const section=document.createElement('section'),heading=document.createElement('h3'),note=document.createElement('p'),list=document.createElement('ul');
    section.className='compatibility-group';section.dataset.support=group.id;
    heading.id=`compatibility-${group.id}`;heading.textContent=group.title;section.setAttribute('aria-labelledby',heading.id);
    note.textContent=group.note;
    for(const {device,primary,processors} of group.devices){
      const item=document.createElement('li'),name=document.createElement('strong'),detail=document.createElement('span');
      name.textContent=device.name;
      detail.textContent=processors.map(chip=>chip?.name||'Processor not identified').join(' or ')||'Processor not identified';
      if(primary.some(chip=>chip.location==='compute_puck'))detail.textContent+=' in the compute puck; the glasses are not benchmarked.';
      item.append(name,detail);list.append(item);
    }
    section.append(heading,note,list);container.append(section);
  }
}
function presentChip(source='native'){
  presentationKey=progressView?`${source}:${progressView.id}:${source==='simulated'?demoGeneration:''}`:'';
  const show=!!progressView&&!dismissedPresentations.has(presentationKey);
  renderChip(document,show?progressView:null,source);
  document.querySelector('.hero').classList.toggle('benchmark-active',show);
  $('back-headset').hidden=source==='simulated'?(!show||!terminal.has(progressView.status)):!terminal.has((session()||job()||report)?.status);
  headset.setPresentation({mode:show?'chip':'headset',runKey:presentationKey,processor:progressView?.processor||'gpu',source,animate:show&&animateNext});
  if(show&&animateNext&&window.innerWidth<=620){
    const stage=$('headset-stage');
    stage.style.scrollMarginTop=(document.querySelector('.hero-actions').offsetHeight+12)+'px';
    stage.scrollIntoView?.({block:'start',behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  }
  if(show)animateNext=false;
}
function renderDelivery(parent){
  const el=$('report-delivery-status');el.hidden=!hosted;
  if(!hosted)return;
  const delivery=status?.report_delivery,ids=new Set(parent?.phases.map(p=>p.run_id)||[]);
  const rows=delivery?.reports.filter(r=>ids.has(r.run_id))||[];
  if(delivery?.error||rows.some(r=>r.status==='pending'&&r.error)){
    el.textContent='Report saved in this browser. Sending will retry automatically.';
  }else if(rows.some(r=>r.status!=='sent')){
    el.textContent='Sending benchmark report… A copy stays in this browser.';
  }else if(rows.length){
    el.textContent=rows.every(r=>r.receipt?.storage==='local')?'Report saved locally.':'Report sent. A copy stays in this browser.';
  }else el.textContent='Finished benchmark reports are sent automatically. Demo results are never sent.';
}
function render(){
  const nativeRunning=activeSession()||active();
  if(nativeRunning&&demo&&demo.controller.snapshot.stage!=='closed'){demo.controller.exit();return;}
  demo?.update(catalog,status?.catalog,busy||!!nativeRunning);
  const preview=demo?.controller.snapshot;
  const simulated=!!preview&&preview.stage!=='closed';
  if(simulated){renderDemo(preview);return;}
  rememberRun(selected);rememberSession();
  renderHistory();
  $('device-demo-badge').hidden=true;
  $('stats-link').href=frontendLink('/stats/',selected);$('stats-link').hidden=hosted;
  const parent=session(),running=activeSession()||active(),run=parent||job()||report;
  const stopping=running?.cancel_requested;
  const blocked=blockedConnection(),preparing=hosted&&!!running?.phases&&!hasNativeRun(running);
  $('run-icon').textContent=running&&!blocked?'■':'▶';
  $('run-label').textContent=stopping?'Stopping…':busy?(blocked?'Connecting…':running?'Stopping…':'Starting…'):running?.runner_outdated?'Update runner':blocked?'Retry connection':running?(preparing?'Cancel':'Stop'):'Run';
  $('run').setAttribute('aria-label',running?.runner_outdated?'Update runner':blocked?'Retry connection':running?(preparing?'Cancel connection':'Stop benchmark'):'Run GPU benchmark');$('run').disabled=(!connected&&!running)||busy||!!stopping||(hosted&&status?.usb_supported===false&&!running);$('run').setAttribute('aria-busy',String(busy||!!stopping));
  $('connection-cancel').hidden=!blocked;$('connection-cancel').disabled=busy||!!stopping;
  $('connection-cancel').textContent=preparing?'Cancel':'Stop';
  const preparationOnly=hosted&&parent&&!hasNativeProgress(parent);
  const finishedMessage=hosted&&parent&&!hasNativeRun(parent)?`${run.status==='canceled'?'Connection canceled. ':''}No benchmark was started.`:`Benchmark ${run?.status}. Results are preserved below.`;
  const progress=run?(run.error||(terminal.has(run.status)?finishedMessage:run.message||(parent?parent.phases.map(p=>p.processor.toUpperCase()).join(' → ')+' · native execution':'Native benchmark running'))):'';
  $('run-status').textContent=progress;$('run-status').hidden=!progress;
  renderResults(document,status,preparationOnly?null:job(),preparationOnly?null:report);
  $('timing-note').textContent=report?.timing_scope||'Native execution timings.';
  renderDelivery(parent);
  renderDevice();renderAdvanced();
  progressView=preparationOnly?null:chipEvidence(parent,reports,status?.catalog?.cases||[])||standaloneChipEvidence(job()||report,report,status?.catalog?.cases||[]);
  presentChip();
  const issue=parent?.phases.find(p=>p.status==='unsupported')?.reason;
  $('notice-message').textContent=serviceError||actionError||issue||'';$('notice').hidden=!(serviceError||actionError||issue);
  $('download-session').hidden=!hosted||!parent||!parent.phases.some(p=>p.run?.report_available);
  const buttons=$('result-processors');buttons.hidden=!parent;
  if(parent){buttons.replaceChildren(...parent.phases.map(phase=>{const button=document.createElement('button');button.type='button';button.textContent=phase.processor.toUpperCase();button.disabled=!reports[phase.run_id];button.setAttribute('aria-pressed',String(selected===phase.run_id));button.onclick=()=>{selected=phase.run_id;report=reports[selected];render();};return button;}));}
}
function renderDemo(preview){
  const controller=demo.controller,parent=preview.session,selectedReport=preview.reports[preview.selected];
  const running=parent?.status==='running';
  $('connection-cancel').hidden=true;$('device-connection').hidden=true;$('previous-runs').hidden=true;
  $('run-icon').textContent=running?'■':'▶';
  $('run-label').textContent=running?'Stop demo':parent?'Run demo again':'Run demo';
  $('run').setAttribute('aria-label',running?'Stop demo':'Run simulated GPU benchmark');
  $('run').disabled=preview.stage!=='connected'||!controller.ready;$('run').setAttribute('aria-busy','false');
  $('run-status').textContent=parent?(running?`Simulated ${parent.processor.toUpperCase()} · sample data only`:parent.status==='complete'?'Demo complete. All results below are simulated.':'Demo stopped. Partial sample results are shown below.'):preview.stage==='connected'?'Ready for a simulated GPU run.':'Choose a device to preview the benchmark.';
  $('run-status').hidden=false;$('notice').hidden=true;
  $('download-session').hidden=true;$('report-delivery-status').hidden=true;renderAdvanced(preview);
  $('device-picker').hidden=true;$('device-retry').hidden=true;$('device-demo-badge').hidden=false;
  $('device-name').textContent=preview.identity?.device_name||'Explore without a headset';
  $('device-chip').textContent=preview.identity?.chip_name||'';
  $('device-help').hidden=false;
  $('device-help').textContent=preview.stage==='connected'?'Simulated connection · no hardware is being used.':preview.stage==='connecting'?'Connecting a simulated device…':'Choose any catalog device in the demo panel.';
  document.querySelector('.device-panel').dataset.state='simulated';
  renderResults(document,controller.displayStatus,selectedReport,selectedReport,'simulated');
  $('timing-note').textContent='Simulated timings and correctness. Sample data is identical across device models and is never saved.';
  progressView=chipEvidence(parent,preview.reports,controller.displayStatus.catalog?.cases||[]);
  presentChip('simulated');
  const buttons=$('result-processors');buttons.hidden=!parent;
  if(parent){
    const focus=document.activeElement?.closest('#result-processors button')?.textContent;
    buttons.replaceChildren(...parent.phases.map(phase=>{
      const button=document.createElement('button');button.type='button';button.textContent=phase.processor.toUpperCase();
      button.disabled=running||phase.status==='pending';button.setAttribute('aria-pressed',String(preview.selected===phase.run_id));
      button.onclick=()=>controller.selectReport(phase.run_id);return button;
    }));
    if(focus)[...buttons.children].find(button=>button.textContent===focus)?.focus({preventScroll:true});
  }
}
async function poll(){
  if(refreshing||busy)return;refreshing=true;const generation=mutation;
  try{
    if(!token)token=(await api('/api/session')).token;
    const next=await api('/api/status');if(generation!==mutation)return;status=next;
    if(!catalog){try{catalog=await api('/api/device-catalog');renderCatalog();}catch{
      const message=document.createElement('p');message.textContent='Device compatibility is unavailable. Retrying automatically…';$('device-catalog').replaceChildren(message);
    }}
    if(!selectedSession&&status.benchmark_sessions?.length){
      const matching=status.benchmark_sessions.find(s=>s.phases.some(p=>p.run_id===selected));
      if(matching)selectedSession=matching.session_id;
    }
    if(activeSession())selectedSession=activeSession().session_id;
    else if(active()){selectedSession=null;selected=active().run_id;}
    const parent=session();
    if(parent){
      for(const phase of parent.phases){const key=phase.run?.progress?.sequence;const previous=observedProgress.get(phase.run_id);if(!previous||previous.sequence!==key)observedProgress.set(phase.run_id,{sequence:key,at:Date.now()});if(phase.run)phase.run.progress_received_at=observedProgress.get(phase.run_id).at;}
      for(const phase of parent.phases){const run=phase.run,key=run?.report_revision||run?.status;
        if(run?.report_available&&reportKeys[phase.run_id]!==key){const value=await api('/api/runs/'+phase.run_id+'/report');if(generation!==mutation)return;reports[phase.run_id]=value;reportKeys[phase.run_id]=key;}}
      const current=parent.phases[parent.current_phase];
      if(!selected||!parent.phases.some(p=>p.run_id===selected)||(!terminal.has(parent.status)&&current?.run))selected=current?.run_id||null;
      report=reports[selected]||null;
    }else{
      if(!selected&&active())selected=active().run_id;
      report=reports[selected]||null;
      const current=job(),key=current?.report_revision||current?.status||'saved';
      if(current){const sequence=current.progress?.sequence,previous=observedProgress.get(current.run_id);if(!previous||previous.sequence!==sequence)observedProgress.set(current.run_id,{sequence,at:Date.now()});current.progress_received_at=observedProgress.get(current.run_id).at;}
      if(selected&&reportKeys[selected]!==key&&(!current||current.report_available||current.counts)){
        report=await api('/api/runs/'+selected+'/report');if(generation!==mutation)return;reports[selected]=report;reportKeys[selected]=key;
      }
    }
    connected=true;serviceError='';render();
  }catch(error){if(generation!==mutation)return;connected=false;serviceError=error.message+(hosted?'':' — reconnect to the local benchmark service.');render();}
  finally{refreshing=false;}
}
async function retry(){
  if(busy)return;token='';actionError='';
  if(hosted&&activeSession()){
    busy=true;mutation++;render();
    try{await browserService.retry(activeSession().session_id);}catch(error){actionError=error.message;}
    finally{busy=false;}
  }
  await poll();render();
}
$('retry-status').onclick=retry;$('device-retry').onclick=retry;
$('back-headset').onclick=()=>{
  if(demo&&demo.controller.snapshot.stage!=='closed')dismissedPresentations.add(presentationKey);
  else{mutation++;selected=null;selectedSession=null;report=null;actionError='';}
  render();$('run').focus({preventScroll:true});document.querySelector('.hero').scrollIntoView?.({block:'start',behavior:'auto'});
};
$('device-picker').onchange=()=>{selectedSerial=$('device-picker').value||null;pendingRequest=null;render();};
$('download-session').onclick=async()=>{
  try{
    const value=await browserService.exportSession(selectedSession),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=selectedSession+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(error){actionError=error.message;render();}
};
function cpuIdentity(){
  const view=deviceView(status,serviceError,selectedSerial),parent=session();
  return parent?.capabilities_checked&&parent.connection_id===view.connectionId?parent.identity:view.identity;
}
function renderAdvanced(preview=null){
  if($('benchmark-test-count')&&status?.catalog?.cases)$('benchmark-test-count').textContent=String(status.catalog.cases.length);
  const identity=preview?preview.identity:cpuIdentity();
  $('advanced-cpu').hidden=!identity?.cpu_eligible||!preview&&status?.cpu_supported===false;
  $('run-cpu').textContent=preview?'Run CPU demo':'Run CPU benchmark';
  $('run-cpu').disabled=preview?preview.stage!=='connected'||preview.session?.status==='running':busy||!!activeSession()||!!active()||!connected||(!hosted&&!deviceView(status,serviceError,selectedSerial).authorized);
  $('cpu-reason').textContent=preview?'Simulated chip with no GPU. Preview CPU testing with sample data.':'This chip is confirmed to have no GPU. CPU capability will be checked before execution.';
}
async function runBenchmark(processor='gpu',cancel=false){
  if(demo&&demo.controller.snapshot.stage!=='closed'){
    if(demo.controller.snapshot.session?.status==='running'){if(processor==='gpu')demo.controller.stop();}
    else if(processor!=='cpu'||demo.controller.snapshot.identity?.cpu_eligible){demoGeneration++;animateNext=true;demo.controller.start(processor);}return;
  }
  if(busy)return;const running=activeSession()||active();if((!connected&&!running)||running?.cancel_requested)return;actionError='';
  if(blockedConnection()&&!cancel&&!running?.runner_outdated){await retry();return;}
  if(processor==='cpu'&&(running||!cpuIdentity()?.cpu_eligible)){actionError=CPU_RESTRICTION;render();return;}
  let device;
  if(!running&&!hosted){
    const view=deviceView(status,'',selectedSerial);device=status.devices.find(d=>d.serial===view.serial&&d.state==='device'&&d.supported);
    if(!device){actionError=view.kind==='multiple'?'Choose one device before running.':view.kind==='absent'?'Connect an Android arm64 device by USB and approve USB debugging.':view.help;render();return;}
    if(!status.apk_ready){actionError='The runner for this website release is unavailable. Please retry later.';render();return;}
  }
  busy=true;mutation++;render();
  try{
    if(hosted&&running?.runner_outdated&&!cancel){
      const created=await browserService.upgrade(running.session_id,crypto.randomUUID().replaceAll('-',''));
      selectedSession=created.session_id;selected=null;report=null;animateNext=true;
      status.benchmark_sessions=[created,...status.benchmark_sessions.filter(s=>s.session_id!==running.session_id&&s.session_id!==created.session_id)];
    }
    else if(running?.session_id){const stopped=await api('/api/benchmark-sessions/'+running.session_id+'/stop',{});status.benchmark_sessions=status.benchmark_sessions.map(s=>s.session_id===stopped.session_id?stopped:s);}
    else if(running){const stopped=await api('/api/runs/'+running.run_id+'/stop',{});status.jobs=status.jobs.map(r=>r.run_id===stopped.run_id?stopped:r);}
    else{
      if(pendingRequest&&pendingRequest.processor!==processor)pendingRequest=null;
      pendingRequest ||= {serial:hosted?(selectedSerial||deviceView(status,'',selectedSerial).serial):device.serial,request_id:crypto.randomUUID().replaceAll('-',''),processor};
      const created=await (hosted?browserService.start(pendingRequest.serial,pendingRequest.request_id,pendingRequest.processor):api('/api/benchmark-sessions',pendingRequest));animateNext=true;selectedSession=created.session_id;selected=created.phases?.[created.current_phase]?.run?.run_id||null;report=null;pendingRequest=null;
      if(created.phases?.[created.current_phase]?.run)status.jobs=[created.phases[created.current_phase].run,...status.jobs.filter(j=>j.run_id!==selected)];
      status.benchmark_sessions=[created,...(status.benchmark_sessions||[]).filter(s=>s.session_id!==created.session_id)];
    }
  }catch(error){if(hosted)console.warn('xrBench connection failed',error.cause||error);actionError=error.message;}finally{busy=false;render();}
}
$('run').onclick=()=>runBenchmark();
$('connection-cancel').onclick=()=>runBenchmark('gpu',true);
$('run-cpu').onclick=()=>runBenchmark('cpu');
await poll();const polling=setInterval(poll,1000);
const clock=setInterval(()=>{if(progressView?.active&&!document.hidden&&(!demo||demo.controller.snapshot.stage==='closed'))$('chip-timer').textContent=clockLabel(Math.max(0,Date.now()-(session()?.started_at||session()?.created_at||job()?.created_at||Date.now()/1000)*1000));},250);
window.addEventListener('pagehide',event=>{if(event.persisted)return;clearInterval(polling);clearInterval(clock);headset.destroy();demo?.destroy();browserService?.dispose();});
