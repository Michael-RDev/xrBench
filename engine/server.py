import hmac
import json
import mimetypes
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from layers import catalog, revision
from engine.analysis import compare
from engine.paths import ROOT
from engine.reports import RUN_ID, canonical
from engine.benchmark_sessions import SESSION_ID
from engine.devices import CATALOG

BASE = ROOT / "src"
STATS = BASE / "stats_for_nerds/web"
BENCH = BASE / "bench_web/web"
SHARED = Path(__file__).with_name("web")


def handler(controller, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def respond(self, status, data, content_type="application/json"):
            payload = canonical(data) if content_type=="application/json" else data
            self.send_response(status)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(payload)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            try:self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError):pass

        def authorized(self, require_token=True, check_fetch=True):
            allowed={f"localhost:{self.server.server_port}",f"127.0.0.1:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed:return False
            origin=self.headers.get("Origin")
            if origin is not None and origin not in {"http://"+host for host in allowed}:return False
            if check_fetch and self.headers.get("Sec-Fetch-Site") in ("cross-site","same-site"):return False
            return not require_token or hmac.compare_digest(self.headers.get("X-XRGPU-Session",""),token)

        def do_GET(self):
            path=urlsplit(self.path).path
            if not self.authorized(require_token=path.startswith("/api/") and path!="/api/session",check_fetch=path.startswith("/api/")):
                self.respond(403,dict(error="Local session required"));return
            try:
                if path=="/healthz":self.respond(200,dict(ok=True));return
                if path=="/api/session":self.respond(200,dict(token=token));return
                if path=="/api/device-catalog":self.respond(200,CATALOG);return
                if path=="/api/status":
                    with controller.lock:jobs=[controller.view(k) for k in reversed(list(controller.jobs))]
                    self.respond(200,dict(**controller.device_snapshot,database=controller.database,jobs=jobs,
                        apk_ready=(controller.build/"apk.json").exists(),catalog=catalog(),development_catalog=catalog("development"),suite_revision=revision(),
                        benchmark_sessions=[controller.session_view(k) for k in reversed(list(getattr(controller,'benchmark_sessions',{})))],
                        **(controller.dashboard_status() if hasattr(controller,'dashboard_status') else {})));return
                if path in ("/api/saved", "/api/v1/results"):
                    query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                    if set(query)-{"limit","offset"} or any(len(v)!=1 for v in query.values()):raise ValueError("Invalid history page")
                    limit,offset=int(query.get("limit",["20"])[0]),int(query.get("offset",["0"])[0])
                    if not 1<=limit<=100 or not 0<=offset<=100000:raise ValueError("Invalid history page")
                    rows=controller.store.recent(limit+1,offset)
                    self.respond(200,dict(runs=rows[:limit],next_offset=offset+limit if len(rows)>limit and offset+limit<=100000 else None));return
                if path=="/api/compare":
                    query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                    if set(query)!={"baseline","current"} or any(len(v)!=1 or not RUN_ID.fullmatch(v[0]) for v in query.values()):
                        raise ValueError("Choose two saved runs")
                    a,b=[controller.report(query[k][0]) for k in ("baseline","current")]
                    if a is None or b is None:self.respond(404,dict(error="Report not available"));return
                    self.respond(200,compare(a,b));return
                parts=path.strip("/").split("/")
                if len(parts)==3 and parts[:2]==['api','benchmark-sessions'] and SESSION_ID.fullmatch(parts[2]):
                    self.respond(200,controller.session_view(parts[2]));return
                if len(parts) in (3,4) and parts[:2]==["api","runs"] and RUN_ID.fullmatch(parts[2]):
                    if len(parts)==3:self.respond(200,controller.view(parts[2]));return
                    if parts[3]=="report":
                        report=controller.report(parts[2])
                        self.respond(200 if report else 404,report or dict(error="Report not available yet"));return
                if len(parts)==4 and parts[:3]==["api","v1","results"] and RUN_ID.fullmatch(parts[3]):
                    result=controller.store.result(parts[3])
                    self.respond(200 if result else 404,result or dict(error="Committed report not found"));return
                files={"/":BENCH/"index.html", "/stats/":STATS/"index.html", "/stats":STATS/"index.html"}
                for name in ("app.js","style.css","device-view.mjs","chip-view.mjs","results-view.mjs",
                             "features.mjs","ascii-headset.mjs","smoke-demo.mjs","processor-policy.mjs"):
                    files["/bench/"+name]=BENCH/name
                for name in ("app.js","style.css","view-model.mjs"):
                    files["/stats/"+name]=STATS/name
                files["/shared/session.mjs"]=SHARED/"session.mjs"
                if path in files and files[path].is_file():
                    file=files[path]
                    self.respond(200,file.read_bytes(),(mimetypes.guess_type(file)[0] or "application/octet-stream")+"; charset=utf-8");return
                self.respond(404,dict(error="Not found"))
            except ValueError:self.respond(400,dict(error="Invalid history page or comparison request"))
            except KeyError:self.respond(404,dict(error="Run not found"))
            except Exception:self.respond(503,dict(error="Local service unavailable; saved reports are preserved"))

        def do_POST(self):
            if not self.authorized():self.respond(403,dict(error="Local session required"));return
            try:
                if self.headers.get("Content-Type","").split(";")[0]!="application/json":raise ValueError("JSON required")
                size=int(self.headers.get("Content-Length","0"))
                if not 0<size<=65536:raise ValueError("Invalid request size")
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError("Expected JSON object")
                path=urlsplit(self.path).path
                if path=="/api/runs":self.respond(202,controller.create(body));return
                if path=="/api/benchmark-sessions":self.respond(202,controller.create_session(body));return
                if path=="/api/devices/probe":self.respond(202,controller.probe_request(body));return
                if path=="/api/watch":self.respond(200,controller.watch_request(body));return
                parts=path.strip("/").split("/")
                if len(parts)==4 and parts[:2]==['api','benchmark-sessions'] and SESSION_ID.fullmatch(parts[2]) and parts[3]=='stop':
                    self.respond(200,controller.stop_session(parts[2]));return
                if len(parts)==4 and parts[:2]==["api","runs"] and RUN_ID.fullmatch(parts[2]):
                    if parts[3]=="stop":self.respond(200,controller.stop(parts[2]));return
                    if parts[3]=="retry":controller.submit(parts[2]);self.respond(200,controller.view(parts[2]));return
                self.respond(404,dict(error="Not found"))
            except (ValueError,TypeError) as error:self.respond(400,dict(error=str(error) or 'Invalid run or watch request'))
            except FileNotFoundError:self.respond(409,dict(error="Build the native benchmark APK before running."))
            except KeyError:self.respond(404,dict(error="Run not found"))
            except Exception:self.respond(503,dict(error="Could not start the native run. Check ADB and the local service."))
    return Handler


def create_server(controller, port=8765):
    return ThreadingHTTPServer(("127.0.0.1",port),handler(controller,secrets.token_urlsafe(32)))
