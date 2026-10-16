import json
import threading
import time
import uuid
from pathlib import Path

from benchmarks.layers import revision, select
from benchmarks.layers.engine.reports import TERMINAL, digest, save, summarize, validate


class Controller:
    def __init__(self, adb, store, state, build, start_workers=True):
        self.adb, self.store, self.state, self.build = adb, store, Path(state), Path(build)
        self.state.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()
        self.submission_lock = threading.Lock()
        self.stopping = threading.Event()
        self.jobs = {}
        self.device_snapshot = dict(devices=[],error="Detecting headsets…")
        self.database = dict(ready=False,message="Checking PostgreSQL…")
        self.start_workers = start_workers
        for path in self.state.glob("run-*/job.json"):
            try:
                job=json.loads(path.read_text());self.jobs[job["run_id"]]=job
                if job["status"] == "starting":
                    self.update(job["run_id"],status="failed",message="Controller stopped during APK setup; start a new run",storage="not applicable")
                if start_workers and job["status"] not in TERMINAL:
                    self.spawn(self.monitor,job["run_id"])
            except (ValueError,KeyError,OSError): continue
        if start_workers:self.spawn(self.background)

    def spawn(self, function, *args):
        threading.Thread(target=function,args=args,daemon=True).start()

    def update(self, run_id, **changes):
        with self.lock:
            self.jobs[run_id].update(changes)
            save(self.state/run_id/"job.json",self.jobs[run_id])

    def view(self, run_id):
        with self.lock:
            job = self.jobs[run_id].copy()
        job.pop("serial",None)
        return job

    def create(self, request):
        if set(request)-{"serial","mode","preset"}:raise ValueError("Run accepts only headset, mode, and preset; every run includes the full suite")
        mode,preset=request.get("mode","isolated"),request.get("preset","smoke")
        if mode not in ("isolated","xr") or preset not in ("smoke","standard","sustained"):raise ValueError("Invalid run configuration")
        cases=select(preset)
        devices=self.adb.devices()
        authorized=[d for d in devices["devices"] if d["state"]=="device" and d["supported"]]
        serial=request.get("serial")
        if not serial and len(authorized)==1:serial=authorized[0]["serial"]
        if serial not in {d["serial"] for d in authorized}:raise ValueError("Select an authorized, supported headset")
        metadata=json.loads((self.build/"apk.json").read_text())
        if metadata["suite_revision"]!=revision():raise ValueError("APK catalog is outdated. Rebuild the benchmark APK.")
        run_id="run-"+uuid.uuid4().hex
        config=dict(run_id=run_id,mode=mode,preset=preset,cases=[c["id"] for c in cases],suite_revision=revision(),
                    runtime_revision=metadata["runtime_revision"],runner_revision=metadata["runner_revision"],
                    apk_sha256=metadata["apk_sha256"])
        with self.lock:
            if any(j["serial"]==serial and j["status"] not in TERMINAL for j in self.jobs.values()):
                raise ValueError("This headset already has an active or disconnected run; recover it first")
            self.jobs[run_id]=dict(run_id=run_id,serial=serial,status="starting",message="Preparing native app",configuration=config,
                created_at=time.time(),storage="pending",cancel_requested=False)
            self.update(run_id)
        if self.start_workers:self.spawn(self.launch,run_id,metadata)
        return self.view(run_id)

    def launch(self, run_id, metadata):
        job=self.jobs[run_id]
        launch_attempted=False
        try:
            self.adb.install(job["serial"],self.build/"xrgpu-benchmarks.apk",metadata["apk_sha256"])
            if job["cancel_requested"]:
                self.update(run_id,status="canceled",message="Canceled before launch",storage="not applicable");return
            launch_attempted=True
            self.update(run_id,status="launching",message="Launching the native headset runner")
            self.adb.start(job["serial"],job["configuration"])
            self.update(run_id,status="running",message="Native runner starting",launched_at=time.time())
            self.monitor(run_id)
        except Exception as error:
            if not launch_attempted:
                self.update(run_id,status="failed",message="APK setup failed: "+str(error),storage="not applicable")
                return
            # An ambiguous launch may already be executing. Recover rather than rerun it.
            self.update(run_id,status="disconnected",message=str(error)+" — reconnect to recover this run")
            self.monitor(run_id)

    def monitor(self, run_id):
        while not self.stopping.wait(1):
            job=self.jobs[run_id]
            try:
                if job["cancel_requested"]:self.adb.stop(job["serial"],run_id)
                progress=self.adb.read(job["serial"],run_id,"progress.json")
                if progress.get("run_id")!=run_id:raise ValueError("Native progress identity mismatch")
                self.update(run_id,status=progress["status"],message=progress.get("message",""),progress=progress)
                report=self.adb.read(job["serial"],run_id,"report.json")
                if report.get("run_id")!=run_id or report.get("configuration")!=job["configuration"]:
                    raise ValueError("Native report does not match requested configuration")
                if report.get("status") in TERMINAL:
                    save(self.state/run_id/"native-report.json",report)
                    native_sha256=digest(report)
                    report=summarize(validate(report))
                    report["native_report_sha256"]=native_sha256
                    save(self.state/run_id/"report.json",report)
                    self.update(run_id,status=report["status"],counts=report["counts"],message="Native run "+report["status"],storage="pending",
                                report_available=True,report_revision=digest(report),progress=report.get("progress",progress))
                    self.submit(run_id);return
                report=summarize(report)
                save(self.state/run_id/"partial.json",report)
                self.update(run_id,counts=report["counts"],report_available=True,report_revision=digest(report),
                            progress=report.get("progress",progress),message=report.get("progress",progress).get("message","Native suite running"))
            except Exception as error:
                self.update(run_id,status="disconnected",message="Waiting for headset/report: "+str(error))

    def stop(self, run_id):
        with self.lock:
            job=self.jobs[run_id]
            if job["status"] in TERMINAL:return self.view(run_id)
            self.update(run_id,cancel_requested=True,message="Stop requested; waiting for current native work unit")
        try:self.adb.stop(job["serial"],run_id)
        except Exception:self.update(run_id,message="Stop queued; reconnect headset to deliver it")
        return self.view(run_id)

    def submit(self, run_id):
        with self.submission_lock:
            self._submit(run_id)

    def _submit(self, run_id):
        path=self.state/run_id/"report.json"
        if not path.exists():return
        try:
            report=json.loads(path.read_text());receipt=self.store.put(report)
            if receipt.get("stored") is not True or receipt.get("run_id")!=run_id or receipt.get("sha256")!=digest(report):
                raise RuntimeError("Database did not acknowledge this exact report")
            save(self.state/run_id/"receipt.json",receipt)
            self.update(run_id,storage="stored",receipt=receipt,storage_error=None)
        except Exception as error:
            self.update(run_id,storage="pending",storage_error=type(error).__name__+": database unavailable or report rejected; local report preserved")

    def report(self, run_id):
        path=self.state/run_id/"report.json"
        if path.exists():return json.loads(path.read_text())
        partial=self.state/run_id/"partial.json"
        if partial.exists():return json.loads(partial.read_text())
        return self.store.get(run_id)

    def background(self):
        while not self.stopping.is_set():
            self.device_snapshot=self.adb.devices()
            try:self.store.health();self.database=dict(ready=True,message="Local PostgreSQL ready")
            except Exception:self.database=dict(ready=False,message="PostgreSQL unavailable; reports will be kept locally and retried")
            with self.lock:pending=[k for k,v in self.jobs.items() if v.get("storage")=="pending" and v["status"] in TERMINAL]
            for run_id in pending:self.submit(run_id)
            self.stopping.wait(3)
