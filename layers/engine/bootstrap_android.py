import hashlib
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path
from zipfile import ZipFile

from layers.engine.paths import ROOT
BASE = ROOT / "artifacts/toolchain"

# Google's repository2-3.xml checksums for the pinned Android packages.
HOST_ARCHIVES = {
    "Windows": ("windows", "win", "086bba43ff2f5eb0e387b15c8278bb4e0d89ba1d",
                "af059bb67cf7786f45ee0db85e2d24985df1b4b6", "e03e78b1d80b396f1c3358e31251cb31740e1110"),
    "Linux": ("linux", "linux", "a7b54a5de87fecd125a17d54f73c446199e72a64",
              "2cfaa0bbb2336e9ec18ed3ecea84fa2e2af607bc", "477254aa5f903c15cf51001717bdf347fb6b53e0"),
    "Darwin": ("darwin", "darwin", "fc20a6bf15a30fb3428c9b60a7308793a362dc6d",
               "93ab8ce91230e067b5add4bfa79919c52b27f072", "6ae73f4de6452dc57e62ec02b68eed92a4c21661"),
}


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
    if platform.system() == "Windows":
        with ZipFile(archive) as zip_file:
            zip_file.extractall(staging)
    else:
        # Preserve executable bits and symlinks on Unix hosts.
        subprocess.run(["unzip", "-qo", str(archive), "-d", str(staging)], check=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    (staging / folder).rename(target)
    shutil.rmtree(staging)


def main():
    system = platform.system()
    if system not in HOST_ARCHIVES:
        raise RuntimeError(f"Unsupported Android build host: {system}")
    ndk_host, adb_host, ndk_sha, build_sha, adb_sha = HOST_ARCHIVES[system]
    base = "https://dl.google.com/android/repository/"
    sdk = BASE / "android-sdk"
    unzip(
        base + f"android-ndk-r28c-{ndk_host}.zip",
        f"ndk-{ndk_host}.zip",
        ndk_sha,
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
        base + f"build-tools_r35_{'macosx' if system == 'Darwin' else ndk_host}.zip",
        f"build-tools-{ndk_host}.zip",
        build_sha,
        sdk / "build-tools/35.0.0",
        "android-15",
    )
    unzip(
        base + f"platform-tools_r37.0.1-{adb_host}.zip",
        f"platform-tools-{adb_host}.zip",
        adb_sha,
        sdk / "platform-tools",
        "platform-tools",
    )
    gradle_url = "https://services.gradle.org/distributions/gradle-8.11.1-bin.zip"
    checksum = "f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6"
    unzip(gradle_url, "gradle.zip", checksum, BASE / "gradle", "gradle-8.11.1", "sha256")
    if system == "Windows":
        print(f"Android toolchain ready at {BASE}. Run python -m layers.engine.bootstrap_shaderc for the host shader compiler.", flush=True)
        return
    glslc = BASE / "shaderc/host/bin/glslc"
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
