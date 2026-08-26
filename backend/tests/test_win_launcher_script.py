"""Structural guards for the .bat that `Launch Pine` actually runs.

The launcher is a 400-line batch script generated from Python, and nothing in
CI can execute it: it wants Windows, a venv, a supervisor and a browser. So the
properties that used to break silently are pinned here instead, by reading the
generated text.

Silently is the operative word. cmd.exe does not fail loudly — a call to a
label that does not exist, a wait that returns instantly, a probe pointed at
the wrong port all just carry on and produce a launch that "did not work".
"""

import json
import os
import re
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest
import supervisor

from app.ports import DEFAULT_BACKEND_PORT, DEFAULT_SUPERVISOR_PORT
from app.services.launcher_layout import _windows_app_launcher_contents


@pytest.fixture(scope='module')
def script():
    return _windows_app_launcher_contents()


def _defined_labels(text):
    return {m.group(1) for m in re.finditer(r'^:([A-Za-z_]\w*)\s*$', text, re.M)}


def _code_only(text):
    """The script with its REM lines dropped, so comments cannot satisfy a check."""
    return '\n'.join(
        line for line in text.splitlines()
        if not line.strip().upper().startswith('REM ')
    )


def _referenced_labels(text):
    refs = set()
    for m in re.finditer(r'\bcall\s+:([A-Za-z_]\w*)', text):
        refs.add(m.group(1))
    for m in re.finditer(r'\bgoto\s+:?([A-Za-z_]\w*)', text):
        refs.add(m.group(1))
    return refs - {'eof'}


# ── The label graph ──────────────────────────────────────────────────────────

def test_every_label_it_calls_exists(script):
    """A call to a missing label is how the installer bug closed the window."""
    missing = _referenced_labels(script) - _defined_labels(script)

    assert missing == set()


def test_no_label_is_left_unreachable(script):
    """:open_browser_fallback sat here for a release, defined and never called."""
    orphans = _defined_labels(script) - _referenced_labels(script)

    assert orphans == set()


# ── Waiting ──────────────────────────────────────────────────────────────────

def test_nothing_waits_with_timeout(script):
    """`timeout /t` returns at once when stdin is not a console.

    The hidden instance runs with its stdio redirected into a log, so every
    loop built on `timeout` spun instead of waiting and reported a timeout in
    milliseconds. WIN_Install.bat documents the same trap.
    """
    assert 'timeout /t' not in _code_only(script)
    assert 'ping -n 2 127.0.0.1' in script


def test_the_one_powershell_sleep_is_the_sub_second_one(script):
    """ping cannot go below a second; anything coarser has no excuse."""
    sleeps = re.findall(r'Start-Sleep[^"]*', script)

    assert sleeps == ['Start-Sleep -Milliseconds 300']


# ── Ports ────────────────────────────────────────────────────────────────────

def test_the_defaults_come_from_app_ports(script):
    """One source for the numbers, so a change here cannot be half-applied."""
    assert f'set "PINE_BACKEND_PORT={DEFAULT_BACKEND_PORT}"' in script
    assert f'set "PINE_SUP_PORT={DEFAULT_SUPERVISOR_PORT}"' in script
    assert '@BACKEND_PORT@' not in script
    assert '@SUPERVISOR_PORT@' not in script


def test_every_supervisor_call_uses_the_resolved_port(script):
    """The supervisor scans upward when its port is busy and records what it got.

    A hardcoded 127.0.0.1:5001 probes whatever else is listening there, decides
    no supervisor is running, and starts a second one.
    """
    hardcoded = re.findall(r'127\.0\.0\.1:(\d+)', script)

    assert hardcoded == []
    assert '127.0.0.1:!PINE_SUP_PORT!' in script


def test_the_ports_are_read_from_the_file_the_supervisor_writes(script):
    assert 'supervisor.port' in script
    assert 'resolve_ports' in _defined_labels(script)
    # Reading it once per launch, not once per loop iteration.
    assert 'if defined PINE_PORTS_RESOLVED goto :eof' in script


# ── Talking to the supervisor ────────────────────────────────────────────────

def test_every_supervisor_post_carries_the_token(script):
    """Supervisor POSTs are token-gated; a launcher without one gets 403.

    That is what left a stale supervisor running: /shutdown was refused, so the
    launch could neither clean it up nor start a working one in its place.
    """
    posts = re.findall(r"method='POST'.*?\)", script)

    assert len(posts) == 2, posts
    for post in posts:
        assert "'X-Pine-Supervisor-Token'" in post


def test_the_token_comes_from_the_port_file_through_the_environment(script):
    """Not through argv, where a process list would show it."""
    assert "get('token','')" in script
    assert "os.environ.get^('PINE_SUP_TOKEN', ''^)" in script
    assert 'PINE_SUP_TOKEN=%%p' in script


# ── The browser ──────────────────────────────────────────────────────────────

def test_the_browser_opens_the_apps_own_origin(script):
    """pine.localhost is the app's origin; 127.0.0.1 is a second, empty one.

    The installer opens the named host, so a launcher opening the numeric one
    hands the user a fresh copy of every UI preference.
    """
    urls = re.findall(r'call :open_browser\S* "(http://[^"]+)"', script)

    assert urls == ['http://pine.localhost:!PINE_BACKEND_PORT!/']


