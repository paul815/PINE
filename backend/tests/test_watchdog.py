"""Tests for the transcription watchdog mechanism."""

import threading
from unittest.mock import MagicMock, patch

import app.services.transcription as tmod


class TestHeartbeat:
    """Verify _touch_heartbeat updates the timestamp."""

    def test_touch_heartbeat_updates_timestamp(self):
        old = tmod._last_heartbeat
        tmod._touch_heartbeat()
        assert tmod._last_heartbeat >= old

    def test_emit_status_touches_heartbeat(self):
        with patch.object(tmod.socketio, 'emit'):
            old = tmod._last_heartbeat
            tmod._emit_status(999, 'transcribing', stage='test')
            assert tmod._last_heartbeat >= old


class TestWorkerGeneration:
    """Verify generation-based worker replacement."""

    def test_old_generation_worker_exits(self):
        """A worker loop with a stale generation should exit without blocking."""
        original_gen = tmod._worker_generation
        tmod._worker_generation = 99
        try:
            mock_app = MagicMock()
            t = threading.Thread(target=tmod._worker_loop, args=(mock_app, 0))
            t.daemon = True
            t.start()
            t.join(timeout=2)
            assert not t.is_alive(), 'Worker with stale generation should exit'
        finally:
            tmod._worker_generation = original_gen


class TestWatchdogTimeout:
    """Verify the watchdog timeout configuration."""

    def test_default_timeout(self, app):
        timeout = tmod._get_watchdog_timeout(app)
        assert timeout == 7200

    def test_minimum_enforced(self, app):
        """Timeout cannot be set below 300 seconds."""
        with app.app_context():
            from app.models.setting import Setting
            Setting.set('transcription_timeout_secs', '10')
        timeout = tmod._get_watchdog_timeout(app)
        assert timeout == 300

    def test_custom_timeout(self, app):
        with app.app_context():
            from app.models.setting import Setting
            Setting.set('transcription_timeout_secs', '3600')
        timeout = tmod._get_watchdog_timeout(app)
        assert timeout == 3600
