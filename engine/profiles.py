"""Portable capability identities. An identity is not hardware attestation."""

import hashlib
import json

IDENTITY_FIELDS = (
    "installation_id",
    "model",
    "manufacturer",
    "os",
    "gpu",
    "vendor_id",
    "device_id",
    "driver",
    "api_version",
    "runner_revision",
)


def identity(profile):
    values = {key: profile.get(key) for key in IDENTITY_FIELDS}
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def assess(profile, backend):
    support = profile.get("backends", {}).get(backend, {})
    return dict(
        status="compatible_unvalidated" if support.get("compatible") is True else "unsupported",
        compatible=support.get("compatible") is True,
        reason=support.get("reason", profile.get("reason", "Backend capabilities unavailable")),
    )
