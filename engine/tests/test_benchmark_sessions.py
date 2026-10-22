import copy
from types import SimpleNamespace

import pytest

from engine.controller import Controller
from engine.devices import identify
from engine.reports import save
from layers import revision


@pytest.fixture
def controller(tmp_path):
    from engine.kernel_package import provenance
    save(tmp_path/'apk.json',dict(suite_revision=revision(),apk_sha256='a'*64,runtime_revision='et',runner_revision='runner',backends=['executorch_cpu','executorch_vulkan'],kernel_package=provenance()))
    adb=SimpleNamespace(devices=lambda:dict(devices=[dict(serial='one',model='Quest 3',state='device',supported=True)]),stop=lambda *a:None)
    return Controller(adb,None,tmp_path/'runs',tmp_path,start_workers=False)


def create(controller, legacy=True):
    public=controller.create_session(dict(serial='one',request_id='request_identity_1234'))
    s=controller.benchmark_sessions[public['session_id']]
    if legacy:
        # A persisted pre-upgrade CPU→GPU plan must still recover unchanged.
        gpu = s['phases'][0]
        config, _ = controller.configuration(dict(backend='executorch_cpu', mode='isolated', preset='smoke', execution_target=s['identity']['execution_location']))
        s['phases'] = [dict(processor='cpu', backend='executorch_cpu', status='pending', configuration=config, run_id='run-'+'f'*32), gpu]
        del s['processor']
    s.update(capabilities_checked=True,capabilities={'backends':{b:dict(compatible=True) for b in ['executorch_cpu','executorch_vulkan']}})
    controller.save_session(s)
    return s


def test_exactly_once_and_reserved_between_phases(controller):
    s=create(controller)
    assert controller.create_session(dict(serial='one',request_id='request_identity_1234'))['session_id']==s['session_id']
    controller.session_tick(s['session_id'])
    first=s['phases'][0]['run_id']
    assert controller.jobs[first]['configuration']['backend']=='executorch_cpu'
    for _ in range(3):controller.session_tick(s['session_id'])
    assert len(controller.jobs)==1
    controller.update(first,status='complete')
    controller.session_tick(s['session_id'])
    with pytest.raises(ValueError,match='session'):controller.create(dict(serial='one'))
    controller.session_tick(s['session_id'])
    second=s['phases'][1]['run_id']
    assert controller.jobs[second]['configuration']['backend']=='executorch_vulkan'
    controller.update(second,status='complete')
    controller.session_tick(s['session_id'])
    assert s['status']=='complete'
    assert len(controller.jobs)==2
    assert 'serial' not in controller.session_view(s['session_id'])


def test_stop_between_phases_never_launches_gpu(controller):
    s=create(controller);controller.session_tick(s['session_id'])
    controller.update(s['phases'][0]['run_id'],status='complete')
    controller.session_tick(s['session_id']);controller.stop_session(s['session_id']);controller.session_tick(s['session_id'])
    assert s['status']=='canceled'
    assert len(controller.jobs)==1


@pytest.mark.parametrize('status',['failed','canceled','disconnected'])
def test_failure_and_disconnect_never_advance(controller,status):
    s=create(controller);controller.session_tick(s['session_id'])
    controller.update(s['phases'][0]['run_id'],status=status)
    controller.session_tick(s['session_id'])
    assert s['status']==status
    assert len(controller.jobs)==1
    if status=='disconnected':
        controller.update(s['phases'][0]['run_id'],status='complete')
        controller.session_tick(s['session_id']);controller.session_tick(s['session_id'])
        assert len(controller.jobs)==2


def test_restart_recovers_same_child_and_reservation(controller):
    s=create(controller);controller.session_tick(s['session_id'])
    child=s['phases'][0]['run_id'];controller.update(child,status='disconnected')
    restored=Controller(controller.adb,None,controller.state,controller.build,start_workers=False)
    restored.session_tick(s['session_id'])
    assert len(restored.jobs)==1
    assert restored.session_reserved('one')
    restored.update(child,status='complete');restored.session_tick(s['session_id']);restored.session_tick(s['session_id'])
    assert list(restored.jobs)[0]==child and len(restored.jobs)==2


