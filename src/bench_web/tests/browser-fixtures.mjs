import {readFile} from 'node:fs/promises';
export const catalog = JSON.parse(await readFile(new URL('../../../layers/catalog.json', import.meta.url)));
export const deviceCatalog = JSON.parse(await readFile(new URL('../../../engine/device-catalog.json', import.meta.url)));
export function manifest() {
  const m = {protocol_version: 1, package: 'org.xrgpu.benchmarks.web', activity: 'org.xrgpu.benchmarks.MainActivity',
    apk_sha256: 'a'.repeat(64), apk: 'a'.repeat(64) + '.apk', apk_bytes: 3, min_sdk: 29, abi: 'arm64-v8a',
    version_code: 1, signing_certificate_sha256: 'b'.repeat(64), suite_revision: 'suite', runner_revision: 'runner', runtime_revision: 'runtime',
    catalog, device_catalog: deviceCatalog, backends: ['executorch_cpu', 'executorch_vulkan'], configurations: {}};
  for (const [p, backend] of [['cpu', 'executorch_cpu'], ['gpu', 'executorch_vulkan']]) m.configurations[p] = {
    mode: 'isolated', preset: 'smoke', operations: [...new Set(catalog.cases.map(c => c.operation))], calibration: 'auto', backend,
    execution_target: 'headset', workload_suite: 'standard', validation_control: 'none', cases: catalog.cases.map(c => c.id),
    suite_revision: m.suite_revision, runtime_revision: m.runtime_revision, runner_revision: m.runner_revision, apk_sha256: m.apk_sha256};
  return m;
}
export function nativeReport(config, status = 'complete') {
  return {schema_version: 2, run_id: config.run_id, status, backend: config.backend, execution_location: config.execution_target,
    configuration: structuredClone(config), suite_revision: config.suite_revision, runtime_revision: config.runtime_revision,
    started_at: '2026-10-20T00:00:00Z', ...(status === 'running' ? {} : {finished_at: '2026-10-20T00:00:01Z'}), device: {model: 'synthetic'},
    results: config.cases.map(id => ({case: id, phase: 'isolated', trial: 0, operation: catalog.cases.find(c => c.id === id).operation,
      status: 'passed', correctness: {passed: true}, samples: [{execution_ms: 1, model_ms: .8, ...(config.backend.includes('cpu') ? {} : {gpu_ms: .5})}]}))};
}
export class MemoryStore {
  constructor() {this.items = []; this.reports = new Map(); this.outbox = new Map();}
  async sessions() {return structuredClone(this.items);}
  async save(s) {const i = this.items.findIndex(v => v.session_id === s.session_id); if (i < 0) this.items.push(structuredClone(s)); else this.items[i] = structuredClone(s);}
  async saveReport(id, r) {this.reports.set(id, structuredClone(r));}
  async report(id) {return structuredClone(this.reports.get(id));}
  async deliveries() {return structuredClone([...this.outbox.values()]);}
  async saveDelivery(row) {this.outbox.set(row.run_id, structuredClone(row));}
}
