import json
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from engine.adb import ADB
from engine import adb as adb_module


@pytest.fixture
def adb_locations(tmp_path, monkeypatch):
    env = {key: str(tmp_path / folder) for key, folder in (
        ("ANDROID_HOME", "Android Home"), ("ANDROID_SDK_ROOT", "Android SDK Root"),
        ("LOCALAPPDATA", "Local App Data"), ("ProgramFiles", "Program Files"),
    )}
    monkeypatch.setattr(adb_module, "ROOT", tmp_path / "repo")
    monkeypatch.setattr(adb_module, "os", SimpleNamespace(name="nt", environ=env))
    monkeypatch.setattr(adb_module.shutil, "which", lambda name: None)

    def install(relative):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    return SimpleNamespace(root=tmp_path, env=env, install=install)


def test_discovery_precedence_and_numeric_unity_versions(adb_locations, monkeypatch):
    locations = adb_locations
    unity = "Program Files/Unity/Hub/Editor/"
    sdk = "/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb.exe"
    paths = [locations.install(path) for path in (
        "on PATH/adb.exe",
        "repo/artifacts/toolchain/android-sdk/platform-tools/adb.exe",
        "Android Home/platform-tools/adb.exe",
        "Android SDK Root/platform-tools/adb.exe",
        "Local App Data/Android/Sdk/platform-tools/adb.exe",
        "Program Files/Meta Quest Developer Hub/resources/bin/adb.exe",
        unity + "6000.5.10f1" + sdk,
        unity + "6000.5.2f1" + sdk,
        unity + "6000.0.61f1" + sdk,
    )]
    # A newer editor without Android support must not hide an older usable SDK.
    (locations.root / unity / "6000.6.1f1").mkdir(parents=True)
    monkeypatch.setattr(adb_module.shutil, "which",
                        lambda name: str(paths[0]) if paths[0].is_file() else None)
    for expected in paths:
        assert ADB().executable == str(expected)
        expected.unlink()
    assert ADB().executable is None


def test_explicit_override_is_authoritative_even_when_missing(adb_locations, monkeypatch):
    fallback = adb_locations.install("on PATH/adb.exe")
    monkeypatch.setattr(adb_module.shutil, "which", lambda name: str(fallback))
    missing = adb_locations.root / "custom/missing-adb.exe"
    adb = ADB(missing)
    assert adb.executable == str(missing)

    def missing_executable(command, **kwargs):
        assert command[0] == str(missing)
        raise FileNotFoundError("not found")

    monkeypatch.setattr(adb_module.subprocess, "run", missing_executable)
    result = adb.devices()
    assert result["devices"] == []
    assert "ADB executable not found" in result["error"]
    assert str(missing) in result["error"] and "--adb" in result["error"]


def test_missing_adb_and_unset_environment_report_setup_guidance(adb_locations):
    adb_locations.env.clear()
    result = ADB().devices()
    assert result["devices"] == []
    assert "ADB executable not found" in result["error"]
    assert "platform-tools" in result["error"] and "--adb" in result["error"]
    assert "USB debugging" not in result["error"]


def test_discovery_skips_directories_and_unreadable_candidates(adb_locations, monkeypatch):
    from pathlib import Path
    bundled = adb_locations.root / "repo/artifacts/toolchain/android-sdk/platform-tools/adb.exe"
    bundled.mkdir(parents=True)
    denied = adb_locations.install("Android Home/platform-tools/adb.exe")
    expected = adb_locations.install("Android SDK Root/platform-tools/adb.exe")
    is_file = Path.is_file

    def accessible_file(path):
        if path == denied:
            raise PermissionError("access denied")
        return is_file(path)

    monkeypatch.setattr(Path, "is_file", accessible_file)
    assert ADB().executable == str(expected)


def test_non_windows_uses_sdk_adb_without_searching_windows_locations(adb_locations):
    adb_module.os.name = "posix"
    expected = adb_locations.install("Android SDK Root/platform-tools/adb")
    adb_locations.install("Program Files/Meta Quest Developer Hub/resources/bin/adb.exe")
    assert ADB().executable == str(expected)
    expected.unlink()
    assert ADB().executable is None


def test_discovered_path_with_spaces_is_one_subprocess_argument(adb_locations, monkeypatch):
    path = adb_locations.install("Program Files/Meta Quest Developer Hub/resources/bin/adb.exe")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"device\n")

    monkeypatch.setattr(adb_module.subprocess, "run", run)
    assert ADB().connected("headset")
    assert calls == [([str(path), "-s", "headset", "get-state"],
                      dict(input=None, capture_output=True, timeout=5))]


@pytest.mark.parametrize("error", [
    PermissionError("access denied"), RuntimeError("cannot connect to daemon"),
    subprocess.TimeoutExpired("adb devices -l", 15),
])
def test_adb_execution_failures_are_not_reported_as_missing(error, monkeypatch):
    adb = ADB("selected-adb")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(adb, "command", fail)
    result = adb.devices()
    assert result["devices"] == []
    assert "ADB command failed" in result["error"]
    assert "selected-adb" in result["error"] and str(error) in result["error"]
    assert "executable not found" not in result["error"]


def test_device_states_and_authorization(monkeypatch):
    adb=ADB("test-adb")
    monkeypatch.setattr(adb,"command",lambda *a,**k:b"List of devices attached\nheadset device model:Quest_3\nlocked unauthorized\n")
    properties={"ro.product.model":b"Quest 3","ro.build.version.sdk":b"32","ro.product.cpu.abilist":b"arm64-v8a"}
    monkeypatch.setattr(adb,"shell",lambda serial,*args,**kw:properties.get(args[-1],b"feature:android.hardware.vr.headtracking"))
    devices=adb.devices()["devices"]
    assert devices[0]["supported"] and devices[0]["model"]=="Quest 3"
    assert not devices[1]["supported"] and "allow USB debugging" in devices[1]["reason"]


