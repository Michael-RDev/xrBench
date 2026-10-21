import {selectedRunId,rememberRun,frontendLink} from '/shared/session.mjs';
import {terminal} from '/stats/view-model.mjs';
import {deviceView} from '/bench/device-view.mjs';
import {renderResults} from '/bench/results-view.mjs';
import {chipEvidence,standaloneChipEvidence,renderChip,clockLabel} from '/bench/chip-view.mjs';
import {mountHeadset} from '/bench/ascii-headset.mjs';
import {CPU_RESTRICTION} from '/bench/processor-policy.mjs';
import {ENABLE_SMOKE_DEMO} from '/bench/features.mjs';

const hosted=document.documentElement.dataset.transport==='webusb';
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
async function api(path,body){
  if(browserService)return browserService.request(path,body);
  const response=await fetch(path,{headers:{'X-XRGPU-Session':token,...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
  const data=await response.json();if(!response.ok)throw Error(data.error||'Local service unavailable');return data;
}
function rememberSession(){if(!selectedSession)return;const url=new URL(location.href);url.searchParams.set('session',selectedSession);history.replaceState(null,'',url);}
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
  $('device-help').textContent=view.help;$('device-help').hidden=!view.help;
  $('device-retry').hidden=['connected','searching'].includes(view.kind)&&activeSession()?.status!=='disconnected';
  if(hosted&&!activeSession()){
    $('device-help').hidden=false;
    $('device-help').textContent=status?.usb_supported===false?'Use desktop Chrome or Edge on the HTTPS website.':view.kind==='multiple'?'Choose your headset, then press Run.':'Press Run to connect and install the runner automatically. Allow USB debugging in your headset.';
  }else if(hosted&&activeSession()){
    $('device-help').hidden=false;$('device-help').textContent='Keep the headset awake with the benchmark app open until the benchmark finishes.';
  }else if(view.kind==='connected'&&!status?.apk_ready){$('device-help').textContent='The developer runner has not been built.';$('device-help').hidden=false;}
  document.querySelector('.device-panel').dataset.state=view.kind;
}
function renderCatalog(){
  if(!catalog)return;const list=$('device-catalog');list.replaceChildren();
  for(const chip of catalog.chips){const item=document.createElement('li'),name=document.createElement('strong'),detail=document.createElement('span');
    name.textContent=chip.name;detail.textContent=chip.execution==='android_probe_required'?'Android native CPU / GPU · capability check required':chip.execution==='planned_metal'?'Apple Metal runner · planned':'Specialized processor · compute runner unavailable';
    const models=catalog.devices.filter(d=>d.chips.some(c=>c.chip_id===chip.id)||d.primary_chip_options?.includes(chip.id)).map(d=>d.name);
    if(models.length)detail.textContent+=' · '+models.join(', ');
    item.append(name,detail);list.append(item);
  }
}
function presentChip(source='native'){
  presentationKey=progressView?`${source}:${progressView.id}:${source==='simulated'?demoGeneration:''}`:'';
  const show=!!progressView&&!dismissedPresentations.has(presentationKey);
  renderChip(document,show?progressView:null,source);
  document.querySelector('.hero').classList.toggle('benchmark-active',show);
  $('back-headset').hidden=!show||!terminal.has(progressView.status);
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
  $('device-demo-badge').hidden=true;
  $('stats-link').href=frontendLink('/stats/',selected);$('stats-link').hidden=hosted;
  const parent=session(),running=activeSession()||active(),run=parent||job()||report;
  const stopping=running?.cancel_requested;
  $('run-icon').textContent=running?'■':'▶';
  $('run-label').textContent=stopping?'Stopping…':busy?(running?'Stopping…':'Starting…'):running?'Stop':run?'Run GPU again':'Run GPU';
  $('run').setAttribute('aria-label',running?'Stop benchmark':'Run GPU benchmark');$('run').disabled=(!connected&&!running)||busy||!!stopping||(hosted&&status?.usb_supported===false&&!running);$('run').setAttribute('aria-busy',String(busy||!!stopping));
  const progress=run?(run.error||(terminal.has(run.status)?`Benchmark ${run.status}. Results are preserved below.`:run.message||(parent?parent.phases.map(p=>p.processor.toUpperCase()).join(' → ')+' · native execution':'Native benchmark running'))):'';
  $('run-status').textContent=progress;$('run-status').hidden=!progress;
  renderResults(document,status,job(),report);
  $('timing-note').textContent=report?.timing_scope||'Native execution timings.';
  renderDelivery(parent);
  renderDevice();renderAdvanced();
  progressView=chipEvidence(parent,reports,status?.catalog?.cases||[])||standaloneChipEvidence(job()||report,report,status?.catalog?.cases||[]);
  presentChip();
  const issue=parent?.phases.find(p=>p.status==='unsupported')?.reason;
  $('notice-message').textContent=serviceError||actionError||issue||'';$('notice').hidden=!(serviceError||actionError||issue);
  if($('setup-help'))$('setup-help').hidden=hosted||!/benchmark app is not ready|\bAPK\b/i.test(actionError);
  $('download-session').hidden=!hosted||!parent||!parent.phases.some(p=>p.run?.report_available);
  const buttons=$('result-processors');buttons.hidden=!parent;
  if(parent){buttons.replaceChildren(...parent.phases.map(phase=>{const button=document.createElement('button');button.type='button';button.textContent=phase.processor.toUpperCase();button.disabled=!reports[phase.run_id];button.setAttribute('aria-pressed',String(selected===phase.run_id));button.onclick=()=>{selected=phase.run_id;report=reports[selected];render();};return button;}));}
}
function renderDemo(preview){
  const controller=demo.controller,parent=preview.session,selectedReport=preview.reports[preview.selected];
  const running=parent?.status==='running';
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
    if(!catalog){try{catalog=await api('/api/device-catalog');renderCatalog();}catch{/* Detection and controls still work without the catalog panel. */}}
    if(!selectedSession&&status.benchmark_sessions?.length){
      const matching=status.benchmark_sessions.find(s=>s.phases.some(p=>p.run_id===selected));
      if(matching||!selected)selectedSession=(matching||activeSession()||status.benchmark_sessions[0]).session_id;
    }
    if(activeSession())selectedSession=activeSession().session_id;
    else if(active()){selectedSession=null;selected=active().run_id;}
    const parent=session();
    if(parent){
      for(const phase of parent.phases){const key=phase.run?.progress?.sequence;const previous=observedProgress.get(phase.run_id);if(!previous||previous.sequence!==key)observedProgress.set(phase.run_id,{sequence:key,at:Date.now()});if(phase.run)phase.run.progress_received_at=observedProgress.get(phase.run_id).at;}
      for(const phase of parent.phases){const run=phase.run,key=run?.report_revision||run?.status;
        if(run?.report_available&&reportKeys[phase.run_id]!==key){const value=await api('/api/runs/'+phase.run_id+'/report');if(generation!==mutation)return;reports[phase.run_id]=value;reportKeys[phase.run_id]=key;}}
      const current=parent.phases[parent.current_phase];
      if(!selected||(!terminal.has(parent.status)&&current?.run))selected=current.run_id;
      report=reports[selected]||null;
    }else{
      if(!selected&&status.jobs.length)selected=(active()||status.jobs[0]).run_id;
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
async function retry(){if(busy)return;token='';actionError='';await poll();}
$('retry-status').onclick=retry;$('device-retry').onclick=retry;
$('back-headset').onclick=()=>{dismissedPresentations.add(presentationKey);render();$('run').focus({preventScroll:true});document.querySelector('.hero').scrollIntoView?.({block:'start',behavior:'auto'});};
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
  const identity=preview?preview.identity:cpuIdentity();
  $('advanced-cpu').hidden=!identity?.cpu_eligible;
  $('run-cpu').textContent=preview?'Run CPU demo':'Run CPU benchmark';
  $('run-cpu').disabled=preview?preview.stage!=='connected'||preview.session?.status==='running':busy||!!activeSession()||!!active()||!connected||!deviceView(status,serviceError,selectedSerial).authorized;
  $('cpu-reason').textContent=preview?'Simulated chip with no GPU. Preview CPU testing with sample data.':'This chip is confirmed to have no GPU. CPU capability will be checked before execution.';
}
async function runBenchmark(processor='gpu'){
  if(demo&&demo.controller.snapshot.stage!=='closed'){
    if(demo.controller.snapshot.session?.status==='running'){if(processor==='gpu')demo.controller.stop();}
    else if(processor!=='cpu'||demo.controller.snapshot.identity?.cpu_eligible){demoGeneration++;animateNext=true;demo.controller.start(processor);}return;
  }
  if(busy)return;const running=activeSession()||active();if((!connected&&!running)||running?.cancel_requested)return;actionError='';
  if(processor==='cpu'&&(running||!cpuIdentity()?.cpu_eligible)){actionError=CPU_RESTRICTION;render();return;}
  let device;
  if(!running&&!hosted){
    const view=deviceView(status,'',selectedSerial);device=status.devices.find(d=>d.serial===view.serial&&d.state==='device'&&d.supported);
    if(!device){actionError=view.kind==='multiple'?'Choose one device before running.':view.kind==='absent'?'Connect an Android arm64 device by USB and approve USB debugging.':view.help;render();return;}
    if(!status.apk_ready){actionError='The benchmark app is not ready on this computer. Complete local runner setup below, then select Retry.';render();return;}
  }
  busy=true;mutation++;render();
  try{
    if(running?.session_id){const stopped=await api('/api/benchmark-sessions/'+running.session_id+'/stop',{});status.benchmark_sessions=status.benchmark_sessions.map(s=>s.session_id===stopped.session_id?stopped:s);}
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
$('run-cpu').onclick=()=>runBenchmark('cpu');
await poll();const polling=setInterval(poll,1000);
const clock=setInterval(()=>{if(progressView?.active&&!document.hidden&&(!demo||demo.controller.snapshot.stage==='closed'))$('chip-timer').textContent=clockLabel(Math.max(0,Date.now()-(session()?.created_at||job()?.created_at||Date.now()/1000)*1000));},250);
window.addEventListener('pagehide',event=>{if(event.persisted)return;clearInterval(polling);clearInterval(clock);headset.destroy();demo?.destroy();browserService?.dispose();});
