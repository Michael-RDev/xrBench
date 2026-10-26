// Typed native operator reports are separate from the legacy FP32 model suite.
const hex=/^[a-f0-9]{64}$/;
const widths={float32:4,float16:2,int32:4,uint32:4,int16:2,int8:1,uint8:1,int64:8,bool:1};
const fail=(value,message)=>{if(!value)throw Error(message);};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const safeFile=value=>typeof value==='string'&&!value.startsWith('/')&&!value.includes('\\')&&value.split('/').every(p=>p&&p!=='.'&&p!=='..');
function tensor(t){
  fail(t&&typeof t.name==='string'&&widths[t.dtype]&&widths[t.storage_dtype]&&Array.isArray(t.shape)&&t.shape.length<=16,'Invalid native typed tensor');
  fail(t.shape.every(n=>Number.isSafeInteger(n)&&n>=0&&n<=0xffffffff),'Invalid native tensor shape');
  fail(t.shape.reduce((a,b)=>a*b,1)*widths[t.storage_dtype]<=64*1024**2&&safeFile(t.file)&&hex.test(t.sha256),'Invalid native tensor bytes/provenance');
}
export function validateNativeCatalog(c){
  fail(c?.kind==='xrbench.native-operator-suite'&&c.operator_count===221&&Array.isArray(c.cases)&&c.cases.length===221,'Expected all 221 native operators');
  fail(hex.test(c.suite_revision)&&hex.test(c.source_package_sha256)&&hex.test(c.native_catalog_sha256),'Missing native suite provenance');
  fail(new Set(c.cases.map(v=>v.id)).size===221,'Duplicate native operators');
  fail(c.runtime_files&&Object.entries(c.runtime_files).filter(([p])=>p.startsWith('shaders/')).length===315,'Expected complete native shader inventory');
  for(const row of c.cases){
    fail(/^(ai\.onnx|com\.microsoft)\.[A-Za-z0-9_]+$/.test(row.id)&&row.operation===row.id&&['legacy','extended'].includes(row.native_api),'Invalid native operator identity');
    fail(/^[a-f0-9]{40}$/.test(row.contract_revision)&&hex.test(row.contract_sha256),'Missing frozen operator contract identity');
    fail(Array.isArray(row.inputs)&&row.inputs.length<=32&&Array.isArray(row.outputs)&&row.outputs.length>0&&row.outputs.length<=16,'Invalid native port inventory');
    for(const t of [...row.inputs,...row.outputs])tensor(t);
    for(const output of row.outputs)fail(output.comparison&&Number.isFinite(output.comparison.atol)&&output.comparison.atol>=0&&Number.isFinite(output.comparison.rtol)&&output.comparison.rtol>=0&&typeof output.comparison.equal_nan==='boolean','Missing native comparison contract');
    fail(Array.isArray(row.shaders)&&row.shaders.length>0&&row.shaders.every(p=>safeFile(p)&&hex.test(c.runtime_files['shaders/'+p])),'Missing native pass dependency');
  }
  return c;
}
export function validateNativeManifest(m,canonical){
  validateNativeCatalog(m.catalog);
  fail(m.package==='org.xrgpu.benchmarks.web.s'+m.signing_certificate_sha256,'Native signing namespace mismatch');
  const c=m.configurations?.gpu;
  fail(c?.backend==='xrgpu_vulkan'&&c.mode==='isolated'&&c.preset==='standard'&&c.workload_suite==='native_operators'&&c.warmups===20&&c.samples===100&&c.calibration==='none'&&c.profiling==='timestamps'&&c.validation_control==='none'&&!c.experiment,'Invalid public native benchmark plan');
  fail(canonical(c.cases)===canonical(m.catalog.cases.map(row=>row.id))&&canonical(c.operations)===canonical(c.cases),'Incomplete public native operator plan');
  fail(same(m.backends,['xrgpu_vulkan'])&&!m.configurations.cpu,'Native GPU benchmark cannot fall back to CPU');
  for(const key of ['suite_revision','runner_revision','runtime_revision','apk_sha256','native_package_sha256','native_catalog_sha256'])fail(typeof m[key]==='string'&&m[key]&&c[key]===m[key],'Native release identity mismatch');
  fail(m.native_package_sha256===m.catalog.source_package_sha256&&m.native_catalog_sha256===m.catalog.native_catalog_sha256&&m.suite_revision===m.catalog.suite_revision,'Native catalog/package mismatch');
  return m;
}
export function validateNativeReport(report,config,catalog,{partial=false,canonical,allowSoftwareTest=false}={}){
  fail(report?.schema_version===3&&/^run-[a-f0-9]{32}$/.test(report.run_id)&&report.run_id===config.run_id,'Invalid native operator report identity');
  fail(canonical(report.configuration)===canonical(config),'Native report configuration mismatch');
  fail(['complete','failed','canceled',...(partial?['running']:[])].includes(report.status),'Invalid native operator report status');
  fail(report.backend==='xrgpu_vulkan'&&config.backend==='xrgpu_vulkan'&&config.mode==='isolated'&&report.execution_location===config.execution_target,'Native backend/target mismatch');
  for(const key of ['suite_revision','runtime_revision','native_package_sha256','native_catalog_sha256'])fail(report[key]===config[key],'Native report release identity mismatch');
  fail(allowSoftwareTest||report.test_only!==true,'Software test evidence cannot be submitted as device results');
  fail(report.device&&typeof report.device==='object'&&!Array.isArray(report.device)&&report.execution_context==='foreground_service','Missing native device/execution context');
  const start=Date.parse(report.started_at),end=Date.parse(report.finished_at);
  fail(Number.isFinite(start)&&(report.status==='running'||Number.isFinite(end)&&end>=start),'Invalid native report timestamps');
  fail(Array.isArray(report.results)&&report.results.length<=catalog.cases.length,'Invalid native result inventory');
  const cases=new Map(catalog.cases.map(c=>[c.id,c])),seen=new Set();
  for(const row of report.results){
    const expected=cases.get(row.case);fail(expected&&!seen.has(row.case)&&row.operation===row.case,'Unknown/duplicate native result');seen.add(row.case);
    fail(['passed','failed','unsupported','interrupted'].includes(row.status)&&row.phase==='isolated'&&row.trial===0,'Invalid native result status');
    for(const key of ['inputs','outputs','attrs','args','contract_revision','contract_sha256'])fail(canonical(row[key])===canonical(expected[key]),'Native tensor/contract descriptor mismatch');
    fail(Array.isArray(row.samples)&&row.samples.length<=config.samples,'Invalid native sample count');
    for(const sample of row.samples){
      fail(Number.isFinite(sample.execution_ms)&&sample.execution_ms>=0&&(sample.gpu_ms===null||Number.isFinite(sample.gpu_ms)&&sample.gpu_ms>=0),'Invalid native timing');
      if(!allowSoftwareTest)fail(Number.isInteger(sample.thermal_status)&&sample.thermal_status>=0&&sample.thermal_status<=6&&typeof sample.charging==='boolean'&&typeof sample.display_interactive==='boolean','Missing measured operating conditions');
    }
    if(row.status==='passed'){
      fail(row.samples.length===config.samples&&['correctness_before','correctness_after','correctness'].every(k=>row[k]?.passed===true),'Incomplete native correctness/samples');
      fail(row.runtime_info?.cpu_compute_fallback===false&&(row.runtime_info.backend==='vulkan'||allowSoftwareTest&&row.runtime_info.backend==='software_vulkan'),'Native GPU execution required');
      fail(allowSoftwareTest||row.runtime_info.test_only!==true,'Software runtime cannot qualify a device');
      fail(Array.isArray(row.actual_outputs)&&row.actual_outputs.length===expected.outputs.length,'Missing typed native outputs');
      row.actual_outputs.forEach((value,i)=>{const target=expected.outputs[i];for(const key of ['name','shape','dtype','storage_dtype'])fail(canonical(value[key])===canonical(target[key]),'Native output logical/storage metadata mismatch');fail(hex.test(value.sha256),'Missing native output digest');if(!target.storage_dtype.startsWith('float'))fail(value.sha256===target.sha256,'Exact native integer output mismatch');});
    }else fail(typeof row.error==='string'&&row.error.length>0||row.status==='interrupted','Missing native failure/unsupported reason');
    fail(Array.isArray(row.shaders),'Missing native shader identity');
    for(const shader of row.shaders)fail(safeFile(shader.file)&&shader.sha256===catalog.runtime_files[shader.file]&&expected.shaders.includes(shader.file.replace(/^shaders\//,'')),'Executed shader differs from release');
  }
  if(report.status==='complete')fail(seen.size===catalog.cases.length,'Completed native report must account for every operator');
  return report;
}
