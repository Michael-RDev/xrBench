import {selectedRunId,rememberRun,frontendLink} from '/shared/session.mjs';
import {terminal,timingLabels,timingScopes,labels,timings,distribution,caseState,needsAttention,casesForReport,filterCases,caseCounts,resolveOperations,sortMeasurements,correctnessObservations,efficiencyRows} from './view-model.mjs';
const $=id=>document.getElementById(id);
let token,status,selectedRun=selectedRunId(),currentReport=null,loadedReport=null,refreshing=false,historyBusy=false;
let layerRenderKey=null,historyRenderKey=null;
let savedRuns=new Map(),nextOffset=null,historyLoaded=false,historyError='',comparison=null,comparisonRequest=0;
const openedCases=new Set(),openedOperations=new Set();
let selectedOperations=null,selectionError='',continuous=false,watchBusy=false,watchLeased=false,watchPending=false,watchConfiguration='',chosenDevice='';
const watchOwner=crypto.randomUUID().replaceAll('-','');
const watchWaiters=[];
const rankingExpanded=new Set();
const rankingSort={correctness:{key:'total_ms',direction:-1},efficiency:{key:'gap_ms',direction:-1}};
function preserveView(action){
  const active=document.activeElement,focus=active?.dataset.focusKey;
  const tableKey=e=>[e.closest('.case')?.id,e.closest('.measurement')?.querySelector('h4')?.textContent,e.getAttribute('aria-label')].join('|');
  const scrolls=new Map([...document.querySelectorAll('.table-scroll')].map(e=>[tableKey(e),[e.scrollLeft,e.scrollTop]]));
  const rawOpen=new Set([...document.querySelectorAll('.raw-details[open] summary')].map(e=>e.dataset.focusKey));
  const x=window.scrollX,y=window.scrollY;
  action();
  for(const summary of document.querySelectorAll('.raw-details summary'))if(rawOpen.has(summary.dataset.focusKey))summary.parentElement.open=true;
  if(focus)[...document.querySelectorAll('[data-focus-key]')].find(e=>e.dataset.focusKey===focus)?.focus({preventScroll:true});
  for(const e of document.querySelectorAll('.table-scroll')){const scroll=scrolls.get(tableKey(e));if(scroll){e.scrollLeft=scroll[0];e.scrollTop=scroll[1];}}
  if(window.scrollX!==x||window.scrollY!==y)window.scrollTo(x,y);
}
const el=(tag,text,className)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;};
const ms=v=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(3)+' ms':'Unavailable';
const value=v=>v===undefined||v===null?'Not recorded':typeof v==='object'?JSON.stringify(v):String(v);
const short=v=>v?String(v).slice(0,12):'Not recorded';
const runLabel=r=>`${r.model || r.device?.model || 'Device'} · ${short(r.run_id)} · ${r.configuration?.preset || 'preset unknown'}`;
const date=v=>v && !Number.isNaN(new Date(v).getTime())?new Date(v).toLocaleString():'Date not recorded';
function notice(message){$('notice').textContent=message;$('notice').hidden=!message;}
function option(v,label){const n=el('option',label);n.value=v;return n;}
function button(label,action){const n=el('button',label);n.addEventListener('click',action);return n;}
function detailList(pairs){const dl=el('dl',undefined,'details');for(const [key,v] of pairs){dl.append(el('dt',key),el('dd',value(v)));}return dl;}
function table(headers,rows,caption){const wrap=el('div',undefined,'table-scroll');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',caption);const t=el('table'),head=el('thead'),tr=el('tr');headers.forEach(h=>{const th=el('th',h);th.scope='col';tr.append(th);});head.append(tr);const body=el('tbody');rows.forEach(cells=>{const row=el('tr');cells.forEach(v=>{const td=el('td');td.append(v instanceof Node?v:document.createTextNode(value(v)));row.append(td);});body.append(row);});t.append(head,body);wrap.append(t);return wrap;}
async function api(path,body){const response=await fetch(path,{headers:{'X-XRGPU-Session':token || '',...(body!==undefined?{'Content-Type':'application/json'}:{})},...(body!==undefined?{method:'POST',body:JSON.stringify(body)}:{})});const data=await response.json();if(!response.ok)throw Error(data.error || 'Local service unavailable');return data;}
function workloadCatalog(){return ($('workload-suite').value==='development'?status?.development_catalog:status?.catalog)?.cases || [];}
function entries(){return casesForReport(status?.development_catalog?.cases || status?.catalog.cases || [],currentReport,status?.suite_revision).filter(e=>currentReport||workloadCatalog().some(c=>c.id===e.id));}
function navigate(){
  const page=['benchmark','layers','runs'].includes(location.hash.slice(1))?location.hash.slice(1):'benchmark';
  for(const a of document.querySelectorAll('nav a')){if(a.getAttribute('href')==='#'+page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');}
  if(page==='runs'){$('runs').open=true;if(token&&!historyLoaded)loadHistory(true);}
  if(location.hash)$(page).scrollIntoView?.({block:'start'});
}
window.addEventListener('hashchange',navigate);
function updateSettings(){
  const catalog=workloadCatalog(),preset=$('preset').value,xr=$('mode').value==='xr';
  try{selectedOperations=resolveOperations(catalog,$('operations').value);selectionError='';}
  catch(error){selectedOperations=null;selectionError=error.message;}
  $('operations').setAttribute('aria-invalid',String(!!selectionError));
  $('operations-help').textContent=selectionError||'* = all · e.g. conv*, linear';
  const count=selectedOperations?.cases.length||0;
  $('suite-summary').textContent=(selectedOperations?.operations.length||0)+' operations · '+count+' cases';
  $('preset-help').textContent=xr?`Paired scene / scene + layer windows at 72 Hz. Planned windows: ${Number((count*({smoke:20,standard:120,sustained:600})[preset]/60).toFixed(1))} minutes, plus setup and warmups.`:({smoke:'5 warmups · 20 samples for each selected case.',standard:'20 warmups · 100 samples for each selected case.',sustained:'Selected cases cycle at 10 Hz for 10 minutes, after 20 warmups per case.'})[preset];
  const experiment=$('experiment').value,cpu=$('backend').value.includes('cpu');
  if(cpu&&xr)selectionError='CPU kernels currently support isolated native tests.';
  if($('backend').value==='executorch_cpu'&&experiment!=='none')selectionError='Choose CPU resident matmul for custom CPU kernel experiments.';
  if(experiment!=='none'&&(selectedOperations?.operations.join(',')!=='matmul'||xr||preset==='sustained'))selectionError='Kernel experiments require matmul, Native layers, and Smoke or Standard.';
  if($('backend').value.endsWith('_resident')&&(selectedOperations?.operations.join(',')!=='matmul'||xr||preset==='sustained'))selectionError='Resident matmul requires matmul, Native layers, and Smoke or Standard.';
  $('experiment-help').textContent=selectionError||(experiment==='none'?'Develop kernels on your computer; execute and validate them on the connected device.':`Balanced randomized pairs: ${preset==='smoke'?6:10} per case. Run stock/stock first, then stock/candidate. ${cpu?'Scalar baseline and ARM NEON candidate use one CPU thread.':'Repeat with GPU timestamps off to check tracing overhead.'}`);
  if(status)showDevices();
}
function showDevices(){
  const previous=chosenDevice||$('device').value,devices=status.devices||[],authorized=devices.filter(d=>d.supported&&d.state==='device');
  $('device').replaceChildren(option('','Choose a device'));
  for(const d of devices){const opt=option(d.serial,`${d.model||'Device'} · ${d.state==='device'?(d.support_label||(d.supported?'Connected':'Unsupported')):d.state}`);opt.disabled=!d.supported||d.state!=='device';$('device').append(opt);}
  if(previous){if(!devices.some(d=>d.serial===previous)){const missing=option(previous,'Selected device · Disconnected');missing.disabled=true;$('device').append(missing);}$('device').value=previous;}
  else if(devices.length===1&&authorized.length===1)$('device').value=authorized[0].serial;
  chosenDevice=$('device').value;$('device-picker').hidden=false;
  const device=devices.find(d=>d.serial===chosenDevice),display=device||(devices.length===1?devices[0]:null);
  $('device-identity').textContent=display?`${display.model||'Device'} · ${display.state}`:devices.length?'Select your device':'No device connected';
  $('device-help').textContent=status.error||display?.reason||'Connect by USB and approve USB debugging on the device.';
  const profile=status.device_profiles?.find(p=>p.serial===chosenDevice)?.profile;
  const stale=profile?.runner_revision&&status.build_identity?.runner_revision&&profile.runner_revision!==status.build_identity.runner_revision;
  const support=stale?null:profile?.backends?.[$('backend').value];
  const probe=status.probes?.find(p=>p.serial===chosenDevice);
  const cpu=$('backend').value.includes('cpu');
  const active=(status.jobs||[]).some(j=>!terminal.has(j.status))||(status.benchmark_sessions||[]).some(s=>!terminal.has(s.status))||(status.probes||[]).some(p=>p.status==='running');
  const xrUnavailable=$('mode').value==='xr'&&(device?.model!=='Quest 3'||!['unknown','headset'].includes($('execution-target').value));
  $('run').disabled=!device?.supported||device.state!=='device'||!status.apk_ready||active||!!selectionError||xrUnavailable||support?.compatible===false;
  $('recalibrate').disabled=$('run').disabled||cpu;
  $('probe-device').disabled=!device?.supported||active||!status.apk_ready;
  $('download-profile').disabled=!profile;
  $('profile-status').textContent=probe?.status==='running'||probe?.status==='failed'?probe.message:support?`${support.status==='compatible_unvalidated'?'Compatible · not yet validated':support.status} · ${cpu?(profile.identity?.chip_name||profile.soc_model||'CPU'):profile.gpu||'GPU unknown'} · ${support.reason}`:stale?'Device profile is from an older build. Inspect capabilities again.':'Capabilities not yet inspected. Running also checks backend requirements before timing.';
  if(xrUnavailable)$('device-help').textContent='The optional XR scene targets Quest 3. Native layer tests do not require the XR scene.';
  if(!status.apk_ready)$('device-help').textContent+=' Build the native APK using src/README.md before running.';
  for(const id of ['mode','preset','operations','device','experiment','profiling','backend','workload-suite','execution-target'])$(id).disabled=active;
  $('profiling').disabled=active||cpu;
  $('experiment').disabled=active||$('backend').value==='executorch_cpu';
  $('continuous').disabled=$('experiment').value!=='none';
}

function allRuns(){const runs=new Map(savedRuns);for(const j of status?.jobs || [])runs.set(j.run_id,{...runs.get(j.run_id),...j});if(currentReport){const old=runs.get(currentReport.run_id)||{};runs.set(currentReport.run_id,{...old,...reportMetadata(currentReport)});}return [...runs.values()].sort((a,b)=>(b.created_at||Date.parse(b.started_at||b.received_at)||0)-(a.created_at||Date.parse(a.started_at||a.received_at)||0));}
function reportMetadata(r){return {run_id:r.run_id,status:r.status,device:r.device,model:r.device?.model,configuration:r.configuration,started_at:r.started_at,report_available:true};}
function selectedJob(){return (status?.jobs || []).find(j=>j.run_id===selectedRun);}
function renderSession(){rememberRun(selectedRun);$('bench-link').href=frontendLink('/',selectedRun);const job=selectedJob(),report=currentReport?.run_id===selectedRun?currentReport:null,r=job || report;const active=(status?.benchmark_sessions||[]).find(s=>!terminal.has(s.status))||(status?.jobs || []).find(j=>!terminal.has(j.status));
  // The later assignment also handles continuous runs and a pending stop request.
  // $('stop').disabled=!active;
  $('stop').textContent=active?.cancel_requested?'Stop requested':'Stop';$('stop').disabled=(!active&&!continuous)||!!active?.cancel_requested;$('stop').title=active?'Stop '+active.run_id:'';$('download').disabled=!report;$('retry').hidden=!job||job.storage!=='pending'||!terminal.has(job.status);$('database').textContent=status?.database.message || 'Checking PostgreSQL…';renderDashboard();renderWatch();$('execution-context').textContent=report?`${report.backend||'Backend not recorded'} · Execution target: ${report.execution_location||'not recorded'} · ${report.device?.model||'Device unknown'} · ${report.timing_scope||'Timing scope not recorded'}`:'';if(!r){$('status-badge').textContent='Idle';$('run-title').textContent='Ready when you are.';$('progress-message').textContent='Select operations and run them on your headset.';$('progress-bar').value=0;$('case-progress').textContent='0 of '+(selectedOperations?.cases.length??63)+' cases finished';for(const key of ['passed','failed','skipped','interrupted'])$(key).textContent='—';return;}
  $('status-badge').textContent=r.status;$('status-badge').className='badge '+(r.status==='failed'?'failed':r.status==='disconnected'?'invalid':'');$('run-title').textContent=r.status==='disconnected'?'Waiting for device connection.':terminal.has(r.status)?`Run ${r.status}.`:'Testing on the connected device.';
  const p=report?.progress || job?.progress,total=p?.total_cases || r.configuration?.cases?.length || 63,completed=p?.completed_cases ?? (r.status==='complete'?total:0);
  $('progress-message').textContent=job?.message || report?.error || (p?.current_case?`Test ${p.case_index} of ${total} — ${p.current_case}`:`${short(r.run_id)} · ${r.status}`);
  $('progress-bar').max=total;$('progress-bar').value=Math.min(total,completed);$('case-progress').textContent=`${completed} of ${total} tests finished`;
  const counts=report?caseCounts(entries(),report):{};for(const key of ['passed','failed','skipped','interrupted'])$(key).textContent=counts[key] ?? '—';
  $('storage').textContent=job?.storage==='stored'?'Saved to local PostgreSQL':job?.storage==='pending'?'Local report / database save pending':job?.storage || (report?'Saved report loaded':'No report yet');
}
function selectReport(report){currentReport=report;layerRenderKey=null;renderLayers();renderLatest();renderSession();renderHistory();}
async function openRun(id,view='layers'){selectedRun=id;loadedReport=null;currentReport=null;layerRenderKey=null;renderSession();renderLayers();renderLatest();location.hash=view;try{const report=await api('/api/runs/'+id+'/report');if(selectedRun!==id)return;loadedReport=id+':'+(selectedJob()?.report_revision || report.status);selectReport(report);}catch(error){if(selectedRun===id)notice(error.message+' — this run has no retrievable report yet.');}}
function rowEvidence(row){const messages=[];if(row.error)messages.push([row.error,'failed']);if(row.reason)messages.push([row.reason,row.status==='skipped'?'skipped':'invalid']);if(row.thermal_discontinuity)messages.push(['Thermal or charging state changed. Timing is invalid for comparison.','invalid']);if(row.focus_lost)messages.push(['XR focus was lost. Timing is invalid for comparison.','invalid']);if(row.timing_valid===false&&row.status==='passed'&&!row.thermal_discontinuity&&!row.focus_lost)messages.push(['Timing was flagged invalid by the report.','invalid']);if(row.status==='interrupted')messages.push([currentReport && !terminal.has(currentReport.status)?'Measurement in progress. These samples are partial.':'Measurement interrupted. Completed samples are preserved, but are not valid for performance comparison.','invalid']);return messages;}
function correctnessTable(row){return table(['Check','Outcome','Max absolute error','Tolerance ratio','atol / rtol'],['correctness_before','correctness_after','correctness'].map((key,i)=>{const c=row[key];const outcome=c?.passed===true?'Passed':c?.passed===false?'Failed':'Not recorded';const text=el('span',outcome,c?.passed===false?'failed':'');if(c?.reason)text.append(el('small',c.reason));return [['Before measurement','After measurement','Latest validation'][i],text,value(c?.max_abs_error),value(c?.max_tolerance_ratio),c?`${value(c.atol)} / ${value(c.rtol)}`:'Not recorded'];}),'Correctness evidence');}
function measurementDetail(row){const section=el('section',undefined,'measurement');section.append(el('h4',`${row.variant||'stock'} · ${row.arm||row.phase} · window / trial ${row.trial+1} · ${labels[row.status] || row.status}`));for(const [message,cls] of rowEvidence(row))section.append(el('p',message,'evidence '+cls));section.append(correctnessTable(row));
  section.append(table(['Timing scope','Samples','Median','p95'],Object.entries(timingLabels).map(([key,label])=>{const d=timings(row,key),name=el('span',label);name.append(el('small',timingScopes[key]));return [name,d?.count ?? 0,el('span',ms(d?.median),'number'),el('span',ms(d?.p95),'number')];}),'Native timing measurements'));
  renderDispatches(section,row);
  if(row.efficiency?.available){const estimate=row.efficiency;section.append(el('h4','Estimated work & calibration reference'),detailList([['Arithmetic',estimate.cost?.flops+' FLOPs'],['Logical traffic',estimate.cost?.logical_bytes+' bytes'],['Reference duration',ms(estimate.reference_ms)],['Estimated limit',estimate.limiting_factor]]),el('p',estimate.cost?.byte_scope+' · Reference duration uses the largest compute, memory or dispatch bound.','hint'));}
  const samples=row.samples || [],last=samples.at(-1);section.append(detailList([['Output shape',last?.output_shape],['Thermal state',row.thermal_start],['Charging',row.charging_start],['Last sample',last?`thermal ${value(last.thermal_status)} · charging ${value(last.charging)}`:null]]));
  if(row.xr){const frame=distribution(row.xr.frame_ms),xr=row.xr;section.append(el('h4','XR frame observations'),detailList([['Frame p95',ms(frame?.p95)],['Frame samples',frame?.count ?? 0],['Metric available',row.xr_metrics_available],['Metric source',xr.frame_metric_source || xr.frame_source || xr.source],['Refresh',xr.refresh_hz===undefined?null:xr.refresh_hz+' Hz'],['Missed rate',xr.missed_frame_rate===undefined?null:xr.missed_frame_rate],['Focus',xr.focused]]));if(xr.error)section.append(el('p',xr.error,'evidence invalid'));}
  const raw=el('details',undefined,'raw-details');const rawSummary=el('summary','Inspect recorded evidence & sample preview');rawSummary.dataset.focusKey='raw:'+row.case+':'+row.phase+':'+row.trial;raw.append(rawSummary);raw.addEventListener('toggle',()=>{if(raw.open&&raw.children.length===1){const {samples:ignored,...evidence}=row;raw.append(el('pre',JSON.stringify({...evidence,samples:samples.slice(0,20)},null,2)));raw.append(el('p',`Showing the first ${Math.min(20,samples.length)} of ${samples.length} samples. Download the full report for every sample.`,'hint'));}});section.append(raw);return section;}
function caseDetail(entry){const body=el('div',undefined,'case-detail'),intro=el('div',undefined,'case-intro');intro.append(detailList([['Inputs',entry.inputs?.map((x,i)=>`${i+1}: ${x.dtype} [${x.shape.join(' × ')}]`).join('; ')],['Parameters',entry.params]]),detailList([['Precision',entry.rows[0]?.precision || currentReport?.device?.precision || (!currentReport?'FP32':null)],['Kernel variant',currentReport?.experiment?'Paired stock / '+currentReport.experiment.kind:currentReport?.device?.variant || (!currentReport?'Stock':null)]]));body.append(intro);if(!entry.rows.length)body.append(el('p',currentReport?'No measurements recorded for this case.':'Select this operation to include every one of its cases in your run.','hint'));entry.rows.forEach(r=>body.append(measurementDetail(r)));
  for(const pair of currentReport?.xr_comparisons || [])if(pair.case===entry.id)body.append(el('p',`XR pair ${pair.trial+1}: ${pair.valid?'frame p95 change with layer '+ms(pair.frame_p95_delta_ms):'comparison unavailable — '+pair.reason}`,'evidence '+(pair.valid?'':'invalid')));return body;}
function renderLayers(){preserveView(renderLayersContent);}
function renderLayersContent(){const key=[selectedRun,loadedReport,status?.suite_revision,$('layer-search').value,$('layer-filter').value].join('|');if(layerRenderKey===key)return;layerRenderKey=key;const list=filterCases(entries(),currentReport,$('layer-search').value,$('layer-filter').value);$('visible-count').textContent=`${list.length} of ${entries().length} cases`;$('layer-run').textContent=currentReport?`${currentReport.device?.model || 'Device'} · ${short(currentReport.run_id)} · ${currentReport.status} · ${currentReport.configuration?.preset || 'preset not recorded'}`:'Catalog preview · no run selected';const target=$('layer-groups');target.replaceChildren();const groups=new Map();for(const c of list){if(!groups.has(c.operation))groups.set(c.operation,[]);groups.get(c.operation).push(c);}for(const [name,cases] of groups){const group=el('details',undefined,'layer-group operation-group'),heading=el('summary',undefined,'group-heading'),operationKey=(currentReport?.run_id||'catalog')+':'+name;heading.dataset.focusKey='operation:'+name;heading.append(el('h3',name),el('span',cases.length+(cases.length===1?' case · ':' cases · ')+cases.filter(c=>c.rows.length).length+' measured','hint'));group.append(heading);group.open=openedOperations.has(operationKey);group.addEventListener('toggle',()=>{if(group.open)openedOperations.add(operationKey);else openedOperations.delete(operationKey);});for(const entry of cases){const item=el('details',undefined,'case');item.id='case-'+entry.id;const summary=el('summary');summary.tabIndex=0;summary.dataset.focusKey='case:'+entry.id;const title=el('span',entry.id,'case-name'),state=caseState(entry,currentReport);title.append(el('small',entry.inputs?.map(x=>'['+x.shape.join(' × ')+']').join(' · ') || 'Shape not recorded'));const stateLabel=el('span',labels[state]+(needsAttention(entry,currentReport)&&state==='passed'?' · flagged':''),'status '+(needsAttention(entry,currentReport)&&state==='passed'?'invalid':state));const latest=entry.rows.filter(r=>r.phase==='isolated').at(-1),timing=latest?timings(latest,'execution_ms'):null;summary.append(title,stateLabel,el('span',timing?ms(timing.median):'—','case-time'));item.append(summary);const key=(currentReport?.run_id||'catalog')+':'+entry.id;item.addEventListener('toggle',()=>{if(item.open){openedCases.add(key);if(item.children.length===1)item.append(caseDetail(entry));}else openedCases.delete(key);});if(openedCases.has(key)){item.open=true;item.append(caseDetail(entry));}group.append(item);}target.append(group);}if(!list.length)target.append(el('p','No cases match these filters.','empty'));}
function renderLatest(){renderDashboard();return;}

function downloadReport(report){const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download=report.run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function downloadRun(id){try{downloadReport(await api('/api/runs/'+id+'/report'));}catch(e){notice(e.message);}}
function renderHistory(){const runs=allRuns(),key=JSON.stringify([runs,nextOffset,historyError]);if(historyRenderKey===key)return;historyRenderKey=key;const target=$('history');target.replaceChildren();for(const r of runs){const row=el('article',undefined,'run-card'),info=el('div'),heading=el('h3',r.model || r.device?.model || 'Device run');info.append(heading,el('p',`${date(r.started_at || (r.created_at?r.created_at*1000:r.received_at))} · ${r.configuration?.mode || 'mode unknown'} · ${r.configuration?.preset || 'preset unknown'} · ${r.status}`));info.append(el('code',r.run_id));const conf=r.configuration || {};info.append(el('p',`Suite ${short(conf.suite_revision || r.suite_revision)} · runtime ${short(conf.runtime_revision || r.runtime_revision)} · runner ${short(conf.runner_revision || r.runner_revision)}`,'muted'),el('p',`APK ${short(conf.apk_sha256 || r.apk_sha256)}`,'muted'));if(r.receipt)info.append(el('p','PostgreSQL receipt · '+r.receipt.sha256,'receipt'));else info.append(el('p',r.storage==='pending'?'Database save pending · local evidence preserved':r.storage || 'Local report','hint'));const actions=el('div',undefined,'buttons'),inspect=button('Inspect layers ↗',()=>openRun(r.run_id));inspect.disabled=!(r.report_available || r.counts);const dl=button('JSON ↓',()=>downloadRun(r.run_id));dl.disabled=inspect.disabled;actions.append(inspect,dl);if(r.storage==='pending'&&terminal.has(r.status))actions.append(button('Retry save',()=>retryRun(r.run_id)));row.append(info,actions);target.append(row);}if(!runs.length)target.append(el('p','No runs in this workspace yet. Your first run will appear here.','empty'));
  for(const id of ['baseline-run','comparison-run']){const select=$(id),chosen=select.value;select.replaceChildren(option('','Choose a run'));for(const r of runs.filter(r=>terminal.has(r.status)&&(r.report_available||r.counts)))select.append(option(r.run_id,runLabel(r)));select.value=chosen;}
  $('compare').disabled=!$('baseline-run').value||!$('comparison-run').value||$('baseline-run').value===$('comparison-run').value;$('more-history').hidden=nextOffset===null;$('history-notice').textContent=historyError || 'Reports from this workspace and local PostgreSQL. All results stay local.';
}
function renderDispatches(target,row){
  if(!row.dispatch_summary?.length)return;
  target.append(el('h4','GPU dispatches'),el('p','Corrected device timestamps. Intervals can overlap; do not add them to predict latency. Full samples and shader hashes are in the report.','hint'));
  target.append(table(['Shader / source','Dispatch','Workgroup / global size','Median','p95','Evidence'],row.dispatch_summary.map(d=>{
    const name=el('span',d.kernel_name||'Unknown shader');name.append(el('small',d.shader?.source||'Source not recorded'));
    return [name,d.dispatch_id,`${value(d.local_size)} / ${value(d.global_size)}`,ms(d.timing?.median),ms(d.timing?.p95),d.complete?'All samples':'Partial'];
  }),'GPU dispatch breakdown'));
}
function renderKernelExperiment(){
  const target=$('kernel-results');if(!target)return;target.replaceChildren();
  const comparison=currentReport?.kernel_comparison;
  if(!comparison){target.append(el('p',currentReport?.experiment?'Collecting all planned experiment pairs…':'Run a stock/stock noise check or a stock/candidate matmul experiment.','empty'));return;}
  target.append(el('p',comparison.note,'hint'));
  for(const result of comparison.results){
    target.append(el('h3',result.case));
    if(!result.available){target.append(el('p',result.reasons.join('; '),'evidence invalid'));continue;}
    target.append(table(['Scope','Mean improvement','95% interval','p95 change'],Object.entries(result.metrics).map(([key,m])=>[timingLabels[key]||key,m.mean_improvement_percent.toFixed(2)+'%',m.ci95_percent.map(n=>n.toFixed(2)+'%').join(' to '),m.mean_p95_change_percent?.toFixed(2)+'%']),'Paired kernel comparison'));
    if(result.noise_control_warning)target.append(el('p','The identical stock arms show a substantial difference. Investigate noise, drift or measurement bias before accepting candidate results.','evidence invalid'));
    target.append(el('p',`${result.pairs} pairs · ${result.outcome.replaceAll('_',' ')} · Positive improvement means faster. Confirm in separate sessions and the application.`,'hint'));
  }
}
async function loadHistory(reset=false){if(historyBusy)return;historyBusy=true;$('refresh-history').disabled=true;$('more-history').disabled=true;try{const offset=reset?0:nextOffset;if(offset===null)return;const page=await api('/api/saved?limit=20&offset='+offset);if(reset)savedRuns.clear();for(const r of page.runs)savedRuns.set(r.run_id,r);nextOffset=page.next_offset;historyError='';historyLoaded=true;}catch(e){historyError='PostgreSQL history unavailable. Local runs and downloaded evidence remain accessible.';}finally{historyBusy=false;$('refresh-history').disabled=false;$('more-history').disabled=false;renderHistory();}}
function renderComparison(){const target=$('comparison');target.replaceChildren();if(!comparison)return;const banner=el('div',undefined,'compatibility');banner.append(el('strong',comparison.compatible?'Configurations match. Check each measurement below.':'Timing differences withheld.'));if(comparison.reasons.length){const list=el('ul');comparison.reasons.forEach(reason=>list.append(el('li',reason)));banner.append(list);}banner.append(el('p',comparison.note,'hint'));target.append(banner);const meta=el('details',undefined,'compare-metadata');meta.append(el('summary','Hardware, configuration & build provenance'));meta.append(table(['Field','Baseline','Comparison'],comparison.fields.map(f=>[f.label,value(f.baseline),value(f.current)]),'Run compatibility and provenance'));target.append(meta);const field=el('div',undefined,'comparison-metric'),label=el('label','Timing scope');label.htmlFor='comparison-metric';const select=el('select');select.id='comparison-metric';Object.entries(timingLabels).forEach(([key,name])=>select.append(option(key,name)));field.append(label,select);target.append(field);const rows=el('div');target.append(rows);const draw=()=>{rows.replaceChildren();rows.append(el('p',timingScopes[select.value]+'. Negative changes mean a lower observed duration.','hint'));rows.append(table(['Case / phase','Baseline median / p95','Comparison median / p95','Median change','p95 change / evidence'],comparison.results.map(r=>{const m=r.metrics[select.value]||{valid:false,reason:'Timing scope not recorded'},name=el('span',r.case);name.append(el('small',r.phase+' · trial '+(r.trial+1)));const a=el('span',ms(m.baseline?.median)+' / '+ms(m.baseline?.p95));a.append(el('small',(m.baseline?.count ?? 0)+' samples'));const b=el('span',ms(m.current?.median)+' / '+ms(m.current?.p95));b.append(el('small',(m.current?.count ?? 0)+' samples'));return [name,a,b,m.valid?ms(m.median_delta_ms)+(m.median_change_percent===null?'':` (${m.median_change_percent.toFixed(1)}%)`):'Withheld',m.valid?ms(m.p95_delta_ms):m.reason];}),'Observed timing comparison'));};select.onchange=draw;draw();}
async function refresh(){if(refreshing)return;refreshing=true;try{const oldStatus=selectedJob()?.status;status=await api('/api/status');showDevices();updateSettings();if(!selectedRun&&status.jobs.length)selectedRun=status.jobs[0].run_id;const job=selectedJob();if(job&&(job.report_available||job.counts)){const key=job.run_id+':'+(job.report_revision||job.status);if(key!==loadedReport){const id=job.run_id;const report=await api('/api/runs/'+id+'/report');if(id===selectedRun){loadedReport=key;selectReport(report);}}}else if(!currentReport)renderLayers();renderSession();renderHistory();if(job?.status!==oldStatus&&terminal.has(job?.status)&&historyLoaded)loadHistory(true);notice('');}catch(e){notice(e.message+' — reconnect to the local benchmark service.');$('run').disabled=true;}finally{refreshing=false;}}
async function retryRun(id){try{await api('/api/runs/'+id+'/retry',{});await refresh();await loadHistory(true);}catch(e){notice(e.message);}}
function requestConfiguration(calibration='auto'){
  if(!selectedOperations||selectionError)throw Error(selectionError||'Choose operations');
  return {...($('backend').value!=='executorch_vulkan'?{backend:$('backend').value}:{}),...($('workload-suite').value!=='standard'?{workload_suite:$('workload-suite').value}:{}),...($('execution-target').value!=='unknown'?{execution_target:$('execution-target').value}:{}),serial:$('device').value,mode:$('mode').value,preset:$('preset').value,operations:selectedOperations.operations,calibration,...($('experiment').value!=='none'?{experiment:$('experiment').value}:{}),...($('profiling').value!=='trace'?{profiling:$('profiling').value}:{})};
}
async function startRun(calibration='auto'){
  $('run').disabled=$('recalibrate').disabled=true;notice('');
  try{const job=await api('/api/runs',requestConfiguration(calibration));selectedRun=job.run_id;loadedReport=null;selectReport({run_id:job.run_id,status:'running',configuration:job.configuration,suite_revision:job.configuration.suite_revision,results:[]});await refresh();}
  catch(e){notice(e.message);if(status)showDevices();}
}
$('mode').onchange=$('preset').onchange=()=>{updateSettings();syncWatch();};
$('experiment').onchange=async()=>{continuous=false;await syncWatch();if($('experiment').value!=='none'){$('operations').value='matmul';$('mode').value='isolated';if($('preset').value==='sustained')$('preset').value='standard';}updateSettings();};
$('backend').onchange=async()=>{continuous=false;await syncWatch();if($('backend').value.includes('cpu')){$('mode').value='isolated';$('profiling').value='trace';}if($('backend').value==='executorch_cpu')$('experiment').value='none';if($('backend').value.endsWith('_resident')){$('operations').value='matmul';$('mode').value='isolated';if($('preset').value==='sustained')$('preset').value='standard';}updateSettings();};
$('workload-suite').onchange=()=>{layerRenderKey=null;updateSettings();renderLayers();syncWatch();};
$('execution-target').onchange=()=>{updateSettings();syncWatch();};
$('probe-device').onclick=async()=>{try{await api('/api/devices/probe',{serial:$('device').value});await refresh();}catch(e){notice(e.message);}};
$('download-profile').onclick=()=>{const p=status.device_profiles?.find(p=>p.serial===$('device').value);if(!p)return;const url=URL.createObjectURL(new Blob([JSON.stringify(redacted(p.profile),null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='device-profile-'+p.profile_id.slice(0,12)+'.json';a.click();URL.revokeObjectURL(url);};
$('profiling').onchange=()=>{updateSettings();syncWatch();};
$('device').onchange=()=>{chosenDevice=$('device').value;showDevices();syncWatch();};
$('operations').addEventListener('input',()=>{updateSettings();if(selectionError)syncWatch();});
$('operations').addEventListener('change',()=>syncWatch());
$('run').onclick=()=>startRun();$('recalibrate').onclick=()=>startRun('refresh');
$('stop').onclick=async()=>{
  continuous=false;await syncWatch();await refresh();
  const session=(status?.benchmark_sessions||[]).find(s=>!terminal.has(s.status));
  if(session){try{await api('/api/benchmark-sessions/'+session.session_id+'/stop',{});await refresh();}catch(e){notice(e.message);}renderSession();return;}
  const job=(status?.jobs || []).find(j=>!terminal.has(j.status));
  if(job)try{await api('/api/runs/'+job.run_id+'/stop',{});await refresh();}catch(e){notice(e.message);}
  renderSession();
};
function redacted(value){
  if(Array.isArray(value))return value.map(redacted);
  if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).filter(([k])=>!['serial','token','owner','path','hostname','identity'].includes(k)).map(([k,v])=>[k,redacted(v)]));
  return value;
}
$('download').onclick=()=>{if(currentReport)downloadReport({...redacted(currentReport),controller_diagnostics:redacted(selectedJob()?.diagnostics||{}),timing_definitions:timingScopes});};
$('retry').onclick=()=>retryRun(selectedRun);
function renderWatch(){
  $('continuous').textContent=continuous?'Disable continuous mode':'Enable continuous mode';
  $('continuous').setAttribute('aria-pressed',String(continuous));
  const lease=status?.watch_status?.find(w=>w.owner===watchOwner);
  $('watch-status').textContent=!continuous?'Continuous mode is off.':document.hidden?'Paused while this page is hidden.':lease?.state||'Watching while this page is visible · once per build and configuration.';
}
async function syncWatch(){
  if(!token)return;
  if(watchBusy){watchPending=true;return new Promise(resolve=>watchWaiters.push(resolve));}
  watchBusy=true;
  try{
    do{
      watchPending=false;
      if(!continuous||document.hidden||selectionError||!$('device').value){
        if(watchLeased){await api('/api/watch',{action:'disable',owner:watchOwner});watchLeased=false;watchConfiguration='';}
      }else{
        const {calibration,...configuration}=requestConfiguration(),key=JSON.stringify(configuration);
        const action=watchLeased&&watchConfiguration===key?'heartbeat':'enable';
        // Mark the potential lease before awaiting: pagehide must release even an in-flight enable.
        watchLeased=true;
        const leases=await api('/api/watch',{action,owner:watchOwner,...configuration});watchConfiguration=key;
        if(status)status.watch_status=leases;
        const run=leases.find(w=>w.owner===watchOwner)?.run_id;
        if(run&&run!==selectedRun){selectedRun=run;currentReport=null;loadedReport=null;}
      }
    }while(watchPending);
  }catch(e){
    continuous=false;
    try{await api('/api/watch',{action:'disable',owner:watchOwner});watchLeased=false;watchConfiguration='';}catch{}
    notice(e.message);
  }finally{watchBusy=false;renderWatch();renderSession();for(const resolve of watchWaiters.splice(0))resolve();}
}
$('continuous').onclick=async()=>{continuous=!continuous;await syncWatch();await refresh();};
document.addEventListener('visibilitychange',()=>syncWatch());
window.addEventListener('pagehide',()=>{if(watchLeased)fetch('/api/watch',{method:'POST',keepalive:true,headers:{'X-XRGPU-Session':token,'Content-Type':'application/json'},body:JSON.stringify({action:'disable',owner:watchOwner})}).catch(()=>{});});
setInterval(()=>{if(continuous)syncWatch();},10000);
$('layer-search').addEventListener('input',renderLayers);$('layer-filter').onchange=renderLayers;
$('refresh-history').onclick=()=>loadHistory(true);$('more-history').onclick=()=>loadHistory();
for(const id of ['baseline-run','comparison-run'])$(id).onchange=()=>{comparisonRequest++;comparison=null;$('comparison').replaceChildren(el('p','Choose Compare runs to inspect these reports.','empty'));$('compare').disabled=!$('baseline-run').value||!$('comparison-run').value||$('baseline-run').value===$('comparison-run').value;};
$('compare').onclick=async()=>{const request=++comparisonRequest;$('compare').disabled=true;try{const result=await api('/api/compare?baseline='+$('baseline-run').value+'&current='+$('comparison-run').value);if(request===comparisonRequest){comparison=result;renderComparison();}}catch(e){notice(e.message);}finally{$('compare').disabled=!$('baseline-run').value||!$('comparison-run').value||$('baseline-run').value===$('comparison-run').value;}};
const initialLayer=new URLSearchParams(location.search).get('layer');if(initialLayer)$('layer-search').value=initialLayer;
navigate();try{token=(await api('/api/session')).token;await refresh();if(selectedRun&&!currentReport)await openRun(selectedRun,location.hash.slice(1)||'benchmark');await loadHistory(true);setInterval(refresh,3000);}catch(e){notice(e.message+' — reload after the local service is available.');}

function inspectCase(id){
  const operation=entries().find(e=>e.id===id)?.operation;if(operation)openedOperations.add((currentReport?.run_id||'catalog')+':'+operation);
  $('layer-search').value=id;$('layer-filter').value='all';openedCases.add((currentReport?.run_id||'catalog')+':'+id);
  layerRenderKey=null;renderLayers();location.hash='layers';
  const target=document.getElementById('case-'+id);target?.scrollIntoView?.({block:'start'});target?.querySelector('summary')?.focus({preventScroll:true});
}
function caseButton(id,scope='xr'){const result=button(id,()=>inspectCase(id));result.className='case-link';result.dataset.focusKey=scope+':rank-case:'+id;return result;}
function ranking(kind,target,columns,rows){
  target.replaceChildren();
  if(!rows.length){target.append(el('p',kind==='correctness'?'Validation observations appear as cases finish.':'Run an isolated benchmark to inspect calibration-relative estimates.','empty'));return;}
  const order=rankingSort[kind],sorted=sortMeasurements(rows,order.key,order.direction),shown=rankingExpanded.has(kind)?sorted:sorted.slice(0,10);
  const wrap=table(columns.map(c=>c.label),shown.map(row=>columns.map(c=>c.render(row))),kind+' ranking');
  const headers=[...wrap.querySelectorAll('th')];
  headers.forEach((th,index)=>{
    const column=columns[index];if(column.numeric)th.dataset.numeric='true';if(!column.key)return;
    th.setAttribute('aria-sort',order.key===column.key?(order.direction===1?'ascending':'descending'):'none');
    const control=button(column.label+(order.key===column.key?(order.direction===1?' ↑':' ↓'):''),()=>{
      rankingSort[kind]={key:column.key,direction:order.key===column.key?-order.direction:-1};renderDashboard();
    });control.dataset.focusKey=kind+':sort:'+column.key;th.replaceChildren(control);
  });
  for(const row of wrap.querySelectorAll('tbody tr'))columns.forEach((c,i)=>{if(c.numeric)row.children[i].dataset.numeric='true';});
  target.append(wrap);
  if(rows.length>10){const expand=button(rankingExpanded.has(kind)?'Show top 10':'Show all '+rows.length+' observations',()=>{if(rankingExpanded.has(kind))rankingExpanded.delete(kind);else rankingExpanded.add(kind);renderDashboard();});expand.className='text-button';expand.dataset.focusKey=kind+':expand';target.append(expand);}
}
function renderDashboard(){
  renderKernelExperiment();preserveView(()=>{
  const diagnostics=currentReport?.diagnostics,stages=diagnostics?.stages_ms||{},grid=el('dl',undefined,'stage-grid');
  for(const [key,label] of [['preparation','Preparation'],['calibration','Calibration'],['warmup','Warmup'],['measurement','Measured execution'],['validation','CPU validation'],['pacing','Pacing'],['reporting','Report serialization'],['other','Other'],['total','Session wall']]){
    const block=el('div',undefined,key==='total'?'total':'');block.append(el('dt',label),el('dd',ms(key==='total'?diagnostics?.session_ms:stages[key])));grid.append(block);
  }
  $('stage-timings').replaceChildren(grid);if(!diagnostics)$('stage-timings').append(el('p',currentReport?'This report predates session-stage diagnostics.':'Session timings become available when a run starts.','hint'));
  const host=selectedJob()?.diagnostics||currentReport?.controller_diagnostics||{};
  $('controller-timings').replaceChildren(...[
    ['Report fetches',host.report_fetches],['Transferred',host.transferred_bytes===undefined?undefined:(host.transferred_bytes/1048576).toFixed(2)+' MiB'],
    ['Transfer wall',host.transfer_ms===undefined?undefined:ms(host.transfer_ms)],['Transfer failures',host.transfer_failures],
    ['Database wall',host.database_ms===undefined?undefined:ms(host.database_ms)],
  ].map(([name,v])=>el('span',name+' · '+(v??'Unavailable'))));
  ranking('correctness',$('correctness-ranking'),[
    {label:'Case',key:'case',render:r=>caseButton(r.case,'correctness:'+r.phase+':'+r.trial+':'+r.index)},
    {label:'Outcome',key:'outcome',render:r=>el('span',r.outcome+(r.flagged?' · flagged':''),'status '+(r.passed?'passed':'failed'))},
    {label:'Validation ms',key:'total_ms',numeric:true,render:r=>ms(r.total_ms)},
    {label:'Inference ms',key:'inference_ms',numeric:true,render:r=>ms(r.inference_ms)},
    {label:'CPU compare ms',key:'comparison_ms',numeric:true,render:r=>ms(r.comparison_ms)},
    {label:'Window',key:'phase',render:r=>r.phase+' · '+(r.trial+1)},
  ],correctnessObservations(entries()));
  const profile=currentReport?.calibration;
  $('calibration-status').textContent=(currentReport?.backend||$('backend').value).includes('cpu')?'CPU execution does not run GPU calibration or report GPU efficiency.':profile?.available
    ?`Calibrated ${date(profile.created_at)} · ${profile.compute_gflops.toFixed(1)} GFLOP/s · ${profile.memory_gbps.toFixed(1)} GB/s · empirical reference`
    :profile?'Calibration unavailable · '+(profile.reason||'No compatible profile'):'First run calibrates the device (up to 15 s). Compatible profiles are reused.';
  ranking('efficiency',$('efficiency-ranking'),[
    {label:'Case / variant',key:'case',render:r=>{const name=el('span');name.append(caseButton(r.case,'efficiency'),el('small',r.variant));return name;}},
    {label:'Est. efficiency',key:'efficiency_percent',numeric:true,render:r=>{const n=el('span',r.available?r.efficiency_percent.toFixed(1)+'%':'Unavailable');if(r.available&&r.efficiency_percent>100)n.append(el('small','Exceeds calibration'));return n;}},
    {label:'Achieved',key:'achieved',numeric:true,render:r=>r.available?(r.achieved<1?r.achieved.toPrecision(3):r.achieved.toFixed(1))+' '+r.achieved_unit:'—'},
    {label:'GPU span',key:'gpu_ms',numeric:true,render:r=>ms(r.gpu_ms)},
    {label:'Execution',key:'execution_ms',numeric:true,render:r=>ms(r.execution_ms)},
    {label:'Est. gap',key:'gap_ms',numeric:true,render:r=>ms(r.gap_ms)},
    {label:'Dispatches',key:'dispatches',numeric:true,render:r=>r.dispatches??'—'},
    {label:'Est. limit / evidence',key:'limiting_factor',render:r=>r.available?r.limiting_factor:r.reason},
  ],efficiencyRows(entries().filter(e=>e.rows.some(r=>r.phase==='isolated'))));
  const xr=$('xr-summary');xr.replaceChildren();
  if(currentReport?.configuration?.mode==='xr'){
    xr.append(el('h3','XR frame impact · paired windows'));
    const rows=(currentReport.xr_comparisons||[]).map(p=>[caseButton(p.case),p.trial+1,p.valid?ms(p.baseline_frame_p95_ms):'Unavailable',p.valid?ms(p.loaded_frame_p95_ms):'Unavailable',p.valid?ms(p.frame_p95_delta_ms):p.reason]);
    xr.append(rows.length?table(['Case','Pair','Baseline p95','Loaded p95','Change / evidence'],rows,'XR paired frame impact'):el('p','Paired frame observations appear when both windows finish.','empty'));
  }
});}
