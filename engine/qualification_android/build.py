"""Package the compiled qualification core and real OpenXR scene in a local APK."""

from pathlib import Path
import shutil
import subprocess

from engine.paths import ENGINE, ROOT


def build_apk(native, output, env):
    native, output = Path(native), Path(output)
    libraries = output / "android-libs/arm64-v8a"
    libraries.mkdir(parents=True, exist_ok=True)
    for name in ("libxrgpu_qualification.so", "libxrgpu_runtime.so", "libopenxr_loader.so"):
        matches = list(native.rglob(name))
        if not matches and name == "libxrgpu_runtime.so" and (output / name).is_file():
            matches = [output / name]
        if len(matches) != 1:
            raise RuntimeError("Expected one compiled qualification library: " + name)
        shutil.copy2(matches[0], libraries / name)
    libcxx = output / "libc++_shared.so"
    if not libcxx.is_file():
        raise RuntimeError("Android C++ runtime missing from qualification build")
    shutil.copy2(libcxx, libraries / libcxx.name)
    environment = dict(env)
    cache = ROOT / "artifacts/cache/gradle/caches"
    if cache.is_dir():
        environment["GRADLE_RO_DEP_CACHE"] = str(cache)
    gradle = ROOT / "artifacts/toolchain/gradle/bin/gradle"
    if not gradle.exists():
        gradle = Path(shutil.which("gradle", path=environment["PATH"]) or "gradle")
    project = ENGINE / "qualification_android"
    subprocess.run([str(gradle), "--no-daemon", "--max-workers=2", "-p", str(project),
                    "-PxrbenchNativeRoot=" + str(libraries.parent), ":app:assembleDebug"],
                   check=True, env=environment)
    destination = output / "xrbench-qualification-debug.apk"
    shutil.copy2(project / "app/build/outputs/apk/debug/app-debug.apk", destination)
    return destination
