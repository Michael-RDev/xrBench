import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from engine.workloads import revision
from engine.paths import BUILD, ENGINE as BASE, ROOT, STATE
from engine.reports import save


def run(command, **kwargs):
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def build_environment(toolchain, pins):
    sdk = Path(os.environ.get("ANDROID_HOME", toolchain / "android-sdk"))
    ndk = Path(os.environ.get("ANDROID_NDK", sdk / "ndk" / pins["android"]["ndk"]))
    env = dict(os.environ, ANDROID_HOME=str(sdk), ANDROID_NDK=str(ndk),
               ANDROID_USER_HOME=str(STATE / "android"), GRADLE_USER_HOME=str(STATE / "gradle"))
    if not env.get("JAVA_HOME"):
        java = shutil.which("java")
        if sys.platform == "darwin" and Path("/usr/libexec/java_home").exists():
            # /usr/bin/java is a launcher stub, not a JDK rooted at /usr.
            env["JAVA_HOME"] = subprocess.check_output(["/usr/libexec/java_home"], text=True).strip()
        elif java:
            env["JAVA_HOME"] = str(Path(java).resolve().parent.parent)
        elif os.name == "nt":
            editors = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Unity/Hub/Editor"
            versions = sorted(editors.glob("*"), key=lambda p: tuple(map(int, re.findall(r"\d+", p.name))), reverse=True)
            for editor in versions:
                jdk = editor / "Editor/Data/PlaybackEngines/AndroidPlayer/OpenJDK"
                if (jdk / "bin/javac.exe").is_file():
                    env["JAVA_HOME"] = str(jdk)
                    break
    if not env.get("JAVA_HOME"):
        raise RuntimeError("JDK 17+ required. Set JAVA_HOME to an installed JDK (Unity's OpenJDK is supported).")
    if not (ndk / "build/cmake/android.toolchain.cmake").is_file():
        raise RuntimeError("Android NDK missing. Run python -m engine.bootstrap_android first.")
    shaders = list((toolchain / "shaderc").glob("*/bin"))
    scripts = Path(sys.executable).parent
    env["PATH"] = os.pathsep.join([str(scripts), str(Path(env["JAVA_HOME"]) / "bin"),
                                  *(str(p) for p in shaders), env["PATH"]])
    return sdk, ndk, env


