"""Catalog identity is descriptive; native capability checks establish eligibility."""
import json
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("device-catalog.json").read_text())


def normalize(value):
    return " ".join(str(value or "").replace("_", " ").lower().split())


def identify(device):
    identifiers = {normalize(device.get(key)) for key in ("model", "product", "device")} - {"", "unknown"}
    candidates = [d for d in CATALOG["devices"] if identifiers.intersection(map(normalize, d["aliases"]))]
    reported = normalize(device.get("soc_model"))
    chip = next((c for c in CATALOG["chips"] if reported and reported in map(normalize, c["aliases"])), None)
    entry = candidates[0] if len(candidates) == 1 else None
    primary = entry["chips"][0]["chip_id"] if entry else None
    allowed = ({c['chip_id'] for c in entry['chips']} | set(entry.get('primary_chip_options', []))) if entry else set()
    conflict = len(candidates) > 1 or bool(chip and entry and chip['id'] not in allowed)
    chip_id = None if conflict else chip["id"] if chip else primary
    known = next((c for c in CATALOG["chips"] if c["id"] == chip_id), None)
    chips = []
    if not conflict:
        for component in entry['chips'] if entry else ([dict(chip_id=chip_id, location='unknown', role='primary')] if chip_id else []):
            component = dict(component)
            if component['chip_id'] is None and chip_id in (entry or {}).get('primary_chip_options', []):
                component['chip_id'] = chip_id
            definition = next((c for c in CATALOG['chips'] if c['id'] == component['chip_id']), {})
            component.update(name=definition.get('name', 'Processor generation unverified'),
                             execution_status=definition.get('execution', 'unverified'), executing=component['chip_id'] == chip_id and chip_id is not None)
            chips.append(component)
    location = next((c['location'] for c in chips if c['executing']), entry['chips'][0]['location'] if entry and not conflict else 'unknown')
    raw_soc = device.get('soc_model') if reported not in ('', 'unknown') else None
    return dict(catalog_version=CATALOG["version"], device_id=entry["id"] if entry else None,
                device_name=entry["name"] if entry else device.get("model", "Connected device"),
                chip_id=chip_id, chip_name=known["name"] if known else "Processor identity needs verification" if conflict else raw_soc or "Processor not identified",
                identity_source="conflicting" if conflict else "reported" if chip else "catalog" if primary else "unknown",
                reported_soc=device.get("soc_model"), visual_model=entry["visual"] if entry and not conflict else "schematic",
                execution_location=location, identity_evidence={key:device.get(key) for key in ('model','product','device','soc_model','soc_manufacturer')},
                chips=chips, source=entry["source"] if entry else (known or {}).get('source'),
                validation_status="unvalidated")
