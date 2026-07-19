"""Tests for the single 0→100 progress scale (ml_worker/progress.py)."""

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
