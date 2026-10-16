import hashlib
import importlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent


def catalog():
    return json.loads((ROOT / "catalog.json").read_text())


OPERATIONS = frozenset(c["operation"] for c in catalog()["cases"])


def _definition(operation):
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown layer operation: {operation}")
    return importlib.import_module(f"{__name__}.{operation}.layer")


def revision():
    sources = [p for p in ROOT.iterdir() if p.suffix in (".py", ".json")]
    for operation in OPERATIONS:
        sources.extend(p for p in (ROOT / operation).rglob("*")
                       if p.suffix in (".py", ".json"))
    identity = hashlib.sha256()
    for path in sorted(sources):
        identity.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        identity.update(path.read_bytes() + b"\0")
    return identity.hexdigest()


def select(preset="smoke"):
    if preset not in ("smoke", "standard", "sustained"):
        raise ValueError("Unknown preset")
    return catalog()["cases"]


def fixtures(case, seed=None):
    seed = catalog()["seed"] if seed is None else seed
    identity = hashlib.sha256(f"{seed}:{case['id']}".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(identity[:8], "little"))
    p = case.get("params", {})
    definition = _definition(case["operation"])

    def rand(shape, scale=.3):
        return (rng.normal(size=shape) * scale).astype(np.float32)

    inputs = [rng.integers(0, p["vocab_size"], s["shape"], dtype=np.int64)
              if s["dtype"] == "int64" else rand(s["shape"]) for s in case["inputs"]]
    if p.get("pattern") == "constant":
        inputs[0].fill(.75)
    if p.get("pattern") == "large":
        inputs[0] += 1000
    if hasattr(definition, "prepare_inputs"):
        definition.prepare_inputs(case, inputs)
    weights = definition.make_weights(case, inputs, rand) if hasattr(definition, "make_weights") else {}
    return tuple(inputs), weights


def reference(case, inputs, weights):
    xs = [np.asarray(x, dtype=np.float64) for x in inputs]
    w = {k: np.asarray(v, dtype=np.float64) for k, v in weights.items()}
    expected = _definition(case["operation"]).reference(case, xs, w)
    return np.ascontiguousarray(expected, dtype=np.float32)


def correctness(actual, expected, atol=1e-4, rtol=1e-4):
    if actual.shape != expected.shape or not np.isfinite(actual).all() or not np.isfinite(expected).all():
        return dict(passed=False, reason="shape mismatch or non-finite value")
    error = np.abs(actual.astype(np.float64) - expected.astype(np.float64))
    ratio = error / (atol + rtol * np.abs(expected.astype(np.float64)))
    return dict(passed=bool((ratio <= 1).all()), max_abs_error=float(error.max()),
                max_tolerance_ratio=float(ratio.max()), atol=atol, rtol=rtol)


def module(case, weights):
    return _definition(case["operation"]).module(case, weights)
