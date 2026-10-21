"""Serve the packaged browser homepage without ADB, a database, or local APIs."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser

from engine.paths import ROOT


class StaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def list_directory(self, path):
        self.send_error(404)
        return None

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


def create_server(directory=ROOT / 'dist', port=8765):
    directory = Path(directory).resolve()
    for name in ('index.html', 'bench/app.js', 'runner/manifest.json'):
        if not (directory / name).is_file():
            raise FileNotFoundError('The browser release has not been packaged. Build the signed web runner and run npm run build on the maintainer machine.')
    return ThreadingHTTPServer(('127.0.0.1', port), partial(StaticHandler, directory=str(directory)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    server = create_server(port=args.port)
    url = f'http://localhost:{server.server_port}/'
    print(f'xrBench browser homepage: {url}', flush=True)
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
