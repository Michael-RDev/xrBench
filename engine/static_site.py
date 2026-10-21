"""Serve the packaged browser homepage and save submitted reports locally for testing."""
import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser

from engine.paths import ROOT, STATE
from engine.report_delivery import LocalReportSink, MAX_REPORT_BYTES


class StaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def list_directory(self, path):
        self.send_error(404)
        return None

    def respond(self, status, data):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        self.connection.settimeout(30)
        host = self.headers.get('Host')
        allowed = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
        # JSON plus same-origin checks prevent another website from writing local files.
        if (host not in allowed or self.headers.get('Origin', 'http://' + str(host)) != 'http://' + str(host)
                or self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site')):
            self.respond(403, dict(error='Same-origin report submission required'))
            return
        if self.path != '/api/reports':
            self.respond(404, dict(error='Not found'))
            return
        if (self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding')
                or self.headers.get('Content-Encoding', 'identity') != 'identity'):
            self.respond(415, dict(error='Send an uncompressed JSON report'))
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_REPORT_BYTES:
                self.respond(413, dict(error='Report exceeds the local receiver size limit'))
                return
            payload = self.rfile.read(size)
            if len(payload) != size:
                raise ValueError('Incomplete request')
            self.respond(200, self.server.report_sink.put(payload))
        except FileExistsError:
            self.respond(409, dict(error='This run ID already has a different report'))
        except (ValueError, UnicodeError):
            self.respond(400, dict(error='Expected a valid, finished native benchmark report'))
        except (OSError, TimeoutError):
            self.respond(503, dict(error='Local report could not be saved; the browser will retry'))

    def send_head(self):
        hosts = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
        if self.headers.get('Host') not in hosts:
            self.send_error(403)
            return None
        if self.path.split('?', 1)[0].startswith('/api/'):
            self.send_error(404, 'The browser homepage has no local API')
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        self.send_header('Permissions-Policy', 'usb=(self)')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        super().end_headers()


def create_server(directory=ROOT / 'dist', port=8765, reports=STATE / 'submitted-reports'):
    directory = Path(directory).resolve()
    reports = Path(reports).resolve()
    if reports.is_relative_to(directory):
        raise ValueError('Save reports outside the served website directory')
    for name in ('index.html', 'bench/app.js', 'runner/manifest.json'):
        if not (directory / name).is_file():
            raise FileNotFoundError('The browser release has not been packaged. Build the signed web runner and run npm run build on the maintainer machine.')
    server = ThreadingHTTPServer(('127.0.0.1', port), partial(StaticHandler, directory=str(directory)))
    server.report_sink = LocalReportSink(reports)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--reports-dir', type=Path, default=STATE / 'submitted-reports', help='Local test report destination (outside dist/)')
    args = parser.parse_args()
    server = create_server(port=args.port, reports=args.reports_dir)
    url = f'http://localhost:{server.server_port}/'
    print(f'xrBench browser homepage: {url}', flush=True)
    print(f'Automatic reports saved locally: {server.report_sink.directory}', flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
