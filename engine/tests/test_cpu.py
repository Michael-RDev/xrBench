import json
import shutil
import subprocess
from pathlib import Path
import pytest
from engine.backends import get_backend
from engine.paths import ROOT


def test_cpu_kernels_irregular_tails(tmp_path):
    compiler=shutil.which('c++')
    if not compiler:pytest.skip('C++ compiler required')
    output=tmp_path/'cpu'
    subprocess.run([compiler,'-std=c++17','-O2','-I',str(ROOT),str(Path(__file__).with_name('native_cpu.cpp')),'-o',str(output)],check=True,capture_output=True)
    assert 'correctness passed' in subprocess.check_output([output],text=True)


def test_cpu_backend_is_explicit_and_does_not_require_vulkan():
    backend=get_backend('executorch_cpu')
    backend.check_device({'vulkan':False},dict(mode='isolated'))
    with pytest.raises(ValueError):backend.check_device({},dict(mode='xr'))
    get_backend('cpu_resident').check_request(dict(mode='isolated',preset='smoke'),[dict(operation='matmul')])
    with pytest.raises(ValueError):get_backend('cpu_resident').check_request(dict(mode='isolated',preset='smoke'),[dict(operation='relu')])


def test_cpu_reports_cannot_contain_gpu_timings(report):
    from engine.reports import validate
    report['backend']='executorch_cpu'
    for row in report['results']:
        for sample in row['samples']:sample.update(gpu_ms=None,gpu_span_ms=None,dispatches=[])
    validate(report)
    report['results'][0]['samples'][0]['gpu_ms']=1
    with pytest.raises(ValueError,match='CPU'):validate(report)


def test_cpu_export_readiness_survives_unsupported_gpu_operator(tmp_path,monkeypatch):
    pytest.importorskip('torch')
    from layers import catalog, export
    spec=catalog();spec['cases']=spec['cases'][:1]
    monkeypatch.setattr(export,'catalog',lambda *args:spec)
    def export_backend(layer,inputs,destination,backend):
        if backend=='executorch_vulkan':raise ValueError('GPU operator unavailable')
        destination.write_bytes(b'CPU export fixture')
        return dict(processor='cpu')
    monkeypatch.setattr(export,'export_program',export_backend)
    result=export.export_suite(tmp_path)['cases'][0]
    assert result['backends']['executorch_vulkan']['export_status']=='unsupported'
    assert result['backends']['executorch_cpu']['export_status']=='ready'
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    assert result['backends']['executorch_cpu']['program'] in manifest['files']
    assert (tmp_path/result['expected']).exists()
