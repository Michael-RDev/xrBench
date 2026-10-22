import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from engine import kernel_package as package
from engine import kernels
from engine.paths import ROOT
from engine.tests import test_controller as controller_tests

controller = controller_tests.controller


@pytest.fixture
def external(tmp_path, monkeypatch):
    """Synthetic package bytes, never substitute implementations for a build."""
    source = tmp_path / "remote-package"
    source.mkdir()
    files = {}
    for name in sorted(package.REQUIRED):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(("synthetic fixture: " + name).encode())
        files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = dict(schema_version=1, package_id=package.PACKAGE_ID, runtime_revision="f" * 40, files=files)
    spec = dict(url=package.REPOSITORY, commit="a" * 40, package_id=package.PACKAGE_ID,
                package_path="kernels/benchmark", content_sha256=hashlib.sha256(package.canonical(manifest)).hexdigest())
    pins = tmp_path / "dependencies.json"
    state = SimpleNamespace(source=source, spec=spec, manifest=manifest, pins=pins, calls=[])

    def write():
        spec["content_sha256"] = hashlib.sha256(package.canonical(manifest)).hexdigest()
        (source / "manifest.json").write_bytes(package.canonical(manifest))
        pins.write_text(json.dumps(dict(xrgpu=spec, executorch=dict(commit="f" * 40))))
    state.write = write
    write()
    monkeypatch.setattr(package, "PINS", pins)
    monkeypatch.setattr(package, "CACHE", tmp_path / "cache")
    monkeypatch.delenv("XRGPU_READ_TOKEN", raising=False)
    return state


def simulate_fetch(external, monkeypatch):
    def git(*args, cwd, env=None):
        external.calls.append(args)
        if args[0] == "rev-parse":
            return external.spec["commit"]
        if args[0] == "checkout":
            shutil.copytree(external.source, cwd / external.spec["package_path"])
        return ""
    monkeypatch.setattr(package, "git", git)


def test_fetch_pin_then_reverify_offline_without_git(external, monkeypatch):
    simulate_fetch(external, monkeypatch)
    resolved = package.resolve(fetch=True)
    assert resolved == package.CACHE / external.spec["commit"] / external.spec["package_path"]
    fetch = next(args for args in external.calls if args[0] == "fetch")
    assert fetch[-2:] == (package.REPOSITORY, external.spec["commit"])
    monkeypatch.setattr(package, "git", lambda *a, **k: pytest.fail("Offline cache invoked Git"))
    assert package.resolve() == resolved
    assert package.resolve(fetch=True) == resolved
    (resolved / "cpu/matmul.h").write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        package.resolve(fetch=True)


def test_missing_cache_is_not_fetched_by_readonly_commands(external, monkeypatch):
    monkeypatch.setattr(package, "git", lambda *a, **k: pytest.fail("Unexpected network access"))
    with pytest.raises(FileNotFoundError, match="kernel_package fetch"):
        package.resolve()
    assert kernels.source_identity() == external.spec["content_sha256"]
    assert not package.CACHE.exists()


@pytest.mark.parametrize("reason", ["authentication failed", "not our ref", "could not resolve host"])
def test_retrieval_failures_leave_no_cache_or_secret_diagnostics(external, monkeypatch, reason):
    def run(*args, **kwargs):
        if args[0][3] == "init":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=128, stdout="", stderr=reason + " secret-token")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="XRGPU_READ_TOKEN") as error:
        package.resolve(fetch=True)
    assert "secret-token" not in str(error.value)
    assert not list(package.CACHE.iterdir())


def test_wrong_fetched_commit_is_rejected(external, monkeypatch):
    monkeypatch.setattr(package, "git", lambda *a, **k: "b" * 40 if a[0] == "rev-parse" else "")
    with pytest.raises(ValueError, match="commit does not match"):
        package.resolve(fetch=True)
    assert not list(package.CACHE.iterdir())


@pytest.mark.parametrize("damage", ["manifest", "file", "missing", "required", "extra", "runtime", "schema", "id"])
def test_package_integrity_and_runtime_contract(external, damage):
    if damage == "manifest":
        (external.source / "manifest.json").write_text("{}")
    elif damage == "file":
        (external.source / "matmul/config.json").write_text("changed")
    elif damage == "missing":
        (external.source / "cpu/matmul.h").unlink()
    elif damage == "extra":
        (external.source / "unlisted.glslh").write_text("unexpected source")
    else:
        if damage == "required":
            external.manifest["files"].pop("cpu/matmul.h")
        if damage == "runtime":
            external.manifest["runtime_revision"] = "e" * 40
        if damage == "schema":
            external.manifest["schema_version"] = 2
        if damage == "id":
            external.manifest["package_id"] = "xrgpu.quest3.fp32.v1"
        external.write()
    with pytest.raises(ValueError):
        package.verify(external.source)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/outside", "a\\b", "a/../b", "a//b",
                                   "a/./b", "a/CON.h", "a/file.", "a/file:stream", ""])
def test_invalid_manifest_paths(external, name):
    external.manifest["files"][name] = "a" * 64
    external.write()
    with pytest.raises(ValueError, match="path"):
        package.verify(external.source)


@pytest.mark.parametrize("field,value", [("commit", "main"), ("url", "https://elsewhere.invalid/repo"),
                                        ("package_path", "../outside"), ("content_sha256", "bad")])
def test_invalid_dependency_pins(external, field, value):
    external.spec[field] = value
    external.pins.write_text(json.dumps(dict(xrgpu=external.spec, executorch=dict(commit="f" * 40))))
    with pytest.raises(ValueError):
        package.specification()


def test_symlink_is_not_a_package_file(external):
    path = external.source / "cpu/matmul.h"
    data = path.read_bytes()
    outside = external.source.parent / "outside.h"
    outside.write_bytes(data)
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("Host cannot create symlinks")
    with pytest.raises(ValueError, match="Links"):
        package.verify(external.source)


def test_auth_is_transient_and_does_not_replace_existing_git_configuration(monkeypatch):
    monkeypatch.setenv("XRGPU_READ_TOKEN", "synthetic-secret")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "test.existing")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "preserved")
    env = package.git_environment()
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "test.existing"
    assert env["GIT_CONFIG_VALUE_0"] == "preserved"
    assert env["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_1"].startswith("Authorization: Basic ")
    assert "XRGPU_READ_TOKEN" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_apk_checks_need_only_pin_and_reject_old_or_changed_package(controller, monkeypatch):
    monkeypatch.setattr(package, "resolve", lambda **k: pytest.fail("Starting benchmark resolved sources"))
    config, metadata = controller.configuration({})
    assert config["kernel_package"] == package.provenance()
    path = controller.build / "apk.json"
    for field in [None, "commit", "content_sha256", "repository"]:
        old = copy.deepcopy(metadata)
        if field is None:
            old.pop("kernel_package")
        else:
            old["kernel_package"][field] = "stale"
        path.write_text(json.dumps(old))
        with pytest.raises(ValueError, match="Rebuild"):
            controller.configuration({})


def test_no_owned_kernel_implementations_in_xrbench():
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    offenders = []
    for name in paths:
        path = Path(name)
        if not name or not (ROOT / path).is_file():
            continue
        if (name.startswith("kernels/") or path.suffix in {".glsl", ".glslh", ".comp", ".spv"}
                or path.name == "matmul.h"):
            offenders.append(name)
    assert not offenders, "Kernel implementations belong in xrGPU: " + ", ".join(offenders)
