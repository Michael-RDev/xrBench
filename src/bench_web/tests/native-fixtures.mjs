// Synthetic protocol fixtures only. These are never device qualification evidence.
import {deviceCatalog} from './browser-fixtures.mjs';
export function nativeManifest(catalog) {
  const sha='a'.repeat(64), certificate='b'.repeat(64);
  catalog ??= {kind:'xrbench.native-operator-suite',operator_count:221,suite_revision:'c'.repeat(64),
    source_package_sha256:'d'.repeat(64),native_catalog_sha256:'e'.repeat(64),
    runtime_files:Object.fromEntries(Array.from({length:315},(_,i)=>[`shaders/test_${i}.spv`,sha])),
    cases:Array.from({length:221},(_,i)=>{const id=`ai.onnx.ProtocolFixture${i}`;
      const tensor=name=>({name,shape:[],dtype:'int64',storage_dtype:'int64',file:`cases/${id}/${name}.bin`,sha256:sha});
      return {id,operation:id,native_api:'extended',contract_revision:'f'.repeat(40),contract_sha256:sha,attrs:{},args:{},
        inputs:[tensor('X')],outputs:[{...tensor('Y'),comparison:{atol:0,rtol:0,equal_nan:false}}],shaders:[`test_${i}.spv`]};})};
  const m={protocol_version:2,package:'org.xrgpu.benchmarks.web.s'+certificate,activity:'org.xrgpu.benchmarks.MainActivity',
    apk_sha256:sha,apk:sha+'.apk',apk_bytes:3,min_sdk:29,abi:'arm64-v8a',version_code:1,signing_certificate_sha256:certificate,
    suite_revision:catalog.suite_revision,runner_revision:sha,runtime_revision:'f'.repeat(40),
    native_package_sha256:catalog.source_package_sha256,native_catalog_sha256:catalog.native_catalog_sha256,
    catalog,device_catalog:deviceCatalog,backends:['xrgpu_vulkan'],configurations:{}};
  m.configurations.gpu={backend:'xrgpu_vulkan',mode:'isolated',preset:'standard',workload_suite:'native_operators',
    warmups:20,samples:100,calibration:'none',profiling:'timestamps',validation_control:'none',execution_target:'headset',
    cases:catalog.cases.map(c=>c.id),operations:catalog.cases.map(c=>c.id)};
  for(const k of ['suite_revision','runner_revision','runtime_revision','apk_sha256','native_package_sha256','native_catalog_sha256'])m.configurations.gpu[k]=m[k];
  return m;
}
export function operatorReport(config,catalog) {
  return {schema_version:3,run_id:config.run_id,status:'complete',configuration:structuredClone(config),
    backend:'xrgpu_vulkan',execution_location:config.execution_target,execution_context:'foreground_service',
    ...Object.fromEntries(['suite_revision','runtime_revision','native_package_sha256','native_catalog_sha256'].map(k=>[k,config[k]])),
    started_at:'2026-10-26T00:00:00Z',finished_at:'2026-10-26T00:00:01Z',device:{model:'synthetic protocol fixture'},
    results:catalog.cases.map(c=>({case:c.id,operation:c.id,phase:'isolated',trial:0,status:'passed',
      ...Object.fromEntries(['inputs','outputs','attrs','args','contract_revision','contract_sha256'].map(k=>[k,structuredClone(c[k])])),
      correctness:{passed:true},correctness_before:{passed:true},correctness_after:{passed:true},
      runtime_info:{backend:'vulkan',cpu_compute_fallback:false},
      actual_outputs:c.outputs.map(({name,shape,dtype,storage_dtype,sha256})=>({name,shape,dtype,storage_dtype,sha256})),
      shaders:[{file:'shaders/'+c.shaders[0],sha256:catalog.runtime_files['shaders/'+c.shaders[0]]}],
      samples:Array.from({length:config.samples},()=>({execution_ms:1,gpu_ms:null,thermal_status:0,charging:false,display_interactive:false}))}))};
}
