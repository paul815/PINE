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

    def test_keeps_a_hyphenated_word_whole(self):
        """Whisper splits "как-то" into two tokens and marks the join by *not*
        putting a space on the second. Joining on spaces printed "как -то" 89
        times in one 38-minute interview."""
        from ml_worker.engines.mlx_engine import synchronize_segments_for_ui
        segs = [{
            'words': [
                {'word': ' во', 'start': 0.0, 'end': 0.3},
                {'word': '-первых', 'start': 0.3, 'end': 0.8},
                {'word': ' как', 'start': 0.9, 'end': 1.2},
                {'word': '-то', 'start': 1.2, 'end': 1.5},
            ],
        }]
        synchronize_segments_for_ui(segs)
        assert segs[0]['text'] == 'во-первых как-то'
        # The UI locates each word inside the text with indexOf, so every word it
        # is handed still has to be findable there.
        for w in segs[0]['words']:
            assert w['word'] in segs[0]['text']

    def test_falls_back_to_spaces_for_words_that_arrive_trimmed(self):
        """Nothing left to read the spacing from, so the old join is the only
        sane answer — which is also what makes running this twice a no-op."""
        from ml_worker.engines.mlx_engine import synchronize_segments_for_ui
        segs = [{'words': [{'word': 'Hello'}, {'word': 'world'}]}]
        synchronize_segments_for_ui(segs)
        assert segs[0]['text'] == 'Hello world'

    def test_is_idempotent(self):
        from ml_worker.engines.mlx_engine import synchronize_segments_for_ui
        segs = [{'words': [{'word': ' как'}, {'word': '-то'}, {'word': ' вот'}]}]
        synchronize_segments_for_ui(segs)
        once = segs[0]['text']
        synchronize_segments_for_ui(segs)
        assert segs[0]['text'] == once == 'как-то вот'


# ---------------------------------------------------------------------------
# 0b. Keeping non-speech away from mlx-whisper (Mac-native; whisperx has a VAD)
# ---------------------------------------------------------------------------

def _tone(secs, freq=425.0, sample_rate=16000, on=None, off=None):
    """A dial tone, optionally rung ``on`` seconds every ``on + off``."""
    import numpy as np
    t = np.arange(int(secs * sample_rate)) / sample_rate
    wave = 0.3 * np.sin(2 * np.pi * freq * t)
    if on is not None:
        wave = wave * ((t % (on + off)) < on)
    return wave.astype(np.float32)


