import json
from types import SimpleNamespace

import pytest

from engine.controller import Controller
from layers import revision, catalog
from engine.reports import digest, save


class FakeADB:
    def devices(self):return {"devices":[dict(serial="test-headset",state="device",supported=True,model="Quest 3")],"error":None}
    def stop(self,*args):raise RuntimeError("disconnected")


@pytest.fixture
def controller(tmp_path):
    save(tmp_path/"apk.json",dict(suite_revision=revision(),apk_sha256="a"*64,runtime_revision="test-runtime",runner_revision="test-runner"))
    return Controller(FakeADB(),None,tmp_path/"runs",tmp_path,start_workers=False)


def test_native_config_selection_and_device_lock(controller):
    job=controller.create({"preset":"smoke"})
    assert len(job["configuration"]["cases"])==63
    assert job["configuration"]["apk_sha256"]=="a"*64
    assert "serial" not in job
    with pytest.raises(ValueError):controller.create({})
    assert controller.stop(job["run_id"])["cancel_requested"]
    assert "reconnect" in controller.view(job["run_id"])["message"]


@pytest.mark.parametrize("mode",["isolated","xr"])
@pytest.mark.parametrize("preset",["smoke","standard","sustained"])
def test_every_preset_queues_the_full_ordered_catalog(controller,mode,preset):
    job=controller.create(dict(mode=mode,preset=preset))
    assert job["configuration"]["cases"]==[c["id"] for c in catalog()["cases"]]
    assert len(set(job["configuration"]["cases"]))==63
    assert len({c["operation"] for c in catalog()["cases"]})==25


def test_configuration_validation(controller):
    for body in ({"mode":"web"},{"cases":["linear_odd"]},{"serial":"wrong"},{"shell":"whoami"},{"operations":["unknown"]},{"cases":[]},{"operations":[]},{"operations":["linear","linear"]},{"calibration":"bad"}):
        with pytest.raises(ValueError):controller.create(body)
    assert controller.create({"mode":"xr"})["configuration"]["mode"]=="xr"


def test_database_outage_preserves_report_and_retries(controller,report):
    job=controller.create({});id=job["run_id"];report["run_id"]=id
    save(controller.state/id/"report.json",report)
    controller.update(id,status="complete")
    def unavailable(_):raise OSError("offline")
    controller.store=SimpleNamespace(put=unavailable)
    controller.submit(id)
    assert controller.report(id)==report
    assert controller.view(id)["storage"]=="pending"
    controller.store=SimpleNamespace(put=lambda r:dict(stored=True,run_id=id,sha256=digest(r)))
    controller.submit(id)
    assert controller.view(id)["storage"]=="stored"
    assert json.loads((controller.state/id/"receipt.json").read_text())["stored"]


def test_restart_recovers_job(controller):
    job=controller.create({})
    # Existing subset runs remain recoverable even though new requests must use the full suite.
    legacy=dict(job["configuration"],cases=["linear_odd"])
    controller.update(job["run_id"],status="running",configuration=legacy)
    restored=Controller(FakeADB(),None,controller.state,controller.build,start_workers=False)
    assert restored.view(job["run_id"])["configuration"]==legacy


def test_launch_disconnect_partial_and_recovery(controller,report):
    import copy
    job=controller.create({});id=job["run_id"]
    report.update(run_id=id,configuration=job["configuration"],execution_location=job["configuration"]["execution_target"])
    template=report["results"][0]
    report["results"]=[dict(template,case=c["id"],operation=c["operation"]) for c in catalog()["cases"]]
    calls=[];polls=iter(range(4));cycle=[0]
    def wait(_):cycle[0]=next(polls);return cycle[0]==3
    controller.stopping=SimpleNamespace(wait=wait)
    controller.adb.install=lambda *a:calls.append("install")
    controller.adb.start=lambda *a:calls.append("start")
    def read(serial,run_id,name):
        assert run_id==id
        if cycle[0]==0:raise OSError("USB disconnected")
        if name=="progress.json":return dict(run_id=id,status="running",completed_results=0)
        value=copy.deepcopy(report)
        if cycle[0]==1:value["status"]="running"
        return value
    controller.adb.read=read
    def put(value):
        assert (controller.state/id/"report.json").exists(),"Local save must precede DB submission"
        return dict(stored=True,run_id=id,sha256=digest(value))
    controller.store=SimpleNamespace(put=put)
    controller.launch(id,json.loads((controller.build/"apk.json").read_text()))
    assert calls==["install","start"]
    assert (controller.state/id/"partial.json").exists()
    assert json.loads((controller.state/id/"native-report.json").read_text())==report
    assert controller.view(id)["status"]=="complete"
    assert controller.view(id)["storage"]=="stored"


def test_false_database_receipt_is_not_accepted(controller,report):
    job=controller.create({});id=job["run_id"];report["run_id"]=id
    save(controller.state/id/"report.json",report)
    controller.store=SimpleNamespace(put=lambda _:dict(stored=True,run_id=id,sha256="wrong"))
    controller.submit(id)
    assert controller.view(id)["storage"]=="pending"
    assert not (controller.state/id/"receipt.json").exists()


def test_stop_before_install_finishes_never_launches(controller):
    job=controller.create({});id=job["run_id"]
    controller.stop(id)
    controller.adb.install=lambda *a:None
    controller.adb.start=lambda *a:pytest.fail("Canceled setup must not start GPU work")
    controller.launch(id,{"apk_sha256":"a"*64})
    assert controller.view(id)["status"]=="canceled"


def test_live_partial_results_are_available_before_completion(controller,report):
    job=controller.create({});id=job["run_id"]
    report.update(run_id=id,configuration=job["configuration"],status="running")
    progress=dict(run_id=id,status="running",completed_cases=1,total_cases=63,case_index=2,current_case="linear_down_1")
    controller.adb.read=lambda serial,run_id,name:progress if name=="progress.json" else report
    ticks=iter([False,True]);controller.stopping=SimpleNamespace(wait=lambda _:next(ticks))
    controller.monitor(id)
    view=controller.view(id)
    assert view["status"]=="running" and view["report_available"]
    assert view["counts"]["passed"]==1 and view["progress"]["completed_cases"]==1
    assert controller.report(id)["results"]==report["results"]
    assert not (controller.state/id/"report.json").exists()
