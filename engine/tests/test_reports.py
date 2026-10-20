import copy
import json

import pytest

from engine.reports import distribution,save,summarize,validate


def test_valid_report_and_atomic_save(report,tmp_path):
    validate(report)
    summary=summarize(report);save(tmp_path/"report.json",summary)
    assert json.loads((tmp_path/"report.json").read_text())==summary
    assert summary["results"][0]["timings"]["execution_ms"]["median"]==1
    assert summary["counts"]["passed"]==1


@pytest.mark.parametrize("change",[{"status":"running"},{"execution_location":"browser"},{"backend":"webgpu"},
    {"schema_version":1},{"run_id":"../escape"},{"finished_at":"2025-01-01T00:00:00Z"},{"device":{"value":float("nan")}}])
def test_invalid_envelope(report,change):
    with pytest.raises(ValueError):validate({**report,**change})


def test_invalid_evidence_and_duplicates(report):
    r=copy.deepcopy(report);r["results"].append(r["results"][0])
    with pytest.raises(ValueError):validate(r)
    r=copy.deepcopy(report);r["results"][0]["samples"][0]["execution_ms"]=-1
    with pytest.raises(ValueError):validate(r)
    r=copy.deepcopy(report);r["results"][0]["correctness"]["passed"]=False
    with pytest.raises(ValueError):validate(r)


def test_unavailable_timing_and_thermal_flags(report):
    assert distribution([1,None]) is None
    assert distribution([1,2,3,4])["p95"]==pytest.approx(3.85)
    report["results"][0]["thermal_discontinuity"]=True
    assert not summarize(report)["results"][0]["timing_valid"]


def test_in_progress_rows_are_only_counted_as_interrupted_when_stopped(report):
    report["status"]="running";report["results"][0]["status"]="interrupted"
    assert summarize(report)["counts"]["interrupted"]==0
    report["status"]="canceled"
    assert summarize(report)["counts"]["interrupted"]==1


def test_xr_comparisons_require_complete_valid_pairs(report):
    report["configuration"]["mode"]="xr"
    baseline=report["results"][0]
    baseline.update(phase="baseline",samples=[],xr_metrics_available=True,thermal_start=0,charging_start=True,
                    xr=dict(frame_ms=[3,4,5],missed_frame_rate=.01))
    loaded=copy.deepcopy(baseline);loaded.update(phase="loaded",samples=[dict(execution_ms=1)],xr=dict(frame_ms=[4,5,6],missed_frame_rate=.03))
    report["results"].append(loaded)
    comparison=summarize(report)["xr_comparisons"][0]
    assert comparison["valid"] and comparison["frame_p95_delta_ms"]==pytest.approx(1)
    assert comparison["missed_frame_rate_delta"]==pytest.approx(.02)
    loaded["xr_metrics_available"]=False
    assert not summarize(report)["xr_comparisons"][0]["valid"]
    loaded["xr_metrics_available"]=True;loaded["thermal_start"]=1
    assert "differs" in summarize(report)["xr_comparisons"][0]["reason"]
    report["results"].pop()
    assert "Incomplete" in summarize(report)["xr_comparisons"][0]["reason"]


def test_atomic_write_keeps_previous_report_on_failure(report,tmp_path,monkeypatch):
    from pathlib import Path
    path=tmp_path/"report.json";save(path,report)
    def fail(*args):raise OSError("rename failed")
    monkeypatch.setattr(Path,"replace",fail)
    with pytest.raises(OSError):save(path,{"replacement":True})
    assert json.loads(path.read_text())==report


def test_optional_diagnostics_are_accounted_and_legacy_reports_remain_valid(report):
    validate(report)
    report['diagnostics']=dict(version=1,session_ms=10,stages_ms=dict(preparation=2,measurement=7,validation=1))
    validate(report)
    report['diagnostics']['stages_ms']['measurement']=8
    with pytest.raises(ValueError,match='account'):validate(report)


@pytest.mark.parametrize('key,value',[('wall_ms',15001),('scratch_bytes',64*1024*1024+1),('compute_gflops',0)])
def test_calibration_limits_are_validated(report,key,value):
    report['calibration']=dict(version=1,available=True,wall_ms=1000,scratch_bytes=32*1024*1024,compute_gflops=10,memory_gbps=1,dispatch_ms=.02)
    validate(report)
    report['calibration'][key]=value
    with pytest.raises(ValueError,match='calibration'):validate(report)
