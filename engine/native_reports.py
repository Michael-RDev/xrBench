"""Validate native typed operator evidence independently of model-layer reports."""

import math
import re


def _need(value, message):
    if not value:
        raise ValueError(message)


def _equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return set(a) == set(b) and all(_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    return a == b


def _real(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            _need(
                item.get("test_only") is not True
                and item.get("simulated") is not True
                and item.get("source") != "simulated"
                and item.get("presentation_source") != "simulated",
                "Software/demo evidence cannot be stored as native device measurements",
            )
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def validate(report, config=None, catalog=None, *, partial=False):
    from engine.reports import canonical

    canonical(report)
    _real(report)
    _need(
        report.get("schema_version") == 3
        and re.fullmatch(r"run-[a-f0-9]{32}", str(report.get("run_id", ""))),
        "Invalid native report identity",
    )
    config = report.get("configuration", {}) if config is None else config
    _need(_equal(report.get("configuration"), config), "Native report configuration mismatch")
    _need(
        report.get("status")
        in (
            ["complete", "failed", "canceled", "running"]
            if partial
            else ["complete", "failed", "canceled"]
        ),
        "Invalid native report status",
    )
    _need(
        report.get("backend") == config.get("backend") == "xrgpu_vulkan"
        and config.get("mode") == "isolated"
        and config.get("execution_target") == report.get("execution_location")
        and report.get("execution_location")
        in {"headset", "phone", "glasses", "compute_puck", "unknown"},
        "Native backend/target mismatch",
    )
    _need(
        report.get("execution_context") == "foreground_service"
        and isinstance(report.get("device"), dict),
        "Missing native execution/device metadata",
    )
    for key in (
        "suite_revision",
        "runtime_revision",
        "native_package_sha256",
        "native_catalog_sha256",
    ):
        _need(
            isinstance(report.get(key), str) and report[key] == config.get(key),
            "Native release identity mismatch",
        )
    from datetime import datetime

    try:
        start = datetime.fromisoformat(report["started_at"].replace("Z", "+00:00"))
        end = (
            datetime.fromisoformat(report["finished_at"].replace("Z", "+00:00"))
            if report["status"] != "running"
            else start
        )
        _need(
            start.tzinfo is not None and end.tzinfo is not None and end >= start,
            "Invalid native timestamps",
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("Invalid native report timestamps") from error
    rows = report.get("results")
    _need(
        isinstance(rows, list)
        and isinstance(config.get("cases"), list)
        and len(rows) <= len(config["cases"]),
        "Invalid native result inventory",
    )
    cases = {c["id"]: c for c in catalog["cases"]} if catalog is not None else None
    seen = set()
    for row in rows:
        identity = row.get("case")
        _need(
            identity in config["cases"]
            and identity not in seen
            and row.get("operation") == identity,
            "Unknown or duplicate native operator",
        )
        seen.add(identity)
        _need(
            row.get("phase") == "isolated"
            and row.get("trial") == 0
            and row.get("status") in {"passed", "failed", "unsupported", "interrupted"},
            "Invalid native result status",
        )
        if cases is not None:
            expected = cases.get(identity)
            _need(expected is not None, "Unknown frozen operator")
            for key in (
                "inputs",
                "outputs",
                "attrs",
                "args",
                "contract_revision",
                "contract_sha256",
            ):
                _need(
                    _equal(row.get(key), expected.get(key)),
                    "Native tensor/contract descriptor mismatch",
                )
        _need(
            re.fullmatch(r"[a-f0-9]{40}", str(row.get("contract_revision", "")))
            and re.fullmatch(r"[a-f0-9]{64}", str(row.get("contract_sha256", ""))),
            "Missing frozen contract provenance",
        )
        samples = row.get("samples")
        _need(
            isinstance(samples, list) and len(samples) <= config.get("samples", 100),
            "Invalid native sample count",
        )
        for sample in samples:
            for key in ("execution_ms", "gpu_ms"):
                value = sample.get(key)
                _need(
                    key == "gpu_ms"
                    and value is None
                    or type(value) in (int, float)
                    and math.isfinite(value)
                    and value >= 0,
                    "Invalid native timing",
                )
            _need(
                type(sample.get("thermal_status")) is int
                and 0 <= sample["thermal_status"] <= 6
                and type(sample.get("charging")) is bool
                and type(sample.get("display_interactive")) is bool,
                "Missing native operating conditions",
            )
        if row["status"] == "passed":
            _need(
                len(samples) == config.get("samples")
                and all(
                    row.get(key, {}).get("passed") is True
                    for key in ("correctness", "correctness_before", "correctness_after")
                ),
                "Incomplete native correctness/samples",
            )
            info = row.get("runtime_info", {})
            _need(
                info.get("backend") == "vulkan" and info.get("cpu_compute_fallback") is False,
                "Native GPU execution required",
            )
            outputs = row.get("actual_outputs")
            _need(
                isinstance(outputs, list) and len(outputs) == len(row.get("outputs", [])),
                "Missing typed native outputs",
            )
            for value, target in zip(outputs, row["outputs"]):
                for key in ("name", "shape", "dtype", "storage_dtype"):
                    _need(
                        _equal(value.get(key), target.get(key)),
                        "Native output logical/storage mismatch",
                    )
                _need(
                    re.fullmatch(r"[a-f0-9]{64}", str(value.get("sha256", ""))),
                    "Missing output digest",
                )
                if not target["storage_dtype"].startswith("float"):
                    _need(
                        value["sha256"] == target["sha256"], "Exact native integer output mismatch"
                    )
        else:
            _need(
                isinstance(row.get("error"), str)
                and bool(row["error"])
                or row["status"] == "interrupted",
                "Missing unsupported/failure reason",
            )
        _need(isinstance(row.get("shaders"), list), "Missing executed shader identity")
        if cases is not None:
            for shader in row["shaders"]:
                file = shader.get("file", "")
                _need(
                    shader.get("sha256") == catalog["runtime_files"].get(file)
                    and file.removeprefix("shaders/") in cases[identity]["shaders"],
                    "Executed shader differs from release",
                )
    if report["status"] == "complete":
        _need(
            seen == set(config["cases"]), "Completed native report must account for every operator"
        )
    return report