def test_transport_uses_fixed_commands_and_validates_paths(monkeypatch):
    adb=ADB("test-adb");calls=[]
    monkeypatch.setattr(adb,"command",lambda *a,**k:calls.append((a,k)) or b"")
    id="run-"+"b"*32
    adb.start("device with spaces",{"run_id":id,"mode":"isolated"})
    assert calls[0][1]["serial"]=="device with spaces"
    assert json.loads(calls[0][1]["data"])["run_id"]==id
    assert calls[0][0][:2]==("shell","-T")
    assert shlex.split(calls[0][0][2])[:3]==["run-as","org.xrgpu.benchmarks","sh"]
    assert "KEYCODE_WAKEUP" in calls[1][0][2]
    assert "am start" in calls[2][0][2]
    with pytest.raises(ValueError):adb.read("device","../../escape","report.json")
    with pytest.raises(ValueError):adb.read("device",id,"../../secret")


def test_matching_apk_skips_install(tmp_path,monkeypatch):
    import hashlib
    apk=tmp_path/"app.apk";apk.write_bytes(b"test-only-apk");digest=hashlib.sha256(apk.read_bytes()).hexdigest()
    adb=ADB("test-adb");calls=[]
    monkeypatch.setattr(adb,"shell",lambda serial,*a,**k: b"package:org.xrgpu.benchmarks\n" if a[:2]==("pm","list") else b"package:/data/app/test/base.apk\n" if a[0]=="pm" else (digest+" base.apk\n").encode())
    monkeypatch.setattr(adb,"command",lambda *a,**k:calls.append(a))
    adb.install("headset",apk,digest)
    assert not calls
    with pytest.raises(ValueError):adb.install("headset",apk,"wrong")


def test_first_install_when_package_is_absent(tmp_path,monkeypatch):
    import hashlib
    apk=tmp_path/"app.apk";apk.write_bytes(b"test-only-apk")
    adb=ADB("test-adb");calls=[]
    monkeypatch.setattr(adb,"shell",lambda *a,**kw:b"")
    monkeypatch.setattr(adb,"command",lambda *a,**kw:calls.append((a,kw)))
    adb.install("headset",apk,hashlib.sha256(apk.read_bytes()).hexdigest())
    assert calls[0][0]==("install","-r",str(apk))
    assert calls[0][1]["serial"]=="headset"


def test_other_headsets_can_be_probed_without_claiming_validation(monkeypatch):
    adb=ADB("test-adb")
    monkeypatch.setattr(adb,"command",lambda *a,**k:b"List of devices attached\nother device\n")
    properties={"ro.product.model":b"Quest 2","ro.build.version.sdk":b"32","ro.product.cpu.abilist":b"arm64-v8a"}
    monkeypatch.setattr(adb,"shell",lambda serial,*args,**kw:properties.get(args[-1],b"feature:android.hardware.vr.headtracking"))
    device=adb.devices()["devices"][0]
    assert device["supported"] and device["support_status"] == "probe_required"
    assert not device["xr_available"]


def test_model_hints_survive_unauthorized_offline_and_property_errors(monkeypatch):
    adb = ADB("test-adb")
    monkeypatch.setattr(adb, "command", lambda *a, **k: (
        b"List of devices attached\nlocked unauthorized model:Quest_3 transport_id:1\n"
        b"asleep offline model:Quest_3S\nunknown unauthorized\nbroken device model:Quest_3\n"))
    def unavailable(*args, **kwargs):
        raise RuntimeError("Device disconnected")
    monkeypatch.setattr(adb, "shell", unavailable)
    devices = adb.devices()["devices"]
    assert [d["model"] for d in devices] == ["Quest 3", "Quest 3S", "Connected device", "Quest 3"]
    assert all(not d["supported"] for d in devices)
    assert "allow USB debugging" in devices[0]["reason"]
    assert "Device disconnected" in devices[3]["reason"]


def test_confirmed_model_overrides_hint_and_keeps_xr_separate(monkeypatch):
    adb = ADB("test-adb")
    monkeypatch.setattr(adb, "command", lambda *a, **k: b"List of devices attached\nheadset device model:Quest_3\n")
    properties = {"ro.product.model": b"Quest 3S", "ro.build.version.sdk": b"32", "ro.product.cpu.abilist": b"arm64-v8a"}
    monkeypatch.setattr(adb, "shell", lambda serial, *args, **kw: properties.get(args[-1], b"feature:android.hardware.vr.headtracking"))
    device = adb.devices()["devices"][0]
    assert device["model"] == "Quest 3S"
    assert device["supported"] and not device["xr_available"]


def test_probe_uses_a_fresh_identity_and_rejects_stale_profiles(monkeypatch):
    adb = ADB("test-adb")
    probe_id = "probe-" + "a" * 32
    monkeypatch.setattr(adb_module.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    calls = []
    result = {"probe_id": probe_id, "profile_version": 1}

    def shell(serial, *args, **kwargs):
        calls.append(args)
        return b"Starting activity" if args[0] == "am" else json.dumps(result).encode()

    monkeypatch.setattr(adb, "shell", shell)
    assert adb.probe("device") == result
    assert calls[0][-3:] == ("--es", "probe_id", probe_id)
    assert calls[1][-1] == f"files/profiles/{probe_id}.json"
    result["probe_id"] = "probe-" + "b" * 32
    with pytest.raises(ValueError, match="identity mismatch"):
        adb.probe("device")
