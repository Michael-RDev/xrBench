const terminal=new Set(['complete','failed','canceled']);
export function clockLabel(ms){const seconds=Math.floor(Math.max(0,ms||0)/1000);return `${Math.floor(seconds/60).toString().padStart(2,'0')}:${(seconds%60).toString().padStart(2,'0')}`;}
export function chipEvidence(session,reports,catalog,now=Date.now()){
  if(!session)return null;
  const phase=session.phases[session.current_phase]||session.phases[0],job=phase?.run,report=reports[phase?.run_id];
  const progress=job?.progress||report?.progress||{};
  const active=!terminal.has(session.status),stamp=job?.progress_received_at;
  const stale=active&&(session.status==='disconnected'||(progress.sequence!==undefined&&Number.isFinite(stamp)&&now-stamp>15000));
  const cases=job?.configuration?.cases||phase?.configuration?.cases||catalog.map(c=>c.id);
  const rows=report?.results||[],count=Number(progress.completed_cases)||0;
  const phaseCases=Object.fromEntries(session.phases.map(p=>{
    const source=reports[p.run_id],plan=p.run?.configuration?.cases||p.configuration?.cases||catalog.map(c=>c.id);
    const progress=p.run?.progress||source?.progress||{},phaseRows=source?.results||[];
    const stopped=terminal.has(session.status)||terminal.has(p.run?.status||p.status);
    const state=id=>{const matches=phaseRows.filter(r=>r.case===id);if(matches.some(r=>r.status==='failed'||r.correctness?.passed===false))return 'failed';
    if(matches.some(r=>r.status==='passed'&&r.timing_valid===false))return 'attention';if(matches.some(r=>r.status==='skipped'))return 'skipped';if(matches.some(r=>r.status==='passed'))return 'passed';
    if(id===progress.current_case)return stopped||stale?'interrupted':p===phase?'running':'pending';return stopped&&matches.length?'interrupted':'pending';};
    return [p.processor,plan.map(id=>({id,state:state(id)}))];
  }));
  const current=rows.findLast(r=>r.case===progress.current_case);
  const recent=rows.filter(r=>r.status==='passed'&&r.timing_valid===true&&r.correctness?.passed===true).slice(-3).reverse().map(r=>{
    const values=(r.samples||[]).map(s=>s.execution_ms).filter(Number.isFinite).sort((a,b)=>a-b);
    const n=values.length;return {name:r.operation,ms:n?(values[Math.floor(n/2)]+values[Math.floor((n-1)/2)])/2:null,samples:n};
  });
  // Elapsed display time is separate from native measurement timestamps.
  const elapsed=Math.max(0,((session.finished_at||now/1000)-(session.created_at||now/1000))*1000);
  let eta=null;
  const durations=rows.filter(r=>r.status==='passed'&&r.case_wall_ms>0).map(r=>r.case_wall_ms).slice(-5).sort((a,b)=>a-b);
  if(!stale&&active&&progress.phase==='measurement'&&durations.length>=3)eta=durations[Math.floor(durations.length/2)]*Math.max(0,cases.length-count);
  const total=Object.values(phaseCases).reduce((n,cases)=>n+cases.length,0);
  const completed=session.phases.reduce((n,p)=>n+(Number(p.run?.progress?.completed_cases||reports[p.run_id]?.progress?.completed_cases)||0),0);
  return {id:session.session_id,active:active&&!stale,stale,processor:phase.processor,status:session.status,
    identity:session.identity,partial:session.partial,
    phase:progress.phase||'preparation',elapsed,eta,completed,total,
    percent:total?Math.min(100,Math.round(completed/total*100)):0,
    cases:phaseCases[phase.processor]||[],phaseCases,current:progress.current_case||'',samples:progress.current_samples||0,
    correctness:current?.correctness?.passed===true?'Passed':current?.correctness?.passed===false?'Failed':'Checking',recent,
    phases:session.phases.map(p=>({processor:p.processor,status:p.run?.status||p.status,reason:p.reason,
      counts:p.run?.counts||reports[p.run_id]?.counts})),error:session.error||phase.reason};
}

// Existing single-backend runs can be recovered here without visiting another UI.
export function standaloneChipEvidence(run,report,catalog,now=Date.now()){
  if(!run)return null;
  const processor=(run.configuration?.backend||report?.backend||'').includes('cpu')?'cpu':'gpu';
  return chipEvidence({session_id:run.run_id,status:run.status,created_at:run.created_at,finished_at:run.finished_at,
    identity:run.identity||report?.identity||{chip_name:report?.device?.soc_model||'Connected processor'},current_phase:0,
    error:run.error||run.message,phases:[{processor,status:run.status,run_id:run.run_id,run,configuration:run.configuration}]},
    {[run.run_id]:report},catalog,now);
}

