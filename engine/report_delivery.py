"""Local testing receiver for automatically submitted native reports.

Validation establishes report structure, not physical hardware attestation.
Keep these files outside the served website and out of Git.
"""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from engine.reports import digest, validate

MAX_REPORT_BYTES = 32 * 1024 * 1024


def simulated(value):
    if isinstance(value, dict):
        return (value.get('simulated') is True or value.get('source') == 'simulated'
                or value.get('presentation_source') == 'simulated'
                or any(simulated(v) for v in value.values()))
    return isinstance(value, list) and any(simulated(v) for v in value)


class LocalReportSink:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def put(self, payload):
        if not 0 < len(payload) <= MAX_REPORT_BYTES:
            raise ValueError('Report exceeds the local receiver size limit')
        try:
            report = json.loads(payload)
            if simulated(report):
                raise ValueError('Simulated reports cannot be submitted')
            validate(report)
            # The native report identity must also agree with its launch configuration.
            if report['configuration'].get('run_id', report['run_id']) != report['run_id']:
                raise ValueError('Run identity mismatch')
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
            raise ValueError('Expected a valid, finished native benchmark report') from error
        checksum = digest(report)
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / (report['run_id'] + '.json')
        # Publish only fully flushed files. Linking is exclusive even across receiver
        # processes, so retries can never replace an existing run with different data.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix='.incoming-', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if digest(json.loads(destination.read_bytes())) != checksum:
                    raise FileExistsError('This run ID already has a different report')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return dict(stored=True, run_id=report['run_id'], storage='local',
                    payload_sha256=hashlib.sha256(payload).hexdigest(), report_sha256=checksum,
                    received_at=datetime.fromtimestamp(destination.stat().st_mtime, timezone.utc).isoformat(),
                    verification='schema-validated; hardware not attested')
