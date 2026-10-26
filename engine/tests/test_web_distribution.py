import copy
import hashlib
import json
import os
import shutil
import subprocess

import pytest

from engine.controller import Controller
from engine.devices import identify
from engine.paths import ROOT
from engine.reports import save, summarize, validate
from engine.web_distribution import publish
from engine.kernel_package import provenance
from engine.workloads import catalog, revision


@pytest.fixture
def web_build(tmp_path):
    build = tmp_path / 'runner'
    build.mkdir()
    data = b'synthetic-only-test-apk'
    (build / 'xrgpu-benchmarks-web.apk').write_bytes(data)
    metadata = dict(package='org.xrgpu.benchmarks.web', apk_sha256=hashlib.sha256(data).hexdigest(),
                    suite_revision=revision(), runtime_revision='test-runtime', runner_revision='test-runner',
                    backends=['executorch_cpu', 'executorch_vulkan'], version_code=1,
                    signing_certificate_sha256='b' * 64, kernel_package=provenance())
    save(build / 'web-apk.json', metadata)
    return build


def test_distribution_uses_existing_configuration_and_matched_assets(tmp_path, web_build):
    output = tmp_path / 'site'
    result = publish(output, web_build)
    assert (output / 'runner' / result['apk']).read_bytes() == b'synthetic-only-test-apk'
    assert result['catalog'] == catalog()
    assert result['kernel_package'] == provenance()
    save(web_build / 'apk.json', json.loads((web_build / 'web-apk.json').read_text()))
    owner = type('Config', (), {'build': web_build})()
    for p, backend in [('cpu', 'executorch_cpu'), ('gpu', 'executorch_vulkan')]:
        expected, _ = Controller.configuration(owner, dict(backend=backend, mode='isolated', preset='standard', execution_target='unknown'))
        assert result['configurations'][p] == expected
        assert expected['kernel_package'] == result['kernel_package']
        assert len(expected['cases']) == 63
        assert expected['calibration'] == 'auto'
        assert expected.get('profiling', 'trace') == 'trace'


@pytest.mark.parametrize('kind', ['checksum', 'package', 'revision', 'signature', 'kernels'])
def test_distribution_rejects_unmatched_or_unsigned_runners(tmp_path, web_build, kind):
    path = web_build / 'web-apk.json'
    value = json.loads(path.read_text())
    key = dict(checksum='apk_sha256', package='package', revision='suite_revision', signature='signing_certificate_sha256', kernels='kernel_package')[kind]
    value[key] = ''
    save(path, value)
    with pytest.raises(ValueError):
        publish(tmp_path / 'site', web_build)


def test_production_bundle_never_contacts_a_local_service(web_build):
    if not shutil.which('node') or not (ROOT / 'node_modules/esbuild').is_dir():
        pytest.skip('npm ci required for production bundle checks')
    import sys
    output = ROOT / 'artifacts/web-distribution-test'
    env = dict(os.environ, XRBENCH_WEB_OUTPUT=str(output), XRBENCH_RUNNER_BUILD=str(web_build), XRBENCH_PYTHON=sys.executable)
    subprocess.run(['node', 'scripts/build-web.mjs'], cwd=ROOT, env=env, check=True, capture_output=True, text=True)
    checked = subprocess.run(['node', '--experimental-vm-modules', 'scripts/verify-web.mjs', str(output)],
                             cwd=ROOT, check=True, capture_output=True, text=True)
    assert 'no localhost/API requests' in checked.stdout


def browser_contract(payload):
    if not shutil.which('node'):
        pytest.skip('Node required for cross-language contract checks')
    source = """
import {validateReport,summarize,identify} from './src/bench_web/web/browser/evidence.mjs';
let text='';for await(const chunk of process.stdin)text+=chunk;
const p=JSON.parse(text);let result;
try{validateReport(p.report,p.config,p.catalog);const s=summarize(p.report);
result={valid:true,counts:s.counts,timings:s.results.map(r=>r.timings),timing_valid:s.results.map(r=>r.timing_valid)};}
catch(e){result={valid:false};}
if(p.device)result.identity=identify(p.device,p.device_catalog);
console.log(JSON.stringify(result));
"""
    result = subprocess.run(['node', '--input-type=module', '-e', source], input=json.dumps(payload),
                            text=True, capture_output=True, cwd=ROOT, check=True)
    return json.loads(result.stdout)


