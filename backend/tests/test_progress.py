"""Tests for the single 0→100 progress scale (ml_worker/progress.py)."""

import pytest

from ml_worker.progress import STAGES, ProgressMapper


class _Harness:
    """Drives a mapper on a fake clock, with the ticker inlined."""

    def __init__(self, duration=4468.0):
        self.now = 0.0
        self.events = []
        self.mapper = ProgressMapper(
            duration, self._record, clock=lambda: self.now)

    def _record(self, **payload):
        self.events.append(payload)

    def emit(self, **kwargs):
        self.mapper(**kwargs)

    def wait(self, secs):
        """Advance the clock, running the ticker the way the thread would."""
        m = self.mapper
        for _ in range(int(secs // m._tick_secs)):
            self.now += m._tick_secs
            if m._paused_at is None and m._drift_pct() - m._pct >= 0.5:
                m._emit(m._message, from_tick=True)

    @property
    def percents(self):
        return [e['percent'] for e in self.events]


def _full_run(h):
    h.emit(stage='loading')
    h.wait(12)
    for pct in (0, 33, 67):
        h.emit(stage='transcribing', message=f'Chunk ({pct}%)', percent=pct)
        h.wait(120)
    h.emit(stage='aligning', message='Aligning word timestamps...')
    h.wait(40)
    for pct in (0, 25, 50, 75):
        h.emit(stage='diarizing', message='Speaker ID', percent=pct)
        h.wait(20)
    h.emit(stage='finalizing')
    h.mapper.finish()


class TestGlobalScale:
    def test_bands_cover_the_whole_scale_in_order(self):
        m = _Harness().mapper
        assert m._bands[STAGES[0]][0] == 0
        assert round(m._bands[STAGES[-1]][1]) == 100
        for earlier, later in zip(STAGES, STAGES[1:]):
            assert m._bands[earlier][1] == m._bands[later][0]

    def test_progress_never_goes_backwards(self):
        h = _Harness()
        _full_run(h)
        assert h.percents == sorted(h.percents)

    def test_run_ends_at_100(self):
        h = _Harness()
        _full_run(h)
        assert h.percents[-1] == 100

    def test_new_stage_does_not_reset_the_number(self):
        h = _Harness()
        h.emit(stage='transcribing', percent=100)
        before = h.percents[-1]
        h.emit(stage='diarizing', percent=0)
        assert h.percents[-1] >= before

    def test_skipped_stage_reads_as_a_jump_forward(self):
        """Diarization is best-effort; failing it must not strand the bar."""
        h = _Harness()
        h.emit(stage='transcribing', percent=100)
        h.emit(stage='finalizing')
        h.mapper.finish()
        assert h.percents[-1] == 100

    def test_a_slow_job_revises_its_estimate_upward(self):
        """The estimate has to be able to grow, not just shrink.

        Regression: the pace was measured against the *displayed* percent, and
        the display is the ticker's own extrapolation clamped forward. When the
        job ran slower than planned the ticker was already ahead, so feeding
        that number back in reproduced the plan almost exactly and the ETA was
        pinned to an optimistic figure for the whole run — measured at roughly
        half the real time on this machine.
        """
        h = _Harness(duration=4468.0)
        plan = h.mapper._total_expected

        h.emit(stage='loading')
        h.wait(30)
        h.emit(stage='transcribing', message='Chunk (0%)', percent=0)
        h.wait(200)
        # 230s spent and only a quarter of transcription done: this job is going
        # to take far longer than the plan allowed.
        h.emit(stage='transcribing', message='Chunk (25%)', percent=25)

        assert h.mapper._predicted_total() > plan * 1.5

    def test_the_bar_holds_still_while_reality_catches_up(self):
        """Anchoring honestly must not let the displayed percent go backwards."""
        h = _Harness(duration=4468.0)
        h.emit(stage='loading')
        h.wait(30)
        h.emit(stage='transcribing', message='Chunk (0%)', percent=0)
        h.wait(200)
        ahead = h.percents[-1]
        h.emit(stage='transcribing', message='Chunk (25%)', percent=25)

        assert h.percents[-1] >= ahead
        assert h.percents == sorted(h.percents)

    def test_eta_reflects_the_revised_pace(self):
        h = _Harness(duration=4468.0)
        h.emit(stage='loading')
        h.wait(30)
        h.emit(stage='transcribing', message='Chunk (0%)', percent=0)
        h.wait(200)
        h.emit(stage='transcribing', message='Chunk (25%)', percent=25)

        eta = h.events[-1]['eta_secs']
        # A quarter done after 230s means something near 700s left, not the
        # ~130s the original plan implied.
        assert eta > 400

    def test_a_learned_scale_stretches_the_plan(self):
        h = _Harness(duration=4468.0)
        slow = ProgressMapper(4468.0, lambda **p: None, scale=2.0)

        assert slow._total_expected > h.mapper._total_expected * 1.8

    def test_a_learned_scale_leaves_the_flat_costs_alone(self):
        """Loading a model off disk does not get cheaper on a fast GPU."""
        plain = ProgressMapper(4468.0, lambda **p: None, scale=1.0)
        slow = ProgressMapper(4468.0, lambda **p: None, scale=2.0)

        assert plain._expected['loading'] == slow._expected['loading']
        assert plain._expected['finalizing'] == slow._expected['finalizing']
        assert slow._expected['transcribing'] == pytest.approx(
            plain._expected['transcribing'] * 2)

    def test_a_learned_scale_does_not_move_the_bands(self):
        """It changes how fast time passes, not where the milestones sit."""
        plain = ProgressMapper(4468.0, lambda **p: None, scale=1.0)
        slow = ProgressMapper(4468.0, lambda **p: None, scale=2.0)

        # The duration-proportional stages keep their proportions to each other.
        assert (slow._bands['diarizing'][1] - slow._bands['transcribing'][0]) \
            > (plain._bands['diarizing'][1] - plain._bands['transcribing'][0])

    def test_a_slow_run_reports_the_scale_it_needed(self):
        h = _Harness(duration=4468.0)
        planned_variable = h.mapper._variable_expected
        h.emit(stage='loading')
        h.now += planned_variable * 2 + h.mapper._flat_expected
        h.mapper.finish()

        assert h.mapper.observed_scale() == pytest.approx(2.0, rel=0.05)

    def test_nothing_is_learned_from_a_short_clip(self):
        """Fixed costs dominate a one-minute file; the ratio is meaningless."""
        h = _Harness(duration=45.0)
        h.emit(stage='loading')
        h.now += 300
        h.mapper.finish()

        assert h.mapper.observed_scale() is None

    def test_an_absurd_measurement_is_clamped(self):
        """A machine that slept mid-job must not poison every job after it."""
        h = _Harness(duration=4468.0)
        h.emit(stage='loading')
        h.now += 60 * 60 * 24
        h.mapper.finish()

        assert h.mapper.observed_scale() <= 5.0

    def test_the_scale_is_measured_against_the_shipped_plan_not_its_own(self):
        """Otherwise the correction compounds and runs away over a few jobs."""
        h = _Harness(duration=4468.0)
        already = ProgressMapper(4468.0, lambda **p: None,
                                 clock=lambda: h.now, scale=2.0)
        h.now = already._flat_expected + already._variable_expected * 2

        assert already.observed_scale() == pytest.approx(2.0, rel=0.05)

    def test_paused_time_is_not_charged_to_the_machine(self):
        """The language prompt waits on the user, not on the GPU."""
        h = _Harness(duration=4468.0)
        h.emit(stage='loading')
        h.mapper.pause()
        h.now += 5000
        h.mapper.resume()
        h.now += h.mapper._flat_expected + h.mapper._variable_expected
        h.mapper.finish()

        assert h.mapper.observed_scale() == pytest.approx(1.0, rel=0.05)

    def test_ticker_moves_between_chunk_milestones(self):
        """Chunks are 30 min apart — the bar has to move in between."""
        h = _Harness()
        h.emit(stage='transcribing', percent=0)
        at_chunk = h.percents[-1]
        h.wait(120)
        assert h.percents[-1] > at_chunk

    def test_ticker_stays_inside_the_current_band(self):
        h = _Harness()
        h.emit(stage='transcribing', percent=0)
        h.wait(6000)
        assert h.percents[-1] < h.mapper._bands['transcribing'][1]

    def test_short_recording_still_reports_a_number(self):
        """Under 30 min nothing chunks, so no engine ever sends a percent."""
        h = _Harness(duration=600.0)
        h.emit(stage='transcribing', message='Transcribing...')
        h.wait(60)
        assert h.percents[-1] > 0


class TestEta:
    def test_eta_is_available_from_the_first_event(self):
        """Before there is any pace to measure, the plan answers."""
        h = _Harness()
        h.emit(stage='loading')
        assert h.events[-1]['eta_secs'] > 0

    def test_eta_shrinks_as_the_job_runs(self):
        h = _Harness()
        h.emit(stage='transcribing', percent=0)
        h.wait(60)
        first = h.events[-1]['eta_secs']
        h.wait(120)
        assert h.events[-1]['eta_secs'] < first

    def test_eta_gone_at_100(self):
        h = _Harness()
        _full_run(h)
        assert h.events[-1]['eta_secs'] is None

    def test_eta_survives_a_stage_change(self):
        """The old per-stage ETA blanked out here; the global one must not."""
        h = _Harness()
        h.emit(stage='transcribing', percent=50)
        h.wait(120)
        h.emit(stage='diarizing', percent=0)
        assert h.events[-1]['eta_secs'] is not None

    def test_slow_machine_gets_a_longer_eta(self):
        fast, slow = _Harness(), _Harness()
        for h, pace in ((fast, 60), (slow, 400)):
            h.emit(stage='transcribing', percent=0)
            h.wait(pace)
            h.emit(stage='transcribing', percent=33)
        assert slow.events[-1]['eta_secs'] > fast.events[-1]['eta_secs']

    def test_waiting_on_the_user_does_not_inflate_the_eta(self):
        h = _Harness()
        h.emit(stage='transcribing', percent=0)
        h.wait(120)
        h.emit(stage='transcribing', percent=33)
        baseline = h.events[-1]['eta_secs']

        h.mapper.pause()
        h.wait(600)
        h.mapper.resume()
        h.emit(stage='transcribing', percent=33)

        assert h.events[-1]['eta_secs'] <= baseline
