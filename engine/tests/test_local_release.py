from pathlib import Path

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
