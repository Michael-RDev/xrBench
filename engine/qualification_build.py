"""Build the local Android runner against the same immutable xrGPU package."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from engine.build import build_environment
from engine.optimized_package import sha, verify
from engine.paths import ENGINE, ROOT
from engine.reports import save


def verify_prebuilt(path, package):
    """Require the actual development core, rather than a matching text string."""
    path = Path(path).resolve()
    for root in (path.parent, path.parent.parent):
        receipt = root / "build.json"
        if not receipt.is_file():
            continue
        metadata = json.loads(receipt.read_text())
        name = path.relative_to(root).as_posix()
        if (metadata.get("kind") == "xrgpu.native.build"
                and metadata.get("build_type") == "development"
                and metadata.get("platform") == "android-arm64"
                and metadata.get("package_sha256") == package["package_sha256"]
                and metadata.get("runtime_revision") == package["runtime_revision"]
                and name in metadata.get("libraries", [])
                and metadata.get("files", {}).get(name) == sha(path)):
            return metadata
    raise ValueError("Prebuilt qualification requires an inventoried development core; build xrGPU --library --experiments first")


def build(package, xrgpu_source, output, jobs=2, xr=False, prebuilt_runtime=None):
    package, xrgpu_source, output = [Path(p).resolve() for p in (package, xrgpu_source, output)]
    manifest = verify(package)
    if not (xrgpu_source / "runtime/include/xrgpu/session.h").is_file():
        raise ValueError("xrGPU checkout with the reusable Session API required")
    pins = json.loads((ROOT / "dependencies.json").read_text())
    if pins["executorch"]["commit"] != manifest["runtime_revision"]:
        raise ValueError("xrBench and optimized package runtime pins differ")
    _, ndk, env = build_environment(ROOT / "artifacts/toolchain", pins)
    output.mkdir(parents=True, exist_ok=True)
    native = output / "native"
    command = ["cmake", "-S", str(ENGINE / "qualification_native"), "-B", str(native), "-G", "Ninja",
               "-DXRGPU_SOURCE_ROOT=" + str(xrgpu_source), "-DXRGPU_KERNEL_PACKAGE_ROOT=" + str(package),
               "-DXRGPU_ENABLE_VULKAN=ON", "-DXRGPU_ENABLE_EXPERIMENTS=ON", "-DXRGPU_BUILD_DEMO=OFF",
               "-DXRGPU_QUALIFICATION_FILE=", "-DPYTHON_EXECUTABLE=" + sys.executable,
               "-DCMAKE_TOOLCHAIN_FILE=" + str(ndk / "build/cmake/android.toolchain.cmake"),
               "-DANDROID_ABI=arm64-v8a", "-DANDROID_PLATFORM=android-29", "-DANDROID_STL=c++_shared",
               "-DCMAKE_BUILD_TYPE=Release", "-DXRBENCH_QUALIFICATION_XR=" + ("ON" if xr else "OFF")]
    cached_kleidiai = xrgpu_source / "artifacts/build/android/kleidiai-source"
    if cached_kleidiai.is_dir():
        command.append("-DKLEIDIAI_SOURCE_DIR=" + str(cached_kleidiai))
    glslc = shutil.which("glslc", path=env["PATH"])
    if not glslc:
        raise RuntimeError("Shaderc missing from the configured Android toolchain")
    command.append("-DGLSLC_PATH=" + glslc)
    if prebuilt_runtime:
        prebuilt_runtime = Path(prebuilt_runtime).resolve()
        verify_prebuilt(prebuilt_runtime, manifest)
        command.append("-DXRGPU_PREBUILT_RUNTIME=" + str(prebuilt_runtime))
    subprocess.run(command, check=True, env=env)
    targets = ["xrgpu-qualification-runner"] + (["xrgpu_qualification"] if xr else [])
    subprocess.run(["cmake", "--build", str(native), "--target", *targets, "-j", str(jobs)], check=True, env=env)
    runner = output / "xrgpu-qualification-runner"
    shutil.copy2(native / runner.name, runner)
    shutil.copy2(prebuilt_runtime or next(native.rglob("libxrgpu_runtime.so")), output)
    shutil.copy2(next(ndk.glob("toolchains/llvm/prebuilt/*/sysroot/usr/lib/aarch64-linux-android/libc++_shared.so")), output)
    metadata = dict(schema_version=1, package_id=manifest["package_id"], package_sha256=manifest["package_sha256"],
                    runtime_revision=manifest["runtime_revision"], runner_sha256=sha(runner),
                    libraries={p.name: sha(p) for p in output.glob("*.so")})
    if xr:
        # The Android wrapper shares the exact runner logic and core library;
        # OpenXR adds real frame evidence to the existing job protocol.
        from engine.qualification_android.build import build_apk
        apk = build_apk(native, output, env)
        metadata.update(xr_apk=str(apk), xr_apk_sha256=sha(apk))
    save(runner.with_suffix(".json"), metadata)
    return dict(runner=str(runner), **metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--xrgpu-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ENGINE / ".build/qualification")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--xr", action="store_true")
    parser.add_argument("--prebuilt-runtime", type=Path, help="Reuse an existing development core built for this package")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 8:
        parser.error("Use 1 through 8 compiler workers")
    print(json.dumps(build(args.package, args.xrgpu_source, args.output, args.jobs, args.xr, args.prebuilt_runtime), indent=2))


if __name__ == "__main__":
    main()
