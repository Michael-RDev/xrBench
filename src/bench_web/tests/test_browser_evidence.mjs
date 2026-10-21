import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateReport, EvidenceError} from '../web/browser/evidence.mjs';
import {manifest, nativeReport} from './browser-fixtures.mjs';

test('malformed native evidence fails explicitly instead of being mistaken for a USB outage', () => {
  const m = manifest(), config = {...m.configurations.cpu, run_id: 'run-' + 'a'.repeat(32)};
  for (const mutate of [
    r => {r.results[0] = null;},
    r => {r.results[0].samples[0] = null;},
    r => {r.results[0].samples[0].execution_ms = Infinity;},
    r => {r.results[0].samples[0].execution_ms = true;},
    r => {r.results[0].validation_observations = {};},
    r => {r.calibration = null;},
    r => {r.diagnostics = null;},
    r => {r.results.pop();},
    r => {r.results[0].samples[0].gpu_ms = 1;},
    r => {r.results[0].correctness.passed = false;},
  ]) {
    const report = nativeReport(config); mutate(report);
    assert.throws(() => validateReport(report, config, m.catalog), EvidenceError);
  }
});
