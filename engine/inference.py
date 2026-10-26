"""Prepare, release and accept native models using the kernels qualified by xrBench.

This is the native Session pipeline. All artifacts and evidence stay local.
Preparing models or passing host acceptance never grants GPU qualification.
"""

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

import numpy as np

from engine.adb import ADB
from engine.optimized_package import contained, digest, qualification_module, sha, verify
from engine.paths import ROOT
from engine.qualification_campaign import assess, prepare as prepare_campaign, target_identity
from engine.reports import save


def xrgpu_modules(source):
    source = Path(source).resolve()
    if not (source / "runtime/model_bundle.py").is_file():
        raise ValueError("An xrGPU checkout with the native model exporter is required")
    sys.path.insert(0, str(source))
    try:
        modules = [importlib.import_module("runtime." + name) for name in ("model_bundle", "kernel_package")]
    finally:
        sys.path.pop(0)
    if any(not Path(m.__file__).resolve().is_relative_to(source / "runtime") for m in modules):
        raise ValueError("Another xrGPU checkout was already imported")
    return modules


def command(source, arguments, log):
    source, log = Path(source).resolve(), Path(log)
    python = source / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise FileNotFoundError("Synchronize the xrGPU project environment first")
    log.parent.mkdir(parents=True, exist_ok=True)
    print("xrGPU: " + shlex.join(arguments), flush=True)
    with log.open("w") as stream:
        result = subprocess.run([str(python), "-B", "-m", *map(str, arguments)], cwd=source,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                                stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError("xrGPU command failed; inspect " + str(log))


def prepare(source, package, output, bundles=(), fixtures=None, allow_download=False):
    """Bind exported model and regression programs to one immutable package."""
    import torch

    torch.set_num_threads(1)
    source, package, output = map(lambda p: Path(p).resolve(), (source, package, output))
    exporter, kernels = xrgpu_modules(source)
    manifest = kernels.verify_implementation(package)
    if output.exists():
        raise FileExistsError("Choose a new pipeline directory; existing inputs are preserved")
    output.mkdir(parents=True)
    if not bundles:
        arguments = ["models.export", "--output", str(output / "exported-models")]
        if not allow_download:
            arguments.append("--offline")
        command(source, arguments, output / "logs/models.log")
        bundles = [output / "exported-models" / n for n in
                   ("minilm-32", "minilm-128", "minilm-256", "mobilenetv2")]
    names, models = set(), []
    for bundle in bundles:
        bundle = Path(bundle).resolve()
        if bundle.name in names:
            raise ValueError("Model bundle directory names must be unique")
        names.add(bundle.name)
        print("Check and bind model: " + bundle.name, flush=True)
        destination = output / "models" / bundle.name
        value = exporter.bind_package(bundle, package, destination, execute_cpu=True)
        if "vulkan" not in value["programs"]:
            raise ValueError("Model has no Vulkan program: " + bundle.name)
        models.append(dict(id="model_" + bundle.name, type="model", bundle=str(destination),
                           model_id=value["model_id"]))
    if fixtures is None:
        from engine.qualification_fixtures import export
        workloads = export(package, source, output / "fixtures")
    else:
        fixtures = Path(fixtures).resolve()
        workloads, rebound = [], {}
        for original in json.loads(fixtures.read_text()):
            row = dict(original)
            bundle = Path(row["bundle"]).resolve()
            if bundle not in rebound:
                destination = output / "fixtures" / str(len(rebound))
                exporter.bind_package(bundle, package, destination)
                rebound[bundle] = destination
            row["bundle"] = str(rebound[bundle])
            workloads.append(row)
    save(output / "workloads.json", workloads + models)
    value = dict(schema_version=1, kind="xrbench.native.pipeline", xrgpu_source=str(source),
                 package=str(package), package_sha256=manifest["package_sha256"],
                 runtime_revision=manifest["runtime_revision"], workloads=str(output / "workloads.json"),
                 workloads_sha256=sha(output / "workloads.json"),
                 status="prepared", hardware_qualification="pending")
    value["pipeline_sha256"] = digest(value)
    save(output / "pipeline.json", value)
    return value


def campaign(pipeline, target, output):
    value = json.loads(Path(pipeline).read_text())
    if value.get("kind") != "xrbench.native.pipeline" or value.get("pipeline_sha256") != digest(
            {k: v for k, v in value.items() if k != "pipeline_sha256"}):
        raise ValueError("Pipeline manifest changed after preparation")
    if sha(value["workloads"]) != value["workloads_sha256"]:
        raise ValueError("Prepared workload inventory changed")
    return prepare_campaign(value["package"], value["package_sha256"],
                            json.loads(Path(value["workloads"]).read_text()), target, output)


def release(source, campaign_path, shortlist, reports, output, jobs=2):
    """Build a production runtime from assessed original hardware evidence."""
    result = assess(campaign_path, reports, shortlist)
    record = result["record"]
    if record["status"] != "qualified":
        raise ValueError("Hardware qualification pending: " + ", ".join(result["missing"]))
    plan = json.loads(Path(campaign_path).read_text())
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", w["id"])
           for w in plan["workloads"] if w["type"] == "model"):
        raise ValueError("Release model workload IDs must be safe directory names")
    _, kernels = xrgpu_modules(source)
    kernel = kernels.verify_implementation(plan["package"])
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Choose a new release directory")
    output.mkdir(parents=True)
    packaged = output / "kernel-package"
    packaged.mkdir()
    for name in ("manifest.json", *kernel["files"]):
        target = packaged / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contained(Path(plan["package"]).resolve(), name), target)
    save(output / "qualification.json", record)
    save(output / "assessment.json", result)
    for path in reports:
        path = Path(path)
        destination = output / "evidence" / (sha(path) + ".json")
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(path, destination)
    shutil.copyfile(campaign_path, output / "campaign.json")
    shutil.copyfile(shortlist, output / "shortlist.json")
    command(source, ["runtime.build", "--library", "--package", str(packaged),
                     "--qualification", str(output / "qualification.json"), "--output", str(output / "runtime"),
                     "--jobs", str(jobs)], output / "build.log")
    runtime = json.loads((output / "runtime/build.json").read_text())
    if (runtime.get("kind") != "xrgpu.native.build" or runtime.get("schema_version") != 1
            or runtime.get("platform") != "android-arm64" or runtime["build_type"] != "production"
            or runtime.get("runtime_revision") != record["runtime_revision"]
            or runtime["package_sha256"] != record["package_sha256"]
            or runtime["qualification_sha256"] != record["qualification_sha256"]
            or runtime.get("runner") not in runtime["files"] or not runtime.get("libraries")
            or any(name not in runtime["files"] for name in runtime["libraries"])):
        raise ValueError("Production build differs from qualified package or policy")
    for name, expected in runtime["files"].items():
        if sha(contained(output / "runtime", name)) != expected:
            raise ValueError("Production artifact differs from build inventory: " + name)
    exporter, _ = xrgpu_modules(source)
    for work in plan["workloads"]:
        if work["type"] == "model":
            exporter.bind_package(work["bundle"], packaged, output / "models" / work["id"])
    artifacts = {p.relative_to(output).as_posix(): sha(p) for p in output.rglob("*")
                 if p.is_file() and not p.is_relative_to(output / "runtime/native")}
    value = dict(schema_version=1, kind="xrbench.native.release", package="kernel-package",
                 package_sha256=record["package_sha256"], qualification_sha256=record["qualification_sha256"],
                 model_selections=result["model_selections"],
                 files=artifacts, status="built", production_device_acceptance="pending")
    value["release_sha256"] = digest(value)
    save(output / "release.json", value)
    return value


