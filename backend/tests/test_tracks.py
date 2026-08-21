"""Tests for per-speaker track handling (ml_worker/tracks.py).

The synthetic material is deliberately interview-shaped: one long answer, a
short back-channel over it, and a lot of silence in between — that is where the
tuning has to hold.
"""

import numpy as np
import pytest

from ml_worker.tracks import (
    SAMPLE_RATE,
    compact,
    detect_speech,
    frame_db,
    remap,
    resolve_bleed,
    speech_mask,
    speech_thresholds,
)

TOLERANCE = 0.05   # generous next to the 20 ms frame; padding moves edges


def _silence(secs):
    return np.zeros(int(secs * SAMPLE_RATE), dtype=np.float32)


def _tone(secs, freq=220.0, amp=0.3):
    t = np.arange(int(secs * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _track(spans, total, amp=0.3, freq=220.0):
    """A silent track of ``total`` seconds with tones at ``spans``."""
    out = _silence(total)
    for start, end in spans:
        tone = _tone(end - start, freq=freq, amp=amp)
        i0 = int(start * SAMPLE_RATE)
        out[i0:i0 + len(tone)] = tone
    return out


# ── finding the speech ──

def test_detect_speech_finds_both_spans():
    audio = _track([(2.0, 6.0), (10.0, 12.5)], total=15.0)
    regions = detect_speech(audio)

    assert len(regions) == 2
    assert regions[0][0] == pytest.approx(2.0, abs=0.3)
    assert regions[0][1] == pytest.approx(6.0, abs=0.3)
    assert regions[1][0] == pytest.approx(10.0, abs=0.3)
    assert regions[1][1] == pytest.approx(12.5, abs=0.3)


def test_short_backchannel_survives():
    """"Угу" is 0.4s and is the moderator's entire contribution here."""
    audio = _track([(1.0, 1.4), (5.0, 9.0)], total=12.0)
    regions = detect_speech(audio)

    assert len(regions) == 2, 'the back-channel was dropped'
    assert regions[0][0] == pytest.approx(1.0, abs=0.3)


def test_isolated_click_is_dropped():
    audio = _track([(1.0, 1.04), (5.0, 9.0)], total=12.0)
    regions = detect_speech(audio)

    assert len(regions) == 1
    assert regions[0][0] == pytest.approx(5.0, abs=0.3)


def test_close_spans_are_merged():
    audio = _track([(2.0, 3.0), (3.3, 4.0)], total=8.0)
    regions = detect_speech(audio)

    assert len(regions) == 1
    assert regions[0][1] - regions[0][0] == pytest.approx(2.5, abs=0.4)


def test_silent_track_yields_nothing():
    assert detect_speech(_silence(20.0)) == []


@pytest.mark.parametrize('share', [0.05, 0.02, 0.01])
def test_a_speaker_who_barely_talks_is_still_found(share):
    """A moderator can hold the floor for 1% of a meeting and still be on it.

    Guards a real regression: with the loud end taken as a high percentile of
    the whole track, anything this sparse fell below it and the entire track
    was read as silence — the moderator vanished from the transcript.
    """
    total = 200.0
    span = total * share
    audio = _track([(20.0, 20.0 + span)], total=total)

    regions = detect_speech(audio)

    assert len(regions) == 1, f'{share:.0%} speech was thrown away'
    assert regions[0][0] == pytest.approx(20.0, abs=0.4)
    assert regions[0][1] == pytest.approx(20.0 + span, abs=0.4)


def test_wall_to_wall_speech_is_one_region():
    regions = detect_speech(_tone(10.0))

    assert len(regions) == 1
    assert regions[0][0] == pytest.approx(0.0, abs=0.1)
    assert regions[0][1] == pytest.approx(10.0, abs=0.1)


def test_thresholds_track_their_own_level():
    """A quiet track and a loud one each get thresholds scaled to themselves."""
    quiet = speech_thresholds(frame_db(_track([(1.0, 4.0)], 8.0, amp=0.02)))
    loud = speech_thresholds(frame_db(_track([(1.0, 4.0)], 8.0, amp=0.6)))

    assert quiet is not None and loud is not None
    assert quiet[0] < loud[0]


# ── compaction and the trip back ──

def _fake_words(spans):
    return [{'word': f'w{i}', 'start': s, 'end': e}
            for i, (s, e) in enumerate(spans)]


def test_compact_drops_the_silence():
    audio = _track([(2.0, 6.0), (10.0, 12.0)], total=20.0)
    regions = detect_speech(audio)
    short, splices = compact(audio, regions)

    assert len(splices) == 2
    assert len(short) / SAMPLE_RATE < 8.0     # 20s of track, ~6.5s of speech
    assert len(short) / SAMPLE_RATE > 6.0


def test_remap_restores_original_timings():
    """The whole point: what goes in as 11.0s must come back as 11.0s."""
    audio = _track([(2.0, 6.0), (10.0, 14.0)], total=20.0)
    regions = detect_speech(audio)
    _, splices = compact(audio, regions)

    # Pick two moments, convert them into compacted time the way the map says,
    # hand them to remap as if the model had reported them, and check the return.
    def to_compact(t):
        for c_start, c_end, o_start in splices:
            if o_start <= t <= o_start + (c_end - c_start):
                return c_start + (t - o_start)
        raise AssertionError(f'{t}s is not inside any region')

    wanted = [(3.0, 3.5), (11.0, 11.6)]
    segments = [{'start': to_compact(s), 'end': to_compact(e), 'text': 'x',
                 'words': _fake_words([(to_compact(s), to_compact(e))])}
                for s, e in wanted]

    out = remap(segments, splices)

    assert len(out) == 2
    for got, (start, end) in zip(out, wanted, strict=False):
        assert got['start'] == pytest.approx(start, abs=TOLERANCE)
        assert got['end'] == pytest.approx(end, abs=TOLERANCE)


def test_segment_spanning_a_splice_is_split():
    splices = [(0.0, 4.0, 2.0), (4.2, 8.2, 30.0)]
    segment = {
        'start': 3.0, 'end': 5.0, 'text': 'a b',
        'words': _fake_words([(3.0, 3.5), (4.5, 5.0)]),
    }

    out = remap([segment], splices)

    assert len(out) == 2, 'a sentence glued across a cut must come apart'
    assert out[0]['start'] == pytest.approx(5.0, abs=TOLERANCE)    # 2.0 + 3.0
    assert out[1]['start'] == pytest.approx(30.3, abs=TOLERANCE)   # 30.0 + 0.3


def test_words_inside_the_inserted_gap_are_dropped():
    """There was no audio in the gap, so anything reported there is invented."""
    splices = [(0.0, 4.0, 2.0), (4.2, 8.2, 30.0)]
    segment = {
        'start': 4.05, 'end': 4.15, 'text': 'ghost',
        'words': _fake_words([(4.05, 4.15)]),
    }

    assert remap([segment], splices) == []


def test_segment_without_words_uses_its_midpoint():
    splices = [(0.0, 4.0, 10.0)]
    segment = {'start': 1.0, 'end': 2.0, 'text': 'mlx has no words'}

    out = remap([segment], splices)

    assert len(out) == 1
    assert out[0]['start'] == pytest.approx(11.0, abs=TOLERANCE)
    assert out[0]['end'] == pytest.approx(12.0, abs=TOLERANCE)


def test_remap_without_splices_returns_nothing():
    assert remap([{'start': 0, 'end': 1, 'text': 'x'}], []) == []


# ── bleed between channels of one recording ──

def _mask_for(audio):
    db = frame_db(audio)
    return db, speech_mask(db, speech_thresholds(db))


def test_bleed_is_taken_from_the_quiet_copy():
    """Same voice on both mics: the near one keeps it, the far one loses it."""
    near = _track([(1.0, 5.0)], total=8.0, amp=0.5)
    far = _track([(1.0, 5.0)], total=8.0, amp=0.02)     # ~28 dB down
    db_near, mask_near = _mask_for(near)
    db_far, mask_far = _mask_for(far)
    assert mask_far.any(), 'precondition: the bleed passes its own gate'

    kept_near, kept_far = resolve_bleed([db_near, db_far], [mask_near, mask_far])

    assert kept_near.sum() == mask_near.sum()
    assert kept_far.sum() == 0


def test_simultaneous_speech_is_kept_on_both():
    """Nobody dominates, so nobody is thrown away — this is real overlap."""
    one = _track([(1.0, 5.0)], total=8.0, amp=0.3, freq=220.0)
    two = _track([(1.0, 5.0)], total=8.0, amp=0.3, freq=440.0)
    db_one, mask_one = _mask_for(one)
    db_two, mask_two = _mask_for(two)

    kept_one, kept_two = resolve_bleed([db_one, db_two], [mask_one, mask_two])

    assert kept_one.sum() == pytest.approx(mask_one.sum(), rel=0.05)
    assert kept_two.sum() == pytest.approx(mask_two.sum(), rel=0.05)


def test_single_track_is_left_alone():
    db, mask = _mask_for(_track([(1.0, 3.0)], total=5.0))

    assert resolve_bleed([db], [mask])[0] is mask
