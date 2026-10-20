import {terminal,casesForReport,caseState,caseCounts,timings,needsAttention,labels} from '/stats/view-model.mjs';

const names={linear:'Linear',matmul:'Matrix multiply',bmm:'Batched matrix multiply',relu:'ReLU',gelu:'GELU',silu:'SiLU',sigmoid:'Sigmoid',tanh:'Tanh',layer_norm:'Layer normalization',rms_norm:'RMS normalization',softmax:'Softmax',sum:'Sum',mean:'Mean',attention:'Attention',add:'Addition',mul:'Multiplication',conv2d:'Convolution',depthwise_conv2d:'Depthwise convolution',batch_norm:'Batch normalization',max_pool2d:'Max pooling',avg_pool2d:'Average pooling',adaptive_avg_pool2d:'Global average pooling',embedding:'Embedding',transpose_copy:'Transpose',concat:'Concatenation'};
const ms=value=>typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('en-US',{minimumFractionDigits:3,maximumFractionDigits:3})+' ms':'—';
export function runEvidence(status,job,report){
  // The job's progress can arrive before the next report snapshot.
  const evidence=job?{...report,status:job.status,configuration:report?.configuration||job.configuration,
    suite_revision:report?.suite_revision||status?.suite_revision,progress:job.progress||report?.progress}:report;
  const entries=casesForReport(status?.catalog?.cases||[],evidence,status?.suite_revision);
  const counts=caseCounts(entries,evidence);
  const done=counts.passed+counts.failed+counts.skipped+counts.interrupted;
  return {evidence,entries,counts,done};
}

