"""Run the actual Android asset extraction and JNI handoff with JVM doubles.

These are filesystem/integrity tests, not evidence of GPU execution.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from engine.paths import ENGINE, ROOT


@pytest.fixture(scope="module")
def asset_runner(tmp_path_factory):
    jar = ROOT / "artifacts/test-deps/json.jar"
    if not jar.is_file() or not shutil.which("javac"):
        if os.environ.get("XRBENCH_REQUIRE_ASSET_TESTS") == "1":
            pytest.fail("Required native asset tests need JDK and repository JSON test jar")
        pytest.skip("JDK and repository JSON test jar required")
    output = tmp_path_factory.mktemp("native-assets-jvm")
    sources = {
        "android/content/Context.java": """
package android.content;
import java.io.*;import android.content.res.AssetManager;import android.content.pm.ApplicationInfo;
public class Context {
 public final File files,assets,apk;
 public Context(File f,File a,File p){files=f;assets=a;apk=p;}
 public File getFilesDir(){return files;}
 public AssetManager getAssets(){return new AssetManager(assets);}
 public ApplicationInfo getApplicationInfo(){return new ApplicationInfo(apk.toString());}
}""",
        "android/content/res/AssetManager.java": """
package android.content.res;import java.io.*;
public class AssetManager {
 private final File root;public static int payloadReads;
 public AssetManager(File r){root=r;}
 public InputStream open(String n)throws IOException{
  if(n.startsWith("native-operators/")&&!n.endsWith("inventory.json"))payloadReads++;
  return new FileInputStream(new File(root,n));
 }
}""",
        "android/content/pm/ApplicationInfo.java": "package android.content.pm;public class ApplicationInfo {public final String sourceDir;public ApplicationInfo(String p){sourceDir=p;}}",
        "android/os/Build.java": """
package android.os;public class Build {
 public static final String MODEL="JVM fixture",MANUFACTURER="test",PRODUCT="test",DEVICE="test",FINGERPRINT="test",SOC_MODEL="test",SOC_MANUFACTURER="test";
 public static final String[] SUPPORTED_ABIS={"arm64-v8a"};
 public static class VERSION {public static final int SDK_INT=34;}
}""",
        "org/xrgpu/benchmarks/Doubles.java": """
package org.xrgpu.benchmarks;
import java.io.*;import java.nio.file.*;import java.util.concurrent.atomic.AtomicBoolean;import org.json.*;
class Runner {static final AtomicBoolean active=new AtomicBoolean();}
class Checks {static void identity(String id){if(!id.matches("run-[a-f0-9]{32}"))throw new IllegalArgumentException();}}
class Fileset {static JSONObject read(File f)throws Exception{return new JSONObject(Files.readString(f.toPath()));}}
class DeviceProfile {static String installationId(android.content.Context c){return "fixture";}}
class NativeOperators {
 static String root;
 static String probe(){return "{\\\"available\\\":true}";}
 static void run(String r,String config,String output,String device){root=r;}
}
""",
        "org/xrgpu/benchmarks/AssetCacheChecks.java": """
