import {selectedRunId,rememberRun,frontendLink} from '/shared/session.mjs';
import {terminal,casesForReport,caseState,needsAttention} from '/stats/view-model.mjs';
import {deviceView} from '/bench/device-view.mjs';
import {renderResults} from '/bench/results-view.mjs';

const $=id=>document.getElementById(id);
let token='',status=null,selected=selectedRunId(),report=null,reportKey=null;
let refreshing=false,busy=false,connected=false,mutation=0,actionError='',serviceError='';
let scene=null,graphicsAvailable=false;

function renderDevice(){
  const view=deviceView(status,serviceError);
  $('device-name').textContent=view.label;
  $('device-help').textContent=view.help;
  $('device-help').hidden=!view.help;
  $('device-help-link').hidden=!['multiple','discovery-error','unauthorized','unsupported'].includes(view.kind);
  $('device-help-link').href=frontendLink('/stats/',selected);
  document.querySelector('.device-stage').classList.toggle('has-model',view.visible);
  document.querySelector('.device-stage').dataset.state=view.kind;
  $('scene').hidden=!view.visible;
  $('viewer-controls').hidden=!view.visible||!graphicsAvailable;
  scene?.setDevice(view);
}

function notice(message){
  $('notice-message').textContent=message;
  $('notice').hidden=!message;
}

async function api(path,body){
  const response=await fetch(path,{
    headers:{'X-XRGPU-Session':token,...(body===undefined?{}:{'Content-Type':'application/json'})},
    ...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)}),
  });
  const data=await response.json();
  if(!response.ok)throw Error(data.error || 'Local service unavailable');
  return data;
}

function job(){return status?.jobs.find(run=>run.run_id===selected);}
function active(){return status?.jobs.find(run=>!terminal.has(run.status));}

function updateScene(){
  renderDevice();
  const entries=casesForReport(status?.catalog.cases || [],report,status?.suite_revision);
  const groups=new Map();
  for(const entry of entries){
    if(!groups.has(entry.operation))groups.set(entry.operation,[]);
    groups.get(entry.operation).push(entry);
  }
  scene?.update([...groups.values()].map(cases=>{
    if(cases.some(c=>needsAttention(c,report)))return 'attention';
    if(cases.some(c=>caseState(c,report)==='running'))return 'running';
    return cases.every(c=>caseState(c,report)==='passed')?'passed':'pending';
  }));
}

function render(){
  rememberRun(selected);
  for(const id of ['stats-link','help-link','results-link'])$(id).href=frontendLink('/stats/',selected);
  const running=active(),run=job() || report;
  const stopping=running?.cancel_requested;
  $('run-label').textContent=stopping?'Stopping…':busy?(running?'Stopping…':'Starting…'):running?'Stop':'Run';
  $('run').setAttribute('aria-label',running?'Stop benchmark':'Run all tests');
  $('run').disabled=!connected||busy||!!stopping;
  $('run').setAttribute('aria-busy',String(busy||!!stopping));
  const progress=run
    ? terminal.has(run.status)
      ? `Run ${run.status}. Results are below.`
      : run.message || `${run.progress?.completed_cases || 0} of ${run.progress?.total_cases || 63} tests finished.`
    : '';
  $('run-status').textContent=progress;
  $('run-status').hidden=!progress;
  renderResults(document,status,job(),report);
  updateScene();
  notice(serviceError || actionError);
}

async function poll(){
  if(refreshing||busy)return;
  refreshing=true;
  const generation=mutation;
  try{
    if(!token)token=(await api('/api/session')).token;
    const next=await api('/api/status');
    if(generation!==mutation)return;
    status=next;
    if(!selected&&status.jobs.length)selected=(active() || status.jobs[0]).run_id;
    const current=job(),key=selected+':'+(current?.report_revision || current?.status || 'saved');
    if(selected&&key!==reportKey&&(!current||current.report_available||current.counts)){
      const nextReport=await api('/api/runs/'+selected+'/report');
      if(generation!==mutation)return;
      report=nextReport;
      reportKey=key;
    }
    connected=true;
    serviceError='';
    render();
  }catch(error){
    if(generation!==mutation)return;
    connected=false;
    serviceError=error.message+' — reconnect to the local benchmark service.';
    render();
  }finally{
    refreshing=false;
  }
}

$('run').onclick=async()=>{
  if(busy||!connected)return;
  const running=active();
  if(running?.cancel_requested)return;
  actionError='';
  if(!running){
    const ready=(status.devices || []).filter(device=>device.supported&&device.state==='device');
    if(ready.length!==1){
      actionError=ready.length>1
        ? 'More than one headset is connected. Choose a headset in Stats for Nerds.'
        : 'Connect your Quest 3 by USB and approve USB debugging inside the headset.';
      render();
      return;
    }
    if(!status.apk_ready){
      actionError='The benchmark app is not ready. Open Stats for Nerds for setup details.';
      render();
      return;
    }
  }
  busy=true;
  mutation++;
  render();
  try{
    if(running){
      const stopped=await api('/api/runs/'+running.run_id+'/stop',{});
      status.jobs=status.jobs.map(run=>run.run_id===stopped.run_id?stopped:run);
    }else{
      const device=status.devices.find(device=>device.supported&&device.state==='device');
      const created=await api('/api/runs',{serial:device.serial,mode:'isolated',preset:'smoke'});
      selected=created.run_id;
      report=null;
      reportKey=null;
      status.jobs=[created,...status.jobs.filter(run=>run.run_id!==created.run_id)];
    }
  }catch(error){
    actionError=error.message;
  }finally{
    busy=false;
    render();
  }
};

let pageClosed=false;
$('reset-view').onclick=()=>scene?.resetView();
import('/bench/scene.bundle.js').then(({createScene})=>{
  if(pageClosed)return;
  scene=createScene($('scene'),{onAvailabilityChange(available){
    graphicsAvailable=available;
    renderDevice();
  }});
  graphicsAvailable=true;
  updateScene();
}).catch(()=>{
  graphicsAvailable=false;
  renderDevice();
});

await poll();
const polling=setInterval(poll,1000);
window.addEventListener('pagehide',event=>{
  if(event.persisted)return;
  pageClosed=true;
  clearInterval(polling);
  scene?.dispose();
});
