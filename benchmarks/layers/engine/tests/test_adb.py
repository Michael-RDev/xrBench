import json
import shlex

import pytest

from benchmarks.layers.engine.adb import ADB


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
    assert shlex.split(calls[0][0][1])[:3]==["run-as","org.xrgpu.benchmarks","sh"]
    assert "am start" in calls[1][0][1]
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


def test_other_headsets_are_explicitly_unsupported(monkeypatch):
    adb=ADB("test-adb")
    monkeypatch.setattr(adb,"command",lambda *a,**k:b"List of devices attached\nother device\n")
    properties={"ro.product.model":b"Quest 2","ro.build.version.sdk":b"32","ro.product.cpu.abilist":b"arm64-v8a"}
    monkeypatch.setattr(adb,"shell",lambda serial,*args,**kw:properties.get(args[-1],b"feature:android.hardware.vr.headtracking"))
    device=adb.devices()["devices"][0]
    assert not device["supported"] and "Quest 3 only" in device["reason"]
