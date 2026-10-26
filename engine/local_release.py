"""Persistent local signing and automatic preparation of the browser release."""

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time

from engine.paths import ROOT, ENGINE, STATE, BUILD


def signing_environment(env):
    """Keep the same identity across local builds; secrets never enter arguments."""
    directory = STATE / "browser-signing"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    metadata = directory / "signing.json"
    key = directory / "runner.keystore"
    if metadata.exists():
        value = json.loads(metadata.read_text())
        if not key.is_file() and value.get("version_code", 0) > 0:
            raise RuntimeError(
                "The local runner signing key is missing; restore its browser-signing directory to preserve its identity"
            )
    else:
        if key.exists():
            raise RuntimeError("Local signing metadata is missing; the existing key was preserved")
        value = dict(password=secrets.token_urlsafe(32), alias="xrbench-local", version_code=0)
        metadata.write_text(json.dumps(value) + "\n")
        metadata.chmod(0o600)
    result = dict(env, XRBENCH_LOCAL_SIGN_PASSWORD=value["password"])
    keytool = Path(env["JAVA_HOME"]) / "bin" / ("keytool.exe" if os.name == "nt" else "keytool")
    if not key.exists():
        subprocess.run(
            [
                str(keytool),
                "-genkeypair",
                "-keystore",
                str(key),
                "-storepass:env",
                "XRBENCH_LOCAL_SIGN_PASSWORD",
                "-keypass:env",
                "XRBENCH_LOCAL_SIGN_PASSWORD",
                "-alias",
                value["alias"],
                "-keyalg",
                "RSA",
                "-keysize",
                "2048",
                "-validity",
                "10000",
                "-dname",
                "CN=xrBench Local,O=xrBench,C=US",
                "-noprompt",
            ],
            env=result,
            check=True,
            capture_output=True,
        )
        key.chmod(0o600)
    certificate = subprocess.check_output(
        [
            str(keytool),
            "-list",
            "-v",
            "-keystore",
            str(key),
            "-storepass:env",
            "XRBENCH_LOCAL_SIGN_PASSWORD",
            "-alias",
            value["alias"],
        ],
        env=result,
        text=True,
    )
    match = re.search(r"SHA256:\s*([A-Fa-f0-9:]+)", certificate)
    if not match:
        raise RuntimeError("Cannot verify the local runner signing identity")
    value["version_code"] += 1
    metadata.write_text(json.dumps(value) + "\n")
    metadata.chmod(0o600)
    result.update(
        XRBENCH_WEB_KEYSTORE=str(key),
        XRBENCH_WEB_STORE_PASSWORD=value["password"],
        XRBENCH_WEB_KEY_PASSWORD=value["password"],
        XRBENCH_WEB_KEY_ALIAS=value["alias"],
        XRBENCH_WEB_CERT_SHA256=match[1].replace(":", "").lower(),
        XRBENCH_WEB_VERSION_CODE=str(value["version_code"]),
    )
    return result


def source_identity():
    paths = [
        p
        for folder in [
            ENGINE / "public_native",
            ENGINE / "android/app/src/main",
            ROOT / "src/bench_web/web",
            ROOT / "server",
        ]
        for p in folder.rglob("*")
        if p.is_file()
    ]
    paths += [
        ROOT / "package-lock.json",
        ROOT / "dependencies.json",
        ENGINE / "public_build.py",
        ENGINE / "native_suite.py",
        ENGINE / "native_release.py",
        ENGINE / "android/app/build.gradle",
        ENGINE / "web_distribution.py",
        ROOT / "scripts/build-web.mjs",
    ]
    value = hashlib.sha256()
    for path in sorted(paths):
        value.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return value.hexdigest()


def ensure_release(*, force=False):
    identity = source_identity()
    stamp = BUILD / "public-release.json"
    if not force and stamp.is_file():
        value = json.loads(stamp.read_text())
        required = [
            ROOT / "dist/index.html",
            ROOT / "dist/bench/app.js",
            ROOT / "dist/runner/manifest.json",
        ]
        if value.get("source_sha256") == identity and all(p.is_file() for p in required):
            manifest = json.loads(required[-1].read_text())
            apk = ROOT / "dist/runner" / manifest["apk"]
            if (
                apk.is_file()
                and hashlib.sha256(apk.read_bytes()).hexdigest() == manifest["apk_sha256"]
            ):
                return ROOT / "dist"
    from engine.public_build import build

    print("Preparing the verified native browser runner…", flush=True)
    previous = ROOT / "dist/runner/manifest.json"
    if previous.is_file():
        manifest = json.loads(previous.read_text())
        checksum = manifest.get("apk_sha256", "")
        if re.fullmatch(r"[a-f0-9]{64}", checksum) and manifest.get("apk") == checksum + ".apk":
            apk = previous.parent / manifest["apk"]
            if apk.is_file() and hashlib.sha256(apk.read_bytes()).hexdigest() == checksum:
                archive = STATE / "browser-releases"
                archive.mkdir(parents=True, exist_ok=True)
                (archive / (checksum + ".json")).write_text(json.dumps(manifest) + "\n")
    build(web=True, local=True)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("The local website needs its installed Node runtime")
    if not (ROOT / "node_modules/esbuild/package.json").is_file():
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("Install Node.js with npm before opening the local website")
        subprocess.run([npm, "ci"], cwd=ROOT, check=True)
    subprocess.run([node, "scripts/build-web.mjs"], cwd=ROOT, check=True)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps(dict(source_sha256=identity, built_at=time.time())) + "\n")
    return ROOT / "dist"
