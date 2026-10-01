"""Secret key persistence and port resolution."""

import os
import threading
import time

from app.config import resolve_secret_key
from app.ports import (
    DEFAULT_BACKEND_PORT,
    DEFAULT_SUPERVISOR_PORT,
    allowed_origins,
    backend_port,
    supervisor_port,
    supervisor_url,
)


class TestSecretKey:
    def test_generates_and_persists_on_first_run(self, tmp_path, monkeypatch):
        monkeypatch.delenv('SECRET_KEY', raising=False)
        first = resolve_secret_key(str(tmp_path))
        assert first
        assert first != 'pine-unset'
        assert (tmp_path / 'secret_key').read_text(encoding='utf-8').strip() == first

    def test_same_key_on_the_next_start(self, tmp_path, monkeypatch):
        monkeypatch.delenv('SECRET_KEY', raising=False)
        assert resolve_secret_key(str(tmp_path)) == resolve_secret_key(str(tmp_path))

    def test_two_installations_do_not_share_a_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv('SECRET_KEY', raising=False)
        a = resolve_secret_key(str(tmp_path / 'a'))
        b = resolve_secret_key(str(tmp_path / 'b'))
        assert a != b

    def test_environment_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv('SECRET_KEY', 'from-the-environment')
        assert resolve_secret_key(str(tmp_path)) == 'from-the-environment'
        assert not (tmp_path / 'secret_key').exists()

    def test_racing_starts_agree_on_one_key(self, tmp_path, monkeypatch):
        """Both processes must end up using the key that is actually on disk."""
        monkeypatch.delenv('SECRET_KEY', raising=False)
        keys = []
        start = threading.Barrier(6)

        def resolve():
            start.wait(timeout=5)
            keys.append(resolve_secret_key(str(tmp_path)))

        threads = [threading.Thread(target=resolve) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        on_disk = (tmp_path / 'secret_key').read_text(encoding='utf-8').strip()
        assert set(keys) == {on_disk}

    def test_a_start_that_finds_an_empty_key_file_waits_for_the_winner(self, tmp_path, monkeypatch):
        """The same race as above, pinned without depending on the scheduler.

        An empty secret_key is what the winner of the race leaves behind for
        the moment between creating the file and writing to it.  A start that
        arrives inside that window used to read nothing and walk off with its
        own key; it has to wait for the key that is actually being published.
        """
        monkeypatch.delenv('SECRET_KEY', raising=False)
        path = tmp_path / 'secret_key'
        path.write_text('', encoding='utf-8')

        def finish_the_write():
            time.sleep(0.05)
            path.write_text('the-winners-key', encoding='utf-8')

        writer = threading.Thread(target=finish_the_write)
        writer.start()
        try:
            assert resolve_secret_key(str(tmp_path)) == 'the-winners-key'
        finally:
            writer.join()

    def test_unwritable_dir_still_yields_a_key(self, tmp_path, monkeypatch):
        """A read-only install must not stop the app from starting."""
        monkeypatch.delenv('SECRET_KEY', raising=False)

        def deny(*args, **kwargs):
            raise OSError('read-only file system')

        monkeypatch.setattr(os, 'open', deny)
        monkeypatch.setattr(os, 'makedirs', deny)
        assert resolve_secret_key(str(tmp_path / 'nope'))


class TestPorts:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv('PINE_BACKEND_PORT', raising=False)
        monkeypatch.delenv('PINE_SUPERVISOR_PORT', raising=False)
        assert backend_port() == DEFAULT_BACKEND_PORT
        assert supervisor_port() == DEFAULT_SUPERVISOR_PORT

    def test_environment_override(self, monkeypatch):
        monkeypatch.setenv('PINE_BACKEND_PORT', '5173')
        assert backend_port() == 5173
        assert allowed_origins() == [
            'http://pine.localhost:5173',
            'http://127.0.0.1:5173',
        ]

    def test_reads_the_environment_every_call(self, monkeypatch):
        """The onboarding handoff rewrites the supervisor port mid-session."""
        monkeypatch.setenv('PINE_SUPERVISOR_PORT', '5001')
        assert supervisor_port() == 5001
        monkeypatch.setenv('PINE_SUPERVISOR_PORT', '5099')
        assert supervisor_port() == 5099
        assert supervisor_url('status') == 'http://127.0.0.1:5099/status'

    def test_garbage_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv('PINE_BACKEND_PORT', 'not-a-port')
        assert backend_port() == DEFAULT_BACKEND_PORT

    def test_empty_value_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv('PINE_BACKEND_PORT', '')
        assert backend_port() == DEFAULT_BACKEND_PORT
