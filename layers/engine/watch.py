"""Visible-page leases and once-per-build scheduling. All access holds controller.lock."""

import time
import uuid

from layers.engine.reports import digest


class WatchRegistry:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.generation = uuid.uuid4().hex
        self.leases = {}
        self.attempted = set()

    def expire(self):
        for serial, lease in list(self.leases.items()):
            if lease["expires"] <= self.clock():
                del self.leases[serial]

    def key(self, serial, config):
        return digest(
            [
                self.generation,
                serial,
                {
                    k: config.get(k)
                    for k in (
                        "apk_sha256",
                        "suite_revision",
                        "runtime_revision",
                        "runner_revision",
                        "mode",
                        "preset",
                        "cases",
                        "profiling",
                    )
                },
            ]
        )

    def update(self, owner, action, serial=None, request=None):
        self.expire()
        if (
            not isinstance(owner, str)
            or len(owner) != 32
            or any(c not in "0123456789abcdef" for c in owner)
        ):
            raise ValueError("Invalid watch owner")
        if action == "disable":
            for key, lease in list(self.leases.items()):
                if lease["owner"] == owner:
                    del self.leases[key]
            return
        if action not in ("enable", "heartbeat") or not serial:
            raise ValueError("Invalid watch request")
        lease = self.leases.get(serial)
        if lease and lease["owner"] != owner:
            raise ValueError("Another visible tab owns continuous mode for this device")
        if action == "heartbeat" and not lease:
            raise ValueError("Watch lease expired; enable again")
        self.update(owner, "disable")
        self.leases[serial] = dict(
            owner=owner,
            request=request,
            expires=self.clock() + 30,
            state="Watching for changes",
            run_id=lease.get("run_id") if lease else None,
        )

    def public(self):
        self.expire()
        return [
            dict(serial=serial, owner=v["owner"], state=v["state"], run_id=v.get("run_id"))
            for serial, v in self.leases.items()
        ]