def test_missing_gpu_retains_cpu_without_fallback(controller):
    s=create(controller);s['capabilities']['backends']['executorch_vulkan']={'compatible':False,'reason':'No GPU compute'}
    controller.session_tick(s['session_id']);controller.update(s['phases'][0]['run_id'],status='complete')
    controller.session_tick(s['session_id']);controller.session_tick(s['session_id'])
    assert s['status']=='complete' and s['partial']
    assert s['phases'][1]['status']=='unsupported'
    assert len(controller.jobs)==1


def test_changed_build_prevents_second_phase(controller):
    s=create(controller);controller.session_tick(s['session_id'])
    controller.update(s['phases'][0]['run_id'],status='complete');controller.session_tick(s['session_id'])
    import json
    path=controller.build/'apk.json';meta=json.loads(path.read_text());meta['runner_revision']='different';save(path,meta)
    controller.session_tick(s['session_id'])
    assert s['status']=='failed' and len(controller.jobs)==1


def test_exact_chip_mapping_conflict_and_companion():
    assert identify({'model':'Quest_3'})['chip_id']=='xr2-gen2'
    assert identify({'model':'Quest 3S'})['visual_model']=='quest-3s'
    assert identify({'model':'Quest 30'})['chip_id'] is None
    bad=identify({'model':'Quest 3','soc_model':'Apple M5'})
    assert bad['identity_source']=='conflicting' and bad['chip_id'] is None
    aura=identify({'model':'XREAL Aura'})
    assert aura['execution_location']=='compute_puck'
    assert aura['chips'][1]['location']=='glasses'
    assert identify({'model':'Apple Vision Pro'})['chip_id'] is None
    assert identify({'model':'Apple Vision Pro','soc_model':'Apple M5'})['chip_id']=='apple-m5'


def test_identifier_conflicts_and_executing_component():
    assert identify({'model':'generic','product':'Quest_3S'})['device_id']=='quest-3s'
    assert identify({'model':'Quest 3','device':'Quest_3S'})['identity_source']=='conflicting'
    assert identify({'model':'unknown','soc_model':'unknown'})['chip_name']=='Processor not identified'
    spatial=identify({'model':'XREAL Aura','soc_model':'XREAL X1S'})
    assert spatial['execution_location']=='glasses'
    assert [c['executing'] for c in spatial['chips']]==[False,True]
    assert identify({'model':'Vision Pro','soc_model':'Qualcomm Snapdragon XR2 Gen 2'})['identity_source']=='conflicting'


def test_multiple_devices_require_explicit_selection_even_when_only_one_authorized(controller):
    devices=[dict(serial='one',model='Quest 3S',state='device',supported=True),dict(serial='two',model='Quest 3',state='unauthorized',supported=False)]
    controller.adb.devices=lambda:dict(devices=devices)
    with pytest.raises(ValueError,match='Select'):controller.create_session(dict(request_id='request_identity_1234'))
    assert controller.create_session(dict(serial='one',request_id='request_identity_1234'))['identity']['device_id']=='quest-3s'


def test_terminal_progress_cannot_advance_before_final_report_recovery(controller):
    s=create(controller);controller.session_tick(s['session_id']);child=s['phases'][0]['run_id']
    def read(serial,run_id,name):
        if name=='progress.json':return dict(run_id=child,status='complete',completed_cases=63)
        # Exercise a session tick in the gap between the two device reads.
        controller.session_tick(s['session_id'])
        assert s['current_phase']==0
        raise OSError('Disconnected before final report transfer')
    controller.adb.read=read
    waits=iter([False,True]);controller.stopping=SimpleNamespace(wait=lambda _:next(waits))
    controller.monitor(child);controller.session_tick(s['session_id'])
    assert s['status']=='disconnected' and len(controller.jobs)==1


def test_stop_during_install_does_not_probe_or_launch(controller):
    s=controller.create_session(dict(serial='one',request_id='request_identity_1234'))
    controller.adb.install=lambda *a:controller.stop_session(s['session_id'])
    controller.adb.probe=lambda *a:pytest.fail('Probe must not start after cancellation')
    controller.monitor_session(s['session_id'])
    assert controller.session_view(s['session_id'])['status']=='canceled'
    assert not controller.jobs


def test_disconnect_between_phases_holds_reservation_until_reconnected(controller):
    s=create(controller);controller.session_tick(s['session_id'])
    controller.update(s['phases'][0]['run_id'],status='complete');controller.session_tick(s['session_id'])
    connected=controller.adb.devices;controller.adb.devices=lambda:dict(devices=[])
    controller.session_tick(s['session_id'])
    assert s['status']=='disconnected' and len(controller.jobs)==1 and controller.session_reserved('one')
    controller.adb.devices=connected;controller.session_tick(s['session_id'])
    assert len(controller.jobs)==2 and s['status']=='running' and 'error' not in s


