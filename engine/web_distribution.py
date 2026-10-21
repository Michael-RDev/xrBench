"""Publish a matched native runner and catalog. Never builds on a visitor's computer."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from engine.controller import Controller
from engine.devices import CATALOG
from engine.paths import BUILD, ROOT
from engine.reports import save
from layers import catalog, revision


def publish(output, build=BUILD):
    output, build = Path(output), Path(build)
    metadata = json.loads((build / 'web-apk.json').read_text())
    apk = build / 'xrgpu-benchmarks-web.apk'
    with apk.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    if metadata.get('package') != 'org.xrgpu.benchmarks.web' or metadata.get('apk_sha256') != checksum:
        raise ValueError('Expected a verified web distribution APK')
    if metadata.get('suite_revision') != revision():
        raise ValueError('Web runner and current workload catalog differ; rebuild the runner')
    if not metadata.get('signing_certificate_sha256') or not metadata.get('version_code'):
        raise ValueError('Web runner requires persistent signing provenance')
    # Use the exact existing configuration builder without starting a controller or DB.
    # Its build metadata filename is fixed; provide a read-only facade for that one file.
    class MetadataPath:
        def __truediv__(self, name):
            if name != 'apk.json':
                raise ValueError('Unexpected configuration dependency')
            return build / 'web-apk.json'
    owner = SimpleNamespace(build=MetadataPath())
    templates = {}
    for processor, backend in [('cpu', 'executorch_cpu'), ('gpu', 'executorch_vulkan')]:
        templates[processor], _ = Controller.configuration(owner, dict(
            backend=backend, mode='isolated', preset='smoke', execution_target='unknown'))
    runner = output / 'runner'
    runner.mkdir(parents=True, exist_ok=True)
    filename = checksum + '.apk'
    shutil.copy2(apk, runner / filename)
    manifest = dict(protocol_version=1, **metadata, apk=filename, apk_bytes=apk.stat().st_size,
                    activity='org.xrgpu.benchmarks.MainActivity', min_sdk=29, abi='arm64-v8a',
                    configurations=templates, catalog=catalog(), device_catalog=CATALOG)
    save(runner / 'manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--build', type=Path, default=BUILD)
    args = parser.parse_args()
    publish(args.output, args.build)
