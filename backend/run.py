import warnings

# Suppress pyannote torchcodec warning: we pass preloaded audio via whisperx.load_audio, never use torchcodec
warnings.filterwarnings('ignore', message='torchcodec')
warnings.filterwarnings('ignore', module=r'pyannote\.audio\.core\.io')
# Suppress PyTorch std() degrees-of-freedom warning from pyannote (harmless edge case)
warnings.filterwarnings('ignore', message='.*degrees of freedom.*')
# Suppress pyannote TF32 reproducibility warning (cosmetic)
warnings.filterwarnings('ignore', message='TensorFloat-32')

import os
import sys
import types
from urllib.parse import urlparse


def _disable_model_telemetry():
    """Disable telemetry/analytics toggles before importing the Flask app."""
    os.environ['PYANNOTE_METRICS_ENABLED'] = 'false'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['DISABLE_TELEMETRY'] = '1'
    os.environ['DO_NOT_TRACK'] = '1'
    os.environ['WANDB_DISABLED'] = 'true'
    os.environ['OTEL_SDK_DISABLED'] = 'true'
    os.environ['OTEL_METRICS_EXPORTER'] = 'none'
    os.environ['OTEL_TRACES_EXPORTER'] = 'none'
    os.environ['OTEL_LOGS_EXPORTER'] = 'none'

    # pyannote.audio 4.x initializes an OTLP exporter at import time, before
    # checking PYANNOTE_METRICS_ENABLED. Stub the telemetry module entirely so
    # no exporter thread or outbound connection is created.
    telemetry_stub = types.ModuleType('pyannote.audio.telemetry')

    def _noop(*args, **kwargs):
        return None

    telemetry_stub.set_opentelemetry_log_level = _noop
    telemetry_stub.set_telemetry_metrics = _noop
    telemetry_stub.track_model_init = _noop
    telemetry_stub.track_pipeline_init = _noop
    telemetry_stub.track_pipeline_apply = _noop
    sys.modules['pyannote.audio.telemetry'] = telemetry_stub
    sys.modules['pyannote.audio.telemetry.metrics'] = telemetry_stub

    try:
        import onnxruntime as ort
        ort.disable_telemetry_events()
    except Exception:
        pass


_disable_model_telemetry()


def _looks_like_disabled_local_proxy(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == 9


def _clear_broken_proxy_placeholders() -> None:
    proxy_keys = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    broken_keys = [key for key in proxy_keys if _looks_like_disabled_local_proxy(os.environ.get(key))]
    if not broken_keys:
        return
    for key in broken_keys:
        os.environ.pop(key, None)
    print(
        "Ignoring disabled proxy placeholder for first-launch downloads:",
        ", ".join(sorted(broken_keys)),
        flush=True,
    )


_clear_broken_proxy_placeholders()

# ffmpeg is deliberately not prepared here. static_ffmpeg.add_paths() downloads
# ~190 MB on a first run, and this line sits above the Flask import, so doing it
# here meant no page could be served until the transfer finished. It now happens
# during the model download, where there is a progress bar; app.services
# .ffmpeg_setup.ensure_ffmpeg() covers everyone who needs the binary.

from app import create_app
from app.extensions import socketio
from app.ports import backend_port

app = create_app()

if __name__ == '__main__':
    _debug = os.environ.get('PINE_DEBUG', '').strip() in ('1', 'true', 'yes')
    _port = backend_port()
    socketio.run(
        app,
        host='127.0.0.1',
        port=_port,
        debug=_debug,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
