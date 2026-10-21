import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from engine.report_delivery import LocalReportSink
from engine.reports import canonical


def test_local_sink_preserves_native_evidence_and_acknowledges_exact_payload(tmp_path, report):
    sink = LocalReportSink(tmp_path)
    payload = json.dumps(report, indent=2).encode()
    receipt = sink.put(payload)
    assert (tmp_path / (report['run_id'] + '.json')).read_bytes() == payload
    assert receipt['stored'] is True and receipt['storage'] == 'local'
    assert receipt['run_id'] == report['run_id']
    assert receipt['payload_sha256'] == hashlib.sha256(payload).hexdigest()
    assert 'not attested' in receipt['verification']
    # An ambiguous response or concurrent browser tab may resend the same run.
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(sink.put, [payload] * 8))
    assert all(r == receipt for r in receipts)
    assert len(list(tmp_path.iterdir())) == 1
    assert sink.put(canonical(report))['report_sha256'] == receipt['report_sha256']
    assert (tmp_path / (report['run_id'] + '.json')).read_bytes() == payload


def test_conflicting_run_never_overwrites_saved_evidence(tmp_path, report):
    sink = LocalReportSink(tmp_path)
    payload = canonical(report)
    sink.put(payload)
    report['device']['model'] = 'different fixture'
    with pytest.raises(FileExistsError):
        sink.put(canonical(report))
    assert (tmp_path / (report['run_id'] + '.json')).read_bytes() == payload
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize('change', [dict(status='running'), dict(simulated=True), dict(source='simulated'),
    dict(run_id='../../escape'), dict(schema_version=1), dict(device=None), dict(results=[None]),
    dict(configuration={'run_id': 'different'}), dict(results=[{'simulated': True}])])
def test_invalid_and_demo_reports_are_rejected(tmp_path, report, change):
    with pytest.raises(ValueError):
        LocalReportSink(tmp_path).put(canonical({**report, **change}))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('status', ['complete', 'failed', 'canceled'])
def test_keeps_failed_and_canceled_run_evidence(tmp_path, report, status):
    report['status'] = status
    report['results'][0].update(status='failed', correctness={'passed': False}, samples=[])
    LocalReportSink(tmp_path).put(canonical(report))
    assert json.loads((tmp_path / (report['run_id'] + '.json')).read_text()) == report


def test_disk_failure_never_acknowledges_or_leaves_partial_json(tmp_path, report, monkeypatch):
    import engine.report_delivery as delivery
    def fail(*args):
        raise OSError('Disk full')
    monkeypatch.setattr(delivery.os, 'link', fail)
    with pytest.raises(OSError, match='Disk full'):
        LocalReportSink(tmp_path).put(canonical(report))
    assert not list(tmp_path.iterdir())
