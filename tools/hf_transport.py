# /// script
# requires-python = ">=3.11"
# dependencies = ["huggingface_hub==2.3.0"]
# ///
"""Isolated transport for prepared xrML kernel releases; never a runtime loader.

Canonical producer copy: xrGPU/hub/transport.py. Both copies are byte-identical.
Run with ``uv run --isolated --locked --script tools/hf_transport.py``.
"""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sys
import tempfile

ENDPOINT = "https://huggingface.co"
INVENTORY = "hub-inventory.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def relative(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./+-]+", value):
        raise ValueError("Invalid release path")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
    if any(p in {"", ".", ".."} or p.endswith(".") or p.split(".")[0].upper() in reserved for p in value.split("/")):
        raise ValueError("Invalid release path")
    return PurePosixPath(value)


def checked_file(root, name):
    target = root
    if root.is_symlink():
        raise ValueError("Links are not valid release files")
    for part in relative(name).parts:
        target = target / part
        if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
            raise ValueError("Links are not valid release files")
    if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Missing release file")
    return target


def digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_table(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("Release must have a nonempty file inventory")
    if len({name.casefold() for name in value if isinstance(name, str)}) != len(value):
        raise ValueError("Ambiguous release paths")
    for name, digest in value.items():
        relative(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid release checksum")
    return value


def credentials(env_file=None):
    """Accept the user's spelling without sourcing shell code or saving a login."""
    values = {}
    if env_file and Path(env_file).is_file():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*(?:export\s+)?(HF_TOKEN|HF_token)\s*=\s*(.*)$", line)
            if match:
                parsed = shlex.split(match[2], comments=True)
                if len(parsed) > 1:
                    raise ValueError("Invalid HF token assignment")
                values[match[1]] = parsed[0] if parsed else ""
    return os.environ.get("HF_TOKEN") or os.environ.get("HF_token") or values.get("HF_TOKEN") or values.get("HF_token") or False


def repo_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"xrML/[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError("Only xrML kernel repositories are supported")
    return value


def fetch(api, download, args):
    repo_id(args.repo_id)
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise ValueError("A full immutable Hub revision is required")
    if not re.fullmatch(r"[0-9a-f]{64}", args.manifest_sha256):
        raise ValueError("A pinned manifest checksum is required")
    prefix = "" if args.package_path == "." else relative(args.package_path).as_posix() + "/"
    output = Path(args.output)
    if output.exists():
        raise ValueError("Fetch output must be a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Resolve the requested commit independently before trusting any bytes.
    info = api.repo_info(args.repo_id, repo_type="kernel", revision=args.revision)
    if info.sha != args.revision:
        raise ValueError("Hub revision differs from the pin")
    with tempfile.TemporaryDirectory(prefix=".hub-download-", dir=output.parent) as temporary:
        root = Path(temporary)
        downloaded, package = root / "download", root / "package"
        package.mkdir()
        def get(name):
            return Path(download(repo_id=args.repo_id, filename=prefix + relative(name).as_posix(),
                                 repo_type="kernel", revision=args.revision, local_dir=downloaded,
                                 token=api.token, endpoint=ENDPOINT))
        manifest_path = get("manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        identity = {k: v for k, v in manifest.items() if k != args.digest_field} if args.digest_field else manifest
        if hashlib.sha256(canonical(identity)).hexdigest() != args.manifest_sha256:
            raise ValueError("Hub manifest differs from its pinned checksum")
        files = file_table(manifest.get("files"))
        if "manifest.json" in files:
            raise ValueError("A manifest cannot inventory itself")
        shutil.copyfile(manifest_path, package / "manifest.json")
        for name, expected in files.items():
            source = get(name)
            if source.is_symlink() or digest_file(source) != expected:
                raise ValueError("Hub file differs from its pinned checksum")
            target = package.joinpath(*relative(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        package.rename(output)
    return dict(repo_id=args.repo_id, revision=args.revision, path=str(output), manifest_sha256=args.manifest_sha256)


def verify_prepared(directory):
    root = Path(directory)
    manifest = json.loads(checked_file(root, INVENTORY).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported publication inventory")
    files = file_table(manifest.get("files"))
    if INVENTORY in files:
        raise ValueError("Publication inventory cannot include itself")
    for name, expected in files.items():
        parts = relative(name).parts
        if any(p.startswith(".") and p != ".gitattributes" for p in parts) or Path(name).suffix.lower() in {".key", ".pem", ".keystore", ".jks"}:
            raise ValueError("Credential or hidden files are not valid publication artifacts")
        if digest_file(checked_file(root, name)) != expected:
            raise ValueError("Prepared release checksum mismatch")
    for path in root.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Links are not valid release files")
        if path.is_file() and path.relative_to(root).as_posix() not in {*files, INVENTORY}:
            raise ValueError("Uninventoried file in prepared release")
    return root, files


def publish(api, operation, args):
    repo_id(args.repo_id)
    root, files = verify_prepared(args.directory)
    if not api.token:
        raise ValueError("Publishing requires HF_TOKEN or HF_token")
    from huggingface_hub.errors import RepositoryNotFoundError
    try:
        info = api.repo_info(args.repo_id, repo_type="kernel")
    except RepositoryNotFoundError:
        api.create_repo(args.repo_id, repo_type="kernel", private=False)
        info = api.repo_info(args.repo_id, repo_type="kernel")
    if info.private:
        raise ValueError("Existing repository is private; its visibility will not be changed")
    if args.parent_commit and info.sha != args.parent_commit:
        raise ValueError("Destination changed after release preparation")
    operations = [operation(path_in_repo=name, path_or_fileobj=str(root / name)) for name in sorted([*files, INVENTORY])]
    result = api.create_commit(repo_id=args.repo_id, repo_type="kernel", operations=operations,
                               commit_message=args.message, parent_commit=args.parent_commit or info.sha)
    return dict(repo_id=args.repo_id, revision=result.oid, url=f"{ENDPOINT}/kernels/{args.repo_id}", private=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("fetch")
    p.add_argument("--repo-id", required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--package-path", default=".")
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--digest-field")
    p.add_argument("--output", required=True, type=Path)
    p = commands.add_parser("publish")
    p.add_argument("--repo-id", required=True)
    p.add_argument("--directory", required=True, type=Path)
    p.add_argument("--message", default="Publish verified native xrGPU release")
    p.add_argument("--parent-commit")
    p = commands.add_parser("probe")
    p.add_argument("--namespace", default="xrML", choices=["xrML"])
    args = parser.parse_args(argv)
    # Set before importing the isolated SDK. It never writes login credentials.
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    logging.disable(logging.CRITICAL)
    try:
        from huggingface_hub import HfApi, hf_hub_download, CommitOperationAdd
        api = HfApi(endpoint=ENDPOINT, token=credentials(args.env_file))
        if args.command == "fetch":
            result = fetch(api, hf_hub_download, args)
        elif args.command == "publish":
            result = publish(api, CommitOperationAdd, args)
        else:
            identity = api.whoami()
            result = dict(account=identity.get("name"), organizations=[
                dict(name=org.get("name"), role=org.get("roleInOrg"))
                for org in identity.get("orgs", []) if org.get("name") == args.namespace])
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError):
        # Only our own validation messages are safe to print; SDK messages and
        # OSError paths can contain a credential-bearing URL or remote content.
        print("xrML transport failed: invalid credentials, release, paths, or local files.", file=sys.stderr)
        return 1
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        suffix = f" (HTTP {status})" if isinstance(status, int) else ""
        print("xrML Hub request failed" + suffix + "; check network, token permissions, and kernel-publishing approval.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
