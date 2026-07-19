"""JSON-lines protocol between the Flask backend and the ML worker process.

One JSON object per line, UTF-8. Parent → worker messages carry an ``op``
key; worker → parent messages carry an ``ev`` key.

Parent → worker:
    {"op": "job", "env": {...JobEnv...}, "job": {...JobRequest...}}
    {"op": "language", "recording_id": N, "code": "ru"}
    {"op": "cancel", "recording_id": N}
    {"op": "ping"}
    {"op": "shutdown"}

Worker → parent:
    {"ev": "ready", "pid": int, "version": str}
    {"ev": "pong"}
    {"ev": "status", "recording_id": N, "stage": str, "message": str,
     "percent": int?, "eta_secs": int?, ...extra}
    {"ev": "language_request", "recording_id": N, "guessed": str,
     "confidence": float, "options": [{"code","probability"}]}
    {"ev": "result", "recording_id": N, "transcript": {...}}
    {"ev": "cancelled", "recording_id": N}
    {"ev": "error", "recording_id": N?, "message": str, "error_type": str}
"""

import json


def dumps_line(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n'


def parse_line(line):
    """Parse one protocol line; returns dict or None for blank/garbage lines."""
    line = (line or '').strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None
