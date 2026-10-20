import json
import threading
import time
import uuid
import secrets
from pathlib import Path

from layers import revision
from engine.selection import resolve_operations
from engine.backends import BACKENDS, TARGETS, get_backend
from engine import profiles
from engine.watch import WatchRegistry
from engine.benchmark_sessions import BenchmarkSessions, DeviceUnavailable
from engine.devices import identify
from engine.reports import TERMINAL, canonical, digest, save, summarize, validate


class Controller(BenchmarkSessions):
    def __init__(self, adb, store, state, build, start_workers=True):
        self.adb, self.store, self.state, self.build = adb, store, Path(state), Path(build)
        self.state.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()
        self.submission_lock = threading.Lock()
        self.stopping = threading.Event()
        self.jobs = {}
        self.probes = {}
        self.profiles = {}
        for path in (self.state.parent/'device-profiles').glob('*.json'):
            try:
                entry=json.loads(path.read_text())
                if entry['profile_id']==profiles.identity(entry['profile']):self.profiles[entry['serial']]=entry
            except (ValueError,KeyError,OSError,TypeError):pass
        self.watch = WatchRegistry()
        self.device_snapshot = dict(devices=[],error="Detecting headsets…")
        self.database = dict(ready=False,message="Checking PostgreSQL…")
        self.start_workers = start_workers
        self.load_sessions()
        for path in self.state.glob("run-*/job.json"):
            try:
                job=json.loads(path.read_text());self.jobs[job["run_id"]]=job
                if job["status"] == "starting":
                    self.update(job["run_id"],status="failed",message="Controller stopped during APK setup; start a new run",storage="not applicable")
                if start_workers and job["status"] not in TERMINAL:
                    self.spawn(self.monitor,job["run_id"])
            except (ValueError,KeyError,OSError): continue
        if start_workers:
            for session in self.benchmark_sessions.values():
                if session['status'] not in TERMINAL:self.start_session_worker(session['session_id'])
            self.spawn(self.background)

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

    def configuration(self, request):
        if set(request)-{"serial","mode","preset","operations","calibration","experiment","profiling","backend","execution_target","workload_suite","validation_control"}:raise ValueError("Unknown run setting")
        mode,preset=request.get("mode","isolated"),request.get("preset","smoke")
        if mode not in ("isolated","xr") or preset not in ("smoke","standard","sustained"):raise ValueError("Invalid run configuration")
        policy=request.get('calibration','auto')
        if policy not in ('auto','refresh'):raise ValueError('Invalid calibration policy')
        profile=request.get('workload_suite','standard')
        operations,cases=resolve_operations(request.get('operations'),profile)
        backend=get_backend(request.get('backend','executorch_vulkan'))
        target=request.get('execution_target','unknown')
        if target not in TARGETS:raise ValueError('Unknown execution target')
        metadata=json.loads((self.build/"apk.json").read_text())
        if metadata["suite_revision"]!=revision():raise ValueError("APK catalog is outdated. Rebuild the benchmark APK.")
        config=dict(mode=mode,preset=preset,operations=operations,calibration=policy,backend=backend.id,
            execution_target=target,workload_suite=profile,validation_control=request.get('validation_control','none'),
            cases=[c['id'] for c in cases],suite_revision=revision(),runtime_revision=metadata['runtime_revision'],
            runner_revision=metadata['runner_revision'],apk_sha256=metadata['apk_sha256'])
        experiment=request.get('experiment','none');profiling=request.get('profiling','trace')
        if experiment not in ('none','aa','candidate') or profiling not in ('trace','latency'):raise ValueError('Invalid kernel experiment setting')
        if experiment!='none':
            if mode!='isolated' or preset=='sustained' or operations!=['matmul']:
                raise ValueError('Kernel experiments require matmul only, Native layers, and Smoke or Standard')
            from engine.kernels import source_identity
            if metadata.get('kernel_experiments_version')!=1 or metadata.get('candidate_revision')!=source_identity():
                raise ValueError('Build the current kernel experiment APK first')
            config.update(experiment=experiment,experiment_pairs=6 if preset=='smoke' else 10,
                          experiment_seed=secrets.randbits(31),candidate_revision=metadata['candidate_revision'])
        if profiling!='trace':config['profiling']=profiling
        backend.check_request(config,cases)
        if backend.id not in metadata.get('backends',['executorch_vulkan']):raise ValueError('Rebuild the APK with the selected backend')
        return config,metadata

    def create(self, request, *, session_owner=None, run_id=None):
        config,metadata=self.configuration(request)
        devices=self.adb.devices()
        authorized=[d for d in devices["devices"] if d["state"]=="device" and d["supported"]]
        serial=request.get("serial")
        if not serial and len(authorized)==1:serial=authorized[0]["serial"]
        if serial not in {d["serial"] for d in authorized}:raise DeviceUnavailable("Select an authorized, supported headset")
        device=next(d for d in authorized if d['serial']==serial)
        get_backend(config['backend']).check_device(device,config)
        run_id=run_id or "run-"+uuid.uuid4().hex
        config['run_id']=run_id
        with self.lock:
            if self.session_reserved(serial, session_owner):raise ValueError('This device has an active CPU/GPU session')
            if run_id in self.jobs:return self.view(run_id)
            if self.probes.get(serial,{}).get('status')=='running':raise ValueError('Wait for capability discovery to finish')
            if any(j["serial"]==serial and j["status"] not in TERMINAL for j in self.jobs.values()):
                raise ValueError("This headset already has an active or disconnected run; recover it first")
            self.jobs[run_id]=dict(run_id=run_id,serial=serial,status="starting",message="Preparing native app",configuration=config,
                created_at=time.time(),storage="pending",cancel_requested=False)
            self.update(run_id)
            self.watch.attempted.add(self.watch.key(serial,config))
        if self.start_workers:self.spawn(self.launch,run_id,metadata)
        return self.view(run_id)

    def watch_request(self, body):
        if set(body)-{'action','owner','serial','mode','preset','operations','profiling','backend','execution_target','workload_suite','validation_control'}:raise ValueError('Unknown watch setting')
        request={k:v for k,v in body.items() if k not in ('action','owner')}
        if request.get('validation_control','none')!='none':raise ValueError('Validation controls cannot run continuously')
        if body.get('action')!='disable':
            self.configuration(request)
            if not isinstance(request.get('serial'),str):raise ValueError('Choose a headset')
        with self.lock:
            self.watch.update(body.get('owner'),body.get('action'),request.get('serial'),request)
            self.watch_tick()
            return self.watch.public()

    def watch_tick(self):
        with self.lock:
            self.watch.expire()
            for serial,lease in self.watch.leases.items():
                if self.session_reserved(serial):
                    lease['state']='Waiting for the CPU/GPU session';continue
                if any(j['serial']==serial and j['status'] not in TERMINAL for j in self.jobs.values()):
                    lease['state']='Waiting for the active run';continue
                try:
                    config,_=self.configuration(lease['request'])
                    key=self.watch.key(serial,config)
                    if key in self.watch.attempted:
                        lease['state']='Watching for a new build or configuration';continue
                    devices=self.device_snapshot.get('devices',[])
                    if not any(d['serial']==serial and d['state']=='device' and d['supported'] for d in devices):
                        lease['state']='Waiting for the selected headset';continue
                    # Mark the attempt first; the device can start even if ADB loses the reply.
                    self.watch.attempted.add(key)
                    job=self.create(lease['request']);lease['run_id']=job['run_id'];lease['state']='Automatic run started'
                except (OSError,ValueError,KeyError) as error:
                    lease['state']=str(error)

    def dashboard_status(self):
        try:build=json.loads((self.build/'apk.json').read_text())
        except (OSError,ValueError):build=None
        with self.lock:
            return dict(backends=[dict(id=b.id,label=b.label) for b in BACKENDS.values()],
                device_profiles=list(self.profiles.values()),probes=list(self.probes.values()),server_generation=self.watch.generation,build_identity=build,watch_status=self.watch.public(),
                calibration_policy='Automatic on first compatible run; cached on the headset',
                calibration_profiles=[j['calibration'] for j in self.jobs.values() if j.get('calibration')])

    def diagnostic(self,run_id,**values):
        with self.lock:
            totals=dict(self.jobs[run_id].get('diagnostics',{}))
            for key,value in values.items():totals[key]=totals.get(key,0)+value
            self.update(run_id,diagnostics=totals)

    def record_profile(self, serial, profile):
        profile=dict(profile, identity=identify(profile))
        envelope=dict(serial=serial,profile_id=profiles.identity(profile),profile=profile)
        with self.lock:
            self.profiles[serial]=envelope
            save(self.state.parent/'device-profiles'/(digest(serial)+'.json'),envelope)
        return envelope

    def probe_request(self, body):
        if set(body)!={'serial'} or not isinstance(body['serial'],str):raise ValueError('Choose one device to inspect')
        serial=body['serial']
        devices=self.adb.devices()['devices']
        if not any(d['serial']==serial and d['state']=='device' and d['supported'] for d in devices):raise ValueError('Choose an authorized Android arm64 device')
        metadata=json.loads((self.build/'apk.json').read_text())
        if metadata.get('device_profiles_version')!=1:raise ValueError('Build the capability discovery APK first')
        with self.lock:
            if self.session_reserved(serial) or self.probes.get(serial,{}).get('status')=='running' or any(j['serial']==serial and j['status'] not in TERMINAL for j in self.jobs.values()):raise ValueError('This device is busy')
            self.probes[serial]=dict(serial=serial,status='running',message='Inspecting native capabilities')
        if self.start_workers:self.spawn(self.probe_device,serial,metadata)
        return dict(self.probes[serial])

    def probe_device(self, serial, metadata):
        try:
            get_backend().install(self.adb,serial,self.build,metadata)
            profile=self.adb.probe(serial)
            self.record_profile(serial,profile)
            result=dict(serial=serial,status='complete',message='Capabilities recorded; hardware validation remains pending')
        except Exception as error:result=dict(serial=serial,status='failed',message=str(error))
        with self.lock:self.probes[serial]=result

    def launch(self, run_id, metadata):
        job=self.jobs[run_id]
        launch_attempted=False
        try:
            backend=get_backend(job["configuration"].get("backend","executorch_vulkan"))
            backend.install(self.adb,job["serial"],self.build,metadata)
            if job["cancel_requested"]:
                self.update(run_id,status="canceled",message="Canceled before launch",storage="not applicable");return
            launch_attempted=True
            self.update(run_id,status="launching",message="Launching the native headset runner")
            backend.launch(self.adb,job["serial"],job["configuration"])
            self.update(run_id,status="running",message="Native runner starting",launched_at=time.time())
            self.monitor(run_id)
        except Exception as error:
            if not launch_attempted:
                self.update(run_id,status="failed",message="APK setup failed: "+str(error),storage="not applicable")
                return
            # The device may already be running. A dropped USB cable isn't a request to run twice.
            self.update(run_id,status="disconnected",message=str(error)+" — reconnect to recover this run")
            self.monitor(run_id)

    def monitor(self, run_id):
        while not self.stopping.wait(1):
            job=self.jobs[run_id]
            transfer_start=time.monotonic();transferred=False
            try:
                if job["cancel_requested"]:self.adb.stop(job["serial"],run_id)
                progress=self.adb.read(job["serial"],run_id,"progress.json")
                if progress.get("run_id")!=run_id:raise ValueError("Native progress identity mismatch")
                # A terminal progress snapshot can arrive before the report is recovered.
                # Keep the reservation until the final evidence has passed validation.
                progress_status = "running" if progress["status"] in TERMINAL else progress["status"]
                self.update(run_id,status=progress_status,message=progress.get("message",""),progress=progress)
                report=self.adb.read(job["serial"],run_id,"report.json")
                self.diagnostic(run_id,transfer_ms=(time.monotonic()-transfer_start)*1000,
                    report_fetches=1,transferred_bytes=len(canonical(report))+len(canonical(progress)))
                transferred=True
                if report.get("run_id")!=run_id or report.get("configuration")!=job["configuration"]:
                    raise ValueError("Native report does not match requested configuration")
                if report.get("status") in TERMINAL:
                    # Keep the device's original report before validation and summaries change our copy.
                    save(self.state/run_id/"native-report.json",report)
                    if report.get("device",{}).get("profile_version")==1:self.record_profile(job["serial"],report["device"])
                    native_sha256=digest(report)
                    report=summarize(validate(report))
                    report["device_identity"]=identify(report.get("device",{}))
                    report["native_report_sha256"]=native_sha256
                    save(self.state/run_id/"report.json",report)
                    self.update(run_id,status=report["status"],counts=report["counts"],message="Native run "+report["status"],storage="pending",
                                report_available=True,report_revision=digest(report),progress=report.get("progress",progress),calibration=report.get('calibration'))
                    self.submit(run_id);return
                report=summarize(report)
                save(self.state/run_id/"partial.json",report)
                self.update(run_id,counts=report["counts"],report_available=True,report_revision=digest(report),
                            calibration=report.get('calibration'),
                            progress=report.get("progress",progress),message=report.get("progress",progress).get("message","Native suite running"))
            except Exception as error:
                if not transferred:self.diagnostic(run_id,transfer_failures=1,transfer_ms=(time.monotonic()-transfer_start)*1000)
                self.update(run_id,status="disconnected",message="Waiting for headset/report: "+str(error))

    def stop(self, run_id):
        with self.lock:
            job=self.jobs[run_id]
            self.watch.leases.pop(job['serial'],None)
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
        begin=time.monotonic()
        try:
            report=json.loads(path.read_text());receipt=self.store.put(report)
            if receipt.get("stored") is not True or receipt.get("run_id")!=run_id or receipt.get("sha256")!=digest(report):
                raise RuntimeError("Database did not acknowledge this exact report")
            save(self.state/run_id/"receipt.json",receipt)
            self.update(run_id,storage="stored",receipt=receipt,storage_error=None)
        except Exception as error:
            self.update(run_id,storage="pending",storage_error=type(error).__name__+": database unavailable or report rejected; local report preserved")
        finally:
            self.diagnostic(run_id,database_attempts=1,database_ms=(time.monotonic()-begin)*1000)

    def report(self, run_id):
        path=self.state/run_id/"report.json"
        if path.exists():return json.loads(path.read_text())
        partial=self.state/run_id/"partial.json"
        if partial.exists():return json.loads(partial.read_text())
        return self.store.get(run_id)

    def background(self):
        while not self.stopping.is_set():
            self.device_snapshot=self.adb.devices()
            self.watch_tick()
            try:self.store.health();self.database=dict(ready=True,message="Local PostgreSQL ready")
            except Exception:self.database=dict(ready=False,message="PostgreSQL unavailable; reports will be kept locally and retried")
            with self.lock:pending=[k for k,v in self.jobs.items() if v.get("storage")=="pending" and v["status"] in TERMINAL]
            for run_id in pending:self.submit(run_id)
            self.stopping.wait(3)