def decisions(info):
    value = info.get("decisions") or {}
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, list):
        return value
    raise ValueError("Invalid native kernel decision inventory")


def check_session(info, model, package=None, qualification=None, expected_selections=None):
    if info.get("build_type") != "production":
        raise ValueError("Acceptance requires a production library with overrides disabled")
    if info.get("runtime_revision") != model["runtime_revision"]:
        raise ValueError("Loaded runtime revision differs from model")
    if any(info.get(k) != model[k] for k in ("model_id", "inputs", "outputs")):
        raise ValueError("Loaded model identity or tensor interface differs from its bundle")
    if package is not None and info.get("kernel_package", {}).get("package_sha256") != package["package_sha256"]:
        raise ValueError("Loaded runtime differs from the tested kernel package")
    if qualification is None:
        return
    if info.get("backend") != "vulkan":
        raise ValueError("GPU acceptance fell back: " + str(info.get("fallback_reason")))
    if (info.get("qualification_sha256") != qualification["qualification_sha256"]
            or info.get("qualification_status") != "qualified" or info.get("qualification_eligible") is not True):
        raise ValueError("Loaded runtime does not contain the expected eligible qualification")
    if target_identity(info["device"]) != qualification["target"]:
        raise ValueError("Device differs from the qualified target")
    selections = [d for d in decisions(info) if d.get("implementation") == "xrgpu"]
    if not selections and expected_selections is None:
        raise ValueError("Model executed no xrGPU kernels")
    for expected in expected_selections or []:
        if not any(d.get("request") == expected["request"] and d.get("variant") == expected["variant"] for d in selections):
            raise ValueError("Model did not select a kernel observed during qualification")
    for decision in selections:
        if not any(decision.get("request") == r["request"] and decision.get("variant") == r["variant"]
                   for r in qualification["rules"]):
            raise ValueError("Model selected a kernel outside its qualified rules")


