"""Pinned native release inputs for automated builds; never called by visitors."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from engine.kernel_package import git, git_environment
from engine.operators import verify_package
from engine.paths import ROOT


def pins():
    return json.loads((ROOT / "dependencies.json").read_text())


def verify_native(package):
    manifest = verify_package(package)
    expected = pins()["xrgpu_native"]
    if (
        manifest.get("source", {}).get("native_catalog_sha256") != expected["catalog_sha256"]
        or len(set(manifest["operations"])) != expected["operator_count"]
        or manifest.get("operation_counts") != {"legacy": 38, "extended": 183}
        or manifest.get("backend") != "vulkan"
        or manifest.get("target") != "android-arm64"
    ):
        raise ValueError("Native package differs from the pinned complete xrGPU catalog")
    return manifest


def json_headers():
    spec = pins()["native_json"]
    cache = ROOT / "artifacts/deps/native-json" / spec["commit"]
    seed = ROOT / "artifacts/deps/executorch/third-party/json"
    for relative, key in [
        ("single_include/nlohmann/json.hpp", "header_sha256"),
        ("LICENSE.MIT", "license_sha256"),
    ]:
        file = cache / relative
        if not file.exists():
            file.parent.mkdir(parents=True, exist_ok=True)
            if (seed / relative).is_file():
                data = (seed / relative).read_bytes()
            else:
                with urllib.request.urlopen(
                    f"https://raw.githubusercontent.com/nlohmann/json/{spec['commit']}/{relative}",
                    timeout=60,
                ) as response:
                    data = response.read(2 * 1024 * 1024)
            if hashlib.sha256(data).hexdigest() != spec[key]:
                raise ValueError("Pinned native JSON dependency checksum mismatch")
            temporary = file.with_suffix(".part")
            temporary.write_bytes(data)
            temporary.replace(file)
        if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != spec[key]:
            raise ValueError("Cached native JSON dependency changed")
    return cache / "single_include"


def resolve(package=None, *, jobs=4):
    """Use an explicit verified package or build its pinned source in clean cache."""
    supplied = package or os.environ.get("XRBENCH_NATIVE_PACKAGE")
    if supplied:
        directory = Path(supplied).absolute()
        verify_native(directory)
        return directory
    local = ROOT.parent / "xrGPU/artifacts/packages/native-kernels-all-v4"
    if local.is_dir():
        verify_native(local)
        return local
    spec = pins()["xrgpu_native"]
    output = ROOT / "artifacts/packages" / ("xrgpu-native-" + spec["commit"])
    if output.exists():
        verify_native(output)
        return output
    sources = ROOT / "artifacts/deps/xrgpu-native"
    sources.mkdir(parents=True, exist_ok=True)
    source = sources / spec["commit"]
    if not source.exists():
        staging = Path(tempfile.mkdtemp(prefix=".source-", dir=sources))
        try:
            env = git_environment()
            git("init", "--quiet", cwd=staging, env=env)
            git(
                "fetch",
                "--quiet",
                "--depth",
                "1",
                spec["url"],
                spec["commit"],
                cwd=staging,
                env=env,
            )
            if git("rev-parse", "FETCH_HEAD", cwd=staging, env=env) != spec["commit"]:
                raise ValueError("Native source revision mismatch")
            git("checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=staging, env=env)
            staging.rename(source)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    if (
        source.is_symlink()
        or git("rev-parse", "HEAD", cwd=source) != spec["commit"]
        or git("status", "--porcelain", cwd=source)
    ):
        raise ValueError("Pinned native source cache changed")
    toolchain = ROOT / "artifacts/toolchain"
    ndk = Path(
        os.environ.get("ANDROID_NDK", toolchain / "android-sdk/ndk" / pins()["android"]["ndk"])
    )
    glslc = next(iter(sorted(toolchain.glob("shaderc/*/bin/glslc*"))), None)
    if glslc is None:
        from engine.bootstrap_shaderc import main as prepare_shader_compiler

        prepare_shader_compiler()
        glslc = next(iter(sorted(toolchain.glob("shaderc/*/bin/glslc*"))), None)
    validator = next(iter(sorted(ndk.glob("shader-tools/*/spirv-val*"))), None)
    if glslc is None or validator is None:
        raise FileNotFoundError("Automated release needs its pinned Android/shader toolchain")
    cmake = shutil.which("cmake") or str(Path(sys.executable).with_name("cmake"))
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "kernels._shared.build",
            "--output",
            str(output),
            "--ndk",
            str(ndk),
            "--glslc",
            str(glslc),
            "--spirv-val",
            str(validator),
            "--cmake",
            cmake,
            "--jobs",
            str(jobs),
        ],
        cwd=source,
        env=dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE="1"),
        check=True,
    )
    verify_native(output)
    return output
