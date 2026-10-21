import hashlib
import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from engine import bootstrap_android as bootstrap
from engine.build import build_environment


@pytest.mark.parametrize("host,ndk,build,adb", [
    ("Windows", "windows", "windows", "win"),
    ("Linux", "linux", "linux", "linux"),
])
def test_bootstrap_selects_verified_host_archives(tmp_path, monkeypatch, host, ndk, build, adb):
    calls = []
    monkeypatch.setattr(bootstrap, "BASE", tmp_path)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: host)
    monkeypatch.setattr(bootstrap, "unzip", lambda *args: calls.append(args))
    bootstrap.main()
    assert [call[0].rsplit("/", 1)[-1] for call in calls] == [
        f"android-ndk-r28c-{ndk}.zip", "platform-35_r02.zip",
        f"build-tools_r35_{build}.zip", f"platform-tools_r37.0.1-{adb}.zip", "gradle-8.11.1-bin.zip",
    ]
    assert all(len(call[2]) in (40, 64) for call in calls)


def test_windows_archive_extraction_handles_spaces_and_preserves_existing_tools(tmp_path, monkeypatch):
    base = tmp_path / "local tools"
    archive = base / "downloads/test.zip"
    archive.parent.mkdir(parents=True)
    with ZipFile(archive, "w") as file:
        file.writestr("package/bin/tool.exe", b"fixture")
    monkeypatch.setattr(bootstrap, "BASE", base)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Windows")
    target = base / "installed tool"
    checksum = hashlib.sha1(archive.read_bytes()).hexdigest()
    bootstrap.unzip("unused", "test.zip", checksum, target, "package")
    assert (target / "bin/tool.exe").read_bytes() == b"fixture"
    (target / "bin/tool.exe").write_bytes(b"existing")
    bootstrap.unzip("unused", "test.zip", checksum, target, "package")
    assert (target / "bin/tool.exe").read_bytes() == b"existing"
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        bootstrap.download("unused", archive, "0" * 40, "sha1")


def test_build_environment_honors_explicit_sdk_ndk_and_java_without_global_changes(tmp_path, monkeypatch):
    sdk, ndk, jdk = [tmp_path / name for name in ("Custom SDK", "Custom NDK", "Java Home")]
    marker = ndk / "build/cmake/android.toolchain.cmake"
    marker.parent.mkdir(parents=True)
    marker.touch()
    for key, value in (("ANDROID_HOME", sdk), ("ANDROID_NDK", ndk), ("JAVA_HOME", jdk)):
        monkeypatch.setenv(key, str(value))
    before = dict(os.environ)
    found_sdk, found_ndk, env = build_environment(tmp_path, {"android": {"ndk": "test"}})
    assert (found_sdk, found_ndk) == (sdk, ndk)
    assert env["JAVA_HOME"] == str(jdk)
    assert str(jdk / "bin") in env["PATH"].split(os.pathsep)
    assert dict(os.environ) == before
    marker.unlink()
    with pytest.raises(RuntimeError, match="bootstrap_android"):
        build_environment(tmp_path, {"android": {"ndk": "test"}})


@pytest.mark.skipif(os.name != "nt", reason="Windows JDK discovery")
def test_windows_build_reuses_highest_installed_unity_jdk(tmp_path, monkeypatch):
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.delenv("ANDROID_NDK", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setattr("engine.build.shutil.which", lambda _: None)
    for version in ("6000.9.1f1", "6000.10.1f1"):
        jdk = Path(os.environ["ProgramFiles"]) / f"Unity/Hub/Editor/{version}/Editor/Data/PlaybackEngines/AndroidPlayer/OpenJDK"
        (jdk / "bin").mkdir(parents=True)
        (jdk / "bin/javac.exe").touch()
    marker = tmp_path / "android-sdk/ndk/test/build/cmake/android.toolchain.cmake"
    marker.parent.mkdir(parents=True)
    marker.touch()
    _, _, env = build_environment(tmp_path, {"android": {"ndk": "test"}})
    assert "6000.10.1f1" in env["JAVA_HOME"]


def test_macos_jdk_discovery_uses_java_home_instead_of_launcher_stub(tmp_path, monkeypatch):
    monkeypatch.delenv('JAVA_HOME',raising=False)
    monkeypatch.delenv('ANDROID_HOME',raising=False)
    monkeypatch.delenv('ANDROID_NDK',raising=False)
    monkeypatch.setattr('engine.build.sys.platform','darwin')
    monkeypatch.setattr('engine.build.shutil.which',lambda _: '/usr/bin/java')
    original=Path.exists
    monkeypatch.setattr(Path,'exists',lambda path:True if path.as_posix()=='/usr/libexec/java_home' else original(path))
    monkeypatch.setattr('engine.build.subprocess.check_output',lambda *args,**kwargs:'/Library/Java/TestJDK/Contents/Home\n')
    marker=tmp_path/'android-sdk/ndk/test/build/cmake/android.toolchain.cmake'
    marker.parent.mkdir(parents=True);marker.touch()
    _,_,env=build_environment(tmp_path,{'android':{'ndk':'test'}})
    assert env['JAVA_HOME']=='/Library/Java/TestJDK/Contents/Home'