def _voice(secs, sample_rate=16000, seed=0):
    """A 120 Hz harmonic stack with vibrato, breath noise and pauses.

    Not speech, but it has what this code reads speech by: a stack of harmonics
    where a tone has one line, and loud/quiet structure for the energy gate.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    t = np.arange(int(secs * sample_rate)) / sample_rate
    f0 = 120 + 8 * np.sin(2 * np.pi * 3 * t)
    phase = 2 * np.pi * np.cumsum(f0) / sample_rate
    wave = sum((1.0 / h) * np.sin(h * phase) for h in range(1, 40))
    wave = wave * 0.2 * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    wave = wave * (np.sin(2 * np.pi * t / 7.0) > -0.7)      # pauses between turns
    return (wave + rng.normal(0, 0.002, t.size)).astype(np.float32)


def _telephone(wave, sample_rate=16000):
    """Band-limit to 300-3400 Hz, as a phone line does before Whisper ever sees it."""
    import numpy as np
    spectrum = np.fft.rfft(wave)
    freqs = np.fft.rfftfreq(wave.size, 1.0 / sample_rate)
    spectrum[(freqs < 300) | (freqs > 3400)] = 0
    return np.fft.irfft(spectrum, wave.size).astype(np.float32)


class TestSpectralFlatness:
    def test_a_tone_and_a_voice_land_orders_of_magnitude_apart(self):
        from ml_worker.constants import MLX_VAD_MIN_FLATNESS
        from ml_worker.engines.mlx_engine import spectral_flatness
        assert spectral_flatness(_tone(5)) < MLX_VAD_MIN_FLATNESS / 100
        assert spectral_flatness(_voice(5)) > MLX_VAD_MIN_FLATNESS * 10

    def test_a_phone_line_does_not_collapse_the_measure(self):
        """The reason flatness is read inside a band. Across the whole spectrum
        the empty bins above 3.4 kHz dominate the geometric mean and drag a voice
        down past any threshold that would separate it from a tone."""
        from ml_worker.constants import MLX_VAD_MIN_FLATNESS
        from ml_worker.engines.mlx_engine import spectral_flatness
        assert spectral_flatness(_telephone(_tone(5))) < MLX_VAD_MIN_FLATNESS
        assert spectral_flatness(_telephone(_voice(5))) > MLX_VAD_MIN_FLATNESS * 10

    def test_too_little_audio_is_not_a_tone(self):
        import numpy as np
        from ml_worker.engines.mlx_engine import spectral_flatness
        assert spectral_flatness(np.zeros(64, dtype=np.float32)) == 1.0


class TestGateSpeechForMlx:
    def test_ringback_is_cut_off_the_front_and_speech_survives(self):
        """The failure this exists for: a call that opens on ringback, which the
        energy gate passes as 'loud' and Whisper writes down as 'Звук колокола.'"""
        import numpy as np
        from ml_worker.engines.mlx_engine import gate_speech
        audio = np.concatenate([
            np.zeros(5 * 16000, dtype=np.float32),
            _tone(20, on=1.0, off=4.0),
            np.zeros(5 * 16000, dtype=np.float32),
            _voice(60),
        ])
        gated, splices = gate_speech(audio)
        assert splices is not None, 'expected the ringback to be cut'
        # Everything kept comes from after the tone ends at 30s.
        assert min(original for _, _, original in splices) >= 25.0
        assert len(gated) < len(audio)

    def test_audio_with_nothing_to_cut_is_passed_through_untouched(self):
        from ml_worker.engines.mlx_engine import gate_speech
        audio = _voice(60)
        gated, splices = gate_speech(audio)
        assert splices is None
        assert gated is audio

    def test_a_gate_that_would_swallow_the_recording_is_ignored(self):
        """The dead-man's switch. Whatever the thresholds decide, dropping half
        the recording is a misread, and the old behaviour is the safe one."""
        import numpy as np
        from ml_worker.engines.mlx_engine import gate_speech
        # A tone throughout: read as speech by energy, as a tone by flatness,
        # leaving nothing at all — so the audio has to go through as it is.
        audio = np.concatenate([np.zeros(2 * 16000, dtype=np.float32), _tone(40)])
        gated, splices = gate_speech(audio)
        assert splices is None
        assert gated is audio

    def test_silence_is_not_mistaken_for_a_recording_to_gate(self):
        import numpy as np
        from ml_worker.engines.mlx_engine import gate_speech
        audio = np.zeros(30 * 16000, dtype=np.float32)
        gated, splices = gate_speech(audio)
        assert splices is None
        assert gated is audio


class _FakeMlx:
    """Stands in for mlx_whisper: records each call's arguments, replies to order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def transcribe(self, path, **kw):
        self.calls.append(kw)
        if self.replies:
            return self.replies.pop(0)
        return {'segments': [], 'language': 'ru'}


def _said(text, start=0.0, end=2.0):
    return {'start': start, 'end': end, 'text': text}


class TestPlanWindows:
    """Where the prompt chain is cut. A boundary in silence costs nothing; one
    through a word costs the word, so silence is looked for first."""

    def test_a_short_take_is_one_window(self):
        from ml_worker.engines.mlx_engine import plan_windows
        assert plan_windows(_voice(30), max_sec=120.0) == [(0.0, 30.0)]

    def test_windows_tile_the_take_without_losing_audio(self):
        import numpy as np
        from ml_worker.engines.mlx_engine import plan_windows
        audio = np.concatenate([_voice(100),
                                np.zeros(4 * 16000, dtype=np.float32),
                                _voice(100)])
        windows = plan_windows(audio, max_sec=60.0)

        assert windows[0][0] == 0.0
        assert windows[-1][1] == pytest.approx(len(audio) / 16000)
        for before, after in zip(windows, windows[1:], strict=False):
            assert before[1] == after[0], 'a window boundary dropped audio'

    def test_the_cut_lands_in_the_silence_between_two_turns(self):
        import numpy as np
        from ml_worker.engines.mlx_engine import plan_windows
        audio = np.concatenate([_voice(50),
                                np.zeros(6 * 16000, dtype=np.float32),
                                _voice(50)])
        windows = plan_windows(audio, max_sec=70.0)

        assert len(windows) == 2
        cut = windows[0][1]
        assert 50.0 <= cut <= 56.0, f'cut at {cut:.1f}s is not in the silence'

    def test_a_take_with_no_gap_is_cut_on_length(self):
        from ml_worker.engines.mlx_engine import plan_windows
        windows = plan_windows(_tone(200), max_sec=60.0)
        assert [round(start) for start, _ in windows] == [0, 60, 120, 180]