def compare_outputs(bundle, fixture, info, read_output):
    names = info.get("output_files", [])
    if len(names) != len(fixture["outputs"]) or any(not re.fullmatch(r"output[0-9]+\.bin", n) for n in names):
        raise ValueError("Native runner returned an invalid output inventory")
    metrics = []
    for filename, spec in zip(names, fixture["outputs"], strict=True):
        dtype = "<f4" if spec["dtype"] == "float32" else "<i8"
        expected = np.frombuffer(contained(bundle, spec["file"]).read_bytes(), dtype=dtype)
        actual = np.frombuffer(read_output(filename), dtype=dtype)
        if actual.shape != expected.shape or not (np.array_equal(actual, expected) if spec["dtype"] == "int64"
                else np.isfinite(actual).all() and np.isfinite(expected).all()
                and np.allclose(actual, expected, atol=1e-4, rtol=1e-4, equal_nan=False)):
            raise ValueError("Native reference mismatch: " + spec["name"])
        metrics.append(dict(name=spec["name"], passed=True,
                            max_abs_error=float(np.max(np.abs(actual.astype(float) - expected.astype(float))))))
    return metrics


def validate(source, bundles, output, runner=None, release_path=None, serial=None, adb_path=None):
    exporter, _ = xrgpu_modules(source)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Choose a new acceptance output directory")
    qualification, package, adb, remote = None, None, None, None
    release_manifest = None
    if release_path:
        root = Path(release_path).resolve()
        release_manifest = json.loads((root / "release.json").read_text())
        if (release_manifest.get("kind") != "xrbench.native.release" or release_manifest.get("schema_version") != 1
                or release_manifest.get("release_sha256") != digest({k: v for k, v in release_manifest.items() if k != "release_sha256"})):
            raise ValueError("Release manifest changed")
        for name, expected in release_manifest["files"].items():
            if sha(contained(root, name)) != expected:
                raise ValueError("Release artifact changed: " + name)
        package_path = root / release_manifest["package"]
        if package_path.is_symlink() or not package_path.resolve().is_relative_to(root):
            raise ValueError("Kernel package must be contained in the release")
        package = verify(package_path, release_manifest["package_sha256"])
        qualification = json.loads((root / "qualification.json").read_text())
        qualification_module(package_path).validate_record(qualification, package, allow_pending=False)
        runtime_root = root / "runtime"
        runtime = json.loads((runtime_root / "build.json").read_text())
        runner = contained(runtime_root, runtime["runner"])
        if not serial:
            raise ValueError("Production GPU acceptance requires an explicit connected headset serial")
        adb = ADB(adb_path)
        if not adb.connected(serial):
            raise ValueError("Selected headset is not connected and authorized")
        remote = "/data/local/tmp/xrbench-inference/" + uuid.uuid4().hex
        adb.shell(serial, "mkdir", "-p", remote)
        adb.command("push", str(runner), remote + "/runner", serial=serial, timeout=120)
        for name in runtime["libraries"]:
            library = contained(runtime_root, name)
            adb.command("push", str(library), remote + "/" + library.name, serial=serial, timeout=120)
        adb.shell(serial, "chmod", "700", remote + "/runner")
        bundles = sorted((root / "models").iterdir())
    elif runner is None or serial:
        raise ValueError("Use --host-runner for host acceptance, or --release and --serial for GPU acceptance")
    if not bundles:
        raise ValueError("At least one exported model bundle is required")
    runner = Path(runner).resolve()
    output.mkdir(parents=True)
    rows = []
    for index, bundle in enumerate(bundles):
        bundle = Path(bundle).resolve()
        model = exporter.verify_bundle(bundle)
        remote_bundle = f"{remote}/model{index}"
        if adb:
            adb.command("push", str(bundle), remote_bundle, serial=serial, timeout=300)
        for fixture in model["fixtures"]:
            destination = output / str(index) / str(fixture["seed"])
            destination.mkdir(parents=True)
            row = dict(model_id=model["model_id"], bundle_sha256=model["bundle_sha256"], seed=fixture["seed"], passed=False)
            try:
                if adb:
                    args = [remote + "/runner", remote_bundle, *[remote_bundle + "/" + v["file"] for v in fixture["inputs"]]]
                    script = "cd " + shlex.quote(remote) + " && exec env LD_LIBRARY_PATH=" + shlex.quote(remote) + " " + shlex.join(args)
                    raw = adb.shell(serial, "sh", "-c", script, timeout=300)
                    def read_output(name):
                        data = adb.shell(serial, "cat", remote + "/" + name, timeout=60)
                        (destination / name).write_bytes(data)
                        return data
                else:
                    args = [str(runner), str(bundle), *[str(bundle / v["file"]) for v in fixture["inputs"]]]
                    process = subprocess.run(args, cwd=destination, capture_output=True, timeout=300)
                    if process.returncode:
                        raise RuntimeError(process.stderr.decode(errors="replace")[-4000:])
                    raw = process.stdout
                    def read_output(name):
                        return (destination / name).read_bytes()
                (destination / "session.json").write_bytes(raw)
                info = json.loads(raw)
                expected = release_manifest["model_selections"][bundle.name] if release_manifest else None
                check_session(info, model, package, qualification, expected)
                row.update(passed=True, backend=info["backend"], decisions=info.get("decisions"),
                           outputs=compare_outputs(bundle, fixture, info, read_output))
            except (OSError, ValueError, RuntimeError, KeyError, subprocess.TimeoutExpired) as error:
                row["error"] = str(error)
            rows.append(row)
            save(output / "validation.json", dict(status="running", execution_target="headset" if adb else "host_cpu",
                 establishes_hardware_qualification=False, production_device_acceptance="pending" if adb else "not_run",
                 passed=False, fixtures=rows))
    result = dict(status="complete", execution_target="headset" if adb else "host_cpu",
                  establishes_hardware_qualification=False, passed=bool(rows) and all(r["passed"] for r in rows),
                  fixtures=rows, release_sha256=release_manifest["release_sha256"] if release_manifest else None,
                  package_sha256=package["package_sha256"] if package else None,
                  qualification_sha256=qualification["qualification_sha256"] if qualification else None)
    if adb and not any(d.get("implementation") == "xrgpu" for row in rows for d in decisions(row)):
        result.update(passed=False, error="No deployed model selected an xrGPU kernel")
    result["production_device_acceptance"] = ("passed" if result["passed"] else "failed") if adb else "not_run"
    save(output / "validation.json", result)
    if not result["passed"]:
        raise RuntimeError("Native acceptance failed; inspect " + str(output / "validation.json"))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="Prepare package-bound HF models and regression workloads offline")
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--bundle", type=Path, action="append", default=[])
    p.add_argument("--fixtures", type=Path, help="Existing qualification workloads.json; rebind without recompiling")
    p.add_argument("--allow-download", action="store_true", help="Allow downloading the pinned example weights")
    p = sub.add_parser("campaign", help="Attach a real hardware target to prepared inputs")
    p.add_argument("--pipeline", type=Path, required=True)
    p.add_argument("--target", type=Path, required=True)
    p = sub.add_parser("release", help="Assess hardware reports and build the qualified production library")
    p.add_argument("--campaign", type=Path, required=True)
    p.add_argument("--shortlist", type=Path, required=True)
    p.add_argument("--jobs", type=int, default=2, choices=range(1, 9))
    p.add_argument("reports", nargs="+", type=Path)
    p = sub.add_parser("validate", help="Run original model fixtures through a production native library")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--host-runner", type=Path)
    mode.add_argument("--release", type=Path)
    p.add_argument("--bundle", type=Path, action="append", default=[])
    p.add_argument("--serial")
    p.add_argument("--adb")
    for p in sub.choices.values():
        p.add_argument("--xrgpu-source", type=Path, default=ROOT.parent / "xrGPU")
        p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.xrgpu_source, args.package, args.output, args.bundle, args.fixtures, args.allow_download)
    elif args.command == "campaign":
        result = campaign(args.pipeline, json.loads(args.target.read_text()), args.output)
    elif args.command == "release":
        result = release(args.xrgpu_source, args.campaign, args.shortlist, args.reports, args.output, args.jobs)
    else:
        result = validate(args.xrgpu_source, args.bundle, args.output, args.host_runner, args.release, args.serial, args.adb)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
