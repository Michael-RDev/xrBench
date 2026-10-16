import json
import subprocess

from layers.engine.paths import ENGINE, ROOT

SUBMODULES = [
    "backends/vulkan/third-party/Vulkan-Headers",
    "backends/vulkan/third-party/VulkanMemoryAllocator",
    "backends/vulkan/third-party/volk", "third-party/flatbuffers",
    "third-party/flatcc", "third-party/json", "third-party/pocketfft",
    "third-party/gflags", "backends/xnnpack/third-party/FXdiv",
    "backends/xnnpack/third-party/cpuinfo", "backends/xnnpack/third-party/pthreadpool",
    "backends/xnnpack/third-party/FP16", "backends/xnnpack/third-party/XNNPACK",
]


def git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def verify():
    pins = json.loads((ROOT / "dependencies.json").read_text())
    for name in ("executorch", "openxr"):
        source = ROOT / "artifacts/deps" / name
        if git("rev-parse", "HEAD", cwd=source).stdout.strip() != pins[name]["commit"]:
            raise RuntimeError(f"Pinned dependency mismatch: {name}")
        changed = set(git("diff", "HEAD", "--name-only", cwd=source).stdout.splitlines())
        expected = {"backends/vulkan/runtime/VulkanBackend.cpp"} if name == "executorch" else set()
        if changed != expected:
            raise RuntimeError(f"Unexpected dependency modifications: {name}: {sorted(changed)}")
        if name == "executorch":
            patch = ENGINE / "patches/0001-capability-timestamps.patch"
            git("apply", "--reverse", "--check", str(patch), cwd=source)
            backend = "backends/vulkan/runtime/VulkanBackend.cpp"
            original = git("show", f"HEAD:{backend}", cwd=source).stdout
            expected_text = ('#include "timing.h"\n' + original).replace("config.enable_querypool = true;", "config.enable_querypool = xrbench::gpu_timestamps_supported;")
            if (source / backend).read_text() != expected_text:
                raise RuntimeError("Vulkan backend has changes beyond capability-gated timestamps")
            extras = git("ls-files", "--others", "--exclude-standard", cwd=source).stdout.splitlines()
            if any(p.endswith((".glsl", ".yaml", ".cpp", ".h")) for p in extras):
                raise RuntimeError("Untracked native sources in dependency checkout; stock kernels required")


def prepare():
    pins = json.loads((ROOT / "dependencies.json").read_text())
    for name in ("executorch", "openxr"):
        spec = pins[name]
        dest = ROOT / "artifacts/deps" / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            git("clone", "--depth", "1", "--branch", spec["ref"], spec["url"], str(dest), cwd=ROOT)
        if git("rev-parse", "HEAD", cwd=dest).stdout.strip() != spec["commit"]:
            raise RuntimeError(f"Unexpected {name} revision; refusing to reset")
        if name == "executorch":
            git("submodule", "update", "--init", "--depth", "1", *SUBMODULES, cwd=dest)
    source = ROOT / "artifacts/deps/executorch"
    patch = ENGINE / "patches/0001-capability-timestamps.patch"
    if git("apply", "--reverse", "--check", str(patch), cwd=source, check=False).returncode:
        git("apply", "--check", str(patch), cwd=source)
        git("apply", str(patch), cwd=source)
    verify()
    print("Pinned stock native dependencies ready.")


if __name__ == "__main__":
    prepare()