def build(jobs=4, web=False):
    if web:
        from engine.public_build import build as build_public
        return build_public(jobs, web=True)
    from engine.prepare import verify
    from engine import kernels, kernel_package
    package = kernel_package.resolve(fetch=True)
    verify()
    build_dir = BUILD
    kernels.prepare_sources(build_dir / "candidate_sources", package)
    pins = json.loads((ROOT / "dependencies.json").read_text())
    for dep in ("executorch", "openxr"):
        actual = subprocess.check_output(["git", "-C", str(ROOT / "artifacts/deps" / dep), "rev-parse", "HEAD"], text=True).strip()
        if actual != pins[dep]["commit"]:
            raise RuntimeError(f"Pinned dependency mismatch: {dep}")
    toolchain = ROOT / "artifacts/toolchain"
    sdk, ndk, env = build_environment(toolchain, pins)
    if web:
        required = ('XRBENCH_WEB_KEYSTORE', 'XRBENCH_WEB_STORE_PASSWORD', 'XRBENCH_WEB_KEY_ALIAS',
                    'XRBENCH_WEB_KEY_PASSWORD', 'XRBENCH_WEB_VERSION_CODE', 'XRBENCH_WEB_CERT_SHA256')
        if any(not env.get(name) for name in required):
            raise RuntimeError('Web distribution requires the persistent signing credentials and version code documented in src/bench_web/README.md')
        if not Path(env['XRBENCH_WEB_KEYSTORE']).is_file() or not env['XRBENCH_WEB_VERSION_CODE'].isdigit() or int(env['XRBENCH_WEB_VERSION_CODE']) < 1:
            raise RuntimeError('Invalid web signing keystore or version code')
    bundle = build_dir / "bundle"
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest["suite_revision"] != revision():
        raise RuntimeError("Layer source changed; rerun uv run --extra export --group dev python -m engine.workloads.export")
    source_paths = sorted((BASE / "native").rglob("*")) + sorted((BASE / "android").rglob("*.java"))
    source_paths += [BASE / "prepare.py", BASE / "build.py", ROOT / "dependencies.json"] + sorted((BASE / "patches").glob("*.patch"))
    source_paths += [BASE / "kernel_hooks.py", BASE / "kernels.py", BASE / "kernel_package.py"]
    source_paths += sorted((BASE / "backends").rglob("*.py"))
    source_paths += [BASE / "android/app/src/main/AndroidManifest.xml"]
    source_paths += sorted((BASE / "android").glob('*.gradle')) + [BASE / 'android/app/build.gradle']
    identity = hashlib.sha256(kernel_package.canonical(kernel_package.provenance()) +
                             b"".join(p.read_bytes() for p in source_paths if p.is_file())).hexdigest()
    dest = build_dir / "lib/arm64-v8a"
    dest.mkdir(parents=True, exist_ok=True)
    native = build_dir / "native"
    generator = "Ninja"
    cache = native / "CMakeCache.txt"
    if cache.exists():
        match = re.search(r"^CMAKE_GENERATOR:INTERNAL=(.+)$", cache.read_text(), re.MULTILINE)
        if match:
            generator = match.group(1)
    cmake = shutil.which("cmake", path=env["PATH"])
    if not cmake or not shutil.which("ninja", path=env["PATH"]):
        raise RuntimeError("CMake and Ninja required. Run uv sync --extra export --group dev first.")
    glslc = shutil.which("glslc", path=env["PATH"])
    if not glslc:
        raise RuntimeError("Shaderc required. Run python -m engine.bootstrap_shaderc first.")
    run([cmake, "-S", BASE / "native", "-B", native,
         "-G", generator, f"-DPYTHON_EXECUTABLE={Path(sys.executable).as_posix()}", f"-DGLSLC_PATH={Path(glslc).as_posix()}",
         f"-DXRGPU_KERNEL_PACKAGE_ROOT={package.as_posix()}",
         f"-DXRGPU_CPU_KERNEL_SOURCE={kernel_package.source_url('cpu/matmul.h')}",
         f"-DCMAKE_TOOLCHAIN_FILE={ndk}/build/cmake/android.toolchain.cmake", "-DANDROID_ABI=arm64-v8a",
         "-DANDROID_PLATFORM=android-29", "-DANDROID_STL=c++_shared", "-DCMAKE_BUILD_TYPE=Release",
         f'-DXRGPU_ET_REVISION={pins["executorch"]["commit"]}', f"-DXRGPU_RUNNER_REVISION={identity}"], env=env)
    run([cmake, "--build", native, "--target", "xrgpu_benchmarks", "xrbench_cpu", "-j", jobs], env=env)
    kernel_manifest = kernels.manifest(native, glslc, package)
    shutil.copy2(native / "libxrgpu_benchmarks.so", dest)
    shutil.copy2(native / "libxrbench_cpu.so", dest)
    loader = next(native.rglob("libopenxr_loader.so"))
    shutil.copy2(loader, dest / "libopenxr_loader.so")
    shutil.copy2(next(ndk.glob("toolchains/llvm/prebuilt/*/sysroot/usr/lib/aarch64-linux-android/libc++_shared.so")), dest)
    assets = build_dir / "assets/bundle"
    if assets.exists():
        shutil.rmtree(assets)
    assets.mkdir(parents=True, exist_ok=True)
    for name in ["manifest.json", *manifest["files"]]:
        shutil.copy2(bundle / name, assets / name)
    save(build_dir / "assets/kernel-manifest.json", kernel_manifest)
    # Refresh generated notices so removed source files cannot linger in the APK.
    license_assets = build_dir / "assets/licenses"
    if license_assets.exists():
        shutil.rmtree(license_assets)
    shutil.copytree(ROOT / "licenses", license_assets)
    shutil.copy2(package / "LICENSE", license_assets / "xrgpu-benchmark-LICENSE.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", build_dir / "assets")
    STATE.mkdir(exist_ok=True)
    key = STATE / "debug.keystore"
    if not web and not key.exists():
        keytool = Path(env["JAVA_HOME"]) / ("bin/keytool.exe" if os.name == "nt" else "bin/keytool")
        run([keytool, "-genkeypair", "-keystore", key, "-storepass", "android", "-keypass", "android",
             "-alias", "androiddebugkey", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
             "-dname", "CN=Android Debug,O=Android,C=US", "-noprompt"], env=env)
        key.chmod(0o600)
    gradle = toolchain / ("gradle/bin/gradle.bat" if os.name == "nt" else "gradle/bin/gradle")
    # The existing Gradle cache is a read-only dependency source for offline builds.
    if (ROOT / "artifacts/cache/gradle/caches").exists():
        env["GRADLE_RO_DEP_CACHE"] = str(ROOT / "artifacts/cache/gradle/caches")
    variant = 'web' if web else 'debug'
    run([gradle, "--no-daemon", "-p", BASE / "android", ':app:assemble' + variant.title()], env=env)
    apk = build_dir / ('xrgpu-benchmarks-web.apk' if web else 'xrgpu-benchmarks.apk')
    shutil.copy2(BASE / f'android/app/build/outputs/apk/{variant}/app-{variant}.apk', apk)
    if web:
        signer = sdk / 'build-tools/35.0.0' / ('apksigner.bat' if os.name == 'nt' else 'apksigner')
        verification = subprocess.check_output([str(signer), 'verify', '--print-certs', str(apk)], env=env, text=True)
        match = re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-fA-F]+)', verification)
        if not match or match[1].lower() != env['XRBENCH_WEB_CERT_SHA256'].lower():
            raise RuntimeError('Web runner signing certificate differs from the pinned distribution identity')
    metadata = dict(apk_sha256=hashlib.sha256(apk.read_bytes()).hexdigest(), suite_revision=revision(),
                    runner_revision=identity, runtime_revision=pins["executorch"]["commit"], package="org.xrgpu.benchmarks.web" if web else "org.xrgpu.benchmarks",
                    device_profiles_version=1, backends=["executorch_vulkan","vulkan_resident","executorch_cpu","cpu_resident"], kernel_experiments_version=1, candidate_revision=kernels.source_identity(), kernel_package=kernel_package.provenance())
    if web:
        metadata.update(version_code=int(env['XRBENCH_WEB_VERSION_CODE']), signing_certificate_sha256=match[1].lower())
    save(build_dir / ('web-apk.json' if web else 'apk.json'), metadata)
    print(f"Native benchmark APK: {apk}")


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Build the dedicated layer APK using the repository's pinned Android toolchain.")
    parser.add_argument("--jobs",type=int,default=4)
    parser.add_argument('--web', action='store_true', help='Build the signed, separately installed browser runner')
    args=parser.parse_args()
    build(args.jobs, web=args.web)
