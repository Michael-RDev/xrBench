"""Collect and assess local hardware evidence; never simulate a connected device."""

import argparse
import copy
import json
import time
from pathlib import Path

from engine import profiles
from engine.adb import ADB
from engine.backends import TARGETS, get_backend
from engine.controller import Controller
from engine.paths import BUILD, STATE
from engine.reports import TERMINAL, digest, save, summarize, validate

STAGES = ("stock", "noise", "candidate", "incorrect", "slower")


def request(
    stage, backend="executorch_vulkan", target="unknown", profiling="trace", suite="development"
):
    if stage not in STAGES:
        raise ValueError("Unknown hardware validation stage")
    return dict(
        backend=backend,
        execution_target=target,
        profiling=profiling,
        workload_suite=suite,
        operations=["matmul"],
        mode="isolated",
        preset="standard",
        experiment="none" if stage == "stock" else "aa" if stage == "noise" else "candidate",
        validation_control=stage if stage in ("incorrect", "slower") else "none",
    )


def assess(reports):
    """Evidence qualification is scoped to one installation, build, backend and case set."""
    if not reports:
        return dict(status="pending", missing=["No hardware reports"], gates={})
    source_hashes = [digest(r) for r in reports]
    reports = [summarize(validate(copy.deepcopy(r))) for r in reports]
    first = reports[0]
    configuration = first["configuration"]
    scope = dict(
        profile_id=profiles.identity(first["device"]),
        backend=first["backend"],
        suite_revision=first["suite_revision"],
        candidate_revision=first.get("kernel_build", {}).get("candidate_revision"),
        apk_sha256=configuration.get("apk_sha256"),
        cases=configuration["cases"],
        workload_suite=configuration.get("workload_suite", "standard"),
        execution_target=first["execution_location"],
    )
    issues = []
    if (
        not first["device"].get("installation_id")
        or not scope["candidate_revision"]
        or not scope["apk_sha256"]
    ):
        issues.append("Missing physical installation or build provenance")
    ids = set()
    for r in reports:
        c = r["configuration"]
        other = dict(
            profile_id=profiles.identity(r["device"]),
            backend=r["backend"],
            suite_revision=r["suite_revision"],
            candidate_revision=r.get("kernel_build", {}).get("candidate_revision"),
            apk_sha256=c.get("apk_sha256"),
            cases=c["cases"],
            workload_suite=c.get("workload_suite", "standard"),
            execution_target=r["execution_location"],
        )
        if other != scope:
            issues.append(
                "Reports have different device, build, backend, workload or execution target"
            )
        if c.get("experiment", "none") != "none" and c.get("experiment_pairs", 0) < 10:
            issues.append("At least ten pairs required for qualification")
        if r["run_id"] in ids:
            issues.append("Duplicate run identity")
        ids.add(r["run_id"])
        if c.get("mode") != "isolated" or c.get("preset") != "standard":
            issues.append("Standard isolated runs required")
        if any(row.get("operation") != "matmul" for row in r["results"]):
            issues.append("Qualification currently covers FP32 matmul only")
    expected = set(scope["cases"])
    if not expected:
        issues.append("Empty case set")

    def complete(r):
        return r["status"] == "complete" and set(x["case"] for x in r["results"]) == expected

    def correct(r):
        # A faster wrong answer still fails. Check correctness at both ends of each timed block.
        return complete(r) and all(
            x["timing_valid"]
            and all(
                x.get(k, {}).get("passed") is True
                for k in ("correctness_before", "correctness_after")
            )
            and len(x["samples"]) >= 100
            for x in r["results"]
        )

    def instrumented(r):
        samples = [s for row in r["results"] for s in row["samples"]]
        return bool(samples) and all(
            s.get("dispatches")
            and all(
                e.get("kernel_name") in r.get("shaders", {})
                and r["shaders"][e["kernel_name"]].get("spirv_sha256")
                for e in s["dispatches"]
            )
            for s in samples
        )

    def uninstrumented(r):
        return all(
            s.get("profiling") == "latency"
            and s.get("gpu_span_ms") is None
            and not s.get("dispatches")
            for row in r["results"]
            for s in row["samples"]
        )

    def comparisons(r):
        return r.get("kernel_comparison", {}).get("results", [])

    gates = {
        "stock_correctness": False,
        "noise_trace": False,
        "noise_latency": False,
        "incorrect_rejected": False,
        "slower_detected": False,
        "candidate_trace": False,
        "candidate_three_sessions": False,
        "multiple_fixture_seeds": False,
    }
    candidates = []
    improvements = {case: 0 for case in expected}
    for r in reports:
        c = r["configuration"]
        kind = c.get("experiment", "none")
        control = c.get("validation_control", "none")
        tracing = c.get("profiling", "trace")
        if kind == "none" and correct(r):
            gates["stock_correctness"] = True
        # Stock/stock tells us whether the comparison is mistaking noise for a win.
        if (
            kind == "aa"
            and correct(r)
            and comparisons(r)
            and all(x["available"] and not x["noise_control_warning"] for x in comparisons(r))
        ):
            gates["noise_" + tracing] = instrumented(r) if tracing == "trace" else uninstrumented(r)
        if control == "incorrect" and complete(r):
            failed = {
                x["case"]
                for x in r["results"]
                if x.get("status") == "failed"
                and x.get("correctness", {}).get("passed") is False
                and x.get("validation_execution", {}).get("kernel_variant") == "candidate"
                and x.get("validation_execution", {}).get("candidate_selected") is True
                and x.get("validation_execution", {}).get("validation_control") == "incorrect"
            }
            gates["incorrect_rejected"] = failed == expected and all(
                not x["available"] for x in comparisons(r)
            )
        if control == "slower" and correct(r) and comparisons(r):
            gates["slower_detected"] = all(
                x["available"] and x["metrics"]["execution_ms"]["ci95_percent"][1] < -5
                for x in comparisons(r)
            )
        if (
            kind == "candidate"
            and control == "none"
            and correct(r)
            and comparisons(r)
            and all(x["available"] for x in comparisons(r))
        ):
            if tracing == "trace":
                gates["candidate_trace"] = instrumented(r)
            elif uninstrumented(r):
                candidates.append(r)
                for x in comparisons(r):
                    improvements[x["case"]] += x["outcome"] == "improvement_observed"
        seeds = {x.get("fixture_seed") for x in r["results"] if type(x.get("fixture_seed")) is int}
        if correct(r) and len(seeds) >= 3:
            gates["multiple_fixture_seeds"] = True
    # Three valid sessions pass this gate; repeated per-case wins are reported separately.
    gates["candidate_three_sessions"] = len(candidates) >= 3
    missing = sorted(set(issues + [k for k, v in gates.items() if not v]))
    return dict(
        version=1,
        status="validated" if not missing else "pending",
        scope=scope,
        gates=gates,
        missing=missing,
        report_sha256=source_hashes,
        reproduced_improvements=[c for c, n in improvements.items() if n >= 3]
        if not missing
        else [],
        note="Validation applies only to these FP32 matmul cases and this device/build. Reports are self-reported, not hardware attestation. Application speedup and other workloads remain unproven.",
    )