package org.xrgpu.benchmarks;
import java.io.*;import java.nio.file.*;import android.content.*;import android.content.res.AssetManager;import org.json.*;
public class AssetCacheChecks {
 public static void main(String[] args)throws Exception{
  Context context=new Context(new File(args[0]),new File(args[1]),new File(args[2]));
  if(args[3].equals("run")){
   NativeOperatorRunner.run(context,"run-"+"1".repeat(32));
   if(NativeOperators.root==null)throw new AssertionError("JNI not called");
   System.out.println(new JSONObject().put("root",NativeOperators.root).put("payload_reads",AssetManager.payloadReads));
  }else{
   File root=NativeOperatorRunner.extract(context,NativeOperatorRunner.metadata(context));
   System.out.println(new JSONObject().put("root",root.getAbsolutePath()).put("payload_reads",AssetManager.payloadReads));
  }
 }
}""",
    }
    paths = []
    for name, code in sources.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code)
        paths.append(str(path))
    actual = ENGINE / "android/app/src/main/java/org/xrgpu/benchmarks/NativeOperatorRunner.java"
    subprocess.run(["javac", "-cp", str(jar), "-d", str(output), str(actual), *paths],
                   check=True, capture_output=True, text=True)

    def run(files, assets, apk, *, mode="extract", error=None):
        result = subprocess.run(["java", "-cp", os.pathsep.join((str(jar), str(output))),
                                 "org.xrgpu.benchmarks.AssetCacheChecks", str(files),
                                 str(assets), str(apk), mode], capture_output=True, text=True,
                                timeout=30)
        if error:
            assert result.returncode != 0, result.stdout
            assert error in result.stderr, result.stderr
            return None
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return run


@pytest.fixture
def asset_symlink(make_symlink):
    if os.environ.get("XRBENCH_REQUIRE_ASSET_TESTS") != "1":
        return make_symlink

    def create(link, target, *, target_is_directory=False):
        # Required Linux CI must fail, rather than silently skip, if its
        # filesystem cannot exercise the production Android failure mode.
        link.symlink_to(target, target_is_directory=target_is_directory)
        return link

    return create


@pytest.fixture
def cache(tmp_path):
    files, assets = tmp_path / "data/data/app/files", tmp_path / "assets"
    files.mkdir(parents=True)
    (assets / "native-operators").mkdir(parents=True)
    apk = tmp_path / "test.apk"
    apk.write_bytes(b"JVM test APK identity, never installed")
    payloads = {"suite.json": json.dumps({"cases": [{"id": "legacy"}, {"id": "extended"}]}).encode(),
                "shaders/test.spv": b"legacy shader fixture", "shaders/extended/test.spv": b"extended shader fixture"}
    for name, data in payloads.items():
        file = assets / "native-operators" / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
    inventory = json.dumps({"files": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}).encode()
    (assets / "native-operators/inventory.json").write_bytes(inventory)
    meta = {name: "a" * 64 for name in ("suite_revision", "runner_revision", "runtime_revision",
                                       "native_package_sha256", "native_catalog_sha256")}
    meta["assets_inventory_sha256"] = hashlib.sha256(inventory).hexdigest()
    (assets / "native-config.json").write_text(json.dumps(meta))
    run_id = "run-" + "1" * 32
    config = dict(meta, run_id=run_id, backend="xrgpu_vulkan", mode="isolated",
                  apk_sha256=hashlib.sha256(apk.read_bytes()).hexdigest(),
                  cases=["legacy", "extended"], warmups=20, samples=100)
    (files / "commands").mkdir()
    (files / "commands" / (run_id + ".json")).write_text(json.dumps(config))
    return dict(files=files, assets=assets, apk=apk, root=files / "native-assets" / meta["assets_inventory_sha256"],
                payloads=payloads, tmp=tmp_path)


def test_android_data_alias_is_resolved_before_jni_and_cached_rerun(asset_runner, cache, asset_symlink):
    c = cache
    alias = c["tmp"] / "data/user/0"
    alias.parent.mkdir()
    asset_symlink(alias, c["tmp"] / "data/data", target_is_directory=True)
    files = alias / "app/files"
    first = asset_runner(files, c["assets"], c["apk"], mode="run")
    assert Path(first["root"]) == c["root"].resolve(), "Native shader loaders require an alias-free root"
    assert first["payload_reads"] == len(c["payloads"])
    for name, data in c["payloads"].items():
        assert (c["root"] / name).read_bytes() == data
    second = asset_runner(files, c["assets"], c["apk"], mode="run")
    assert second["root"] == first["root"]
    assert second["payload_reads"] == 0, "Verified cached shaders must not require re-extraction"


@pytest.mark.parametrize("name", ["shaders/test.spv", "shaders/extended/test.spv"])
def test_cache_corruption_is_repaired_and_bad_apk_assets_are_rejected(asset_runner, cache, name):
    c = cache
    asset_runner(c["files"], c["assets"], c["apk"])
    shader = c["root"] / name
    shader.write_bytes(b"corrupt cached bytes")
    result = asset_runner(c["files"], c["assets"], c["apk"])
    assert result["payload_reads"] == 1
    assert shader.read_bytes() == c["payloads"][name]
    shader.write_bytes(b"corrupt cache again")
    (c["assets"] / "native-operators" / name).write_bytes(b"corrupt packaged bytes")
    asset_runner(c["files"], c["assets"], c["apk"], error="Native asset checksum mismatch")
    assert shader.read_bytes() == b"corrupt cache again"


@pytest.mark.parametrize("target", ["native-assets", "inventory", "shaders", "extended", "shader"])
def test_links_inside_the_cache_are_rejected(asset_runner, cache, asset_symlink, target):
    c = cache
    asset_runner(c["files"], c["assets"], c["apk"])
    paths = {"native-assets": c["root"].parent, "inventory": c["root"],
             "shaders": c["root"] / "shaders", "extended": c["root"] / "shaders/extended",
             "shader": c["root"] / "shaders/test.spv"}
    link = paths[target]
    outside = c["tmp"] / "outside"
    link.rename(outside)
    asset_symlink(link, outside, target_is_directory=outside.is_dir())
    asset_runner(c["files"], c["assets"], c["apk"], error="Native asset links are forbidden")


def test_stale_partial_link_cannot_overwrite_another_file(asset_runner, cache, asset_symlink):
    c = cache
    asset_runner(c["files"], c["assets"], c["apk"])
    shader = c["root"] / "shaders/test.spv"
    shader.write_bytes(b"corrupt cache")
    outside = c["tmp"] / "outside"
    outside.write_bytes(b"must survive")
    partial = shader.with_name(shader.name + ".part")
    asset_symlink(partial, outside)
    asset_runner(c["files"], c["assets"], c["apk"])
    assert outside.read_bytes() == b"must survive"
    assert shader.read_bytes() == c["payloads"]["shaders/test.spv"]
    assert not shader.is_symlink()


def test_inventory_checksum_failure_prevents_cache_creation(asset_runner, cache):
    c = cache
    (c["assets"] / "native-operators/inventory.json").write_text('{"files":{}}')
    asset_runner(c["files"], c["assets"], c["apk"], error="Native asset inventory checksum mismatch")
    assert not c["root"].exists()


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a/../b", "./shader", "a//b", "a\\b", "a/", ""])
def test_inventory_paths_cannot_escape_the_cache(asset_runner, cache, name):
    c = cache
    inventory = json.dumps({"files": {name: hashlib.sha256(b"fixture").hexdigest()}}).encode()
    (c["assets"] / "native-operators/inventory.json").write_bytes(inventory)
    meta_path = c["assets"] / "native-config.json"
    meta = json.loads(meta_path.read_text())
    meta["assets_inventory_sha256"] = hashlib.sha256(inventory).hexdigest()
    meta_path.write_text(json.dumps(meta))
    asset_runner(c["files"], c["assets"], c["apk"], error="asset path")


def test_failed_replacement_removes_temporary_file(asset_runner, cache):
    c = cache
    asset_runner(c["files"], c["assets"], c["apk"])
    shader = c["root"] / "shaders/test.spv"
    shader.unlink()
    shader.mkdir()
    asset_runner(c["files"], c["assets"], c["apk"], error="Exception")
    assert not list(shader.parent.glob(".native-*.part"))


def test_published_apk_assets_extract_and_reuse_through_android_alias(asset_runner, tmp_path, asset_symlink):
    apk = os.environ.get("XRBENCH_ASSET_TEST_APK")
    if not apk:
        pytest.skip("Set XRBENCH_ASSET_TEST_APK for an existing native release asset check")
    assets = tmp_path / "assets"
    with zipfile.ZipFile(apk) as archive:
        for name in archive.namelist():
            if name == "assets/native-config.json" or name.startswith("assets/native-operators/"):
                if name.endswith("/"):
                    continue
                relative = Path(name).relative_to("assets")
                assert not relative.is_absolute() and ".." not in relative.parts
                target = assets / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    inventory = json.loads((assets / "native-operators/inventory.json").read_text())["files"]
    files = tmp_path / "data/data/app/files"
    files.mkdir(parents=True)
    alias = tmp_path / "data/user/0"
    alias.parent.mkdir()
    asset_symlink(alias, tmp_path / "data/data", target_is_directory=True)
    aliased_files = alias / "app/files"
    first = asset_runner(aliased_files, assets, apk)
    root = Path(first["root"])
    assert root == root.resolve(), "The root passed to native loaders must have no directory aliases"
    assert first["payload_reads"] == len(inventory)
    for name, sha in inventory.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == sha
    second = asset_runner(aliased_files, assets, apk)
    assert second["root"] == first["root"] and second["payload_reads"] == 0
