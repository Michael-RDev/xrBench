import {selectedRunId,rememberRun,frontendLink} from '/shared/session.mjs';
import {terminal,casesForReport,caseState,needsAttention,caseCounts} from '/stats/view-model.mjs';

const $=id=>document.getElementById(id);
let token='',status=null,selected=selectedRunId(),report=null,reportKey=null,refreshing=false,starting=false,scene=null,tilesKey=null;
const element=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
function notice(message){$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path,body){
  const response=await fetch(path,{headers:{'X-XRGPU-Session':token,...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});
  const data=await response.json();
  if(!response.ok)throw Error(data.error || 'Local service unavailable');
  return data;
}
function job(){return status?.jobs.find(r=>r.run_id===selected);}
function active(){return status?.jobs.find(r=>!terminal.has(r.status));}
function links(){
  rememberRun(selected);
  for(const [id,hash] of [['stats-link',''],['inspect-link','#layers'],['details-link','#layers'],['history-link','#runs']])$(id).href=frontendLink('/stats/',selected)+hash;
}
function settings(){
  const count=status?.catalog.cases.length || 63,preset=$('preset').value;
  $('preset-help').textContent=$('mode').value==='xr'?`${count} cases · ${count*({smoke:20,standard:120,sustained:600})[preset]/60} minutes of paired windows, plus setup and warmups.`:({smoke:'5 warmups · 20 samples · every case.',standard:'20 warmups · 100 samples · every case.',sustained:'All cases cycle at 10 Hz for 10 minutes, after 20 warmups per case.'})[preset];
}
function devices(){
  const previous=$('device').value,items=status.devices || [],ready=items.filter(d=>d.supported&&d.state==='device');
  $('device').replaceChildren();const empty=element('option','Choose a headset');empty.value='';$('device').append(empty);
  for(const d of items){const option=element('option',`${d.model || 'Headset'} · ${d.state==='device'?(d.supported?'Ready':'Unsupported'):d.state}`);option.value=d.serial;option.disabled=!ready.includes(d);$('device').append(option);}
  $('device').value=ready.some(d=>d.serial===previous)?previous:ready.length===1?ready[0].serial:'';
  $('device-picker').hidden=items.length<2;
  const chosen=ready.find(d=>d.serial===$('device').value),display=chosen || (items.length===1?items[0]:null);
  $('device-identity').textContent=display?`${display.model || 'Headset'} · ${display.state==='device'?(display.supported?'Connected':'Unsupported'):display.state}`:items.length?'Choose your headset.':'Connect your headset.';
  $('device-help').textContent=status.error || display?.reason || (chosen?'Ready for native execution. The controller installs the matching app when you press Run.':'Connect Quest 3 by USB and allow debugging inside the headset.');
  if(!status.apk_ready)$('device-help').textContent+=' Build the native APK first; see the repository README.';
  $('run').disabled=starting||!chosen||!status.apk_ready||!!active();
  $('mode').disabled=$('preset').disabled=!!active()||starting;
}
function layers(){
  const entries=casesForReport(status?.catalog.cases || [],report,status?.suite_revision),groups=new Map();
  for(const entry of entries){if(!groups.has(entry.operation))groups.set(entry.operation,[]);groups.get(entry.operation).push(entry);}
  const visual=[];
  const data=[...groups].map(([operation,cases])=>{
    const done=cases.filter(c=>c.rows.length&&caseState(c,report)!=='running').length;
    const attention=cases.some(c=>needsAttention(c,report));
    const running=cases.some(c=>caseState(c,report)==='running');
    const state=attention?'attention':running?'running':done===cases.length?'passed':'pending';
    visual.push(state);
    return {operation,done,count:cases.length,state};
  });
  const key=JSON.stringify([selected,data]);
  if(key!==tilesKey){
    tilesKey=key;$('layers').replaceChildren();
    for(const group of data){
      const a=element('a');a.className='layer-tile '+group.state;a.href=frontendLink('/stats/',selected)+(selected?'&':'?')+'layer='+encodeURIComponent(group.operation)+'#layers';
      const label=element('span',group.operation.replaceAll('_',' '));label.className='name';
      const state=element('span');state.className='state';state.append(element('i'),document.createTextNode(`${group.done} / ${group.count} · ${group.state==='attention'?'check':group.state==='running'?'running':group.state==='passed'?'passed':'pending'}`));
      a.append(label,state);$('layers').append(a);
    }
  }
  scene?.update(visual);
  return entries;
}
function render(){
  links();devices();settings();
  const run=job() || report,a=active(),entries=layers();
  $('stop').disabled=!a||a.cancel_requested;$('stop').textContent=a?.cancel_requested?'Stop requested':'Stop';$('stop').title=a?'Stop '+a.run_id:'';
  $('download').disabled=!report;$('retry').hidden=!(job()?.storage==='pending'&&terminal.has(job()?.status));
  $('database').textContent=status.database?.message || 'Checking PostgreSQL…';
  $('storage').textContent=job()?.storage==='stored'?'Report saved to local PostgreSQL':job()?.storage==='pending'?'Database save pending · local evidence preserved':job()?.storage || (report?'Saved report loaded':'No report yet');
  if(!run)return;
  const p=report?.progress || job()?.progress,total=p?.total_cases || run.configuration?.cases?.length || 63,completed=p?.completed_cases ?? (run.status==='complete'?total:0);
  $('status-badge').textContent=run.status;
  $('run-title').textContent=terminal.has(run.status)?`Run ${run.status}.`:'Working on your headset.';
  $('progress-message').textContent=job()?.message || report?.error || (p?.current_case?`Test ${p.case_index} of ${total} — ${p.current_case}`:'Inspect the saved evidence below.');
  $('progress').max=total;$('progress').value=Math.min(completed,total);$('completed').textContent=`${completed} of ${total} tests finished`;
  const counts=report?caseCounts(entries,report):{};
  for(const key of ['passed','failed','skipped','interrupted'])$(key).textContent=counts[key] ?? '—';
  $('scene-caption').textContent=completed?`${completed} of ${total} tests finished.`:'A closer look at what’s inside.';
}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{
    status=await api('/api/status');
    if(!selected&&status.jobs.length)selected=(active() || status.jobs[0]).run_id;
    const j=job(),key=selected+':'+(j?.report_revision || j?.status || 'saved');
    if(selected&&key!==reportKey&&(!j||j.report_available||j.counts)){
      report=await api('/api/runs/'+selected+'/report');reportKey=key;
    }
    render();notice('');
  }catch(error){
    if(status)render();
    notice(error.message+' — reconnect to the local service or choose a saved run in Stats for Nerds.');$('run').disabled=true;
  }finally{refreshing=false;}
}
$('mode').onchange=$('preset').onchange=settings;$('device').onchange=devices;
$('run').onclick=async()=>{
  if(starting)return;starting=true;$('run').disabled=true;
  try{const created=await api('/api/runs',{serial:$('device').value,mode:$('mode').value,preset:$('preset').value});selected=created.run_id;report=null;reportKey=null;await refresh();}
  catch(error){notice(error.message);}finally{starting=false;}
};
$('stop').onclick=async()=>{const run=active();if(!run)return;try{await api('/api/runs/'+run.run_id+'/stop',{});await refresh();}catch(error){notice(error.message);}};
$('retry').onclick=async()=>{if(!selected)return;try{await api('/api/runs/'+selected+'/retry',{});await refresh();}catch(error){notice(error.message);}};
$('download').onclick=()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const a=element('a');a.href=url;a.download=report.run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
import('/bench/scene.bundle.js').then(({createScene})=>{scene=createScene($('scene'));if(status)layers();}).catch(()=>{$('scene-caption').textContent='3D preview unavailable. Benchmark controls are ready.';});
async function poll(){
  try{if(!token)token=(await api('/api/session')).token;await refresh();}catch(error){notice(error.message+' — reconnect to the local benchmark service.');}
}
await poll();setInterval(poll,3000);
