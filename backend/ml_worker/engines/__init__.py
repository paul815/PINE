"""Engine registry: pick and construct the right EngineAdapter for a model id."""

from .base import (  # noqa: F401 — re-exported interface
    EngineAdapter,
    EngineCapabilities,
    LanguageProbe,
    TranscribeContext,
    TranscribeOutput,
)

ENGINE_WHISPERX = 'whisperx'
ENGINE_MLX = 'mlx'
ENGINE_ONNX = 'onnx'


def engine_kind_for_model(stt_model_id: str) -> str:
    """Map an installed STT model id to the engine that runs it."""
    model_id = (stt_model_id or '').strip()
    if model_id.startswith('mlx-'):
        return ENGINE_MLX
    if model_id.startswith('parakeet-'):
        return ENGINE_ONNX
    return ENGINE_WHISPERX


def select_engine_config(stt_model_id: str, prefer_mps: bool,
                         detected_device: str | None = None,
                         detected_compute: str | None = None):
    """Return (engine, device, compute_type) for the requested STT model.

    ``detected_device``/``detected_compute`` carry an earlier CUDA/CPU probe so
    the choice is stable across calls.
    """
    kind = engine_kind_for_model(stt_model_id)
    if kind == ENGINE_MLX:
        return ENGINE_MLX, 'cpu', None
    if kind == ENGINE_ONNX:
        # Deliberately CPU everywhere: onnxruntime-gpu carries its own CUDA and
        # cuDNN wheels, and two CUDA runtimes in one process with torch is how
        # that breaks. The card stays with diarization.
        return ENGINE_ONNX, 'cpu', 'int8'
    compute_type = 'int8'
    if prefer_mps:
        # WhisperX/CTranslate2 can't use MPS; CPU int8 is the best Mac fallback.
        return ENGINE_WHISPERX, 'cpu', compute_type
    return (ENGINE_WHISPERX,
            detected_device or 'cpu',
            detected_compute or compute_type)


def engine_capabilities(stt_model_id: str) -> EngineCapabilities:
    """Capabilities of the engine that would run this model (no ML imports)."""
    from .mlx_engine import MlxWhisperEngine
    from .onnx_engine import OnnxAsrEngine
    from .whisperx_engine import WhisperXEngine
    kind = engine_kind_for_model(stt_model_id)
    if kind == ENGINE_MLX:
        return MlxWhisperEngine.capabilities
    if kind == ENGINE_ONNX:
        return OnnxAsrEngine.capabilities
    return WhisperXEngine.capabilities


def create_engine(env, prefer_mps: bool = False) -> EngineAdapter:
    """Construct (but do not load) the adapter for ``env.stt_model_id``."""
    kind = engine_kind_for_model(env.stt_model_id)
    if kind == ENGINE_MLX:
        from .mlx_engine import MlxWhisperEngine
        return MlxWhisperEngine(env)
    if kind == ENGINE_ONNX:
        from .onnx_engine import OnnxAsrEngine
        return OnnxAsrEngine(env)
    from .whisperx_engine import WhisperXEngine, detect_torch_device
    engine = WhisperXEngine(env)
    if prefer_mps:
        engine._device = 'cpu'
        engine._compute_type = 'int8'
    else:
        engine._device, engine._compute_type = detect_torch_device()
    return engine
