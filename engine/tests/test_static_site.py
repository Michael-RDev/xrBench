import threading
import urllib.error
import urllib.request

import pytest

from engine.static_site import create_server


def test_browser_server_serves_packaged_assets_without_local_apis(tmp_path):
    for name, contents in [('index.html', '<html data-transport="webusb">Run</html>'),
                           ('bench/app.js', '/* bundled browser */'), ('runner/manifest.json', '{}'),
                           ('runner/example.apk', 'verified-native-runner')]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    server = create_server(tmp_path, 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f'http://localhost:{server.server_port}'
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            assert b'data-transport="webusb"' in response.read()
            assert response.headers['Permissions-Policy'] == 'usb=(self)'
        with urllib.request.urlopen(url + '/runner/example.apk', timeout=3) as response:
            assert response.read() == b'verified-native-runner'
        for route in ('/api/status', '/runner/', '/../engine/.env'):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url + route, timeout=3)
            assert error.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(url, headers={'Host': 'untrusted.example'}), timeout=3)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_browser_server_requires_a_packaged_release(tmp_path):
    with pytest.raises(FileNotFoundError, match='not been packaged'):
        create_server(tmp_path, 0)
