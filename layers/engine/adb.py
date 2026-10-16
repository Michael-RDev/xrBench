import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from layers.engine.paths import ROOT
from layers.engine.reports import RUN_ID, canonical

PACKAGE = "org.xrgpu.benchmarks"


class ADB:
    def __init__(self, executable=None):
        bundled = ROOT / "artifacts/toolchain/android-sdk/platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
        self.executable = executable or shutil.which("adb") or str(bundled)

    def command(self, *args, serial=None, data=None, timeout=15):
        command = [self.executable] + (["-s", serial] if serial else []) + list(args)
        result = subprocess.run(command, input=data, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or result.stdout.decode(errors="replace").strip() or "ADB command failed")
        return result.stdout

    def shell(self, serial, *args, data=None, timeout=15):
        return self.command("exec-out", shlex.join(args), serial=serial, data=data, timeout=timeout)

    def devices(self):
        try:
            output = self.command("devices", "-l").decode()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            return dict(devices=[], error="ADB unavailable. Install platform-tools and enable developer mode/USB debugging. " + str(error))
        devices = []
        for line in output.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2: continue
            serial, state = fields[:2]
            hints = dict(field.split(":", 1) for field in fields[2:] if ":" in field)
            row = dict(serial=serial, state=state,
                model=hints.get("model", "Connected headset").replace("_", " "), supported=False)
            if state == "device":
                try:
                    row["model"] = self.shell(serial,"getprop","ro.product.model").decode().strip()
                    row["sdk"] = int(self.shell(serial,"getprop","ro.build.version.sdk").decode().strip())
                    row["abi"] = self.shell(serial,"getprop","ro.product.cpu.abilist").decode().strip()
                    features = self.shell(serial,"pm","list","features").decode()
                    row["supported"] = row["sdk"] >= 29 and "arm64-v8a" in row["abi"] and ("vr.headtracking" in features or "Quest" in row["model"]) and row["model"] == "Quest 3"
                    row["support_label"] = "Quest 3 target" if row["model"] == "Quest 3" else "Unsupported headset"
                    if not row["supported"]: row["reason"] = "This version supports Quest 3 only; other headsets and glasses are not yet validated."
                except Exception as error: row["reason"] = str(error)
            elif state == "unauthorized": row["reason"] = "Put on the headset and allow USB debugging."
            else: row["reason"] = "Reconnect the USB cable and enable developer mode."
            devices.append(row)
        return dict(devices=devices, error=None)

    def install(self, serial, apk, expected_hash):
        if hashlib.sha256(Path(apk).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("APK checksum mismatch; rebuild the benchmark app")
        packages = self.shell(serial,"pm","list","packages",PACKAGE).decode().splitlines()
        if "package:"+PACKAGE in packages:
            installed = self.shell(serial,"pm","path",PACKAGE).decode().strip()
            path = installed.splitlines()[0].removeprefix("package:") if installed else ""
            if path.startswith("/data/app/"):
                actual = self.shell(serial,"sha256sum",path,timeout=30).decode().split()[0]
                if actual == expected_hash: return
        # Never uninstall another signature or erase its data to force an installation.
        self.command("install","-r",str(apk),serial=serial,timeout=180)

    def start(self, serial, config):
        run_id = config["run_id"]
        if not RUN_ID.fullmatch(run_id): raise ValueError("Invalid run identity")
        script = f"mkdir -p files/commands files/runs/{run_id} && cat > files/commands/{run_id}.part && mv files/commands/{run_id}.part files/commands/{run_id}.json"
        self.shell(serial,"run-as",PACKAGE,"sh","-c",script,data=canonical(config))
        output=self.shell(serial,"am","start","-W","-n",PACKAGE+"/.MainActivity","--es","run_id",run_id,timeout=30).decode()
        if "Error:" in output or "Error type" in output:raise RuntimeError("Android could not launch the benchmark: "+output.strip())

    def read(self, serial, run_id, filename):
        if not RUN_ID.fullmatch(run_id) or filename not in ("progress.json","report.json"):
            raise ValueError("Invalid report path")
        value = self.shell(serial,"run-as",PACKAGE,"cat",f"files/runs/{run_id}/{filename}",timeout=10)
        if len(value) > 64*1024*1024: raise ValueError("Native report exceeds size limit")
        return json.loads(value)

    def stop(self, serial, run_id):
        if not RUN_ID.fullmatch(run_id): raise ValueError("Invalid run identity")
        self.shell(serial,"run-as",PACKAGE,"touch",f"files/runs/{run_id}/cancel")

    def connected(self, serial):
        return self.command("get-state",serial=serial,timeout=5).strip() == b"device"
