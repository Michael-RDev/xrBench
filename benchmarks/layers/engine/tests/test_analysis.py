import copy

import pytest

from benchmarks.layers.engine.analysis import compare
from benchmarks.layers.engine.reports import digest


@pytest.fixture
def pair(report):
    report['timing_scope']='native-v1'
    report['device'].update(gpu='test-gpu',driver=12,os='test-os',precision='fp32',variant='stock')
    report['results'][0].update(thermal_start=0,charging_start=False)
    other=copy.deepcopy(report);other['run_id']='run-'+'b'*32
    other['configuration']['apk_sha256']='b'*64
    other['results'][0]['samples'][0]['execution_ms']=.75
    return report,other


def test_observed_deltas_and_missing_provenance_do_not_rewrite_evidence(pair):
    a,b=pair;before=[digest(r) for r in pair]
    result=compare(a,b)
    assert result['compatible']
    m=result['results'][0]['metrics']['execution_ms']
    assert m['valid'] and m['median_change_percent']==-25
    assert m['p95_delta_ms']==-.25
    assert not result['results'][0]['metrics']['input_staging_ms']['valid']
    assert [digest(r) for r in pair]==before
    assert result['baseline']['apk_sha256'] is None


@pytest.mark.parametrize('change', ['gpu','driver','os','precision','variant','suite','mode','preset','scope','active'])
def test_incompatible_runs_never_show_deltas(pair,change):
    a,b=pair
    if change in b['device']:b['device'][change]='different'
    elif change in ('mode','preset'):b['configuration'][change]='different'
    elif change=='suite':b['suite_revision']='different'
    elif change=='scope':b.pop('timing_scope')
    elif change=='active':b['status']='running'
    result=compare(a,b)
    assert not result['compatible'] and result['reasons']
    assert result['results'][0]['metrics']['execution_ms']['median_delta_ms'] is None


@pytest.mark.parametrize('change',['failed','unsupported','interrupted','correctness','before','thermal','focus','charging','missing_power','flag','inputs'])
def test_individual_invalid_evidence_is_withheld(pair,change):
    a,b=pair;r=b['results'][0]
    if change in ('failed','interrupted'):r['status']=change
    elif change=='unsupported':r['status']='skipped'
    elif change=='correctness':r['correctness']['passed']=False
    elif change=='before':r['correctness_before']={'passed':False}
    elif change=='thermal':r['thermal_discontinuity']=True
    elif change=='focus':r['focus_lost']=True
    elif change=='charging':r['charging_start']=True
    elif change=='missing_power':r.pop('thermal_start')
    elif change=='flag':r['timing_valid']=False
    elif change=='inputs':r['inputs']=[{'shape':[999]}]
    result=compare(a,b)
    assert result['compatible']
    assert not result['results'][0]['valid']
    assert result['results'][0]['metrics']['model_ms']['median_delta_ms'] is None


def test_missing_case_zero_duration_and_legacy_metadata(pair):
    a,b=pair;b['results'][0]['case']='another-case'
    assert all(not r['valid'] for r in compare(a,b)['results'])
    b['results'][0]['case']=a['results'][0]['case']
    a['results'][0]['samples'][0]['execution_ms']=0
    assert compare(a,b)['results'][0]['metrics']['execution_ms']['median_change_percent'] is None
    b['device']={'model':'legacy'}
    assert not compare(a,b)['compatible']


def test_xr_requires_same_scene(pair):
    a,b=pair
    for r in pair:
        r['configuration']['mode']='xr';r.update(scene_revision='scene1',scene_settings={'width':1440},refresh_hz=72,inference_hz=10)
    assert compare(a,b)['compatible']
    b['scene_settings']['width']=1024
    assert not compare(a,b)['compatible']