export function renderChip(document,data,source='native'){
  const $=id=>document.getElementById(id);$('chip-dashboard').hidden=!data;$('chip-details').hidden=!data;if(!data)return;
  const simulated=source==='simulated';
  $('chip-dashboard').dataset.source=source;
  $('chip-source-title').textContent=simulated?'BENCHMARK PREVIEW':'ON-DEVICE COMPUTE';
  document.querySelector('.chip-live').textContent=simulated?'SIMULATED':'NATIVE';
  document.querySelector('.chip-now .micro-label').textContent=data.active?'NOW RUNNING':data.stale?'DEVICE STATUS':'RUN STATUS';
  document.querySelector('.chip-history .micro-label').textContent=simulated?'SAMPLE RESULTS':'RECENT RESULTS';
  document.querySelector('.chip-evidence summary').textContent=simulated?'Simulated case status details':'Case status details';
  $('chip-gauge').setAttribute('aria-label',simulated?'Simulated suite completed':'Suite completed');
  $('chip-dashboard').dataset.processor=data.processor;
  $('chip-kicker').textContent=data.stale?'Waiting for device':data.active?`${data.processor.toUpperCase()} · ${data.phase.replaceAll('_',' ')}`:data.status==='complete'?(data.partial?'Partial suite complete':'Benchmark complete'):data.status==='canceled'?'Benchmark stopped':'Run needs attention';
  if(simulated)$('chip-kicker').textContent=data.active?`${data.processor.toUpperCase()} · simulated progress`:data.status==='complete'?'Demo complete':'Demo stopped';
  $('chip-identity').textContent=[data.identity?.chip_name||'Identifying processor',data.identity?.execution_location?.replaceAll('_',' ')].filter(Boolean).join(' · ');
  $('chip-timer').textContent=clockLabel(data.elapsed);
  $('chip-eta').textContent=data.stale?'Progress paused':!data.active?'Results preserved':data.eta===null?'ETA · Estimating…':`Phase ETA · ~${clockLabel(data.eta)}`;
  $('chip-percent').textContent=data.percent+'%';$('chip-gauge').setAttribute('aria-valuenow',String(data.percent));
  $('chip-gauge').style.setProperty('--progress',data.percent+'%');
  $('chip-count').textContent=`${data.completed} / ${data.total} cases ${simulated?'simulated':'processed on device'}`;
  $('chip-operation').textContent=data.current||data.error||(data.stale?'Waiting for device':data.active?(simulated?'Preparing demo…':'Preparing native kernels…'):'Finished');
  $('chip-correctness').textContent=simulated?'Simulated correctness':data.current?`Correctness · ${data.correctness}`:'Native execution';
  $('chip-samples').textContent=`${data.samples} ${simulated?'synthetic ':''}samples`;
  for(const region of document.querySelectorAll('.chip-region')){
    const phase=data.phases.find(p=>p.processor===region.dataset.processor);
    region.hidden=!phase;
    const live=!!phase&&data.active&&data.processor===region.dataset.processor;
    region.dataset.active=String(live);region.querySelector('span').textContent=simulated?`Simulated · ${phase?.status||'pending'}`:data.stale&&data.processor===region.dataset.processor?'Waiting':phase?.status||'Not part of this run';
  }
  const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
  // Keep per-case native evidence available independently of the summary.
  const evidence=$('chip-case-evidence'),key=JSON.stringify([source,data.phaseCases]);
  if(evidence.dataset.evidence!==key){
    evidence.dataset.evidence=key;
    evidence.replaceChildren(...Object.entries(data.phaseCases).map(([processor,cases])=>{
      const group=node('section'),list=node('ul');group.append(node('h3',processor.toUpperCase()));
      list.append(...cases.map(c=>node('li',`${c.id} · ${simulated?'simulated ':''}${c.state}`)));group.append(list);return group;
    }));
  }
  $('chip-recent').replaceChildren(...data.recent.map(r=>{const line=node('div',undefined,'recent-line');line.append(node('span',r.name),node('span',r.ms===null?'—':r.ms.toFixed(2)+' ms'));return line;}));
  if(!data.recent.length)$('chip-recent').append(node('span',simulated?'Waiting for sample results':'Waiting for validated results','empty-recent'));
  $('processor-phases').replaceChildren(...data.phases.map(p=>{const el=node('div',undefined,'processor-phase');el.dataset.state=p.status;
    el.append(node('strong',p.processor.toUpperCase()),node('span',p.reason||`${p.status}${p.counts?` · ${p.counts.passed||0} passed`:''}`));return el;}));
}
