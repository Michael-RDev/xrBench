"""Durable GPU-default sessions, with recovery of existing multi-phase runs."""
import copy
import hashlib
import json
import re
import time
import uuid

from engine.devices import identify
from engine.reports import TERMINAL, save

SESSION_ID = re.compile(r"bench-[0-9a-f]{32}\Z")
REQUEST_ID = re.compile(r"[a-zA-Z0-9_-]{16,80}\Z")


class DeviceUnavailable(ValueError):
    """The chosen transport is temporarily absent or awaiting authorization."""


class BenchmarkSessions:
    def load_sessions(self):
        self.benchmark_sessions = {}
        self.session_workers = set()
        for path in (self.state.parent / 'benchmark-sessions').glob('bench-*.json'):
            try:
                value = json.loads(path.read_text())
                if SESSION_ID.fullmatch(value['session_id']):
                    self.benchmark_sessions[value['session_id']] = value
            except (OSError, ValueError, KeyError):
                continue

    def save_session(self, session):
        session['updated_at'] = time.time()
        save(self.state.parent / 'benchmark-sessions' / (session['session_id'] + '.json'), session)

    def session_view(self, session_id):
        with self.lock:
            session = copy.deepcopy(self.benchmark_sessions[session_id])
            session['connection_id'] = hashlib.sha256(session.pop('serial').encode()).hexdigest()[:24]
            session.pop('request_id', None)
            for phase in session['phases']:
                if phase.get('run_id') in self.jobs:
                    phase['run'] = self.view(phase['run_id'])
            return session

    def session_reserved(self, serial, owner=None):
        return any(s['serial'] == serial and s['status'] not in TERMINAL and s['session_id'] != owner
                   for s in self.benchmark_sessions.values())

    def create_session(self, request):
        if set(request) - {'serial', 'request_id', 'processor'} or not isinstance(request.get('request_id'), str) or not REQUEST_ID.fullmatch(request['request_id']):
            raise ValueError('A valid idempotent request_id and device serial are required')
        processor = request.get('processor', 'gpu')
        if processor not in ('gpu', 'cpu'):
            raise ValueError('Processor must be gpu or cpu')
        with self.lock:
            for session in self.benchmark_sessions.values():
                if session['request_id'] == request['request_id']:
                    if request.get('serial') and request['serial'] != session['serial']:
                        raise ValueError('This request_id belongs to another device')
                    if (session.get('processor') and session['processor'] != processor or
                            not session.get('processor') and 'processor' in request):
                        raise ValueError('This request_id belongs to a different processor plan')
                    return self.session_view(session['session_id'])
            discovered = self.adb.devices()['devices']
            ready = [d for d in discovered if d.get('supported') and d['state'] == 'device']
            serial = request.get('serial')
            if not serial and len(discovered) == 1 and len(ready) == 1:
                serial = ready[0]['serial']
            device = next((d for d in ready if d['serial'] == serial), None)
            if device is None:
                raise ValueError('Select one authorized Android device')
            if self.session_reserved(serial) or any(j['serial'] == serial and j['status'] not in TERMINAL for j in self.jobs.values()) or self.probes.get(serial, {}).get('status') == 'running':
                raise ValueError('This device is busy; recover or stop its active run first')
            identity = identify(device)
            if processor == 'cpu' and not identity['cpu_eligible']:
                raise ValueError('CPU testing requires a chip confirmed to have no GPU')
            phases = []
            for backend in ['executorch_vulkan' if processor == 'gpu' else 'executorch_cpu']:
                config, metadata = self.configuration(dict(backend=backend, mode='isolated', preset='smoke', execution_target=identity['execution_location']))
                phases.append(dict(processor=processor, backend=backend, status='pending', configuration=config,
                                   run_id='run-' + uuid.uuid4().hex))
            session = dict(session_id='bench-' + uuid.uuid4().hex, request_id=request['request_id'], serial=serial,
                           status='starting', created_at=time.time(), cancel_requested=False, phases=phases,
                           identity=identity, processor=processor, current_phase=0, capabilities_checked=False)
            self.benchmark_sessions[session['session_id']] = session
            self.watch.leases.pop(serial, None)
            self.save_session(session)
            self.start_session_worker(session['session_id'])
            return self.session_view(session['session_id'])

    def start_session_worker(self, session_id):
        if self.start_workers and session_id not in self.session_workers:
            self.session_workers.add(session_id)
            self.spawn(self.monitor_session, session_id)

    def session_tick(self, session_id):
        """Advance one state under the controller lock, durable before launch."""
        with self.lock:
            s = self.benchmark_sessions[session_id]
            if s['status'] in TERMINAL:
                return
            phase = s['phases'][s['current_phase']]
            job = self.jobs.get(phase['run_id'])
            if s['cancel_requested']:
                if job and job['status'] not in TERMINAL:
                    self.stop(job['run_id'])
                    return
                phase['status'] = job['status'] if job else 'canceled'
                s['status'] = 'canceled'
                s['finished_at'] = time.time()
                self.save_session(s)
                return
            if job:
                phase['status'] = job['status']
                s['status'] = 'disconnected' if job['status'] == 'disconnected' else 'running'
                if job['status'] not in TERMINAL:
                    self.save_session(s)
                    return
                if job['status'] != 'complete':
                    s['status'] = job['status']
                    s['finished_at'] = time.time()
                    self.save_session(s)
                    return
            elif phase['status'] == 'pending':
                if not s.get('capabilities_checked'):
                    return
                if s.get('processor') == 'cpu':
                    evidence = {**s['identity'].get('identity_evidence', {}), **s.get('capabilities', {})}
                    if not identify(evidence)['cpu_eligible']:
                        phase.update(status='unsupported', reason='CPU testing requires a chip confirmed to have no GPU')
                        s.update(status='failed', error=phase['reason'], finished_at=time.time())
                        self.save_session(s)
                        return
                support = s.get('capabilities', {}).get('backends', {}).get(phase['backend'], {})
                if not support.get('compatible'):
                    phase.update(status='unsupported', reason=support.get('reason', 'Backend capability unavailable'))
                    if s.get('processor'):
                        s['error'] = phase['reason']
                else:
                    config, metadata = self.configuration(dict(backend=phase['backend'], mode='isolated', preset='smoke', execution_target=s['identity']['execution_location']))
                    if config != phase['configuration']:
                        s.update(status='failed', error='Build, suite, or device identity changed during session; start a new session', finished_at=time.time())
                        self.save_session(s)
                        return
                    try:
                        self.create(dict(serial=s['serial'], backend=phase['backend'], mode='isolated', preset='smoke', execution_target=s['identity']['execution_location']),
                                    session_owner=session_id, run_id=phase['run_id'])
                    except DeviceUnavailable:
                        s.update(status='disconnected', error='Reconnect the selected device to continue; the next phase has not started')
                        self.save_session(s)
                        return
                    phase['status'] = 'starting'
                    s['status'] = 'running'
                    s.pop('error', None)
                    self.save_session(s)
                    return
            if phase['status'] in ('complete', 'unsupported'):
                if s['current_phase'] + 1 < len(s['phases']):
                    s['current_phase'] += 1
                else:
                    s.update(status='complete', finished_at=time.time(), partial=any(p['status'] == 'unsupported' for p in s['phases']))
                self.save_session(s)

    def monitor_session(self, session_id):
        try:
            s = self.benchmark_sessions[session_id]
            if not s.get('capabilities_checked') and not s['cancel_requested']:
                metadata = json.loads((self.build / 'apk.json').read_text())
                self.adb.install(s['serial'], self.build / 'xrgpu-benchmarks.apk', metadata['apk_sha256'])
                if s['cancel_requested']:
                    self.session_tick(session_id)
                    return
                caps = self.adb.probe(s['serial'])
                self.record_profile(s['serial'], caps)
                with self.lock:
                    s['capabilities'] = caps
                    s['capabilities_checked'] = True
                    # Use native identity when present; don't discard catalog aliases on empty properties.
                    evidence = dict(s['identity'].get('identity_evidence', {}))
                    evidence.update({k: v for k, v in caps.items() if v not in (None, '', 'unknown')})
                    s['identity'] = identify(evidence)
                    self.save_session(s)
            while not self.stopping.is_set():
                self.session_tick(session_id)
                if self.benchmark_sessions[session_id]['status'] in TERMINAL:
                    return
                self.stopping.wait(.25)
        except Exception as error:
            with self.lock:
                s = self.benchmark_sessions[session_id]
                s.update(status='failed', error=str(error), finished_at=time.time())
                self.save_session(s)
        finally:
            self.session_workers.discard(session_id)

    def stop_session(self, session_id):
        with self.lock:
            s = self.benchmark_sessions[session_id]
            if s['status'] not in TERMINAL:
                s['cancel_requested'] = True
                self.watch.leases.pop(s['serial'], None)
                self.save_session(s)
                phase = s['phases'][s['current_phase']]
                if phase['run_id'] in self.jobs:
                    self.stop(phase['run_id'])
            return self.session_view(session_id)
