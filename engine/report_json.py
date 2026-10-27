"""Resource budgets for untrusted report JSON before recursive report processing."""

import json
import math

MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 750000
MAX_STRING = 65536
MAX_ARRAY = 25000
MAX_KEYS = 256


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON member')
        result[key] = value
    return result


def _preflight(text):
    # Reject deep input before json.loads can recurse. Bound lexical tokens as
    # well, so a small-number array cannot allocate millions of Python objects.
    depth = tokens = string_length = 0
    in_string = escaped = literal = False
    for char in text:
        if in_string:
            string_length += 1
            if string_length > MAX_STRING * 6 + 1:
                raise ValueError('Report string exceeds the resource budget')
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string, string_length, literal = True, 0, False
            tokens += 1
        elif char in '[{':
            depth += 1
            literal = False
            tokens += 1
            if depth > MAX_DEPTH:
                raise ValueError('Report nesting exceeds the resource budget')
        elif char in ']}':
            depth -= 1
            literal = False
        elif char in ',: \t\r\n':
            literal = False
        elif not literal:
            tokens += 1
            literal = True
        if tokens > MAX_NODES * 2:
            raise ValueError('Report complexity exceeds the resource budget')


def _string(value):
    if len(value) > MAX_STRING or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError('Report string exceeds the resource budget')


def load_report_json(payload):
    if not isinstance(payload, (bytes, bytearray)) or not 0 < len(payload) <= MAX_REPORT_BYTES:
        raise ValueError('Report exceeds the size limit')
    text = payload.decode('utf-8')
    _preflight(text)
    value = json.loads(text, object_pairs_hook=_unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite report')))
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError('Report complexity exceeds the resource budget')
        if isinstance(item, dict):
            if len(item) > MAX_KEYS:
                raise ValueError('Report object exceeds the resource budget')
            for key, child in item.items():
                _string(key)
                pending.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_ARRAY:
                raise ValueError('Report array exceeds the resource budget')
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            _string(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError('Non-finite report')
    if not isinstance(value, dict):
        raise ValueError('Expected a report object')
    return value
