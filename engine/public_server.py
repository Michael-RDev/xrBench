"""Local website plus transactional PostgreSQL report ingestion; no host ADB."""

import argparse
import copy
import hashlib
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
import webbrowser

from engine.paths import ROOT, STATE
from engine.report_delivery import MAX_REPORT_BYTES
from engine.reports import summarize, validate
from engine.local_http import (LocalHTTPServer, LocalRequestMixin, PublicFilesMixin,
                               RequestError, read_json_body)
from engine.report_json import load_report_json


class PostgresReports:
    def __init__(self, store, manifests):
        self.store = store
        self.manifests = manifests

    def put(self, payload):
        raw = load_report_json(payload)
        config = raw.get("configuration", {})
        manifest = next(
            (m for m in self.manifests if m["apk_sha256"] == config.get("apk_sha256")), None
        )
        if manifest is None:
            raise ValueError("Unknown native runner release")
        processor = "gpu" if raw.get("backend") in {"xrgpu_vulkan", "executorch_vulkan"} else "cpu"
        if processor not in manifest["configurations"]:
            raise ValueError("Unsupported native processor")
        expected = dict(
            manifest["configurations"][processor],
            run_id=raw.get("run_id"),
            execution_target=raw.get("execution_location"),
        )
        if raw.get("schema_version") == 3:
            from engine.native_reports import validate as validate_native

            validate_native(raw, expected, manifest["catalog"])
        else:
            if config != expected:
                raise ValueError("Report does not match a known runner configuration")
            validate(raw)
        normalized = summarize(copy.deepcopy(raw))
        normalized["native_report"] = raw
        receipt = self.store.put(normalized)
        # Store.put returns only after its transaction has committed.
        return dict(
            stored=True,
            storage="postgresql",
            run_id=raw["run_id"],
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            report_sha256=receipt["sha256"],
        )


class PublicHandler(LocalRequestMixin, PublicFilesMixin, SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def list_directory(self, path):
        self.send_error(404)
        return None

    def respond(self, status, value):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self):
        allowed = {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}
        host = self.headers.get("Host")
        return (
            host in allowed
            and self.headers.get("Origin", "http://" + str(host)) == "http://" + str(host)
            and self.headers.get("Sec-Fetch-Site") not in {"cross-site", "same-site"}
        )

    def do_POST(self):
        if not self.authorized():
            self.respond(403, dict(error="Same-origin report submission required"))
            return
        if self.path != "/api/reports":
            self.respond(404, dict(error="Not found"))
            return
        try:
            payload = read_json_body(self, MAX_REPORT_BYTES)
            self.respond(200, self.server.report_sink.put(payload))
        except RequestError as error:
            self.respond(error.status, dict(error=str(error)))
        except FileExistsError:
            self.respond(409, dict(error="This run already has different native evidence"))
        except (ValueError, UnicodeError, KeyError, TypeError):
            self.respond(
                400, dict(error="Expected a finished native report matching this website release")
            )
        except Exception:
            self.respond(
                503,
                dict(
                    error="PostgreSQL did not confirm storage. Results stay queued in your browser and retry automatically."
                ),
            )

    def send_head(self):
        # Static assets are public. Browser/extension navigation may legitimately
        # have a cross-site initiator; only report writes require same-origin.
        if self.headers.get("Host") not in {
            f"localhost:{self.server.server_port}",
            f"127.0.0.1:{self.server.server_port}",
        }:
            self.send_error(403)
            return None
        if self.path.split("?", 1)[0].startswith("/api/"):
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        self.send_header("Permissions-Policy", "usb=(self)")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()


def create_server(store, directory=ROOT / "dist", port=8765, manifests=None):
    directory = Path(directory).resolve()
    if manifests is None:
        manifests = [json.loads((directory / "runner/manifest.json").read_text())]
        for file in sorted((STATE / "browser-releases").glob("*.json")):
            previous = json.loads(file.read_text())
            if (
                previous.get("apk_sha256") == file.stem
                and previous["apk_sha256"] != manifests[0]["apk_sha256"]
            ):
                manifests.append(previous)
    server = LocalHTTPServer(
        ("127.0.0.1", port), partial(PublicHandler, directory=str(directory))
    )
    server.report_sink = PostgresReports(store, manifests)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    from engine.startup import prepare_database
    from engine.local_release import ensure_release

    store = prepare_database()
    directory = ensure_release()
    server = create_server(store, directory, args.port)
    url = f"http://localhost:{server.server_port}/"
    print("xrBench: " + url, flush=True)
    print("Browser USB → native xrGPU kernels → PostgreSQL. No local ADB server.", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
