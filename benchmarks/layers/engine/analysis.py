import copy

from benchmarks.layers.engine.reports import TERMINAL, summarize


TIMINGS = ("execution_ms", "model_ms", "gpu_ms", "input_staging_ms", "output_staging_ms", "submit_and_wait_ms")


def metadata(report):
    config, device = report.get("configuration", {}), report.get("device", {})
    return dict(run_id=report["run_id"], status=report.get("status"), model=device.get("model"),
                started_at=report.get("started_at"), finished_at=report.get("finished_at"),
                configuration=config, device=device, suite_revision=report.get("suite_revision"),
                runtime_revision=report.get("runtime_revision"),
                runner_revision=config.get("runner_revision", device.get("runner_revision")),
                apk_sha256=config.get("apk_sha256"), report_available=True)


def compare(baseline, current):
    # Summaries from historical reports are recomputed on copies, not trusted or saved.
    a, b = [summarize(copy.deepcopy(r)) for r in (baseline, current)]
    for original, view in ((baseline, a), (current, b)):
        for raw, row in zip(original.get("results", []), view.get("results", [])):
            if raw.get("timing_valid") is False:row["timing_valid"] = False
    reasons, fields = [], []

    def field(label, left, right, required=True):
        missing = left is None or right is None or left == "" or right == ""
        equal = not missing and left == right
        fields.append(dict(label=label, baseline=left, current=right, matches=equal))
        if required and not equal:
            reasons.append(label + (" not recorded in both runs" if missing else " differs"))

    if a["run_id"] == b["run_id"]:reasons.append("Choose two different runs")
    if a.get("status") not in TERMINAL or b.get("status") not in TERMINAL:
        reasons.append("Both runs must be finished")
    for name in ("backend", "suite_revision", "timing_scope"):
        field(name.replace("_", " ").capitalize(), a.get(name), b.get(name))
    for name in ("mode", "preset"):
        field(name.capitalize(), a.get("configuration", {}).get(name), b.get("configuration", {}).get(name))
    for name in ("model", "gpu", "driver", "os", "precision", "variant"):
        field(name.replace("_", " ").capitalize(), a.get("device", {}).get(name), b.get("device", {}).get(name))
    for name in ("runtime_revision", "runner_revision", "apk_sha256"):
        left, right = metadata(a).get(name), metadata(b).get(name)
        field(name.replace("_", " ").capitalize(), left, right, required=False)
    if a.get("configuration", {}).get("mode") == "xr":
        for name in ("scene_revision", "scene_settings", "refresh_hz", "inference_hz"):
            field(name.replace("_", " ").capitalize(), a.get(name), b.get(name))

    def index(report):return {(r["case"], r["phase"], r["trial"]): r for r in report.get("results", [])}
    left, right = index(a), index(b)
    rows = []
    for key in dict.fromkeys([*left, *right]):
        x, y = left.get(key), right.get(key)
        issues = list(reasons)
        if x is None or y is None:
            issues.append("Measurement missing from one run")
        else:
            if x.get("operation") != y.get("operation"):issues.append("Operation differs")
            for name in ("inputs", "params", "precision"):
                if x.get(name) != y.get(name):issues.append(name + " differs")
            for label, row in (("Baseline", x), ("Comparison", y)):
                if not row["timing_valid"]:issues.append(label + ": incorrect, incomplete, or thermal/focus transition")
                if row.get("correctness", {}).get("passed") is not True:
                    issues.append(label + ": correctness evidence unavailable or failed")
                for check in ("correctness_before", "correctness_after"):
                    if row.get(check, {}).get("passed") is False:issues.append(label + ": " + check + " failed")
            for name in ("thermal_start", "charging_start"):
                if x.get(name) is None or y.get(name) is None:issues.append(name + " not recorded in both runs")
                elif x[name] != y[name]:issues.append(name + " differs")
        metrics = {}
        for name in TIMINGS:
            before = (x or {}).get("timings", {}).get(name)
            after = (y or {}).get("timings", {}).get(name)
            valid = not issues and before is not None and after is not None
            metrics[name] = dict(baseline=before, current=after, valid=valid,
                reason="; ".join(issues) if issues else (None if valid else "Timing unavailable or incomplete"),
                median_delta_ms=after["median"]-before["median"] if valid else None,
                p95_delta_ms=after["p95"]-before["p95"] if valid else None,
                median_change_percent=(after["median"]/before["median"]-1)*100 if valid and before["median"]>0 else None)
        rows.append(dict(case=key[0], phase=key[1], trial=key[2], operation=(x or y)["operation"],
                         valid=not issues, reasons=issues, metrics=metrics))
    return dict(baseline=metadata(a), current=metadata(b), compatible=not reasons, reasons=reasons,
                fields=fields, results=rows,
                note="Observed changes between runs, not proof of a kernel speedup. Matching hardware metadata does not identify the same physical headset. Timing scopes overlap and must not be added.")
