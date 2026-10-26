"""Build the small native-operator APK used by the website's automatic install."""

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

from engine.paths import ROOT, ENGINE, BUILD
from engine.reports import canonical, save
from engine import native_release, native_suite


def signing_package(certificate):
    if not isinstance(certificate, str) or not re.fullmatch(r"[a-f0-9]{64}", certificate):
        raise ValueError("Expected a pinned runner signing certificate")
    # Different signing identities cannot block installation or erase old evidence.
    return "org.xrgpu.benchmarks.web.s" + certificate


def build(jobs=4, *, web=True, package=None, local=False):
    from engine.build import build_environment

    pins = native_release.pins()
    toolchain = ROOT / "artifacts/toolchain"
    if (
        not (toolchain / "android-sdk/ndk" / pins["android"]["ndk"]).is_dir()
        or not (toolchain / "gradle/bin").is_dir()
    ):
        from engine.bootstrap_android import main as prepare_android

        prepare_android()
    package = native_release.resolve(package, jobs=jobs)
    package_manifest = native_release.verify_native(package)
    json_include = native_release.json_headers()
    output = BUILD / "public-native"
    output.mkdir(parents=True, exist_ok=True)
    assets = output / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    assets.mkdir()
    suite = native_suite.prepare(package, assets / "native-operators")
    source_files = [p for p in (ENGINE / "public_native").rglob("*") if p.is_file()]
    source_files += list((ENGINE / "android/app/src/main").rglob("*.java"))
    source_files += [
        ENGINE / "android/app/src/main/AndroidManifest.xml",
        ENGINE / "android/app/build.gradle",
        Path(__file__),
        ENGINE / "native_suite.py",
        ENGINE / "native_release.py",
        ROOT / "dependencies.json",
    ]
    identity = hashlib.sha256(
        canonical(
            {p.relative_to(ROOT).as_posix(): native_suite.sha(p) for p in sorted(source_files)}
        )
    ).hexdigest()
    sdk, ndk, env = build_environment(ROOT / "artifacts/toolchain", pins)
    if local:
        from engine.local_release import signing_environment

        env = signing_environment(env)
    certificate = env.get("XRBENCH_WEB_CERT_SHA256", "").lower()
    if web:
        required = [
            "XRBENCH_WEB_KEYSTORE",
            "XRBENCH_WEB_STORE_PASSWORD",
            "XRBENCH_WEB_KEY_ALIAS",
            "XRBENCH_WEB_KEY_PASSWORD",
            "XRBENCH_WEB_VERSION_CODE",
        ]
        if any(not env.get(key) for key in required) or not re.fullmatch(
            r"[a-f0-9]{64}", certificate
        ):
            raise RuntimeError(
                "The automated release needs its persistent signing secrets; visitors never supply them"
            )
        package_name = signing_package(certificate)
    else:
        package_name = "org.xrgpu.benchmarks"
    configuration = dict(
        protocol_version=2,
        suite_revision=suite["suite_revision"],
        runner_revision=identity,
        runtime_revision=pins["xrgpu_native"]["commit"],
        native_package_sha256=package_manifest["package_sha256"],
        native_catalog_sha256=suite["native_catalog_sha256"],
        assets_inventory_sha256=native_suite.sha(assets / "native-operators/inventory.json"),
    )
    save(assets / "native-config.json", configuration)
    shutil.copyfile(
        native_release.json_headers().parent / "LICENSE.MIT", assets / "native-json-LICENSE.MIT"
    )
    cmake = shutil.which("cmake", path=env["PATH"])
    if not cmake:
        raise FileNotFoundError("CMake is required by the automated native release")
    native = output / "build"
    subprocess.run(
        [
            cmake,
            "-S",
            str(ENGINE / "public_native"),
            "-B",
            str(native),
            "-DXRGPU_NATIVE_PACKAGE=" + str(package),
            "-DNLOHMANN_JSON_INCLUDE=" + str(json_include),
            "-DCMAKE_TOOLCHAIN_FILE=" + str(ndk / "build/cmake/android.toolchain.cmake"),
            "-DANDROID_ABI=arm64-v8a",
            "-DANDROID_PLATFORM=android-29",
            "-DANDROID_STL=c++_shared",
            "-DCMAKE_BUILD_TYPE=Release",
        ],
        env=env,
        check=True,
    )
    subprocess.run([cmake, "--build", str(native), "-j", str(jobs)], env=env, check=True)
    libs = output / "lib/arm64-v8a"
    libs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        native / "libxrbench_native_operators.so", libs / "libxrbench_native_operators.so"
    )
    for name in ["libxrgpu_operators.so", "libxrgpu_extended_operators.so", "libc++_shared.so"]:
        shutil.copyfile(package / "lib/android-arm64" / name, libs / name)
    variant = "web" if web else "debug"
    gradle = (
        ROOT / "artifacts/toolchain/gradle/bin" / ("gradle.bat" if os.name == "nt" else "gradle")
    )
    subprocess.run(
        [
            str(gradle),
            "--no-daemon",
            "-p",
            str(ENGINE / "android"),
            "-PxrbenchPublicNative=true",
            ":app:assemble" + variant.title(),
        ],
        env=env,
        check=True,
    )
    apk = BUILD / ("xrgpu-benchmarks-web.apk" if web else "xrgpu-benchmarks.apk")
    shutil.copyfile(ENGINE / f"android/app/build/outputs/apk/{variant}/app-{variant}.apk", apk)
    if web:
        signer = sdk / "build-tools/35.0.0" / ("apksigner.bat" if os.name == "nt" else "apksigner")
        verification = subprocess.check_output(
            [str(signer), "verify", "--print-certs", str(apk)], env=env, text=True
        )
        match = re.search(r"Signer #1 certificate SHA-256 digest: ([a-fA-F0-9]+)", verification)
        if not match or match[1].lower() != certificate:
            raise ValueError("The signed APK does not match the release certificate")
    metadata = dict(
        configuration,
        package=package_name,
        apk_sha256=native_suite.sha(apk),
        native_backend="xrgpu_vulkan",
        backends=["xrgpu_vulkan"],
        native_source=pins["xrgpu_native"],
        native_suite=suite,
    )
    if web:
        metadata.update(
            version_code=int(env["XRBENCH_WEB_VERSION_CODE"]),
            signing_certificate_sha256=certificate,
        )
    save(BUILD / ("web-apk.json" if web else "apk.json"), metadata)
    return apk
