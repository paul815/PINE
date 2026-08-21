"""Parakeet TDT via onnx-asr — the CPU engine that leaves the GPU to diarization.

Parakeet is a token-and-duration Transducer: it emits per-token times of its own,
so this engine needs no wav2vec2 alignment stage. Diarization stays external
(the shared pyannote stage).

Deliberately CPU-only. onnxruntime-gpu ships its own CUDA and cuDNN wheels, and
loading those into the same process as torch means two CUDA runtimes fighting
over one card. Running here on the CPU costs RTF 0.102 instead of 0.036 and buys
something better: the card is free for pyannote, so the two stages stop
competing and the job costs roughly max(STT, diarize) rather than the sum.
"""

import gc
import logging
import time

from ..audio import fmt_elapsed, load_audio_file
from .base import (
    EngineAdapter,
    EngineCapabilities,
    TranscribeContext,
    TranscribeOutput,
)

log = logging.getLogger(__name__)

# onnx-asr's own name for the weights; the local snapshot is passed separately
# as ``path`` so nothing reaches for the network once onboarding is done.
ONNX_MODEL_NAME = 'nemo-parakeet-tdt-0.6b-v3'
ONNX_VAD_NAME = 'silero'
# int8 on the CPU: 0.102 RTF against 0.115 for fp32, and a third of the download.
ONNX_QUANTIZATION = 'int8'

CPU_PROVIDERS = ['CPUExecutionProvider']

# Silero's defaults cut on every short pause, which for an interview means
# hundreds of two-second fragments and one model call each. These are the
# `vad-xlong` window from the ONNX sweep — lowest divergence from the NeMo
# reference (1.50%) at a median word drift of 0.024 s.
VAD_MAX_SPEECH_SEC = 45.0
VAD_MIN_SILENCE_MS = 500.0
VAD_SPEECH_PAD_MS = 200.0

# SentencePiece marks a word start with U+2581, not a space.
SENTENCEPIECE_SPACE = '▁'

PROGRESS_MIN_INTERVAL_SEC = 2.0
PROGRESS_ETA_MIN_FRACTION = 0.10


def absolute_stamps(stamps, seg_start, seg_end):
    """Return token stamps on the recording's clock.

    onnx-asr hands the ASR a VAD-cut waveform, so stamps normally come back
    relative to their segment. Detected rather than assumed: when the largest
    stamp already sits past the segment's own span they are absolute already.
    """
    if not stamps:
        return []
    span = seg_end - seg_start
    if max(stamps) <= span + 0.5:
        return [s + seg_start for s in stamps]
    return list(stamps)


def tokens_to_words(tokens, stamps, seg_end):
    """Group SentencePiece tokens into words with start/end times.

    ``stamps`` are per token and already absolute. A token's end is the next
    token's start — a Transducer times word starts, not their tails.
    """
    words = []
    for tok, t in zip(tokens, stamps, strict=False):
        starts_word = tok.startswith((SENTENCEPIECE_SPACE, ' '))
        clean = tok.replace(SENTENCEPIECE_SPACE, ' ')
        if starts_word or not words:
            words.append({'start': float(t), 'end': None, 'word': clean})
        else:
            words[-1]['word'] += clean

    out = []
    for i, w in enumerate(words):
        text = w['word'].strip()
        if not text:
            continue
        end = words[i + 1]['start'] if i + 1 < len(words) else seg_end
        # 'score' has no meaning for a TDT — it is carried because the unified
        # segment format and the recording UI both expect the field.
        out.append({'word': text, 'start': w['start'],
                    'end': float(end), 'score': 1.0})
    return out