def test_a_failed_browser_open_falls_through_to_the_fallbacks(script):
    open_browser = script.split(':open_browser\n', 1)[1].split('\n:', 1)[0]

    assert 'call :open_browser_fallback' in open_browser


# ── Batch mechanics ──────────────────────────────────────────────────────────

def test_no_comment_separates_a_command_from_its_errorlevel_test(script):
    """`if errorlevel` reads the last command's result — keep them adjacent.

    A REM in between reads as if it belongs to the check, and the next person
    to add a line there will put a real command in the gap and silently break
    the test. Cheap to forbid, expensive to debug.
    """
    lines = [line.strip() for line in script.splitlines()]
    offenders = [
        (i, lines[i - 1])
        for i, line in enumerate(lines)
        if i and re.match(r'if (not )?errorlevel', line)
        and lines[i - 1].upper().startswith('REM')
    ]

    assert offenders == []


def test_delayed_expansion_is_on(script):
    """Everything above is written with !VAR!, which is inert without this."""
    assert 'setlocal EnableDelayedExpansion' in script


def test_the_script_is_written_with_crlf(tmp_path, monkeypatch):
    """An LF .bat loses cmd's byte offsets and label lookup fails outright."""
    from app.services import launcher_layout

    monkeypatch.setattr(launcher_layout, 'IS_MAC', False)
    (tmp_path / 'backend').mkdir()
    launcher_layout._promote_platform_launcher(repo_root=str(tmp_path))

    written = (tmp_path / 'backend' / 'Launch Pine.bat').read_bytes()

    assert written.count(b'\n') == written.count(b'\r\n')
    assert written.count(b'\r\n') > 0


# ── The python one-liners, actually run ──────────────────────────────────────
#
# Everything above reads the script as text. These two run the parts of it that
# are real code, because the failure mode they guard is invisible to a substring
# check: the payloads are written with cmd's escapes (^( ^) ^>) and reach python
# only after cmd strips them. Get one wrong and the launcher hands python a
# SyntaxError, swallows it with 2>nul, and carries on as if the call had failed
# for some ordinary reason.

_TOKEN = 'tok-launcher-test'


def _cmd_unescape(snippet, supervisor_port):
    """What cmd.exe hands python, from what the .bat holds."""
    for escaped, plain in (('^(', '('), ('^)', ')'), ('^>', '>'), ('^&', '&')):
        snippet = snippet.replace(escaped, plain)
    return snippet.replace('!PINE_SUP_PORT!', str(supervisor_port))


def _one_liners(script, supervisor_port):
    return [_cmd_unescape(m, supervisor_port)
            for m in re.findall(r'python -c "(.*?)"', script)]


class _SupervisorStub:
    """A supervisor whose backend has died — the state the launcher recovers from."""

    _backend_port = 5100
    _supervisor_port = 5101

    def __init__(self):
        self.commands = []

    def backend_running(self):
        return False

    def backend_ready(self):
        return False

    def lease_count(self):
        return 0

    def restart_backend(self):
        self.commands.append('restart')

    def request_shutdown(self, **_kwargs):
        self.commands.append('shutdown')


@pytest.fixture
def live_supervisor(tmp_path, monkeypatch):
    """A real supervisor HTTP server plus the port file it publishes."""
    monkeypatch.setattr(supervisor, 'SUPERVISOR_TOKEN', _TOKEN)
    monkeypatch.setattr(supervisor, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(supervisor, 'PORT_FILE_PATH', tmp_path / 'supervisor.port')

    stub = _SupervisorStub()
    server = ThreadingHTTPServer(
        ('127.0.0.1', 0), supervisor.make_supervisor_handler(stub, threading.Event()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    supervisor._write_port_file(server.server_address[1], 5100)
    try:
        yield stub, server.server_address[1], tmp_path / 'supervisor.port'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _run(code, port_file, token):
    return subprocess.run(
        [sys.executable, '-c', code],
        env={**os.environ, 'PINE_PORT_FILE': str(port_file), 'PINE_SUP_TOKEN': token},
        capture_output=True, text=True, timeout=30,
    )


def test_the_port_file_readers_return_what_the_supervisor_published(script, live_supervisor):
    _stub, port, port_file = live_supervisor
    published = json.loads(port_file.read_text(encoding='utf-8'))
    readers = [c for c in _one_liners(script, port) if 'PINE_PORT_FILE' in c]

    assert len(readers) == 3, 'ports and token, one read each'
    printed = [_run(c, port_file, _TOKEN).stdout.strip() for c in readers]

    assert printed == [str(published['backend_port']),
                       str(published['supervisor_port']),
                       published['token']]


def test_the_posts_command_the_supervisor_when_they_carry_the_token(script, live_supervisor):
    stub, port, port_file = live_supervisor
    posts = [c for c in _one_liners(script, port) if "method='POST'" in c]

    for code in posts:
        assert _run(code, port_file, _TOKEN).returncode == 0

    assert sorted(stub.commands) == ['restart', 'shutdown']


def test_the_same_posts_are_refused_without_it(script, live_supervisor):
    """Proves the calls are really authenticated, not merely accompanied by a header."""
    stub, port, port_file = live_supervisor
    posts = [c for c in _one_liners(script, port) if "method='POST'" in c]

    for code in posts:
        result = _run(code, port_file, '')
        assert result.returncode != 0
        assert '403' in result.stderr

    assert stub.commands == []
