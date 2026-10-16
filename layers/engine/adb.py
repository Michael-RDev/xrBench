import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from layers.engine.paths import ROOT
from layers.engine.reports import RUN_ID, canonical

PACKAGE = "org.xrgpu.benchmarks"


def _adb_candidates():
    name = "adb.exe" if os.name == "nt" else "adb"
    yield ROOT / "artifacts/toolchain/android-sdk/platform-tools" / name
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if root := os.environ.get(variable):
            yield Path(root) / "platform-tools" / name
    if os.name != "nt":
        return
    if local := os.environ.get("LOCALAPPDATA"):
        yield Path(local) / "Android/Sdk/platform-tools" / name
    if programs := os.environ.get("ProgramFiles"):
        yield Path(programs) / "Meta Quest Developer Hub/resources/bin" / name
        editors = Path(programs) / "Unity/Hub/Editor"
        try:
            versions = sorted(editors.iterdir(), key=lambda path: (
                tuple(int(part) for part in re.findall(r"\d+", path.name)), path.name
            ), reverse=True)
        except OSError:
            return
        for version in versions:
            yield version / "Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools" / name


def _find_adb(executable=None):
    if executable is not None:
        return str(executable)
    if found := shutil.which("adb"):
        return found
    for candidate in _adb_candidates():
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


class ADB:
    def __init__(self, executable=None):
        self.executable = _find_adb(executable)

    def command(self, *args, serial=None, data=None, timeout=15):
        if self.executable is None:
            raise FileNotFoundError("No ADB installation found")
        command = [self.executable] + (["-s", serial] if serial else []) + list(args)
        result = subprocess.run(command, input=data, capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or result.stdout.decode(errors="replace").strip() or "ADB command failed")
        return result.stdout

    def shell(self, serial, *args, data=None, timeout=15):
        # shell v2 with no PTY forwards stdin, preserves bytes, and reports the
        # remote exit status. exec-out drops piped input on Windows, leaving a
        # zero-byte command file and a cat process waiting forever.
        return self.command("shell", "-T", shlex.join(args), serial=serial, data=data, timeout=timeout)

    def devices(self):
        try:
            output = self.command("devices", "-l").decode()
        except FileNotFoundError:
            location = f' at "{self.executable}"' if self.executable is not None else ""
            return dict(devices=[], error=(
                f"ADB executable not found{location}. Install Android SDK platform-tools "
                'or select an existing installation with --adb "path/to/adb". '
                "Restart xrBench after changing the installation or launch options."
            ))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            return dict(devices=[], error=(
                f'ADB command failed using "{self.executable}". Check that this ADB installation '
                f"can run, or select another with --adb. {error}"
            ))
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
        self.shell(serial,"input","keyevent","KEYCODE_WAKEUP")
        output=self.shell(serial,"am","start","-W","-n",PACKAGE+"/.MainActivity","--es","run_id",run_id,timeout=30).decode()
        if "Error:" in output or "Error type" in output:raise RuntimeError("Android could not launch the benchmark: "+output.strip())

    def read(self, serial, run_id, filename):
        if not RUN_ID.fullmatch(run_id) or filename not in ("progress.json","report.json"):
            raise ValueError("Invalid report path")
        value = self.shell(serial,"run-as",PACKAGE,"cat",f"files/runs/{run_id}/{filename}",timeout=10)
        if len(value) > 64*1024*1024: raise ValueError("Native report exceeds size limit")
        if not value.strip():raise RuntimeError("No native progress yet. Wake the headset and keep the benchmark app open.")
        return json.loads(value)

    def stop(self, serial, run_id):
        if not RUN_ID.fullmatch(run_id): raise ValueError("Invalid run identity")
        self.shell(serial,"run-as",PACKAGE,"touch",f"files/runs/{run_id}/cancel")

    def connected(self, serial):
        return self.command("get-state",serial=serial,timeout=5).strip() == b"device"
