"""Tests for the per-track transcription path (ml_worker/multitrack.py).

These go through the real ffmpeg decode rather than stubbing it, because the
decode is where the whole idea can quietly break: one stray ``-ac 1`` averages
the speakers back together and the attribution this path exists for is gone.
"""

import os
import shutil
import subprocess

import numpy as np
import pytest

from ml_worker.audio import load_audio_file, write_wav
from ml_worker.engines.base import TranscribeOutput
from ml_worker.errors import TranscriptionCancelled
from ml_worker.multitrack import join_runs, run_multitrack
from ml_worker.pipeline import PipelineEvents, TrackSpec, map_speakers
from ml_worker.tracks import SAMPLE_RATE

pytestmark = pytest.mark.skipif(
    shutil.which('ffmpeg') is None, reason='ffmpeg not on PATH')


def _speech(spans, total, freq=220.0, amp=0.3):
    """A silent track with tones where somebody is talking."""
    out = np.zeros(int(total * SAMPLE_RATE), dtype=np.float32)
    for start, end in spans:
        t = np.arange(int((end - start) * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
        i0 = int(start * SAMPLE_RATE)
        out[i0:i0 + len(t)] = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return out


class FakeEngine:
    """Reports one segment covering whatever audio it was handed."""

    id = 'fake'

    def __init__(self, language='ru'):
        self.calls = []
        self._language = language

    def load(self):
        pass

    def transcribe(self, audio_path, total_duration, language, ctx):
        self.calls.append({'path': audio_path, 'duration': total_duration,
                           'language': language})
        ctx.check_cancel()
        ctx.on_status(stage='transcribing', message='Transcribing...', percent=50)
        half = total_duration / 2
        words = [
            {'word': 'one', 'start': 0.0, 'end': half},
            {'word': 'two', 'start': half, 'end': total_duration},
        ]
        return TranscribeOutput(
            segments=[{'start': 0.0, 'end': total_duration,
                       'text': 'one two', 'words': words}],
            language=language or self._language,
            duration_seconds=total_duration,
            audio=np.zeros(4, dtype=np.float32),
        )


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, **payload):
        self.events.append(payload)

    @property
    def percents(self):
        return [e['percent'] for e in self.events if e.get('percent') is not None]


def _events(recorder=None, check_cancel=None):
    return PipelineEvents(
        status=recorder or Recorder(),
        check_cancel=check_cancel or (lambda: None),
    )


def _two_files(tmp_path):
    """Moderator asks early, participant answers later — separate files."""
    a = str(tmp_path / 'audio1234_Ivan Petrov.wav')
    b = str(tmp_path / 'audio1235_Maria.wav')
    write_wav(a, _speech([(2.0, 6.0)], total=20.0, freq=220.0))
    write_wav(b, _speech([(10.0, 16.0)], total=20.0, freq=440.0))
    return [
        TrackSpec(index=0, path=a, speaker_name='Ivan Petrov'),
        TrackSpec(index=1, path=b, speaker_name='Maria'),
    ]


# ── separate files, one per participant ──

def test_each_track_is_transcribed_on_its_own(tmp_path):
    engine = FakeEngine()
    specs = _two_files(tmp_path)

    segments, names, language = run_multitrack(engine, specs, 'ru', _events())

    assert len(engine.calls) == 2, 'one pass per speaker'
    assert names == {'TRACK_00': 'Ivan Petrov', 'TRACK_01': 'Maria'}
    assert language == 'ru'
    assert {s['speaker'] for s in segments} == {'TRACK_00', 'TRACK_01'}


def test_segments_land_on_the_original_timeline(tmp_path):
    segments, _, _ = run_multitrack(
        FakeEngine(), _two_files(tmp_path), 'ru', _events())

    by_speaker = {s['speaker']: s for s in segments}
    assert by_speaker['TRACK_00']['start'] == pytest.approx(2.0, abs=0.5)
    assert by_speaker['TRACK_00']['end'] == pytest.approx(6.0, abs=0.5)
    assert by_speaker['TRACK_01']['start'] == pytest.approx(10.0, abs=0.5)
    assert by_speaker['TRACK_01']['end'] == pytest.approx(16.0, abs=0.5)


def test_only_the_speech_is_sent_to_the_engine(tmp_path):
    """20s of track, ~4s of speech — the pauses must not reach the model."""
    engine = FakeEngine()
    run_multitrack(engine, _two_files(tmp_path)[:1], 'ru', _events())

    assert engine.calls[0]['duration'] < 6.0
    assert engine.calls[0]['duration'] > 3.5


def test_merged_segments_are_sorted(tmp_path):
    segments, _, _ = run_multitrack(
        FakeEngine(), _two_files(tmp_path), 'ru', _events())

    assert [s['start'] for s in segments] == sorted(s['start'] for s in segments)


def test_silent_track_is_skipped_not_fatal(tmp_path):
    quiet = str(tmp_path / 'nobody.wav')
    write_wav(quiet, np.zeros(int(20.0 * SAMPLE_RATE), dtype=np.float32))
    specs = _two_files(tmp_path) + [
        TrackSpec(index=2, path=quiet, speaker_name='Silent')]

    engine = FakeEngine()
    segments, names, _ = run_multitrack(engine, specs, 'ru', _events())

    assert len(engine.calls) == 2, 'the silent track was still transcribed'
    assert 'TRACK_02' not in {s['speaker'] for s in segments}
    assert names['TRACK_02'] == 'Silent'


def test_language_is_locked_in_after_the_first_track(tmp_path):
    """Otherwise each track auto-detects and one talk comes back bilingual."""
    engine = FakeEngine(language='ru')
    _, _, language = run_multitrack(
        engine, _two_files(tmp_path), None, _events())

    assert language == 'ru'
    assert engine.calls[0]['language'] is None
    assert engine.calls[1]['language'] == 'ru'


def test_temp_files_are_cleaned_up(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    run_multitrack(FakeEngine(), _two_files(tmp_path), 'ru', _events(),
                   work_dir=str(work))

    assert os.listdir(work) == []


def test_cancellation_stops_the_run(tmp_path):
    calls = {'n': 0}

    def check_cancel():
        calls['n'] += 1
        if calls['n'] > 3:
            raise TranscriptionCancelled('stop')

    with pytest.raises(TranscriptionCancelled):
        run_multitrack(FakeEngine(), _two_files(tmp_path), 'ru',
                       _events(check_cancel=check_cancel))


# ── one file, a channel per speaker ──

def _stereo_file(tmp_path):
    left = str(tmp_path / 'l.wav')
    right = str(tmp_path / 'r.wav')
    stereo = str(tmp_path / 'interview.wav')
    write_wav(left, _speech([(2.0, 6.0)], total=20.0, freq=220.0))
    write_wav(right, _speech([(10.0, 16.0)], total=20.0, freq=440.0))
    subprocess.run(
        ['ffmpeg', '-y', '-v', 'error', '-i', left, '-i', right,
         '-filter_complex', '[0:a][1:a]join=inputs=2:channel_layout=stereo[a]',
         '-map', '[a]', stereo],
        check=True, capture_output=True)
    return stereo


def test_channels_are_not_averaged_together(tmp_path):
    """The bug this guards: -ac 1 mixes both speakers into every track."""
    stereo = _stereo_file(tmp_path)

    left = load_audio_file(stereo, channel=0)
    right = load_audio_file(stereo, channel=1)

    # Each channel is loud only where its own speaker talks.
    assert np.abs(left[int(3 * SAMPLE_RATE):int(5 * SAMPLE_RATE)]).max() > 0.1
    assert np.abs(left[int(12 * SAMPLE_RATE):int(14 * SAMPLE_RATE)]).max() < 0.01
    assert np.abs(right[int(12 * SAMPLE_RATE):int(14 * SAMPLE_RATE)]).max() > 0.1
    assert np.abs(right[int(3 * SAMPLE_RATE):int(5 * SAMPLE_RATE)]).max() < 0.01


def test_multichannel_file_splits_into_speakers(tmp_path):
    stereo = _stereo_file(tmp_path)
    specs = [
        TrackSpec(index=0, path=stereo, speaker_name='Channel 1', channel=0),
        TrackSpec(index=1, path=stereo, speaker_name='Channel 2', channel=1),
    ]

    segments, names, _ = run_multitrack(FakeEngine(), specs, 'ru', _events())

    by_speaker = {s['speaker']: s for s in segments}
    assert set(by_speaker) == {'TRACK_00', 'TRACK_01'}
    assert by_speaker['TRACK_00']['start'] == pytest.approx(2.0, abs=0.5)
    assert by_speaker['TRACK_01']['start'] == pytest.approx(10.0, abs=0.5)
    assert names['TRACK_01'] == 'Channel 2'


# ── progress and naming as the rest of the pipeline sees them ──

def test_progress_reports_preparation_then_transcription(tmp_path):
    recorder = Recorder()
    run_multitrack(FakeEngine(), _two_files(tmp_path), 'ru', _events(recorder))

    stages = [e['stage'] for e in recorder.events]
    # Decoding and gating every track up front takes real time on a long
    # meeting; it reports as 'loading' so the UI is not silent through it.
    assert set(stages) == {'loading', 'transcribing'}
    assert stages.index('transcribing') > stages.index('loading')
    assert recorder.percents == sorted(recorder.percents), 'progress went backwards'
    assert recorder.percents[-1] == pytest.approx(100.0)


def test_tracks_are_weighted_by_how_much_speech_they_hold(tmp_path):
    """A moderator who speaks a fifth of the time is not half the work."""
    quiet = str(tmp_path / 'moderator.wav')
    talker = str(tmp_path / 'participant.wav')
    write_wav(quiet, _speech([(1.0, 3.0)], total=40.0, freq=220.0))
    write_wav(talker, _speech([(5.0, 35.0)], total=40.0, freq=440.0))
    specs = [
        TrackSpec(index=0, path=quiet, speaker_name='Moderator'),
        TrackSpec(index=1, path=talker, speaker_name='Participant'),
    ]

    recorder = Recorder()
    run_multitrack(FakeEngine(), specs, 'ru', _events(recorder))

    # The milestone at the end of the first track should sit near its share of
    # the speech (~2s of ~32s), nowhere near the halfway mark that counting
    # tracks 1/N apiece would produce.
    after_first = min(p for p in recorder.percents if p > 0)
    assert after_first < 25, f'first track ended at {after_first}%, expected ~8%'
    assert recorder.percents[-1] == pytest.approx(100.0)


def test_a_heartbeat_without_a_percent_does_not_move_the_bar_back(tmp_path):
    """Engines heartbeat with a message only; that must not reset the track."""
    class Heartbeating(FakeEngine):
        def transcribe(self, audio_path, total_duration, language, ctx):
            ctx.on_status(stage='transcribing', message='Transcribing...',
                          percent=80)
            ctx.on_status(stage='transcribing', message='2m elapsed')
            return super().transcribe(audio_path, total_duration, language, ctx)

    recorder = Recorder()
    run_multitrack(Heartbeating(), _two_files(tmp_path), 'ru', _events(recorder))

    assert recorder.percents == sorted(recorder.percents)
    assert any(e.get('percent') is None for e in recorder.events), \
        'the heartbeat should pass through without a percent'


def test_track_names_reach_the_transcript(tmp_path):
    segments, names, _ = run_multitrack(
        FakeEngine(), _two_files(tmp_path), 'ru', _events())

    mapping = map_speakers(segments, names)

    assert mapping == {'TRACK_00': 'Ivan Petrov', 'TRACK_01': 'Maria'}
    assert {s['speaker'] for s in segments} == {'Ivan Petrov', 'Maria'}


def test_duplicate_track_names_stay_distinct():
    """Two guests called Maria must not merge into one speaker."""
    segments = [{'speaker': 'TRACK_00'}, {'speaker': 'TRACK_01'}]

    mapping = map_speakers(segments, {'TRACK_00': 'Maria', 'TRACK_01': 'Maria'})

    assert len(set(mapping.values())) == 2


# ── the branch as the pipeline runs it ──

def _run_pipeline(tmp_path, monkeypatch, job_kwargs):
    from ml_worker.pipeline import JobEnv, JobRequest, MLPipeline

    engine = FakeEngine()
    asked = {}

    def _stub_models(self, env, need_diarizer=True):
        asked['need_diarizer'] = need_diarizer
        self._engine = engine

    monkeypatch.setattr(MLPipeline, '_ensure_models', _stub_models)

    env = JobEnv(stt_model_id='fake', model_dir='', diarize_dir='',
                 pyannote_cache='')
    job = JobRequest(recording_id=7, forced_language='ru', **job_kwargs)
    return MLPipeline().run(env, job, PipelineEvents()), engine, asked


def test_pipeline_takes_the_multitrack_branch(tmp_path, monkeypatch):
    specs = _two_files(tmp_path)
    payload, engine, asked = _run_pipeline(tmp_path, monkeypatch, {
        'audio_path': specs[0].path,
        'tracks': [{'index': s.index, 'path': s.path,
                    'speaker_name': s.speaker_name} for s in specs],
    })

    assert asked['need_diarizer'] is False, 'pyannote must not even be loaded'
    assert len(engine.calls) == 2
    assert payload['recording_id'] == 7
    assert payload['language'] == 'ru'
    assert payload['speakers'] == {'TRACK_00': 'Ivan Petrov', 'TRACK_01': 'Maria'}
    assert {s['speaker'] for s in payload['segments']} == {'Ivan Petrov', 'Maria'}
    assert [s['start'] for s in payload['segments']] == sorted(
        s['start'] for s in payload['segments'])


def test_pipeline_without_tracks_still_wants_the_diarizer(tmp_path, monkeypatch):
    """The single-file path has to be left exactly as it was."""
    from ml_worker.pipeline import JobEnv, JobRequest, MLPipeline

    asked = {}
    engine = FakeEngine()

    def _stub_models(self, env, need_diarizer=True):
        asked['need_diarizer'] = need_diarizer
        self._engine = engine
        # The stub installs no diarizer, so the run cannot get further — by
        # which point it has already told us what it asked for.
        raise RuntimeError('stop here')

    monkeypatch.setattr(MLPipeline, '_ensure_models', _stub_models)
    env = JobEnv(stt_model_id='fake', model_dir='', diarize_dir='',
                 pyannote_cache='')
    job = JobRequest(recording_id=7, audio_path=_two_files(tmp_path)[0].path,
                     forced_language='ru')

    with pytest.raises(RuntimeError):
        MLPipeline().run(env, job, PipelineEvents())

    assert asked['need_diarizer'] is True


def test_single_file_job_reports_no_tracks():
    from ml_worker.pipeline import JobRequest

    assert JobRequest(recording_id=1, audio_path='x.mp3').track_specs() == []


def test_track_specs_come_back_sorted_from_the_wire():
    from ml_worker.pipeline import JobRequest

    job = JobRequest(recording_id=1, audio_path='x.mp3', tracks=[
        {'index': 1, 'path': 'b.m4a', 'speaker_name': 'B'},
        {'index': 0, 'path': 'a.m4a', 'speaker_name': 'A'},
    ])

    assert [t.speaker_name for t in job.track_specs()] == ['A', 'B']
# ── one turn, one segment ──

def _seg(start, end, text, speaker='TRACK_00', words=True):
    seg = {'start': start, 'end': end, 'text': text, 'speaker': speaker}
    if words:
        seg['words'] = [{'word': text, 'start': start, 'end': end}]
    return seg


def test_a_turn_split_by_a_breath_is_joined_back_up():
    joined = join_runs([_seg(0.0, 1.0, 'Речь о том'),
                        _seg(1.4, 2.0, 'и наш канал')])

    assert len(joined) == 1
    assert joined[0]['text'] == 'Речь о том и наш канал'
    assert (joined[0]['start'], joined[0]['end']) == (0.0, 2.0)
    assert len(joined[0]['words']) == 2, 'the words come along'


def test_a_real_pause_still_ends_the_segment():
    joined = join_runs([_seg(0.0, 1.0, 'a'), _seg(5.0, 6.0, 'b')])

    assert [s['text'] for s in joined] == ['a', 'b']


def test_another_speaker_in_between_keeps_the_turns_apart():
    """Joining across someone else would put a segment on top of their turn."""
    joined = join_runs([
        _seg(0.0, 1.0, 'a'),
        _seg(1.1, 1.3, 'угу', speaker='TRACK_01'),
        _seg(1.4, 2.0, 'b'),
    ])

    assert [s['text'] for s in joined] == ['a', 'угу', 'b']


def test_joining_stops_at_the_length_cap():
    segments = [_seg(i * 2.0, i * 2.0 + 1.0, str(i)) for i in range(30)]

    joined = join_runs(segments, max_gap=1.5, max_len=10.0)

    assert len(joined) > 1
    assert max(s['end'] - s['start'] for s in joined) <= 10.0


def test_a_segment_without_words_is_not_joined_to_one_with_them():
    """The transcript is rebuilt from words; half a set would lose the rest."""
    joined = join_runs([_seg(0.0, 1.0, 'a', words=False), _seg(1.2, 2.0, 'b')])

    assert [s['text'] for s in joined] == ['a', 'b']


def test_the_segments_handed_in_are_left_alone():
    segments = [_seg(0.0, 1.0, 'a'), _seg(1.2, 2.0, 'b')]

    join_runs(segments)

    assert [s['end'] for s in segments] == [1.0, 2.0]


def test_a_track_cut_in_two_comes_back_as_one_segment(tmp_path):
    """End to end: two speech regions on one track, one line in the transcript."""
    path = str(tmp_path / 'audio1234_Ivan.wav')
    write_wav(path, _speech([(2.0, 5.0), (6.5, 9.0)], total=12.0))
    specs = [TrackSpec(index=0, path=path, speaker_name='Ivan')]

    segments, _, _ = run_multitrack(FakeEngine(), specs, 'ru', _events())

    assert len(segments) == 1
    assert segments[0]['start'] == pytest.approx(2.0, abs=0.5)
    assert segments[0]['end'] == pytest.approx(9.0, abs=0.5)