export function renderResults(document,status,job,report,source='native'){
  const $=id=>document.getElementById(id),run=job||report;
  const simulated=source==='simulated';
  $('results').dataset.source=source;
  document.querySelector('.results-heading .eyebrow').textContent=simulated?'Simulated results · sample data':'Measured on your device';
  $('results-title').textContent=simulated?'A preview of every layer.':'Every layer. On your silicon.';
  $('results-link').hidden=simulated;
  document.querySelector('.results-note').textContent=simulated
    ? 'All timings and correctness outcomes below are simulated. These illustrative values are identical across device models and do not indicate hardware support or performance.'
    : 'Each layer shows its median latency range across measured input shapes. Expand a layer for individual cases. Warmups and correctness checks are excluded.';
  $('results').hidden=!run;
  if(!run)return;
  const {evidence,entries,counts,done}=runEvidence(status,job,report);
  const finished=terminal.has(run.status),current=entries.find(c=>c.id===evidence?.progress?.current_case);
  const phase=run.cancel_requested&&!finished?'Stopping':({starting:'Preparing headset',launching:'Launching',running:'Benchmarking',disconnected:'Waiting for connection',complete:'Run complete',failed:'Run failed',canceled:'Run stopped'}[run.status]||run.status);
  $('run-phase').textContent=simulated?`Demo · ${phase}`:phase;
  $('run-phase').dataset.state=run.status;
  $('current-layer').textContent=finished
    ? (run.status==='complete'?`${counts.passed} of ${entries.length} cases ${simulated?'have simulated passing outcomes':'passed correctness checks'}.`:report?.error||run.message||report?.reason||(simulated?'Partial sample results are shown below.':'Completed results are preserved below.'))
    : current?`${names[current.operation]||current.operation} · ${current.id}`:run.message||'Installing and opening the native benchmark app…';
  const preset=evidence?.configuration?.preset||'smoke';
  const device=report?.device?.model||'Connected device';
  const processor=(report?.backend||job?.configuration?.backend||'').includes('cpu')?'CPU':'GPU';
  $('run-context').textContent=simulated?`${device} · Simulated ${processor} · 20 synthetic samples per case`:`${device} · Native ${processor} · FP32 · ${evidence?.configuration?.mode==='xr'?'XR impact':preset==='sustained'?'Sustained measurement':`${preset==='standard'?'100':'20'} samples per case`}`;
  $('completion-count').textContent=`${done} / ${entries.length} cases processed`;
  $('suite-progress').max=entries.length||1;
  $('suite-progress').value=done;
  const node=(tag,text,className)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
  $('result-counts').replaceChildren(...[['passed','passed'],['failed','failed'],['skipped','unsupported'],['interrupted','interrupted']].filter(([key])=>key==='passed'||counts[key]).map(([key,label])=>node('span',`${counts[key]} ${label}`,`count-${key}`)));
  const open=new Set([...$('layer-results').querySelectorAll('details[open]')].map(el=>el.dataset.operation));
  const focused=document.activeElement?.closest('.layer-group')?.dataset.operation;
  const groups=new Map();
  for(const entry of entries){if(!groups.has(entry.operation))groups.set(entry.operation,[]);groups.get(entry.operation).push(entry);}
  const details=[];
  for(const [operation,cases] of groups){
    const states=cases.map(c=>caseState(c,evidence));
    const active=states.includes('running'),attention=cases.some(c=>needsAttention(c,evidence));
    const passed=states.filter(s=>s==='passed').length;
    const group=node('details',undefined,'layer-group');group.dataset.operation=operation;group.open=open.has(operation);
    const summary=node('summary');
    const title=node('span',names[operation]||operation,'layer-name');
    const marks=node('span',undefined,'case-marks');marks.setAttribute('aria-hidden','true');
    states.forEach(state=>{const mark=node('i');mark.dataset.state=state;marks.append(mark);});
    const state=node('span',active?'Running':attention?'Needs attention':passed===cases.length?'Passed':'Waiting','layer-state');state.dataset.state=active?'running':attention?'attention':passed===cases.length?'passed':'pending';
    const medians=cases.flatMap(c=>c.rows.filter(r=>r.phase==='isolated'&&r.status==='passed'&&r.timing_valid===true)
      .map(r=>timings(r,'execution_ms')?.median).filter(v=>typeof v==='number'));
    const range=medians.length?Math.min(...medians)===Math.max(...medians)?ms(medians[0]):`${ms(Math.min(...medians)).replace(' ms','')}–${ms(Math.max(...medians))}`:'—';
    const latency=node('span',range,'layer-latency');latency.setAttribute('aria-label',`${simulated?'Simulated median latency range':'Median latency range across measured cases'}: ${range}`);
    summary.append(title,node('span',`${passed} / ${cases.length}`,'layer-count'),latency,marks,state);group.append(summary);
    const scroll=node('div',undefined,'case-table-wrap'),table=node('table');
    const caption=node('caption',`${names[operation]||operation} ${simulated?'simulated ':''}benchmark cases`,'sr-only');
    const head=node('thead'),heading=node('tr');
    (simulated?['Case / input shape','Simulated status','Synthetic samples','Sample median','Sample p95','Sample GPU median']:['Case / input shape','Status','Samples','Median','p95','GPU median']).forEach(text=>{const th=node('th',text);th.scope='col';heading.append(th);});
    head.append(heading);const body=node('tbody');
    for(const entry of cases){
      const row=node('tr'),cell=node('td'),state=caseState(entry,evidence);
      cell.append(node('span',entry.id,'case-id'),node('small',(entry.inputs||[]).map(i=>(i.shape||[]).join(' × ')).join(' · '),'case-shape'));
      const result=entry.rows.find(r=>r.phase==='isolated')||entry.rows[0];
      const latency=result?timings(result,'execution_ms'):null,gpu=result?timings(result,'gpu_ms'):null;
      const invalid=result&&(result.timing_valid===false||result.thermal_discontinuity||result.focus_lost);
      const reason=result?.reason||result?.error;
      if(reason)cell.append(node('small',String(reason),'case-reason'));
      const statusCell=node('td',labels[state]||state);statusCell.dataset.state=state;
      if(invalid)statusCell.append(node('small','Timing flagged','case-reason'));
      // Only successful, valid observations are presented as performance results.
      const measured=state==='passed'&&!invalid;
      row.append(cell,statusCell,node('td',String(result?.samples?.length||0)),node('td',ms(measured?latency?.median:null)),node('td',ms(measured?latency?.p95:null)),node('td',ms(measured?gpu?.median:null)));
      body.append(row);
    }
    table.append(caption,head,body);scroll.append(table);group.append(scroll);details.push(group);
  }
  $('layer-results').replaceChildren(...details);
  if(focused)details.find(el=>el.dataset.operation===focused)?.querySelector('summary').focus({preventScroll:true});
}
