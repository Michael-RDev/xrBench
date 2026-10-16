import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from layers import revision
from layers.engine.paths import BUILD, ENGINE as BASE, ROOT, STATE
from layers.engine.reports import save


def run(command, **kwargs):
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def build_environment(toolchain, pins):
    sdk = Path(os.environ.get("ANDROID_HOME", toolchain / "android-sdk"))
    ndk = Path(os.environ.get("ANDROID_NDK", sdk / "ndk" / pins["android"]["ndk"]))
    env = dict(os.environ, ANDROID_HOME=str(sdk), ANDROID_NDK=str(ndk),
               ANDROID_USER_HOME=str(STATE / "android"), GRADLE_USER_HOME=str(STATE / "gradle"))
    if not env.get("JAVA_HOME"):
        java = shutil.which("java")
        if java:
            env["JAVA_HOME"] = str(Path(java).resolve().parent.parent)
        elif os.name == "nt":
            editors = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Unity/Hub/Editor"
            versions = sorted(editors.glob("*"), key=lambda p: tuple(map(int, re.findall(r"\d+", p.name))), reverse=True)
            for editor in versions:
                jdk = editor / "Editor/Data/PlaybackEngines/AndroidPlayer/OpenJDK"
                if (jdk / "bin/javac.exe").is_file():
                    env["JAVA_HOME"] = str(jdk)
                    break
        elif Path("/usr/libexec/java_home").exists():
            env["JAVA_HOME"] = subprocess.check_output(["/usr/libexec/java_home"], text=True).strip()
    if not env.get("JAVA_HOME"):
        raise RuntimeError("JDK 17+ required. Set JAVA_HOME to an installed JDK (Unity's OpenJDK is supported).")
    if not (ndk / "build/cmake/android.toolchain.cmake").is_file():
        raise RuntimeError("Android NDK missing. Run python -m layers.engine.bootstrap_android first.")
    shaders = list((toolchain / "shaderc").glob("*/bin"))
    scripts = Path(sys.executable).parent
    env["PATH"] = os.pathsep.join([str(scripts), str(Path(env["JAVA_HOME"]) / "bin"),
                                  *(str(p) for p in shaders), env["PATH"]])
    return sdk, ndk, env


def build(jobs=4):
    from layers.engine.prepare import verify
    verify()
    build_dir = BUILD
    pins = json.loads((ROOT / "dependencies.json").read_text())
    for dep in ("executorch", "openxr"):
        actual = subprocess.check_output(["git", "-C", str(ROOT / "artifacts/deps" / dep), "rev-parse", "HEAD"], text=True).strip()
        if actual != pins[dep]["commit"]:
            raise RuntimeError(f"Pinned dependency mismatch: {dep}")
    toolchain = ROOT / "artifacts/toolchain"
    sdk, ndk, env = build_environment(toolchain, pins)
    bundle = build_dir / "bundle"
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest["suite_revision"] != revision():
        raise RuntimeError("Layer source changed; rerun uv run --extra export --group dev python -m layers.export")
    source_paths = sorted((BASE / "native").glob("*")) + sorted((BASE / "android").rglob("*.java"))
    source_paths += [BASE / "prepare.py", BASE / "build.py", ROOT / "dependencies.json"] + sorted((BASE / "patches").glob("*.patch"))
    identity = hashlib.sha256(b"".join(p.read_bytes() for p in source_paths if p.is_file())).hexdigest()
    dest = build_dir / "lib/arm64-v8a"
    dest.mkdir(parents=True, exist_ok=True)
    native = build_dir / "native"
    cmake = shutil.which("cmake", path=env["PATH"])
    if not cmake or not shutil.which("ninja", path=env["PATH"]):
        raise RuntimeError("CMake and Ninja required. Run uv sync --extra export --group dev first.")
    glslc = shutil.which("glslc", path=env["PATH"])
    if not glslc:
        raise RuntimeError("Shaderc required. Run python -m layers.engine.bootstrap_shaderc first.")
    run([cmake, "-S", BASE / "native", "-B", native,
         "-G", "Ninja", f"-DPYTHON_EXECUTABLE={Path(sys.executable).as_posix()}", f"-DGLSLC_PATH={Path(glslc).as_posix()}",
         f"-DCMAKE_TOOLCHAIN_FILE={ndk}/build/cmake/android.toolchain.cmake", "-DANDROID_ABI=arm64-v8a",
         "-DANDROID_PLATFORM=android-29", "-DANDROID_STL=c++_shared", "-DCMAKE_BUILD_TYPE=Release",
         f'-DXRGPU_ET_REVISION={pins["executorch"]["commit"]}', f"-DXRGPU_RUNNER_REVISION={identity}"], env=env)
    run([cmake, "--build", native, "--target", "xrgpu_benchmarks", "-j", jobs], env=env)
    shutil.copy2(native / "libxrgpu_benchmarks.so", dest)
    loader = next(native.rglob("libopenxr_loader.so"))
    shutil.copy2(loader, dest / "libopenxr_loader.so")
    shutil.copy2(next(ndk.glob("toolchains/llvm/prebuilt/*/sysroot/usr/lib/aarch64-linux-android/libc++_shared.so")), dest)
    assets = build_dir / "assets/bundle"
    if assets.exists(): shutil.rmtree(assets)
    assets.mkdir(parents=True, exist_ok=True)
    for name in ["manifest.json", *manifest["files"]]:
        shutil.copy2(bundle / name, assets / name)
    shutil.copytree(ROOT / "licenses", build_dir / "assets/licenses", dirs_exist_ok=True)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", build_dir / "assets")
    STATE.mkdir(exist_ok=True)
    key = STATE / "debug.keystore"
    if not key.exists():
        keytool = Path(env["JAVA_HOME"]) / ("bin/keytool.exe" if os.name == "nt" else "bin/keytool")
        run([keytool, "-genkeypair", "-keystore", key, "-storepass", "android", "-keypass", "android",
             "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-dname", "CN=Android Debug,O=Android,C=US", "-noprompt"], env=env)
        key.chmod(0o600)
    gradle = toolchain / ("gradle/bin/gradle.bat" if os.name == "nt" else "gradle/bin/gradle")
    # The existing Gradle cache is a read-only dependency source for offline builds.
    if (ROOT / "artifacts/cache/gradle/caches").exists():
        env["GRADLE_RO_DEP_CACHE"] = str(ROOT / "artifacts/cache/gradle/caches")
    run([gradle, "--no-daemon", "-p", BASE / "android", ":app:assembleDebug"], env=env)
    apk = build_dir / "xrgpu-benchmarks.apk"
    shutil.copy2(BASE / "android/app/build/outputs/apk/debug/app-debug.apk", apk)
    metadata = dict(apk_sha256=hashlib.sha256(apk.read_bytes()).hexdigest(), suite_revision=revision(),
                    runner_revision=identity, runtime_revision=pins["executorch"]["commit"], package="org.xrgpu.benchmarks")
    save(build_dir / "apk.json", metadata)
    print(f"Native benchmark APK: {apk}")


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Build the dedicated layer APK using the repository's pinned Android toolchain.")
    parser.add_argument("--jobs",type=int,default=4)
    args=parser.parse_args()
    build(args.jobs)