def test_browser_and_python_agree_on_standard_report_validation_and_summaries(report):
    report = copy.deepcopy(report)
    report['configuration'].update(backend=report['backend'], execution_target='headset', run_id=report['run_id'],
                                   suite_revision=report['suite_revision'], runtime_revision=report['runtime_revision'])
    cases = dict(cases=[dict(id='linear_odd', operation='linear')])
    expected = summarize(validate(copy.deepcopy(report)))
    actual = browser_contract(dict(report=report, config=report['configuration'], catalog=cases))
    assert actual == dict(valid=True, counts=expected['counts'], timings=[r['timings'] for r in expected['results']],
                          timing_valid=[r['timing_valid'] for r in expected['results']])
    for mutate in [lambda r: r.update(schema_version=9), lambda r: r['results'][0]['samples'][0].update(execution_ms=-1),
                   lambda r: r['results'][0]['correctness'].update(passed=False),
                   lambda r: r['results'].append(copy.deepcopy(r['results'][0])),
                   lambda r: r.update(backend='executorch_cpu')]:
        bad = copy.deepcopy(report)
        mutate(bad)
        with pytest.raises(ValueError):
            validate(bad)
        assert not browser_contract(dict(report=bad, config=report['configuration'], catalog=cases))['valid']


@pytest.mark.parametrize('change', ['valid', 'thermal', 'charging', 'correctness', 'timestamp_domain'])
def test_optimization_summaries_preserve_samples_and_exclude_invalid_timings(report, change):
    report['configuration'].update(backend=report['backend'], execution_target='headset', run_id=report['run_id'],
                                   suite_revision=report['suite_revision'], runtime_revision=report['runtime_revision'], preset='standard')
    row = report['results'][0]
    row.update(thermal_start=0, charging_start=False)
    row['samples'] = [dict(execution_ms=i + 1, gpu_span_ms=.5, thermal_status=0, charging=False,
                           gpu_span_scope='single graph, common queue timestamp domain') for i in range(100)]
    if change == 'thermal':
        row['samples'][-1]['thermal_status'] = 1
    if change == 'charging':
        row['samples'][-1]['charging'] = True
    if change == 'correctness':
        row.update(status='failed', correctness={'passed': False})
    if change == 'timestamp_domain':
        row['samples'][-1].pop('gpu_span_scope')
    expected = summarize(validate(copy.deepcopy(report)))['results'][0]
    actual = browser_contract(dict(report=report, config=report['configuration'], catalog=dict(cases=[dict(id='linear_odd', operation='linear')])))
    assert actual['valid']
    assert actual['timing_valid'] == [expected['timing_valid']]
    assert actual['timings'] == [expected['timings']]
    assert expected['samples'] == row['samples']
    if change in ('thermal', 'charging', 'correctness'):
        assert not expected['timing_valid'] and all(value is None for value in expected['timings'].values())
    else:
        assert expected['timings']['execution_ms']['median'] == 50.5
        assert expected['timings']['execution_ms']['p95'] == pytest.approx(95.05)
        assert (expected['timings']['gpu_span_ms'] is None) == (change == 'timestamp_domain')


@pytest.mark.parametrize('device', [dict(model='Quest_3'), dict(model='Quest 3S'),
    dict(model='Quest 3', soc_model='Apple M5'), dict(model='XREAL Aura'),
    dict(model='XREAL Aura', soc_model='XREAL X1S'), dict(model='Quest 30')])
def test_browser_device_identity_matches_python(device, report):
    from engine.devices import CATALOG
    expected = identify(device)
    actual = browser_contract(dict(report=report, config=report['configuration'], catalog=catalog(), device=device, device_catalog=CATALOG))['identity']
    for field in ('device_id', 'chip_id', 'execution_location', 'identity_source'):
        assert actual[field] == expected[field]


@pytest.mark.parametrize('native', [{}, {'gpu': 'Detected GPU'}, {'vulkan': False},
    {'backends': {'executorch_vulkan': {'compatible': True}}}, {'soc_model': 'Apple M5'}])
def test_browser_and_python_agree_on_gpu_absence_policy(native, report, monkeypatch):
    from engine.devices import CATALOG
    chip = next(c for c in CATALOG['chips'] if c['id'] == 'xr2-gen2')
    monkeypatch.setitem(chip, 'gpu_presence', 'absent')
    monkeypatch.setitem(chip, 'gpu_presence_source', 'https://fixture.invalid/no-gpu-spec')
    device = dict(model='Quest 3', **native)
    expected = identify(device)
    actual = browser_contract(dict(report=report, config=report['configuration'], catalog=catalog(), device=device, device_catalog=CATALOG))['identity']
    for field in ('gpu_presence', 'gpu_presence_source', 'cpu_eligible'):
        assert actual[field] == expected[field]
