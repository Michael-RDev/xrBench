import hashlib
import json
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from layers.engine.paths import ROOT
BASE = ROOT / "artifacts/toolchain"


def download(url, path, expected=None, algorithm="sha256", headers=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        print(f"Downloading {path.name}", flush=True)
        request = urllib.request.Request(url, headers=headers or {})
        temporary = path.with_suffix(path.suffix + ".partial")
        with urllib.request.urlopen(request, timeout=120) as src, temporary.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        temporary.replace(path)
    digest = hashlib.new(algorithm)
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    if expected and digest.hexdigest() != expected:
        raise RuntimeError(f"Checksum mismatch: {path}")


def unzip(url, filename, checksum, target, folder, algorithm="sha1"):
    if target.exists():
        return
    archive = BASE / "downloads" / filename
    download(url, archive, checksum, algorithm)
    staging = BASE / ("unpack-" + filename)
    staging.mkdir(parents=True, exist_ok=True)
    subprocess.run(["unzip", "-qo", str(archive), "-d", str(staging)], check=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    (staging / folder).rename(target)
    shutil.rmtree(staging)


def main():
    base = "https://dl.google.com/android/repository/"
    sdk = BASE / "android-sdk"
    unzip(
        base + "android-ndk-r28c-darwin.zip",
        "ndk.zip",
        "fc20a6bf15a30fb3428c9b60a7308793a362dc6d",
        sdk / "ndk/28.2.13676358",
        "android-ndk-r28c",
    )
    unzip(
        base + "platform-35_r02.zip",
        "platform.zip",
        "0bb560a90a7a2cbd0dd8348224d518b638fe7949",
        sdk / "platforms/android-35",
        "android-35",
    )
    unzip(
        base + "build-tools_r35_macosx.zip",
        "build-tools.zip",
        "93ab8ce91230e067b5add4bfa79919c52b27f072",
        sdk / "build-tools/35.0.0",
        "android-15",
    )
    unzip(
        base + "platform-tools_r37.0.1-darwin.zip",
        "platform-tools.zip",
        "6ae73f4de6452dc57e62ec02b68eed92a4c21661",
        sdk / "platform-tools",
        "platform-tools",
    )
    gradle_url = "https://services.gradle.org/distributions/gradle-8.11.1-bin.zip"
    checksum = "f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6"
    unzip(gradle_url, "gradle.zip", checksum, BASE / "gradle", "gradle-8.11.1", "sha256")
    # A relocatable public Homebrew bottle supplies only the shader compiler.
    shader_sha = "1313ffbae9ac1559f7160cc89657ecd5fc324895ff46e15a4bd198ec3bbebec0"
    archive = BASE / "downloads/shaderc.tar.gz"
    if not archive.exists():
        auth = json.loads(
            urllib.request.urlopen(
                "https://ghcr.io/token?service=ghcr.io&scope=repository:homebrew/core/shaderc:pull"
            ).read()
        )
        download(
            "https://ghcr.io/v2/homebrew/core/shaderc/blobs/sha256:" + shader_sha,
            archive,
            shader_sha,
            headers={"Authorization": "Bearer " + auth["token"]},
        )
    download(
        "https://ghcr.io/v2/homebrew/core/shaderc/blobs/sha256:" + shader_sha, archive, shader_sha
    )
    if not (BASE / "shaderc").exists():
        with tarfile.open(archive) as tar:
            tar.extractall(BASE, filter="data")
    glslc = next((BASE / "shaderc").glob("*/bin/glslc"))
    env_file = BASE / "env.sh"
    import shlex

    q = shlex.quote
    env_file.write_text(
        f"export ANDROID_HOME={q(str(sdk))}\n"
        f"export ANDROID_NDK={q(str(sdk / 'ndk/28.2.13676358'))}\n"
        f"export GRADLE_USER_HOME={q(str(ROOT / 'artifacts/cache/gradle'))}\n"
        f'export PATH={q(str(glslc.parent))}:{q(str(BASE / "gradle/bin"))}:{q(str(sdk / "platform-tools"))}:"$PATH"\n'
    )
    print(f"Toolchain ready. Source {env_file}", flush=True)


if __name__ == "__main__":
    main()
