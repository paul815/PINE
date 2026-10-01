"""The conftest guard that keeps app shutdown from killing the test runner.

Several routes terminate the backend from a daemon thread after a short sleep.
That is correct in production — sys.exit() inside a daemon thread would not stop
the process — but under pytest the thread killed the *runner*: the suite exited
with status 0, printed no summary and wrote no --junitxml, while roughly 78% of
the tests never ran.  A full-suite run looked green.

These tests pin the guard in place.  If someone removes it from conftest, they
fail loudly here instead of the suite silently truncating itself.

Assertions filter the shared record twice on purpose -- by position and by
thread ident.  Shutdown threads armed by other tests are still in flight while
these run, so the record legitimately contains their entries too, and an ident
alone does not identify a thread for long: see _exits_from.
"""

import os
import threading


def _exits_from(calls, idents, start=0):
    """Exit requests made by the given threads during this test.

    Both filters are needed.  `start` drops what was recorded before the test
    began: the record is never cleared, and CPython hands a thread ident back
    out once the thread has exited.  On Linux that reuse is immediate, so the
    reset thread of the previous test passes its ident straight to the thread
    this test is about to start, and its graceful_exit shows up here as if this
    test had caused it.  `idents` then drops the threads armed by *other* tests
    that are still in flight.
    """
    return [(name, args) for name, args, ident in calls[start:] if ident in idents]


def _run_spawned_threads(before, timeout=5.0):
    """Join whatever daemon threads the call under test started; return idents."""
    spawned = [t for t in threading.enumerate() if t not in before]
    assert spawned, 'expected the route to start a shutdown thread'
    for t in spawned:
        t.join(timeout)
        assert not t.is_alive(), f'{t!r} did not finish within {timeout}s'
    return {t.ident for t in spawned}


class TestProcessExitGuard:

    def test_exit_primitives_are_stubbed(self):
        """os._exit and os.execv must not be the real thing during tests."""
        assert getattr(os._exit, '_pine_exit_guard', False), \
            'os._exit is live — the suite can be killed mid-run'
        assert getattr(os.execv, '_pine_exit_guard', False), \
            'os.execv is live — the runner image can be replaced'

    def test_graceful_exit_returns_instead_of_terminating(self, process_exit_calls):
        """Reaching graceful_exit must be survivable and observable."""
        from app import shutdown as app_shutdown

        start = len(process_exit_calls)
        app_shutdown.graceful_exit(0)

        assert _exits_from(process_exit_calls, {threading.get_ident()}, start) == \
            [('graceful_exit', (0,))]

    def test_reset_shutdown_thread_does_not_kill_the_runner(self, monkeypatch, process_exit_calls):
        """The real /api/settings/reset shutdown path, run to completion.

        This is the thread that used to end the run 22% in: it sleeps 0.8s and
        then calls graceful_exit(0).  We let it run for real and join it, so the
        test only passes if the process is still here afterwards.
        """
        from app.api import settings as settings_api

        # Keep the test hermetic: the thread POSTs to the supervisor port, which
        # may be a PINE instance actually running on this machine.
        monkeypatch.setattr(
            settings_api.urllib.request,
            'urlopen',
            lambda req, timeout=None: None,
        )

        before = set(threading.enumerate())
        start = len(process_exit_calls)
        settings_api._shutdown_backend_after_reset()
        idents = _run_spawned_threads(before)

        assert _exits_from(process_exit_calls, idents, start) == [('graceful_exit', (0,))]

    def test_restart_route_does_not_replace_the_runner(self, client, process_exit_calls):
        """/api/utils/restart calls os.execv, which would swap the pytest image."""
        before = set(threading.enumerate())
        start = len(process_exit_calls)

        r = client.post('/api/utils/restart')
        assert r.status_code == 200

        idents = _run_spawned_threads(before)

        # execv was stubbed, so it returned instead of raising, and the fallback
        # branch that would Popen a second runner never ran.
        assert [name for name, _ in _exits_from(process_exit_calls, idents, start)] == ['os.execv']