def preserve_report(controller, job, report, destination):
    run_id = job["run_id"]
    if report.get("run_id") != run_id or report.get("configuration") != job["configuration"]:
        raise ValueError("Native report identity mismatch")
    save(destination / (run_id + "-partial.json"), report)
    if report.get("status") not in TERMINAL:
        return False
    path = destination / (run_id + ".json")
    save(path, report)
    save(destination / (run_id + "-analysis.json"), summarize(validate(copy.deepcopy(report))))
    controller.update(run_id, status=report["status"], storage="local", message=str(path))
    print(path)
    return True


def recover(args):
    adb = ADB(args.adb)
    controller = Controller(adb, None, STATE / "qualification-jobs", BUILD, start_workers=False)
    job = controller.jobs.get(args.run_id)
    if job is None or job["serial"] != args.serial:
        raise ValueError("Choose the original run and device from qualification-jobs")
    report = adb.read(args.serial, args.run_id, "report.json")
    if not preserve_report(controller, job, report, args.output):
        raise RuntimeError(
            "Partial report saved; the device run is not finished. Recover again after it stops."
        )


def collect(args):
    adb = ADB(args.adb)
    backend = get_backend(args.backend)
    controller = Controller(adb, None, STATE / "qualification-jobs", BUILD, start_workers=False)
    config = request(args.stage, args.backend, args.target, args.profiling, args.suite)
    config["serial"] = args.serial
    job = controller.create(config)
    run_id = job["run_id"]
    metadata = json.loads((BUILD / "apk.json").read_text())
    backend.install(adb, args.serial, BUILD, metadata)
    profile = adb.probe(args.serial)
    args.output.mkdir(parents=True, exist_ok=True)
    save(args.output / (run_id + "-profile.json"), profile)
    if not profiles.assess(profile, args.backend)["compatible"]:
        controller.update(
            run_id,
            status="failed",
            storage="local",
            message="Unsupported backend; capability profile preserved",
        )
        raise ValueError("Selected backend is unsupported; capability profile saved locally")
    launched = False
    finished = False
    try:
        launched = True
        controller.update(run_id, status="running", storage="local")
        backend.launch(adb, args.serial, job["configuration"])
        deadline = time.monotonic() + args.timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                progress = adb.read(args.serial, run_id, "progress.json")
                if progress.get("message") != last:
                    last = progress.get("message", "")
                    print(last, flush=True)
                report = adb.read(args.serial, run_id, "report.json")
            except (OSError, RuntimeError, json.JSONDecodeError):
                time.sleep(1)
                continue
            if preserve_report(controller, job, report, args.output):
                finished = True
                return
            time.sleep(1)
        raise TimeoutError("Hardware validation timed out; reports remain on the device")
    finally:
        if launched and not finished:
            controller.update(
                run_id,
                status="disconnected",
                storage="local",
                message="Validation interrupted; recover the saved device report before another run",
            )
            try:
                adb.stop(args.serial, run_id)
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run one hardware stage on a locally connected device")
    run.add_argument("--serial", required=True)
    run.add_argument("--adb")
    run.add_argument("--stage", choices=STAGES, required=True)
    run.add_argument(
        "--backend", choices=("executorch_vulkan", "vulkan_resident"), default="executorch_vulkan"
    )
    run.add_argument("--target", choices=TARGETS, default="unknown")
    run.add_argument("--profiling", choices=("trace", "latency"), default="trace")
    run.add_argument("--suite", choices=("standard", "development"), default="development")
    run.add_argument("--timeout", type=int, default=1800)
    run.add_argument("--output", type=Path, default=STATE / "hardware-evidence")
    resume = sub.add_parser(
        "recover", help="Retrieve a saved run without starting or reinstalling anything"
    )
    resume.add_argument("--serial", required=True)
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--adb")
    resume.add_argument("--output", type=Path, default=STATE / "hardware-evidence")
    check = sub.add_parser("assess", help="Recompute qualification from original device reports")
    check.add_argument("reports", nargs="+", type=Path)
    check.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        if not 30 <= args.timeout <= 7200:
            parser.error("Timeout must be between 30 and 7200 seconds")
        collect(args)
    elif args.command == "recover":
        recover(args)
    else:
        if args.output and args.output.resolve() in [p.resolve() for p in args.reports]:
            parser.error("Preserve original reports; choose a separate output")
        result = assess([json.loads(p.read_text()) for p in args.reports])
        if args.output:
            save(args.output, result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
