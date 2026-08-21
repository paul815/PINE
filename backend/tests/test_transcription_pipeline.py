"""Tier 2 tests: Mocked transcription pipeline — speaker assignment, chunking,
cancellation, requeue, and status flow.

The ML pipeline lives in the Flask-free ``ml_worker`` package; queue/cancel/
status orchestration stays in ``app.services.transcription``. Tests below
target whichever side owns the logic.
"""

import os
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest


def _module_available(name):
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# Heavy ML packages are installed dynamically by model_manager.py during
# onboarding, not pinned in requirements.txt. A bare checkout (and CI) has no
# torch, so the tests that exercise real tensor code are skipped rather than
# failed — the mocked tests above them still run everywhere.
# Requires both: these exercise real tensor code and its numpy interop, and a
# torch present without numpy is a half-finished install rather than a usable
# one — checking only torch lets such a state through and the tests then fail
# on the numpy import instead of skipping.
needs_torch = pytest.mark.skipif(
    not (_module_available('torch') and _module_available('numpy')),
    reason='requires the torch/numpy stack, installed during onboarding rather than from requirements.txt',
)

needs_numpy = pytest.mark.skipif(
    not _module_available('numpy'),
    reason='requires numpy, pulled in with the ML stack during onboarding',
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal mock of pyannote Segment (named tuple with start/end)
PyannoteSegment = namedtuple('PyannoteSegment', ['start', 'end'])


class FakeDiarization:
    """Mimics pyannote.core.Annotation enough for assign_speakers_simple."""

    def __init__(self, turns):
        """turns: list of (start, end, speaker_label)"""
        self._turns = turns

    def itertracks(self, yield_label=False):
        for start, end, speaker in self._turns:
            seg = PyannoteSegment(start, end)
            if yield_label:
                yield seg, None, speaker
            else:
                yield seg, None


def _make_diarizer(engine_kind='mlx', device='cpu', pipeline=None):
    """Create a Diarizer without loading any ML models."""
    from ml_worker.diarize import Diarizer
    d = Diarizer(env=None, engine_kind=engine_kind, device=device)
    d.pipeline = pipeline
    return d


# ---------------------------------------------------------------------------
# 0. MLX segment text sync (Mac-native UI contract)
# ---------------------------------------------------------------------------

class TestSynchronizeMlxSegmentsForUi:
    def test_joins_trimmed_words_into_segment_text(self):
        from ml_worker.engines.mlx_engine import synchronize_segments_for_ui
        segs = [{
            'start': 0.0,
            'end': 1.0,
            'text': 'wrong',
            'words': [
                {'word': ' Hello', 'start': 0.0, 'end': 0.5},
                {'word': ' world', 'start': 0.5, 'end': 1.0},
            ],
        }]
        synchronize_segments_for_ui(segs)
        assert segs[0]['text'] == 'Hello world'
        assert segs[0]['words'][0]['word'] == 'Hello'
        assert segs[0]['words'][1]['word'] == 'world'


class TestCleanTranscriptSegments:
    def test_drops_empty_and_zero_length_artifacts(self):
        from ml_worker.pipeline import clean_transcript_segments
        segs = [
            {
                'start': 1.0,
                'end': 2.0,
                'text': ' Hello world ',
                'speaker': 'A',
                'words': [
                    {'word': ' Hello', 'start': 1.0, 'end': 1.4},
                    {'word': 'world ', 'start': 1.4, 'end': 2.0},
                ],
            },
            {
                'start': 2.0,
                'end': 2.0,
                'text': '',
                'speaker': '',
                'words': [],
            },
            {
                'start': 3.0,
                'end': 4.0,
                'text': 'Broken',
                'speaker': 'B',
                'words': [
                    {'word': 'Broken', 'start': 3.5, 'end': 3.5},
                ],
            },
        ]
        out = clean_transcript_segments(segs)
        assert out == [{
            'start': 1.0,
            'end': 2.0,
            'text': 'Hello world',
            'speaker': 'A',
            'words': [
                {'word': 'Hello', 'start': 1.0, 'end': 1.4},
                {'word': 'world', 'start': 1.4, 'end': 2.0},
            ],
        }]


# ---------------------------------------------------------------------------
# 1. assign_speakers_simple
# ---------------------------------------------------------------------------

class TestAssignSpeakersSimple:
    """Tests for ml_worker.diarize.assign_speakers_simple."""

    def test_basic_speaker_assignment(self):
        from ml_worker.diarize import assign_speakers_simple
        diarization = FakeDiarization([
            (0.0, 5.0, 'SPEAKER_00'),
            (5.0, 10.0, 'SPEAKER_01'),
        ])
        segments = [
            {'start': 0.5, 'end': 4.5, 'text': 'Hello there',
             'words': [{'start': 0.5, 'end': 2.0}, {'start': 2.0, 'end': 4.5}]},
            {'start': 5.5, 'end': 9.0, 'text': 'Hi back',
             'words': [{'start': 5.5, 'end': 7.0}, {'start': 7.0, 'end': 9.0}]},
        ]
        result = assign_speakers_simple(diarization, segments)
        assert result[0]['speaker'] == 'SPEAKER_00'
        assert result[1]['speaker'] == 'SPEAKER_01'

    def test_no_overlap_gives_empty_speaker(self):
        from ml_worker.diarize import assign_speakers_simple
        diarization = FakeDiarization([
            (0.0, 2.0, 'SPEAKER_00'),
        ])
        # Segment is outside diarization range
        segments = [
            {'start': 10.0, 'end': 15.0, 'text': 'Silence zone',
             'words': [{'start': 10.0, 'end': 15.0}]},
        ]
        result = assign_speakers_simple(diarization, segments)
        # No overlap → speaker key either absent or empty
        assert result[0].get('speaker', '') == ''

    def test_majority_vote_across_words(self):
        from ml_worker.diarize import assign_speakers_simple
        # Speaker A covers 0-6s, Speaker B covers 6-10s
        diarization = FakeDiarization([
            (0.0, 6.0, 'A'),
            (6.0, 10.0, 'B'),
        ])
        # Segment spans both, but more words fall in A's range
        segments = [
            {'start': 0.0, 'end': 10.0, 'text': 'Long sentence',
             'words': [
                 {'start': 0.0, 'end': 2.0},   # A
                 {'start': 2.0, 'end': 4.0},   # A
                 {'start': 4.0, 'end': 5.5},   # A
                 {'start': 7.0, 'end': 9.0},   # B
             ]},
        ]
        result = assign_speakers_simple(diarization, segments)
        assert result[0]['speaker'] == 'A'  # majority

    def test_three_speakers(self):
        from ml_worker.diarize import assign_speakers_simple
        diarization = FakeDiarization([
            (0.0, 3.0, 'S1'),
            (3.0, 6.0, 'S2'),
            (6.0, 9.0, 'S3'),
        ])
        segments = [
            {'start': 0.5, 'end': 2.5, 'text': 'One', 'words': [{'start': 0.5, 'end': 2.5}]},
            {'start': 3.5, 'end': 5.5, 'text': 'Two', 'words': [{'start': 3.5, 'end': 5.5}]},
            {'start': 6.5, 'end': 8.5, 'text': 'Three', 'words': [{'start': 6.5, 'end': 8.5}]},
        ]
        result = assign_speakers_simple(diarization, segments)
        assert result[0]['speaker'] == 'S1'
        assert result[1]['speaker'] == 'S2'
        assert result[2]['speaker'] == 'S3'

    def test_no_words_uses_segment_times(self):
        """Segments without words should use segment start/end for speaker lookup."""
        from ml_worker.diarize import assign_speakers_simple
        diarization = FakeDiarization([(0.0, 5.0, 'SOLO')])
        segments = [{'start': 1.0, 'end': 3.0, 'text': 'No words key'}]
        result = assign_speakers_simple(diarization, segments)
        assert result[0]['speaker'] == 'SOLO'

    def test_empty_diarization(self):
        from ml_worker.diarize import assign_speakers_simple
        diarization = FakeDiarization([])
        segments = [
            {'start': 0.0, 'end': 5.0, 'text': 'Hello',
             'words': [{'start': 0.0, 'end': 5.0}]},
        ]
        result = assign_speakers_simple(diarization, segments)
        # Empty diarization → speaker key either absent or empty
        assert result[0].get('speaker', '') == ''


# ---------------------------------------------------------------------------
# 2. map_speakers
# ---------------------------------------------------------------------------

class TestMapSpeakers:
    """Tests for ml_worker.pipeline.map_speakers."""

    def test_basic_renaming(self):
        from ml_worker.pipeline import map_speakers
        segments = [
            {'speaker': 'SPEAKER_00', 'text': 'Hi'},
            {'speaker': 'SPEAKER_01', 'text': 'Hello'},
            {'speaker': 'SPEAKER_00', 'text': 'Question'},
        ]
        mapping = map_speakers(segments)
        assert mapping['SPEAKER_00'] == 'Moderator'
        assert mapping['SPEAKER_01'] == 'Participant 1'
        # Verify segments were updated in-place
        assert segments[0]['speaker'] == 'Moderator'
        assert segments[1]['speaker'] == 'Participant 1'
        assert segments[2]['speaker'] == 'Moderator'

    def test_empty_segments(self):
        from ml_worker.pipeline import map_speakers
        mapping = map_speakers([])
        assert mapping == {}

    def test_no_speaker_key(self):
        from ml_worker.pipeline import map_speakers
        segments = [{'text': 'No speaker'}]
        mapping = map_speakers(segments)
        assert mapping == {}

    def test_many_speakers(self):
        """More than 6 speakers should get generic 'Speaker N' labels."""
        from ml_worker.pipeline import map_speakers
        segments = [{'speaker': f'SPEAKER_{i:02d}', 'text': f's{i}'} for i in range(8)]
        mapping = map_speakers(segments)
        assert mapping['SPEAKER_00'] == 'Moderator'
        assert mapping['SPEAKER_05'] == 'Participant 5'
        assert mapping['SPEAKER_06'] == 'Speaker 7'
        assert mapping['SPEAKER_07'] == 'Speaker 8'


# ---------------------------------------------------------------------------
# 3. Chunk decision constants (re-exported for the API layer and tests)
# ---------------------------------------------------------------------------

class TestChunkConstants:
    """Verify chunking threshold and overlap constants."""

    def test_chunk_threshold(self):
        from app.services.transcription import CHUNK_THRESHOLD_SEC
        assert CHUNK_THRESHOLD_SEC == 1800  # 30 min

    def test_chunk_size(self):
        from app.services.transcription import CHUNK_SIZE_SEC
        assert CHUNK_SIZE_SEC == 1800

    def test_chunk_overlap(self):
        from app.services.transcription import CHUNK_OVERLAP_SEC
        assert CHUNK_OVERLAP_SEC == 30


class TestAlignModelFailureCaching:
    """Ensure failed align loads are memoized to avoid repeated retries."""

    @staticmethod
    def _make_engine():
        from ml_worker.engines.whisperx_engine import WhisperXEngine
        return WhisperXEngine(env=None)

    def test_failed_align_model_is_not_retried(self):
        eng = self._make_engine()

        class FakeWhisperX:
            calls = 0

            @staticmethod
            def load_align_model(language_code, device, model_name=None):
                FakeWhisperX.calls += 1
                raise RuntimeError('align download failed')

        with pytest.raises(RuntimeError, match='align download failed'):
            eng._get_align_model(FakeWhisperX, 'ru', 'cpu')

        with pytest.raises(RuntimeError, match='previously failed'):
            eng._get_align_model(FakeWhisperX, 'ru', 'cpu')

        # First attempt exhausts both configured Russian candidates; second attempt is blocked by cache.
        assert FakeWhisperX.calls == 2

    def test_align_model_fallback_tries_next_candidate(self):
        eng = self._make_engine()

        class FakeWhisperX:
            calls = []

            @staticmethod
            def load_align_model(language_code, device, model_name=None):
                FakeWhisperX.calls.append(model_name)
                if model_name == 'anton-l/wav2vec2-large-xlsr-53-russian':
                    raise RuntimeError('first model failed')
                return object(), {'language': language_code, 'type': 'huggingface', 'dictionary': {}}

        model, metadata = eng._get_align_model(FakeWhisperX, 'ru', 'cpu')
        assert model is not None
        assert metadata['language'] == 'ru'
        assert FakeWhisperX.calls == [
            'anton-l/wav2vec2-large-xlsr-53-russian',
            'jonatasgrosman/wav2vec2-large-xlsr-53-russian',
        ]
        assert ('ru', 'cpu') not in eng._failed_align_keys


# ---------------------------------------------------------------------------
# 4. Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    """Tests for cancellation signal machinery."""

    def test_check_cancel_raises_when_set(self):
        from app.services.transcription import (
            TranscriptionCancelled,
            _check_cancel,
            _register_cancel,
            _unregister_cancel,
        )
        ev = _register_cancel(99990)
        ev.set()
        with pytest.raises(TranscriptionCancelled):
            _check_cancel(99990)
        _unregister_cancel(99990)

    def test_check_cancel_noop_when_not_set(self):
        from app.services.transcription import (
            _check_cancel,
            _register_cancel,
            _unregister_cancel,
        )
        _register_cancel(99991)
        # Should NOT raise
        _check_cancel(99991)
        _unregister_cancel(99991)

    def test_cancel_transcription_returns_true_when_active(self):
        from app.services.transcription import (
            _register_cancel,
            _unregister_cancel,
            cancel_transcription,
        )
        _register_cancel(99992)
        assert cancel_transcription(99992) is True
        _unregister_cancel(99992)

    def test_cancel_transcription_returns_false_when_inactive(self):
        from app.services.transcription import cancel_transcription
        assert cancel_transcription(99993) is False

    def test_cancel_transcription_wakes_language_waiter(self):
        import threading

        from app.services.transcription import (
            _language_waiters,
            _language_waiters_lock,
            cancel_transcription,
        )
        rid = 99994
        ev = threading.Event()
        with _language_waiters_lock:
            _language_waiters[rid] = {'event': ev, 'language': None, 'cancelled': False}
        assert cancel_transcription(rid) is True
        with _language_waiters_lock:
            st = _language_waiters.pop(rid, None)
        assert st['cancelled'] is True
        assert ev.is_set()


# ---------------------------------------------------------------------------
# 5. Requeue interrupted
# ---------------------------------------------------------------------------

class TestRequeueInterrupted:
    """Tests for requeue_interrupted() on startup."""

    def test_stuck_recordings_requeued(self, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording

            proj = Project(name='Requeue Test', folder_name='rq_test')
            db.session.add(proj)
            db.session.flush()

            # Create recordings stuck in different states
            stuck1 = Recording(project_id=proj.id, original_name='s1.mp3',
                               stored_name='s1.mp3', transcription_status='transcribing')
            stuck2 = Recording(project_id=proj.id, original_name='s2.mp3',
                               stored_name='s2.mp3', transcription_status='transcribing')
            stuck3 = Recording(project_id=proj.id, original_name='s3.mp3',
                               stored_name='s3.mp3', transcription_status='awaiting_language')
            ok = Recording(project_id=proj.id, original_name='ok.mp3',
                           stored_name='ok.mp3', transcription_status='transcribed')
            db.session.add_all([stuck1, stuck2, stuck3, ok])
            db.session.commit()
            s1id, s2id, s3id, okid = stuck1.id, stuck2.id, stuck3.id, ok.id

        with patch('app.services.transcription.enqueue') as mock_enqueue:
            from app.services.transcription import requeue_interrupted
            requeue_interrupted(app)

        # Stuck recordings should be set to pending and enqueued
        with app.app_context():
            from app.models.recording import Recording as Rec
            assert db.session.get(Rec, s1id).transcription_status == 'pending'
            assert db.session.get(Rec, s2id).transcription_status == 'pending'
            assert db.session.get(Rec, s3id).transcription_status == 'pending'
            assert db.session.get(Rec, okid).transcription_status == 'transcribed'
            assert mock_enqueue.call_count == 3

    def test_no_stuck_recordings(self, app):
        """No stuck recordings → no enqueue calls."""
        with patch('app.services.transcription.enqueue') as mock_enqueue:
            from app.services.transcription import requeue_interrupted
            requeue_interrupted(app)
            assert mock_enqueue.call_count == 0


# ---------------------------------------------------------------------------
# 6. Diarization MPS-to-CPU fallback
# ---------------------------------------------------------------------------

class TestDiarizeCPUFallback:
    """Tests for Diarizer.run MPS-to-CPU fallback retry (Mac native pyannote path)."""

    @needs_torch
    def test_mps_fallback_to_cpu(self):
        """If diarization fails on MPS, retry on CPU and assign speakers."""
        annotation = FakeDiarization([
            (0.0, 5.0, 'SPEAKER_00'),
            (5.0, 10.0, 'SPEAKER_01'),
        ])

        class FakePipeline:
            def __init__(self):
                self.model = MagicMock()
                self.to = MagicMock(return_value=self)
                self._n = 0

            def __call__(self, audio_input, **kwargs):
                self._n += 1
                if self._n == 1:
                    raise RuntimeError('MPS op not supported')
                return annotation

        d = _make_diarizer(engine_kind='mlx', device='mps', pipeline=FakePipeline())

        segments = [
            {'start': 0.5, 'end': 4.5, 'text': 'Hello',
             'words': [{'start': 0.5, 'end': 4.5}]},
            {'start': 5.5, 'end': 9.0, 'text': 'Hi',
             'words': [{'start': 5.5, 'end': 9.0}]},
        ]

        dummy_audio = {"waveform": __import__('torch').zeros(1, 16000), "sample_rate": 16000}
        result = d.run(dummy_audio, segments, 1)

        assert d.device == 'cpu'
        assert d.pipeline.to.call_count == 1
        assert result[0]['speaker'] == 'SPEAKER_00'
        assert result[1]['speaker'] == 'SPEAKER_01'

    @needs_torch
    def test_cpu_failure_no_retry(self):
        """If already on CPU, failure should propagate (no infinite retry)."""

        class FailPipe:
            model = MagicMock()

            def to(self, device):
                return self

            def __call__(self, audio_input, **kwargs):
                raise RuntimeError('Diarization broken')

        d = _make_diarizer(engine_kind='mlx', device='cpu', pipeline=FailPipe())

        dummy_audio = {"waveform": __import__('torch').zeros(1, 16000), "sample_rate": 16000}
        with pytest.raises(RuntimeError, match='Diarization broken'):
            d.run(dummy_audio, [], 1)

    @needs_torch
    def test_mps_success_no_fallback(self):
        """If MPS works, no fallback to CPU should happen."""
        annotation = FakeDiarization([
            (0.0, 5.0, 'SPEAKER_00'),
        ])

        class OkPipe:
            def __init__(self):
                self.model = MagicMock()
                self.to = MagicMock(return_value=self)

            def __call__(self, audio_input, **kwargs):
                return annotation

        d = _make_diarizer(engine_kind='mlx', device='mps', pipeline=OkPipe())

        segments = [
            {'start': 0.5, 'end': 4.5, 'text': 'Hello',
             'words': [{'start': 0.5, 'end': 4.5}]},
        ]

        dummy_audio = {"waveform": __import__('torch').zeros(1, 16000), "sample_rate": 16000}
        # Device is faked as 'mps' on a non-Mac host; stub the MPS-only cache clear.
        with patch('torch.mps.empty_cache'):
            result = d.run(dummy_audio, segments, 1)

        assert d.device == 'mps'
        d.pipeline.to.assert_not_called()
        assert result[0]['speaker'] == 'SPEAKER_00'


class TestStubTorchcodec:
    """Tests for compat.stub_torchcodec sys.modules injection."""

    def _clean(self):
        import sys
        for key in list(sys.modules):
            if key == 'torchcodec' or key.startswith('torchcodec.'):
                del sys.modules[key]

    def test_stub_replaces_real_module(self):
        from ml_worker import compat
        self._clean()
        compat.stub_torchcodec()
        import sys
        assert getattr(sys.modules.get('torchcodec'), '_pine_stub', False)
        assert getattr(sys.modules.get('torchcodec.decoders'), '_pine_stub', False)
        assert getattr(sys.modules.get('torchcodec._core.ops'), '_pine_stub', False)
        self._clean()

    def test_stub_allows_importlib_find_spec(self):
        """Python 3.13+ raises ValueError if torchcodec is in sys.modules but __spec__ is None
        (transformers checks this at import time)."""
        import importlib.util

        from ml_worker import compat

        self._clean()
        compat.stub_torchcodec()
        try:
            spec = importlib.util.find_spec('torchcodec')
        finally:
            self._clean()
        assert spec is not None
        assert spec.name == 'torchcodec'

    def test_stub_idempotent(self):
        from ml_worker import compat
        self._clean()
        compat.stub_torchcodec()
        compat.stub_torchcodec()  # second call — no error
        import sys
        assert getattr(sys.modules.get('torchcodec'), '_pine_stub', False)
        self._clean()

    def test_stub_clears_broken_partial_import(self):
        import sys
        import types

        from ml_worker import compat
        self._clean()
        broken = types.ModuleType('torchcodec')
        sys.modules['torchcodec'] = broken
        compat.stub_torchcodec()
        assert getattr(sys.modules.get('torchcodec'), '_pine_stub', False), \
            "stub should have replaced the broken module"

    def test_stub_skips_when_real_works(self):
        """When real torchcodec loads AND decodes successfully, the shim should NOT be installed."""
        import sys
        import types

        from ml_worker import compat
        self._clean()

        # Plant a fake "real" torchcodec whose AudioDecoder accepts a file path
        # (the smoke test in stub_torchcodec instantiates AudioDecoder)
        fake = types.ModuleType('torchcodec')
        fake_dec = types.ModuleType('torchcodec.decoders')
        fake_dec.AudioDecoder = type('AudioDecoder', (), {
            '__init__': lambda self, *a, **kw: None,
        })
        fake.decoders = fake_dec
        sys.modules['torchcodec'] = fake
        sys.modules['torchcodec.decoders'] = fake_dec

        compat.stub_torchcodec()

        assert not getattr(sys.modules.get('torchcodec'), '_pine_stub', False), \
            "real torchcodec should not be replaced by the shim"
        self._clean()

    @needs_torch
    def test_audio_decoder_loads_wav(self, tmp_path):
        """The shim AudioDecoder should load audio via ffmpeg."""
        import struct
        import wave

        from ml_worker import compat
        self._clean()
        compat.stub_torchcodec()

        # Create a 1-second 16kHz mono WAV with the stdlib wave module
        wav_path = str(tmp_path / 'test.wav')
        n_frames = 16000
        with wave.open(wav_path, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack(f'<{n_frames}h', *([0] * n_frames)))

        try:
            from torchcodec.decoders import AudioDecoder
            dec = AudioDecoder(wav_path)
        except (RuntimeError, FileNotFoundError, OSError):
            pytest.skip('ffmpeg not available on this platform')

        # metadata
        assert dec.metadata.sample_rate == 16000
        assert dec.metadata.num_channels == 1
        assert dec.metadata.num_frames == n_frames

        # get_all_samples
        samples = dec.get_all_samples()
        assert samples.data.shape[0] == 1       # mono
        assert samples.data.shape[1] == n_frames
        assert samples.sample_rate == 16000

        # get_samples_played_in_range (first 0.5s)
        chunk = dec.get_samples_played_in_range(0.0, 0.5)
        assert chunk.data.shape == (1, 8000)
        assert chunk.sample_rate == 16000
        self._clean()


# ---------------------------------------------------------------------------
# DiarizeOutput unwrap (pyannote 4.x)
# ---------------------------------------------------------------------------

class TestDiarizeOutputUnwrap:
    """Ensure the native Diarizer.run unwraps DiarizeOutput-style pyannote results."""

    @needs_numpy
    def test_unwraps_diarize_output(self):
        """Pipeline returning DiarizeOutput should feed assign_speakers_simple."""
        from dataclasses import dataclass

        annotation = FakeDiarization([
            (0.0, 5.0, 'SPEAKER_00'),
            (5.0, 10.0, 'SPEAKER_01'),
        ])

        @dataclass
        class DiarizeOutput:
            speaker_diarization: object
            exclusive_speaker_diarization: object = None
            speaker_embeddings: object = None

        class Pipe:
            model = MagicMock()

            def __call__(self, x, **kwargs):
                return DiarizeOutput(speaker_diarization=annotation)

        d = _make_diarizer(engine_kind='mlx', device='cpu', pipeline=Pipe())

        segments = [
            {'start': 1.0, 'end': 4.0, 'text': 'Hello',
             'words': [{'start': 1.0, 'end': 2.0, 'word': 'Hello'}]},
            {'start': 6.0, 'end': 9.0, 'text': 'World',
             'words': [{'start': 6.0, 'end': 7.0, 'word': 'World'}]},
        ]

        import numpy as np
        diarize_input = np.zeros(16000 * 10, dtype=np.float32)

        result = d.run(diarize_input, segments, 'test-rec')
        assert result[0]['speaker'] == 'SPEAKER_00'
        assert result[1]['speaker'] == 'SPEAKER_01'


class TestSpeakerCountThreading:
    """Per-recording speaker count is forwarded into the diarization pipeline."""

    def test_speaker_kwargs_exact_and_fallback(self):
        from ml_worker.diarize import speaker_kwargs
        assert speaker_kwargs(2) == {'num_speakers': 2}
        assert speaker_kwargs(1) == {'num_speakers': 1}
        assert speaker_kwargs(None) == {'min_speakers': 2, 'max_speakers': 4}
        assert speaker_kwargs(0) == {'min_speakers': 2, 'max_speakers': 4}

    def _capture_pipe_diarizer(self, captured):
        annotation = FakeDiarization([(0.0, 5.0, 'SPEAKER_00')])

        class CapturePipe:
            model = MagicMock()

            def __call__(self, audio_input, **kwargs):
                captured.update(kwargs)
                return annotation

        return _make_diarizer(engine_kind='mlx', device='cpu', pipeline=CapturePipe())

    @needs_torch
    def test_num_speakers_passed_to_pipeline(self):
        captured = {}
        d = self._capture_pipe_diarizer(captured)
        segments = [{'start': 0.5, 'end': 4.5, 'text': 'Hi',
                     'words': [{'start': 0.5, 'end': 4.5, 'word': 'Hi'}]}]
        dummy_audio = {"waveform": __import__('torch').zeros(1, 16000),
                       "sample_rate": 16000}
        d.run(dummy_audio, segments, 1, num_speakers=2)
        assert captured == {'num_speakers': 2}

    @needs_torch
    def test_no_count_falls_back_to_min_max(self):
        captured = {}
        d = self._capture_pipe_diarizer(captured)
        segments = [{'start': 0.5, 'end': 4.5, 'text': 'Hi',
                     'words': [{'start': 0.5, 'end': 4.5, 'word': 'Hi'}]}]
        dummy_audio = {"waveform": __import__('torch').zeros(1, 16000),
                       "sample_rate": 16000}
        d.run(dummy_audio, segments, 1)
        assert 'num_speakers' not in captured
        assert 'min_speakers' in captured and 'max_speakers' in captured


# ---------------------------------------------------------------------------
# Mac pipeline: MLX cache clearing
# ---------------------------------------------------------------------------

class TestClearMlxCache:
    """_clear_mlx_cache should be a safe no-op when mlx is not installed."""

    def test_no_mlx_installed(self):
        from app.services.transcription import _clear_mlx_cache
        # Should not raise even when mlx is not installed (Windows/Linux)
        _clear_mlx_cache()

    def test_mlx_cache_called(self):
        """When mlx is available, mx.clear_cache() should be called."""
        from app.services.transcription import _clear_mlx_cache
        mock_mx = MagicMock()
        with patch.dict('sys.modules', {'mlx': MagicMock(), 'mlx.core': mock_mx}):
            _clear_mlx_cache()
            # If mlx.core was importable, clear_cache would be called


# ---------------------------------------------------------------------------
# Mac pipeline: model choice
# ---------------------------------------------------------------------------

class TestMacModelChoice:
    """get_models_for_setup should select the quality model on Mac."""

    @patch('app.services.model_manager.IS_MAC', True)
    def test_model_on_mac(self):
        from app.services.model_manager import get_models_for_setup
        ids = get_models_for_setup(modules=[])
        assert 'mlx-whisper-large-v3' in ids

    @patch('app.services.model_manager.IS_MAC', True)
    def test_normalize_rewrites_whisperx_to_mlx_on_mac(self):
        """WhisperX is never installed on Mac, so the ID must not survive."""
        from app.services.model_manager import normalize_stt_model_id
        assert normalize_stt_model_id('whisperx-large-v3') == 'mlx-whisper-large-v3'
        assert normalize_stt_model_id('mlx-whisper-large-v3') == 'mlx-whisper-large-v3'

    @patch('app.services.model_manager.IS_MAC', False)
    def test_normalize_rewrites_mlx_to_whisperx_off_mac(self):
        from app.services.model_manager import normalize_stt_model_id
        assert normalize_stt_model_id('mlx-whisper-large-v3') == 'whisperx-large-v3'
        assert normalize_stt_model_id('whisperx-large-v3') == 'whisperx-large-v3'

    @patch('app.services.pip_installer.IS_MAC', True)
    @patch('app.services.pip_installer._is_package_installed')
    def test_check_ml_deps_never_asks_for_whisperx_on_mac(self, mock_is_installed):
        from app.services.pip_installer import check_ml_deps
        mock_is_installed.return_value = False
        deps = check_ml_deps()
        assert 'whisperx' not in deps
        assert 'mlx-whisper' in deps


class TestEngineSelection:
    def test_whisperx_large_v3_reuses_detected_cuda_defaults(self):
        from ml_worker.engines import select_engine_config

        engine, device, compute_type = select_engine_config(
            'whisperx-large-v3',
            prefer_mps=False,
            detected_device='cuda',
            detected_compute='float16',
        )

        assert engine == 'whisperx'
        assert device == 'cuda'
        assert compute_type == 'float16'

    def test_mlx_model_maps_to_mlx_engine(self):
        from ml_worker.engines import select_engine_config

        engine, device, compute_type = select_engine_config(
            'mlx-whisper-large-v3', prefer_mps=True)

        assert engine == 'mlx'
        assert device == 'cpu'
        assert compute_type is None

    def test_whisperx_on_mac_prefers_cpu_int8(self):
        from ml_worker.engines import select_engine_config

        engine, device, compute_type = select_engine_config(
            'whisperx-large-v3', prefer_mps=True,
            detected_device='cuda', detected_compute='float16')

        assert engine == 'whisperx'
        assert device == 'cpu'
        assert compute_type == 'int8'

    def test_parakeet_stays_on_cpu_even_with_a_card(self):
        """The card is for diarization; two CUDA runtimes in one process is the bug."""
        from ml_worker.engines import select_engine_config

        engine, device, compute_type = select_engine_config(
            'parakeet-tdt-0.6b-v3-onnx', prefer_mps=False,
            detected_device='cuda', detected_compute='float16')

        assert engine == 'onnx'
        assert device == 'cpu'
        assert compute_type == 'int8'

    def test_parakeet_capabilities_skip_alignment(self):
        from ml_worker.engines import engine_capabilities

        caps = engine_capabilities('parakeet-tdt-0.6b-v3-onnx')
        assert caps.word_timestamps is True     # TDT times its own tokens
        assert caps.diarization == 'external'   # shared pyannote stage


class TestParakeetSelectable:
    """Parakeet is offered on every platform, unlike the per-platform Whispers."""

    @patch('app.services.model_manager.IS_MAC', True)
    def test_survives_normalize_on_mac(self):
        from app.services.model_manager import normalize_stt_model_id
        assert normalize_stt_model_id('parakeet-tdt-0.6b-v3-onnx') == 'parakeet-tdt-0.6b-v3-onnx'

    @patch('app.services.model_manager.IS_MAC', False)
    def test_survives_normalize_off_mac(self):
        from app.services.model_manager import normalize_stt_model_id
        assert normalize_stt_model_id('parakeet-tdt-0.6b-v3-onnx') == 'parakeet-tdt-0.6b-v3-onnx'

    def test_unknown_model_still_falls_back(self):
        from app.services.model_manager import get_default_stt_model, normalize_stt_model_id
        assert normalize_stt_model_id('parakeet-something-else') == get_default_stt_model()

    @patch('app.services.pip_installer._is_package_installed', return_value=False)
    def test_deps_ask_for_onnx_not_whisperx(self, mock_installed):
        from app.services.pip_installer import check_ml_deps

        deps = check_ml_deps('parakeet-tdt-0.6b-v3-onnx')
        assert 'onnx-asr' in deps
        assert 'onnxruntime' in deps
        assert 'whisperx' not in deps
        # Diarization still rides on pyannote whichever engine transcribes.
        assert 'pyannote-audio' in deps


class TestParakeetSegmentMapping:
    """onnx-asr hands back per-token stamps; the UI needs words."""

    def test_relative_stamps_are_rebased_onto_the_recording(self):
        from ml_worker.engines.onnx_engine import absolute_stamps

        assert absolute_stamps([0.0, 0.4], 10.0, 12.0) == [10.0, 10.4]

    def test_absolute_stamps_are_left_alone(self):
        from ml_worker.engines.onnx_engine import absolute_stamps

        assert absolute_stamps([10.0, 10.4], 10.0, 12.0) == [10.0, 10.4]

    def test_tokens_group_into_words_with_times(self):
        from ml_worker.engines.onnx_engine import tokens_to_words

        words = tokens_to_words(
            ['▁any', 'way', '▁we', '▁talked'], [1.0, 1.2, 1.5, 1.8], seg_end=2.4)

        assert [w['word'] for w in words] == ['anyway', 'we', 'talked']
        assert words[0]['start'] == 1.0
        # A word ends where the next one starts; the last one ends with the segment.
        assert words[0]['end'] == 1.5
        assert words[-1]['end'] == 2.4

    def test_empty_tokens_produce_no_words(self):
        from ml_worker.engines.onnx_engine import tokens_to_words

        assert tokens_to_words([], [], seg_end=1.0) == []


# ---------------------------------------------------------------------------
# Mac pipeline: _write_wav helper
# ---------------------------------------------------------------------------

class TestWriteWav:
    """Test the _write_wav helper produces valid WAV files."""

    @needs_numpy
    def test_write_and_read_wav(self, tmp_path):
        import numpy as np

        from app.services.transcription import _write_wav
        audio = np.sin(np.linspace(0, 2 * np.pi * 440, 16000, dtype=np.float32))
        wav_path = str(tmp_path / 'test.wav')
        _write_wav(wav_path, audio)

        # Verify it's a valid WAV: check RIFF header
        with open(wav_path, 'rb') as f:
            header = f.read(4)
            assert header == b'RIFF'
            f.seek(8)
            assert f.read(4) == b'WAVE'


class TestModelPreflight:
    """A model that was never downloaded is a setup problem, caught before the job."""

    def _ready(self, app, model_id, models_path):
        from app.extensions import db
        from app.models.ml_model import MLModel
        os.makedirs(os.path.join(models_path, model_id), exist_ok=True)
        with app.app_context():
            row = db.session.get(MLModel, model_id)
            row.status = 'ready'
            db.session.commit()

    def test_missing_model_refuses_the_job(self, app):
        from app.services.transcription.job_runner import _preflight_stt_model

        with pytest.raises(RuntimeError) as exc:
            _preflight_stt_model(app)
        assert 'not installed' in str(exc.value)

    def test_installed_model_passes(self, app, temp_dir):
        from app.services.model_manager import get_default_stt_model
        from app.services.transcription.job_runner import _preflight_stt_model

        models_path = os.path.join(temp_dir, 'models')
        self._ready(app, get_default_stt_model(), models_path)

        assert _preflight_stt_model(app) == get_default_stt_model()

    def test_parakeet_without_its_vad_refuses(self, app, temp_dir):
        from app.extensions import db
        from app.models.setting import Setting
        from app.services.transcription.job_runner import _preflight_stt_model

        models_path = os.path.join(temp_dir, 'models')
        self._ready(app, 'parakeet-tdt-0.6b-v3-onnx', models_path)
        with app.app_context():
            Setting.set('stt_model_id', 'parakeet-tdt-0.6b-v3-onnx')
            db.session.commit()

        with pytest.raises(RuntimeError) as exc:
            _preflight_stt_model(app)
        assert 'silero-vad-onnx' in str(exc.value)
