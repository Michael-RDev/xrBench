import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from layers.engine.server import handler


def test_session_origin_host_and_path_boundaries():
    server=ThreadingHTTPServer(("127.0.0.1",0),handler(SimpleNamespace(),"test-session"))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    url=f"http://127.0.0.1:{server.server_port}"
    def request(path,headers=None,body=None):
        return urllib.request.urlopen(urllib.request.Request(url+path,headers=headers or {},data=body),timeout=3)
    try:
        with request("/api/session") as r:assert json.load(r)["token"]=="test-session"
        with request("/") as r:assert b"Run all tests" in r.read()
        for path, marker in (
            ("/stats/", b"stats for nerds"),
            ("/stats", b"stats for nerds"),
            ("/bench/app.js", b"/shared/session.mjs"),
            ("/bench/style.css", b".device-stage"),
            ("/bench/device-view.mjs", b"deviceView"),
            ("/bench/results-view.mjs", b"renderResults"),
            ("/stats/app.js", b"/shared/session.mjs"),
            ("/stats/style.css", b".compare-metadata"),
            ("/stats/view-model.mjs", b"casesForReport"),
            ("/shared/session.mjs", b"frontendLink"),
        ):
            with request(path) as r:
                assert marker in r.read()
        with request("/",{"Sec-Fetch-Site":"cross-site"}) as r:assert b"Run all tests" in r.read()
        with pytest.raises(urllib.error.HTTPError) as error:request("/api/session",{"Sec-Fetch-Site":"cross-site"})
        assert error.value.code==403
        for headers in ({},{"X-XRGPU-Session":"wrong"},{"X-XRGPU-Session":"test-session","Origin":"https://evil.example"},
                        {"X-XRGPU-Session":"test-session","Host":"evil.example"}):
            with pytest.raises(urllib.error.HTTPError) as error:request("/api/runs",headers,b"{}")
            assert error.value.code==403
        with pytest.raises(urllib.error.HTTPError) as error:request("/../.env")
        assert error.value.code==404
    finally:server.shutdown();server.server_close();worker.join()


def test_http_create_stop_recovery_and_report_download(tmp_path,report):
    from layers.engine.controller import Controller
    from layers import revision
    from layers.engine.reports import digest,save
    saved={};stops=[]
    def put(value):saved[value["run_id"]]=value;return dict(stored=True,run_id=value["run_id"],sha256=digest(value))
    adb=SimpleNamespace(devices=lambda:dict(devices=[dict(serial="test",state="device",supported=True)]),stop=lambda *args:stops.append(args))
    store=SimpleNamespace(put=put,get=saved.get)
    save(tmp_path/"apk.json",dict(suite_revision=revision(),runtime_revision="test-runtime",runner_revision="test-runner",apk_sha256="a"*64))
    controller=Controller(adb,store,tmp_path/"runs",tmp_path,start_workers=False)
    server=ThreadingHTTPServer(("127.0.0.1",0),handler(controller,"test-session"))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    def request(path,body=None):
        headers={"X-XRGPU-Session":"test-session","Content-Type":"application/json"}
        data=json.dumps(body).encode() if body is not None else None
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{server.server_port}"+path,headers=headers,data=data),timeout=3) as response:
            return json.load(response)
    try:
        for subset in ({"cases":["linear_odd"]},{"operations":["unknown"]}):
            with pytest.raises(urllib.error.HTTPError) as error:request("/api/runs",subset)
            assert error.value.code==400
        assert not controller.jobs
        job=request("/api/runs",{})
        assert len(job["configuration"]["cases"])==63
        id=job["run_id"]
        assert request("/api/runs/"+id)["configuration"]==job["configuration"]
        assert request("/api/runs/"+id+"/stop",{})["cancel_requested"]
        assert stops==[("test",id)]
        report.update(run_id=id,configuration=job["configuration"],status="canceled")
        adb.read=lambda serial,run_id,name:dict(run_id=id,status="canceled") if name=="progress.json" else report
        ticks=iter([False,True]);controller.stopping=SimpleNamespace(wait=lambda _:next(ticks))
        controller.monitor(id)
        downloaded=request("/api/runs/"+id+"/report")
        assert downloaded==saved[id]
        assert digest(downloaded)==request("/api/runs/"+id)["receipt"]["sha256"]
    finally:server.shutdown();server.server_close();worker.join()


def test_saved_pagination_db_only_reports_comparisons_and_outage(tmp_path,report):
    import copy
    from layers.engine.controller import Controller
    from layers.engine.reports import save, digest
    from layers.engine.analysis import metadata
    records=[]
    for i in range(3):
        r=copy.deepcopy(report);r['run_id']='run-'+str(i)*32
        records.append(r)
    offline=[False]
    def recent(limit,offset):
        if offline[0]:raise OSError('database offline')
        return [metadata(r) for r in records[offset:offset+limit]]
    def get(id):
        if offline[0]:raise OSError('database offline')
        return next((r for r in records if r['run_id']==id),None)
    controller=Controller(None,SimpleNamespace(recent=recent,get=get),tmp_path/'state',tmp_path,start_workers=False)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(controller,'test-session'))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    def request(path,token='test-session'):
        with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
            headers={'X-XRGPU-Session':token}),timeout=3) as response:return json.load(response)
    try:
        first=request('/api/saved?limit=2&offset=0')
        assert len(first['runs'])==2 and first['next_offset']==2
        assert request('/api/saved?limit=2&offset=2')['next_offset'] is None
        for query in ('limit=0','limit=101','offset=-1','limit=2&limit=3','offset=100001','limit=x','secret=1'):
            with pytest.raises(urllib.error.HTTPError) as e:request('/api/saved?'+query)
            assert e.value.code==400
        id=records[0]['run_id'];other=records[1]['run_id']
        assert not controller.jobs
        assert request('/api/runs/'+id+'/report')==records[0]
        before=digest(records[0])
        comparison=request('/api/compare?baseline='+id+'&current='+other)
        assert not comparison['compatible']  # Old report lacks required hardware/scope metadata.
        assert digest(records[0])==before
        with pytest.raises(urllib.error.HTTPError) as e:request('/api/compare?baseline='+id+'&current='+other,token='bad')
        assert e.value.code==403
        with pytest.raises(urllib.error.HTTPError) as e:request('/api/compare?baseline=../private&current='+other)
        assert e.value.code==400
        save(controller.state/id/'report.json',records[0]);offline[0]=True
        with pytest.raises(urllib.error.HTTPError) as e:request('/api/saved')
        assert e.value.code==503
        assert request('/api/runs/'+id+'/report')==records[0]
    finally:server.shutdown();server.server_close();worker.join()
