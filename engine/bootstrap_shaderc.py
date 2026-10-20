"""Build the pinned host shader compiler; the NDK compiler is too old for Vulkan kernels."""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from engine.paths import ROOT
from engine.prepare import git


def checkout(url, revision, destination, ref=None):
    if not destination.exists():
        destination.mkdir(parents=True)
        git("init", cwd=destination)
        git("remote", "add", "origin", url, cwd=destination)
        git("fetch", "--depth", "1", "origin", ref or revision, cwd=destination)
        git("checkout", "--detach", "FETCH_HEAD", cwd=destination)
    if git("rev-parse", "HEAD", cwd=destination).stdout.strip() != revision:
        raise RuntimeError(f"Unexpected shader compiler source revision: {destination}")
    if git("diff", "HEAD", "--name-only", cwd=destination).stdout.strip():
        raise RuntimeError(f"Modified shader compiler source: {destination}")


def main():
    pin = json.loads((ROOT / "dependencies.json").read_text())["shaderc"]
    source = ROOT / "artifacts/deps/shaderc"
    checkout(pin["url"], pin["commit"], source, pin["ref"])
    # Read the release's own dependency pins without executing its DEPS file.
    tree = ast.parse((source / "DEPS").read_text())
    versions = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "vars" for t in n.targets))
    for name, repo, key in (("glslang", "glslang", "glslang_revision"),
                            ("spirv-headers", "SPIRV-Headers", "spirv_headers_revision"),
                            ("spirv-tools", "SPIRV-Tools", "spirv_tools_revision")):
        print(f"Preparing {repo}", flush=True)
        checkout(f"https://github.com/KhronosGroup/{repo}.git", versions[key], source / "third_party" / name)
    build = ROOT / "artifacts/toolchain/shaderc-build"
    output = ROOT / "artifacts/toolchain/shaderc/host"
    generator = ["-G", "Visual Studio 17 2022", "-A", "x64"] if os.name == "nt" else ["-G", "Ninja"]
    subprocess.run(["cmake", "-S", str(source), "-B", str(build), *generator,
                    "-DCMAKE_BUILD_TYPE=Release", "-DSHADERC_SKIP_TESTS=ON", "-DSHADERC_SKIP_EXAMPLES=ON",
                    "-DSPIRV_SKIP_TESTS=ON", f"-DPython_EXECUTABLE={sys.executable}",
                    f"-DCMAKE_INSTALL_PREFIX={output}"], check=True)
    subprocess.run(["cmake", "--build", str(build), "--config", "Release", "--target", "glslc_exe", "-j", "8"], check=True)
    import shutil
    binary = build / ("glslc/Release/glslc.exe" if os.name == "nt" else "glslc/glslc")
    (output / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, output / "bin" / binary.name)
    print(f"Shader compiler ready at {output / 'bin'}", flush=True)


if __name__ == "__main__":
    main()
