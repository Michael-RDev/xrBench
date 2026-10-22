"""Fetch and verify the immutable xrGPU sources used to build the runner.

Only preparation/build commands fetch. Provenance and APK checks read the pin,
so starting a benchmark never requires a network or a source checkout.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile

from engine.paths import ROOT

PINS = ROOT / "dependencies.json"
CACHE = ROOT / "artifacts/deps/xrgpu"
PACKAGE_ID = "xrgpu.benchmark-kernels.v1"
REPOSITORY = "https://github.com/Michael-RDev/xrGPU.git"
REQUIRED = frozenset({
    "matmul/config.json", "matmul/xrbench_matmul_scalar.glsl",
    "matmul/xrbench_matmul_scalar.yaml", "matmul/xrbench_matmul_vec.glsl",
    "matmul/xrbench_matmul_vec.yaml", "cpu/matmul.h", "resident/matmul.comp",
    "calibration/compute.comp", "calibration/memory.comp", "calibration/dispatch.comp", "LICENSE",
})


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def relative_path(value):
    # Also reject Windows aliases and drive/ADS syntax on Unix build hosts.
    if not isinstance(value, str) or not value or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        raise ValueError("Invalid xrGPU package path")
    parts = value.split("/")
    devices = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
    if any(p in ("", ".", "..") or p.endswith(".") or p.split(".")[0].upper() in devices for p in parts):
        raise ValueError("Invalid xrGPU package path")
    return PurePosixPath(value)


def specification():
    pins = json.loads(PINS.read_text(encoding="utf-8"))
    spec = pins.get("xrgpu", {})
    if (spec.get("url") != REPOSITORY or spec.get("package_id") != PACKAGE_ID
            or not re.fullmatch(r"[0-9a-f]{40}", spec.get("commit", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", spec.get("content_sha256", ""))):
        raise ValueError("Invalid pinned xrGPU kernel dependency")
    relative_path(spec.get("package_path"))
    return spec, pins["executorch"]["commit"]


def provenance():
    spec, _ = specification()
    return dict(repository=spec["url"], commit=spec["commit"], package_id=spec["package_id"],
                package_path=spec["package_path"], content_sha256=spec["content_sha256"])


def source_url(relative):
    spec, _ = specification()
    relative_path(relative)
    return f'{spec["url"].removesuffix(".git")}/blob/{spec["commit"]}/{spec["package_path"]}/{relative}'


def check_metadata(metadata):
    if metadata.get("kernel_package") != provenance():
        raise ValueError("APK kernel package is outdated. Rebuild the benchmark APK from the pinned xrGPU sources.")


def contained_file(root, name):
    parts = relative_path(name).parts
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
            raise ValueError("Links are not allowed in the xrGPU kernel package")
    if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError("Missing xrGPU package file: " + name)
    return target


def verify(directory, spec=None, runtime_revision=None):
    if spec is None:
        spec, runtime_revision = specification()
    root = Path(directory)
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ValueError("Links are not allowed in the xrGPU kernel package")
    manifest = json.loads(contained_file(root, "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1 or manifest.get("package_id") != PACKAGE_ID
            or hashlib.sha256(canonical(manifest)).hexdigest() != spec["content_sha256"]):
        raise ValueError("xrGPU kernel manifest identity or checksum mismatch")
    if manifest.get("runtime_revision") != runtime_revision:
        raise ValueError("xrGPU kernel package requires a different ExecuTorch revision")
    files = manifest.get("files")
    if not isinstance(files, dict) or not REQUIRED.issubset(files):
        raise ValueError("xrGPU kernel package is missing required files")
    if len({name.casefold() for name in files}) != len(files):
        raise ValueError("Ambiguous xrGPU package paths")
    for name, expected in files.items():
        target = contained_file(root, name)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid xrGPU file checksum")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("xrGPU kernel file checksum mismatch: " + name)
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Links are not allowed in the xrGPU kernel package")
        if path.is_file() and name not in files and name not in {"manifest.json", "README.md"}:
            raise ValueError("Unmanifested xrGPU package file: " + name)
    return root


def git_environment():
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    token = env.pop("XRGPU_READ_TOKEN", "")
    if token:
        # Git consumes transient config from the environment, never a command
        # argument, credential-bearing URL, or persistent repository config.
        index = int(env.get("GIT_CONFIG_COUNT", "0"))
        env[f"GIT_CONFIG_KEY_{index}"] = "http.https://github.com/.extraheader"
        encoded = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env[f"GIT_CONFIG_VALUE_{index}"] = "Authorization: Basic " + encoded
        env["GIT_CONFIG_COUNT"] = str(index + 1)
    return env


def git(*args, cwd, env=None):
    try:
        result = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd,
                                env=env, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Could not run Git for the pinned xrGPU kernel package") from None
    if result.returncode:
        # Git/credential helper diagnostics may contain credentials. Do not echo.
        raise RuntimeError("Cannot retrieve the pinned xrGPU commit. Check network access, commit availability, "
                           "and XRGPU_READ_TOKEN or your Git credential helper.")
    return result.stdout.strip()


def resolve(*, fetch=False):
    spec, runtime = specification()
    checkout = CACHE / spec["commit"]
    package = checkout / spec["package_path"]
    if checkout.exists():
        if checkout.is_symlink() or (hasattr(checkout, "is_junction") and checkout.is_junction()):
            raise ValueError("xrGPU dependency cache must not be a link")
        # Check each ancestor of the package, not only its final directory.
        contained_file(checkout, spec["package_path"] + "/manifest.json")
        return verify(package, spec, runtime)
    if not fetch:
        raise FileNotFoundError("Pinned xrGPU kernels are not cached. Run python -m engine.kernel_package fetch.")
    CACHE.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".fetch-", dir=CACHE))
    try:
        env = git_environment()
        git("init", "--quiet", cwd=staging, env=env)
        git("fetch", "--quiet", "--depth", "1", spec["url"], spec["commit"], cwd=staging, env=env)
        if git("rev-parse", "FETCH_HEAD", cwd=staging, env=env) != spec["commit"]:
            raise ValueError("Fetched xrGPU commit does not match the dependency pin")
        git("checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=staging, env=env)
        contained_file(staging, spec["package_path"] + "/manifest.json")
        verify(staging / spec["package_path"], spec, runtime)
        try:
            staging.rename(checkout)
        except FileExistsError:
            # Another build may have fetched the same immutable package.
            return resolve()
        return package
    finally:
        if staging.exists():
            # Staging is created directly inside CACHE and never user-selected.
            # Git objects can be read-only on Windows.
            if staging.is_symlink() or not staging.resolve().is_relative_to(CACHE.resolve()):
                raise ValueError("Refusing to remove an xrGPU staging path outside the dependency cache")
            def writable(function, path, _):
                os.chmod(path, 0o700)
                function(path)
            shutil.rmtree(staging, onerror=writable)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "verify"))
    args = parser.parse_args()
    try:
        directory = resolve(fetch=args.command == "fetch")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(dict(path=str(directory), **provenance()), indent=2))


if __name__ == "__main__":
    main()
