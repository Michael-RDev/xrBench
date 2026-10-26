"""Read a verified, immutable xrGPU inference package for local qualification.

The benchmark-source dependency remains in ``engine.kernel_package``. This
module handles the distinct compiled inference package, never copies kernels
into xrBench, and never downloads or executes an unverified package.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import types

PACKAGE_ID = "xrgpu.quest3.fp32.v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def contained(root, name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Invalid package-relative path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")):
        raise ValueError("Invalid package-relative path")
    target = root.joinpath(*relative.parts)
    if any(p.is_symlink() for p in (target, *target.parents) if p != root.parent):
        raise ValueError("Symlinks are not valid package files")
    if not target.resolve().is_relative_to(root) or not target.is_file():
        raise ValueError("Missing package file: " + name)
    return target


def verify(directory, expected_sha256=None, runtime_revision=None):
    root = Path(directory).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("package_id") != PACKAGE_ID:
        raise ValueError("Unsupported optimized xrGPU package")
    content = {k: v for k, v in manifest.items() if k != "package_sha256"}
    if digest(content) != manifest.get("package_sha256"):
        raise ValueError("Optimized package manifest digest mismatch")
    if expected_sha256 is not None and manifest["package_sha256"] != expected_sha256:
        raise ValueError("Optimized package differs from campaign pin")
    if runtime_revision and manifest.get("runtime_revision") != runtime_revision:
        raise ValueError("Optimized package runtime revision mismatch")
    if manifest.get("selections") != []:
        raise ValueError("Qualification must remain outside immutable kernel packages")
    if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("Optimized package has no file inventory")
    for name, expected in manifest["files"].items():
        if sha(contained(root, name)) != expected:
            raise ValueError("Optimized package file digest mismatch: " + name)
    for name, shader in manifest.get("shaders", {}).items():
        binary = "spirv/" + name + ".spv"
        if manifest["files"].get(binary) != shader.get("spirv_sha256"):
            raise ValueError("Shader registry and packaged binary differ: " + name)
    return manifest


def qualification_module(directory, expected_sha256=None):
    root = Path(directory).resolve()
    manifest = verify(root, expected_sha256)
    path = contained(root, "integration/qualification.py")
    if "integration/qualification.py" not in manifest["files"]:
        raise ValueError("Package does not contain its shared qualification contract")
    # Avoid __pycache__ writes inside an immutable package.
    module = types.ModuleType("_xrgpu_qualification_" + manifest["package_sha256"])
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module