class TestRunawayDetector:
    """What tells a looping window from a person repeating themselves."""

    def test_a_line_repeated_past_the_limit_is_a_runaway(self):
        from ml_worker.engines.mlx_engine import is_runaway
        assert is_runaway([_said('Звук колокола.')] * 4)

    def test_someone_saying_da_three_times_is_not(self):
        from ml_worker.engines.mlx_engine import is_runaway
        assert not is_runaway([_said('Да.'), _said('да'), _said('Да!')])

    def test_a_window_that_reads_its_prompt_back_is_a_runaway(self):
        """The first step of the failure: the window stops hearing the audio and
        starts copying what it was handed."""
        from ml_worker.engines.mlx_engine import is_runaway
        assert is_runaway([_said('Звук колокола.'), _said('Звук колокола')],
                          prompt='…а потом звук колокола.')

    def test_one_short_answer_inside_a_long_prompt_is_not(self):
        from ml_worker.engines.mlx_engine import is_runaway
        assert not is_runaway([_said('Да.')],
                              prompt='Да, мы понимаем, что люди адаптируются.')

    def test_the_tail_handed_on_is_cut_on_a_word(self):
        from ml_worker.engines.mlx_engine import prompt_tail
        assert prompt_tail('раз два три четыре пять', limit=10) == 'пять'
        assert prompt_tail('раз два', limit=10) == 'раз два'


class TestDecodeWindows:
    """The chain PINE holds in place of the one mlx-whisper would hold itself."""

    def _engine(self):
        from ml_worker.engines import mlx_engine
        engine = mlx_engine.MlxWhisperEngine(env=None)
        engine._model_path = 'fake-model'
        return engine

    def _run(self, monkeypatch, replies, windows):
        import sys

        import numpy as np
        from ml_worker.engines import mlx_engine
        fake = _FakeMlx(replies)
        monkeypatch.setitem(sys.modules, 'mlx_whisper', fake)
        monkeypatch.setattr(mlx_engine, 'plan_windows',
                            lambda samples, **kw: windows)
        samples = np.zeros(int(windows[-1][1] * 16000), dtype=np.float32)
        segments, lang = self._engine()._decode_windows(samples)
        return fake, segments, lang

    def test_each_window_is_seeded_with_the_last_one(self, monkeypatch):
        fake, segments, lang = self._run(
            monkeypatch,
            [{'segments': [_said(' Мы ушли в создание нового сайта.')], 'language': 'ru'},
             {'segments': [_said(' Блокировок там нет.')], 'language': 'ru'}],
            [(0.0, 1.0), (1.0, 2.0)])

        assert lang == 'ru'
        assert fake.calls[0].get('initial_prompt') is None, 'nothing to carry yet'
        assert fake.calls[0]['condition_on_previous_text'] is True
        assert fake.calls[1]['initial_prompt'] == 'Мы ушли в создание нового сайта.'
        # Settled on the first window rather than re-detected on every one.
        assert fake.calls[1]['language'] == 'ru'
        assert [seg['start'] for seg in segments] == [0.0, 1.0]

    def test_a_looping_window_is_decoded_again_with_nothing_carried_in(self, monkeypatch):
        fake, segments, _ = self._run(
            monkeypatch,
            [{'segments': [_said(' Звук колокола.')], 'language': 'ru'},
             {'segments': [_said('Звук колокола.')] * 5, 'language': 'ru'},
             {'segments': [_said(' Люди адаптируются.')], 'language': 'ru'},
             {'segments': [_said(' Сайт работает без VPN.')], 'language': 'ru'}],
            [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)])

        assert len(fake.calls) == 4, 'expected the middle window to be decoded twice'
        assert fake.calls[2].get('initial_prompt') is None
        assert fake.calls[2]['condition_on_previous_text'] is False
        # And the text that set the loop off is not handed to the next window.
        assert fake.calls[3].get('initial_prompt') is None
        assert [seg['text'].strip() for seg in segments] == [
            'Звук колокола.', 'Люди адаптируются.', 'Сайт работает без VPN.']

    def test_a_window_that_comes_back_empty_keeps_the_prompt(self, monkeypatch):
        fake, _, _ = self._run(
            monkeypatch,
            [{'segments': [_said(' Люди адаптируются.')], 'language': 'ru'},
             {'segments': [], 'language': 'ru'},
             {'segments': [_said(' Сайт работает.')], 'language': 'ru'}],
            [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)])

        assert fake.calls[2]['initial_prompt'] == 'Люди адаптируются.'