def test_new_sessions_default_to_gpu_and_never_queue_cpu(controller):
    s = create(controller, legacy=False)
    assert s['processor'] == 'gpu'
    assert [p['processor'] for p in s['phases']] == ['gpu']
    assert len(s['phases'][0]['configuration']['cases']) == 63
    controller.session_tick(s['session_id'])
    child = s['phases'][0]['run_id']
    assert controller.jobs[child]['configuration']['backend'] == 'executorch_vulkan'
    controller.update(child, status='complete')
    controller.session_tick(s['session_id'])
    assert s['status'] == 'complete' and len(controller.jobs) == 1
    with pytest.raises(ValueError, match='processor plan'):
        controller.create_session(dict(serial='one', request_id=s['request_id'], processor='cpu'))


def test_gpu_unavailable_is_not_permission_to_run_cpu(controller):
    s = create(controller, legacy=False)
    s['capabilities']['backends']['executorch_vulkan'] = dict(compatible=False, reason='Driver unavailable')
    controller.session_tick(s['session_id'])
    assert not controller.jobs and s['partial']
    assert s['error'] == 'Driver unavailable'
    with pytest.raises(ValueError, match='confirmed to have no GPU'):
        controller.create_session(dict(serial='one', request_id='another_identity_1234', processor='cpu'))
    with pytest.raises(ValueError, match='Processor'):
        controller.create_session(dict(serial='one', request_id='another_identity_1234', processor='npu'))


@pytest.fixture
def no_gpu_chip(monkeypatch):
    from engine.devices import CATALOG
    chip = next(c for c in CATALOG['chips'] if c['id'] == 'xr2-gen2')
    # Test-only hardware policy; the production catalog is never marked absent.
    monkeypatch.setitem(chip, 'gpu_presence', 'absent')
    monkeypatch.setitem(chip, 'gpu_presence_source', 'https://fixture.invalid/no-gpu-spec')
    return chip


@pytest.mark.parametrize('gpu_evidence', [{}, {'gpu': 'Detected GPU'}, {'vulkan': True},
    {'physical_device_count': 1}, {'backends': {'executorch_vulkan': {'compatible': True}}}])
def test_explicit_cpu_is_rechecked_against_native_evidence(controller, no_gpu_chip, gpu_evidence):
    public = controller.create_session(dict(serial='one', request_id='cpu_request_identity', processor='cpu'))
    s = controller.benchmark_sessions[public['session_id']]
    s.update(capabilities_checked=True, capabilities={
        'backends': {'executorch_cpu': {'compatible': True}, 'executorch_vulkan': {'compatible': False}}, **gpu_evidence})
    controller.session_tick(s['session_id'])
    if gpu_evidence:
        assert not controller.jobs and s['status'] == 'failed'
    else:
        assert len(controller.jobs) == 1 and s['phases'][0]['processor'] == 'cpu'
        assert controller.create_session(dict(serial='one', request_id=s['request_id'], processor='cpu'))['session_id'] == s['session_id']
        with pytest.raises(ValueError, match='processor plan'):
            controller.create_session(dict(serial='one', request_id=s['request_id']))


def test_cpu_still_requires_native_cpu_backend(controller, no_gpu_chip):
    public = controller.create_session(dict(serial='one', request_id='cpu_request_identity', processor='cpu'))
    s = controller.benchmark_sessions[public['session_id']]
    s.update(capabilities_checked=True, capabilities={'backends': {'executorch_cpu': {'compatible': False, 'reason': 'CPU unavailable'}}})
    controller.session_tick(s['session_id'])
    assert not controller.jobs and s['phases'][0]['status'] == 'unsupported'


def test_absence_needs_source_and_unambiguous_executing_chip(no_gpu_chip):
    from engine.devices import gpu_eligibility
    assert not gpu_eligibility({'gpu_presence': 'absent'})['cpu_eligible']
    assert not gpu_eligibility(no_gpu_chip, conflict=True)['cpu_eligible']
    assert identify({'model': 'Quest 3'})['cpu_eligible']
    assert not identify({'model': 'Quest 3', 'soc_model': 'Apple M5'})['cpu_eligible']
    assert not identify({'model': 'Vision Pro'})['cpu_eligible']
