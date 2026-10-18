"""Versioned logical-work estimates; these are not hardware utilization counters."""

import math
from layers.engine.reports import distribution

VERSION = 1


def cost_model(row):
    try:
        shapes = [x["shape"] for x in row["inputs"]]
        if not shapes or any(not s or any(type(d) is not int or d <= 0 for d in s) for s in shapes):
            return None
        if any(x.get("dtype") != "float32" for x in row["inputs"]):
            return None
        p = row.get("params", {})
        op = row["operation"]
        a = shapes[0]
        n = math.prod(a)
        weights = 0
        if op == "linear":
            out = n // a[-1] * p["out_features"]
            weights = a[-1] * p["out_features"]
            flops = 2 * out * a[-1] + (out if p.get("bias", True) else 0)
            if p.get("bias", True):
                weights += p["out_features"]
        elif op in ("matmul", "bmm"):
            b = shapes[1]
            if a[-1] != b[-2]:
                return None
            out = math.prod(a[:-1]) * b[-1]
            flops = 2 * out * a[-1]
        elif op in ("conv2d", "depthwise_conv2d"):
            batch, channels, h, w = a
            k = p["kernel"]
            stride = p.get("stride", 1)
            pad = p.get("padding", 0)
            groups = channels if op == "depthwise_conv2d" else p.get("groups", 1)
            out = (
                batch
                * p["out_channels"]
                * ((h + 2 * pad - k) // stride + 1)
                * ((w + 2 * pad - k) // stride + 1)
            )
            weights = p["out_channels"] * (channels // groups) * k * k
            flops = 2 * out * (channels // groups) * k * k + out
            weights += p["out_channels"]
        elif op in ("add", "mul"):
            dims = list(zip(*[([1] * (max(map(len, shapes)) - len(s)) + s) for s in shapes]))
            if any(any(v not in (1, max(d)) for v in d) for d in dims):
                return None
            out = math.prod(max(d) for d in dims)
            flops = out
        elif op in ("sum", "mean"):
            out = math.prod(a[:-1])
            flops = n - out + (out if op == "mean" else 0)
        elif op in ("transpose_copy", "concat"):
            out = n if op == "transpose_copy" else sum(math.prod(s) for s in shapes)
            flops = 0
        else:
            return None
        return dict(
            version=VERSION,
            flops=flops,
            logical_bytes=4 * (sum(math.prod(s) for s in shapes) + weights + out),
            byte_scope="Logical inputs, weights and outputs; not measured DRAM traffic",
        )
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
        return None


def efficiency(row, profile):
    def unavailable(reason):
        return dict(available=False, reason=reason)

    cost = cost_model(row)
    if row.get("phase") != "isolated":
        return unavailable("XR windows are analyzed separately")
    if not row.get("timing_valid") or row.get("correctness", {}).get("passed") is not True:
        return unavailable("Correctness or timing is not valid")
    if not cost:
        return unavailable("No supported logical cost model")
    if not profile or not profile.get("available"):
        return unavailable("Device calibration unavailable")
    if profile.get("version") != VERSION:
        return unavailable("Calibration version differs")
    if profile.get("mode", "isolated") != "isolated":
        return unavailable("Calibration measurement scope differs")
    if any(
        profile.get(k) is None or profile[k] != row.get(k)
        for k in ("thermal_start", "charging_start")
    ):
        return unavailable("Calibration conditions differ")
    span = distribution([s.get("gpu_span_ms") for s in row.get("samples", [])])
    dispatches = distribution([s.get("dispatch_count") for s in row.get("samples", [])])
    if (
        not span
        or span["median"] <= 0
        or not dispatches
        or dispatches["min"] <= 0
        or any(
            s.get("gpu_span_scope") != "single graph, common queue timestamp domain"
            for s in row.get("samples", [])
        )
    ):
        return unavailable("Common-queue GPU span unavailable")
    c, m, launch = (profile.get(k) for k in ("compute_gflops", "memory_gbps", "dispatch_ms"))
    if any(type(v) not in (float, int) or not math.isfinite(v) or v <= 0 for v in (c, m, launch)):
        return unavailable("Invalid calibration")
    limits = {
        "compute": cost["flops"] / (c * 1e6),
        "memory": cost["logical_bytes"] / (m * 1e6),
        "dispatch": launch * dispatches["median"],
    }
    bound = max(limits, key=limits.get)
    reference = limits[bound]
    gpu = span["median"]
    return dict(
        available=True,
        cost=cost,
        gpu_ms=gpu,
        dispatches=dispatches["median"],
        reference_ms=reference,
        efficiency_percent=100 * reference / gpu,
        gap_ms=max(0, gpu - reference),
        limiting_factor=bound,
        achieved=cost["flops"] / (gpu * 1e6)
        if cost["flops"]
        else cost["logical_bytes"] / (gpu * 1e6),
        achieved_unit="GFLOP/s" if cost["flops"] else "GB/s",
        estimated=True,
    )


def enrich(report):
    profile = report.get("calibration")
    for row in report.get("results", []):
        row["efficiency"] = efficiency(row, profile)
    return report