class TestMlxLanguageProbeWindow:
    """Where the language probe listens. Reading the first 30 s of a phone call
    means reading ringback, which is how a Russian interview came back as English
    at p=0.29 — and stopped the pipeline to ask the user about it."""

    def _engine_over(self, monkeypatch, audio):
        from ml_worker.engines import mlx_engine
        monkeypatch.setattr(mlx_engine, 'load_audio_range',
                            lambda path, offset, duration: audio)
        return mlx_engine.MlxWhisperEngine(env=None)

    def test_skips_the_ringback_and_listens_to_the_speech(self, monkeypatch):
        import numpy as np
        from ml_worker.constants import MLX_VAD_MIN_FLATNESS
        from ml_worker.engines.mlx_engine import spectral_flatness
        audio = np.concatenate([_tone(30, on=1.0, off=4.0), _voice(60)])
        engine = self._engine_over(monkeypatch, audio)

        window = engine.probe_window('call.m4a', 30.0, total_duration=90.0)

        assert len(window) == 30 * 16000
        assert spectral_flatness(window) > MLX_VAD_MIN_FLATNESS * 10, \
            'the probe is still listening to the tone'

    def test_falls_back_to_the_head_when_it_finds_no_speech(self, monkeypatch):
        import numpy as np
        audio = np.zeros(90 * 16000, dtype=np.float32)
        engine = self._engine_over(monkeypatch, audio)

        window = engine.probe_window('silence.wav', 30.0, total_duration=90.0)

        assert len(window) == 30 * 16000

    def test_speech_near_the_end_still_gets_a_full_window(self, monkeypatch):
        """Pulled back from the end rather than handed the two seconds left."""
        import numpy as np
        audio = np.concatenate([_tone(70, on=1.0, off=4.0), _voice(20)])
        engine = self._engine_over(monkeypatch, audio)

        window = engine.probe_window('call.m4a', 30.0, total_duration=90.0)

        assert len(window) == 30 * 16000


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

    @staticmethod
    def _seg(text, start, end, speaker='Participant 1'):
        words = text.split()
        step = (end - start) / len(words)
        return {
            'start': start, 'end': end, 'text': text, 'speaker': speaker,
            'words': [{'word': w, 'start': start + i * step,
                       'end': start + (i + 1) * step}
                      for i, w in enumerate(words)],
        }

    def test_drops_the_credits_whisper_wrote_over_silence(self):
        # The three found in one real whisperx interview.
        from ml_worker.pipeline import clean_transcript_segments
        segs = [
            self._seg('Продолжение следует.', 308.962, 329.515),
            self._seg('Продолжение следует...', 794.759, 802.015),
            self._seg('Субтитры создавал DimaTorzok', 1988.092, 1989.307,
                      speaker=''),
        ]
        assert clean_transcript_segments(segs) == []

    def test_a_credit_is_dropped_whoever_it_is_attributed_to(self):
        from ml_worker.pipeline import clean_transcript_segments
        segs = [self._seg('Редактор субтитров А.Семкин', 10.0, 11.0)]
        assert clean_transcript_segments(segs) == []

    def test_keeps_a_common_phrase_said_at_normal_pace(self):
        from ml_worker.pipeline import clean_transcript_segments
        segs = [self._seg('Продолжение следует.', 10.0, 11.0)]
        assert len(clean_transcript_segments(segs)) == 1

    def test_keeps_a_phrase_quoted_inside_a_longer_answer(self):
        from ml_worker.pipeline import clean_transcript_segments
        segs = [
            self._seg('А в конце было написано субтитры создавал кто-то', 10.0, 14.0),
            self._seg('Ну и продолжение следует, как говорится', 20.0, 50.0),
        ]
        assert len(clean_transcript_segments(segs)) == 2

    def test_keeps_ordinary_speech_with_no_speaker(self):
        from ml_worker.pipeline import clean_transcript_segments
        segs = [self._seg('Да, я согласен.', 10.0, 11.0, speaker='')]
        assert len(clean_transcript_segments(segs)) == 1


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

    def test_stub_exposes_the_name_pyannote_imports(self):
        """pyannote.audio.core.io does ``from torchcodec import AudioSamples``.

        When that fails it warns that decoding is broken and falls back — which
        is what our own shim used to look like from the outside.
        """
        from ml_worker import compat
        self._clean()
        compat.stub_torchcodec()
        try:
            from torchcodec import AudioSamples
            from torchcodec.decoders import AudioDecoder
            fields = AudioSamples._fields
        finally:
            self._clean()
        assert fields == ('data', 'pts_seconds', 'sample_rate')
        assert AudioDecoder is not None

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

    @needs_torch
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

    def test_unknown_model_falls_back_to_the_default(self):
        from app.services.model_manager import get_default_stt_model, normalize_stt_model_id
        assert normalize_stt_model_id('no-such-model') == get_default_stt_model()

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


