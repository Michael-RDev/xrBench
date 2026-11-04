import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from engine import local_release


@pytest.mark.parametrize("name", [
    "engine/web/session.mjs",
    "src/stats_for_nerds/web/view-model.mjs",
    "engine/device-catalog.json",
    "scripts/hosting-config.mjs",
    "engine/android/settings.gradle",
])
def test_release_identity_tracks_transitive_build_inputs(monkeypatch, name):
    original = local_release.source_identity()
    target = local_release.ROOT / name
    read_bytes = Path.read_bytes

    def changed(path):
        data = read_bytes(path)
        return data + b"\nchanged build input\n" if path == target else data

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert local_release.source_identity() != original


def test_release_identity_tracks_public_configuration_without_secret_churn(monkeypatch):
    monkeypatch.setenv("XRBENCH_REPORT_DELIVERY", "local")
    original = local_release.source_identity()
    monkeypatch.setenv("XRBENCH_REPORT_DELIVERY", "download")
    changed = local_release.source_identity()
    assert changed != original
    monkeypatch.setenv("XRBENCH_WEB_STORE_PASSWORD", "synthetic-secret")
    assert local_release.source_identity() == changed


@pytest.fixture
def cached_release(tmp_path, monkeypatch):
    from engine import public_build

    site = tmp_path / "dist"
    assets = {"bench/app.js": b"window.fixture = true;", "bench/style.css": b"body {}"}
    script_integrity = base64.b64encode(hashlib.sha256(assets["bench/app.js"]).digest()).decode()
    assets["index.html"] = (
        '<html><head></head><body><script src="./bench/app.js" '
        f'integrity="sha256-{script_integrity}"></script></body></html>'
    ).encode()
    files = {name: hashlib.sha256(data).hexdigest() for name, data in assets.items()}
    site_sha = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assets["index.html"] = assets["index.html"].replace(
        b"<head>", f'<head>\n  <meta name="xrbench-site-sha256" content="{site_sha}">'.encode()
    )
    assets["site-manifest.json"] = json.dumps(
        dict(schema_version=1, site_sha256=site_sha, files=files)
    ).encode()

    def publish(*args, **kwargs):
        for name, data in assets.items():
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    publish()
    apk = b"synthetic signed runner"
    apk_sha = hashlib.sha256(apk).hexdigest()
    runner = site / "runner"
    runner.mkdir()
    (runner / (apk_sha + ".apk")).write_bytes(apk)
    (runner / "manifest.json").write_text(json.dumps(dict(apk=apk_sha + ".apk", apk_sha256=apk_sha)))
    build_directory = tmp_path / "build"
    build_directory.mkdir()
    (build_directory / "public-release.json").write_text(json.dumps(dict(source_sha256="source")))
    esbuild = tmp_path / "node_modules/esbuild/package.json"
    esbuild.parent.mkdir(parents=True)
    esbuild.write_text("{}")
    monkeypatch.setattr(local_release, "ROOT", tmp_path)
    monkeypatch.setattr(local_release, "BUILD", build_directory)
    monkeypatch.setattr(local_release, "STATE", tmp_path / "state")
    monkeypatch.setattr(local_release, "source_identity", lambda: "source")
    monkeypatch.setattr(local_release.shutil, "which", lambda name: "node")
    build = Mock()
    web_build = Mock(side_effect=publish)
    monkeypatch.setattr(public_build, "build", build)
    monkeypatch.setattr(local_release.subprocess, "run", web_build)
    return SimpleNamespace(site=site, assets=assets, build=build, web_build=web_build)


def test_intact_cached_release_skips_building(cached_release):
    assert local_release.ensure_release() == cached_release.site
    cached_release.build.assert_not_called()
    cached_release.web_build.assert_not_called()


@pytest.mark.parametrize("name", [
    "bench/style.css", "bench/app.js", "index.html", "site-manifest.json",
])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_damaged_cached_website_rebuilds(cached_release, name, damage):
    target = cached_release.site / name
    if damage == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b"\nchanged cached asset")

    assert local_release.ensure_release() == cached_release.site
    cached_release.build.assert_called_once_with(web=True, local=True)
    cached_release.web_build.assert_called_once()
    assert target.read_bytes() == cached_release.assets[name]
    # The repaired release can be reused without another build.
    assert local_release.ensure_release() == cached_release.site
    assert cached_release.build.call_count == cached_release.web_build.call_count == 1


@pytest.mark.parametrize("manifest", [None, [], {"schema_version": 1, "files": None}])
def test_malformed_cached_site_manifest_rebuilds(cached_release, manifest):
    (cached_release.site / "site-manifest.json").write_text(json.dumps(manifest))

    assert local_release.ensure_release() == cached_release.site
    cached_release.build.assert_called_once_with(web=True, local=True)
    cached_release.web_build.assert_called_once()