class OnnxAsrEngine(EngineAdapter):
    id = 'onnx'
    capabilities = EngineCapabilities(
        word_timestamps=True,
        diarization='external',
        streaming=False,
        multilingual=True,
    )

    def __init__(self, env=None):
        super().__init__(env)
        self._model = None
        self._model_dir = None

    def load(self):
        model_dir = self.env.model_dir
        if self._model is not None and self._model_dir == model_dir:
            return
        self._model = None

        import onnx_asr
        log.info('Loading Parakeet (onnx-asr, CPU int8) from %s ...', model_dir)
        model = onnx_asr.load_model(
            ONNX_MODEL_NAME,
            path=model_dir,
            quantization=ONNX_QUANTIZATION,
            providers=CPU_PROVIDERS,
        )
        # The VAD needs the provider spelled out too: onnxruntime's own default
        # is every provider it was built with, so an onnxruntime-gpu in the venv
        # would put this one model on the card while the ASR stays on the CPU.
        vad = onnx_asr.load_vad(ONNX_VAD_NAME, path=self.env.vad_dir or None,
                                providers=CPU_PROVIDERS)
        # Order matters: with_vad first, then with_timestamps. The reverse
        # silently yields plain results with no timestamps at all.
        self._model = model.with_vad(
            vad,
            max_speech_duration_s=VAD_MAX_SPEECH_SEC,
            min_silence_duration_ms=VAD_MIN_SILENCE_MS,
            speech_pad_ms=VAD_SPEECH_PAD_MS,
        ).with_timestamps()
        self._model_dir = model_dir
        log.info('Parakeet loaded.')

    def probe_language(self, audio_path, probe_audio):
        """No probe: Parakeet identifies the language itself, per segment.

        It also rejects a ``language`` kwarg outright, so there is nothing to
        confirm with the user and nothing to force. Returning None puts the
        pipeline on its auto-detect path.
        """
        return None

    def transcribe(self, audio_path, total_duration, language,
                   ctx: TranscribeContext) -> TranscribeOutput:
        if language:
            log.info('Parakeet detects language per segment — the requested '
                     '%s is not forced on it', language)

        ctx.on_status(stage='transcribing',
                      message='Transcribing audio... (CPU, GPU left to speaker detection)')

        # A file path would restrict onnx-asr to plain PCM WAV; PINE accepts
        # whatever ffmpeg can open, so hand it the decoded waveform instead.
        audio = load_audio_file(audio_path)
        if total_duration <= 0:
            total_duration = len(audio) / 16000

        segments = []
        started = time.monotonic()
        # onnx-asr runs the VAD over the whole file before it yields anything, so
        # the pace is timed from the first segment: including the VAD pass would
        # make the first estimate several times too long.
        transcribe_started = None
        last_report = 0.0

        for res in self._model.recognize(audio, sample_rate=16000):
            ctx.check_cancel()
            if transcribe_started is None:
                transcribe_started = time.monotonic()

            text = (res.text or '').strip()
            if not text:
                continue

            stamps = absolute_stamps(res.timestamps or [], res.start, res.end)
            words = tokens_to_words(res.tokens or [], stamps, res.end) if res.tokens else []
            segments.append({
                'start': round(float(res.start), 2),
                'end': round(float(res.end), 2),
                'text': ' '.join(w['word'] for w in words) if words else text,
                'words': words,
            })

            now = time.monotonic()
            if now - last_report >= PROGRESS_MIN_INTERVAL_SEC:
                last_report = now
                elapsed = now - transcribe_started
                done = min(float(res.end) / total_duration, 1.0) if total_duration else 0
                pct = round(done * 100)
                # Below a tenth of the file the rate is still mostly noise, and a
                # wrong ETA is worse than none.
                eta = (round(elapsed / done - elapsed)
                       if done >= PROGRESS_ETA_MIN_FRACTION else None)
                msg = (f'Transcribing... {pct}% — ~{fmt_elapsed(eta)} remaining'
                       if eta else f'Transcribing... {pct}%')
                ctx.on_status(stage='transcribing', message=msg,
                              percent=pct, eta_secs=eta)

        log.info('Parakeet produced %d segments, %d words in %s',
                 len(segments), sum(len(s['words']) for s in segments),
                 fmt_elapsed(time.monotonic() - started))

        del audio
        gc.collect()

        return TranscribeOutput(
            segments=segments,
            # Parakeet reports no language of its own; the project preset is the
            # only thing PINE knows here, and it may well be empty.
            language=language or '',
            duration_seconds=total_duration,
            audio=None,
        )