class TestHfHubOfflineShim:
    """compat.patch_hf_hub_is_offline_mode — what broke onboarding's warm-up.

    Fake modules rather than the real huggingface_hub: the shim has to behave
    the same whether or not the interpreter running the suite has the ML stack.
    """

    def _fake_hub(self, monkeypatch, *, with_helper=False, offline=False):
        import sys
        import types

        constants = types.ModuleType('huggingface_hub.constants')
        constants.HF_HUB_OFFLINE = offline
        hub = types.ModuleType('huggingface_hub')
        hub.constants = constants
        if with_helper:
            hub.is_offline_mode = lambda: 'the original'
        monkeypatch.setitem(sys.modules, 'huggingface_hub', hub)
        monkeypatch.setitem(sys.modules, 'huggingface_hub.constants', constants)
        return hub, constants

    def _unpatched(self, monkeypatch):
        from ml_worker import compat
        monkeypatch.setattr(compat, '_HF_HUB_OFFLINE_MODE_PATCHED', False)
        return compat

    def test_restores_the_helper_the_package_dropped(self, monkeypatch):
        compat = self._unpatched(monkeypatch)
        hub, _ = self._fake_hub(monkeypatch, offline=True)

        compat.patch_hf_hub_is_offline_mode()

        assert hub.is_offline_mode() is True

    def test_reads_the_flag_each_call_rather_than_snapshotting_it(self, monkeypatch):
        """apply_hf_offline flips the constant at runtime and must stay in charge."""
        compat = self._unpatched(monkeypatch)
        hub, constants = self._fake_hub(monkeypatch, offline=False)

        compat.patch_hf_hub_is_offline_mode()
        assert hub.is_offline_mode() is False

        constants.HF_HUB_OFFLINE = True
        assert hub.is_offline_mode() is True

    def test_leaves_a_real_helper_alone(self, monkeypatch):
        compat = self._unpatched(monkeypatch)
        hub, _ = self._fake_hub(monkeypatch, with_helper=True)

        compat.patch_hf_hub_is_offline_mode()

        assert hub.is_offline_mode() == 'the original'


class TestTrustedTorchLoad:
    """compat.trusted_torch_load — the scoped loan the Flask warm-up takes."""

    def _fake_torch(self, monkeypatch, calls):
        import sys
        import types

        torch = types.ModuleType('torch')

        def load(*args, **kwargs):
            calls.append(kwargs.get('weights_only', 'unset'))
            return 'checkpoint'

        torch.load = load
        monkeypatch.setitem(sys.modules, 'torch', torch)
        return torch, load

    def test_relaxes_the_loader_and_hands_it_back(self, monkeypatch):
        from ml_worker import compat
        calls = []
        torch, original = self._fake_torch(monkeypatch, calls)

        with compat.trusted_torch_load():
            torch.load('model.bin')
        torch.load('model.bin')

        assert calls == [False, 'unset']
        assert torch.load is original

    def test_hands_it_back_after_a_failed_load(self, monkeypatch):
        from ml_worker import compat
        torch, original = self._fake_torch(monkeypatch, [])

        with pytest.raises(RuntimeError):
            with compat.trusted_torch_load():
                raise RuntimeError('checkpoint is corrupt')

        assert torch.load is original

    def test_steps_aside_when_the_worker_already_patched_torch(self, monkeypatch):
        from ml_worker import compat
        calls = []
        torch, original = self._fake_torch(monkeypatch, calls)
        original._pine_trusted_checkpoint_wrap = True

        with compat.trusted_torch_load():
            assert torch.load is original

        assert torch.load is original

    def test_honours_the_strict_weights_only_override(self, monkeypatch):
        from ml_worker import compat
        calls = []
        torch, original = self._fake_torch(monkeypatch, calls)
        monkeypatch.setenv('PINE_TORCH_STRICT_WEIGHTS_ONLY', '1')

        with compat.trusted_torch_load():
            torch.load('model.bin')

        assert calls == ['unset']
        assert torch.load is original
