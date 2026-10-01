"""One allowlist drives all three resets, and the scripts cannot re-copy it.

backend/pyproject.toml was created for audit item P3-1 and added to none of the
three hand-maintained copies of this list, so the next reset deleted the ruff
config outright. The root lists had drifted too: the in-app reset was missing
.github and README.md that the scripts kept. These tests fail if any of that
comes back.
"""

import os
import re
import shutil
import subprocess
import sys

import pytest

from app.api.settings import (
    BACKEND_PRESERVE_LIST,
    PRESERVE_LIST_DIR,
    ROOT_PRESERVE_LIST,
    load_preserve_list,
)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESET_BAT = os.path.join(BACKEND_DIR, 'reset_win.bat')
RESET_COMMAND = os.path.join(BACKEND_DIR, 'reset.command')

# Two or more quoted literals in a row inside `in (...)`, none of them a %VAR%:
# that is a hand-written list, not a read of the allowlist file.
INLINE_BAT_LIST = re.compile(r'in \(\s*(?:"[^"%\n]+"\s+)+"[^"%\n]+"\s*\)')
# `ROOT_PRESERVE=(` followed by anything other than an immediate `)`.
INLINE_SH_ARRAY = re.compile(r'PRESERVE=\(\s*[^)\s]')

# Without these the app will not start or lint; a reset that drops one leaves an
# install that cannot be repaired from inside the app.
BACKEND_ESSENTIALS = {
    'app',
    'ml_worker',
    'pyproject.toml',
    'pytest.ini',
    'requirements-lock.txt',
    'requirements.txt',
    'run.py',
    'scripts',
    'supervisor.py',
    'templates',
    'tests',
    'tools',
}
ROOT_ESSENTIALS = {
    '.git',
    '.github',
    '.gitignore',
    'LICENSE',
    'README.md',
    'backend',
    'documentation',
}
# Recreated on the next run, or deliberately wiped by the reset.
NEVER_PRESERVED = {'.venv', 'backups', 'data', 'logs', 'projects'}


def read_script(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read()


class TestTheListsThemselves:
    def test_both_lists_load(self):
        assert len(load_preserve_list(ROOT_PRESERVE_LIST)) >= 8
        assert len(load_preserve_list(BACKEND_PRESERVE_LIST)) >= 8

    def test_backend_list_keeps_what_running_and_linting_need(self):
        assert BACKEND_ESSENTIALS <= load_preserve_list(BACKEND_PRESERVE_LIST)

    def test_root_list_keeps_the_repo_itself(self):
        assert ROOT_ESSENTIALS <= load_preserve_list(ROOT_PRESERVE_LIST)

    def test_user_data_is_never_preserved(self):
        both = load_preserve_list(ROOT_PRESERVE_LIST) | load_preserve_list(BACKEND_PRESERVE_LIST)
        assert not (NEVER_PRESERVED & both)

    def test_the_lists_survive_the_reset_that_reads_them(self):
        # They live in backend/tools/, so 'tools' has to stay on the backend list
        # or the second reset would run without an allowlist at all.
        assert os.path.basename(PRESERVE_LIST_DIR) == 'tools'
        assert 'tools' in load_preserve_list(BACKEND_PRESERVE_LIST)

    def test_a_truncated_list_is_refused(self, tmp_path, monkeypatch):
        short = tmp_path / 'reset_preserve_backend.txt'
        short.write_text('# nearly empty\napp\nrun.py\n', encoding='utf-8')
        monkeypatch.setattr('app.api.settings.PRESERVE_LIST_DIR', str(tmp_path))
        with pytest.raises(RuntimeError, match='truncated'):
            load_preserve_list('reset_preserve_backend.txt')

    def test_a_missing_list_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr('app.api.settings.PRESERVE_LIST_DIR', str(tmp_path))
        with pytest.raises(OSError):
            load_preserve_list('reset_preserve_backend.txt')

    def test_comments_and_blanks_are_ignored(self, tmp_path, monkeypatch):
        listing = tmp_path / 'reset_preserve_backend.txt'
        listing.write_text(
            '# a comment\n\napp  \nml_worker\nrun.py\nsupervisor.py\n'
            'templates\ntests\ntools  # trailing note\nscripts\n',
            encoding='utf-8',
        )
        monkeypatch.setattr('app.api.settings.PRESERVE_LIST_DIR', str(tmp_path))
        assert load_preserve_list('reset_preserve_backend.txt') == {
            'app',
            'ml_worker',
            'run.py',
            'supervisor.py',
            'templates',
            'tests',
            'tools',
            'scripts',
        }


class TestScriptsShareTheLists:
    def test_both_scripts_read_both_lists(self):
        for path in (RESET_BAT, RESET_COMMAND):
            text = read_script(path)
            assert ROOT_PRESERVE_LIST in text, f'{path} does not read {ROOT_PRESERVE_LIST}'
            assert BACKEND_PRESERVE_LIST in text, f'{path} does not read {BACKEND_PRESERVE_LIST}'

    def test_no_script_keeps_its_own_copy(self):
        # reset_win.bat has other legitimate inline lists (the launchers, the
        # dev files it moves back). Only a run of literals that are themselves
        # allowlist entries means the allowlist was copied back in.
        known = load_preserve_list(ROOT_PRESERVE_LIST) | load_preserve_list(BACKEND_PRESERVE_LIST)
        for match in INLINE_BAT_LIST.finditer(read_script(RESET_BAT)):
            literals = set(re.findall(r'"([^"%\n]+)"', match.group(0)))
            overlap = literals & known
            assert len(overlap) < 2, (
                f'reset_win.bat grew an inline allowlist again: {sorted(overlap)[:5]}'
            )
        found = INLINE_SH_ARRAY.search(read_script(RESET_COMMAND))
        assert not found, f'reset.command grew an inline array again: {found.group(0)[:80]!r}'

    def test_both_scripts_refuse_to_delete_without_a_list(self):
        for path in (RESET_BAT, RESET_COMMAND):
            assert 'nothing was deleted' in read_script(path), f'{path} has no guard'


def _bash_candidates():
    """Every bash worth trying, best guess first."""
    yield shutil.which('bash')
    if sys.platform != 'win32':
        return
    # Git for Windows ships a real bash, so locate it from git itself rather
    # than from ProgramFiles, which is not set in every environment that runs
    # this suite (a Git Bash shell is one of them).
    git = shutil.which('git')
    roots = [os.path.dirname(os.path.dirname(git))] if git else []
    roots.append(os.path.join(r'C:\Program Files', 'Git'))
    for root in roots:
        yield os.path.join(root, 'bin', 'bash.exe')
        yield os.path.join(root, 'usr', 'bin', 'bash.exe')


def _find_working_bash():
    """Path to a bash that actually runs, or None.

    Finding the name on PATH is not enough on Windows. The GitHub runner has
    C:\\Windows\\System32\\bash.exe -- the WSL launcher -- ahead of Git's bash,
    and with no distribution installed it exits 1 after printing "Use
    'wsl.exe --install <Distro>' to install." in UTF-16. shutil.which happily
    returns it, and the test then reports a broken shell as a broken parser.
    So probe each candidate and take the first that answers, which on the
    runner is the bash Git for Windows ships: the check keeps running there
    rather than skipping.
    """
    for candidate in _bash_candidates():
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            done = subprocess.run([candidate, '-c', 'exit 0'], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return candidate
    return None


BASH = _find_working_bash()


@pytest.mark.skipif(BASH is None, reason='no working bash on this machine')
class TestShellParserAgreesWithPython:
    def _read_via_bash(self, list_path):
        source = read_script(RESET_COMMAND)
        body = re.search(r'^read_preserve_list\(\) \{.*?^\}', source, re.M | re.S)
        assert body, 'read_preserve_list() not found in reset.command'
        script = body.group(0) + '\nread_preserve_list "$1"\n'
        done = subprocess.run(
            [BASH, '-s', '--', list_path],
            input=script,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert done.returncode == 0, done.stderr
        return {line.strip() for line in done.stdout.splitlines() if line.strip()}

    def test_backend_list_parses_the_same(self):
        path = os.path.join(PRESERVE_LIST_DIR, BACKEND_PRESERVE_LIST)
        assert self._read_via_bash(path) == load_preserve_list(BACKEND_PRESERVE_LIST)

    def test_root_list_parses_the_same(self):
        path = os.path.join(PRESERVE_LIST_DIR, ROOT_PRESERVE_LIST)
        assert self._read_via_bash(path) == load_preserve_list(ROOT_PRESERVE_LIST)


@pytest.mark.skipif(sys.platform != 'win32', reason='cmd.exe only')
class TestCmdParserAgreesWithPython:
    def _read_via_cmd(self, list_path, tmp_path):
        source = read_script(RESET_BAT)
        # Reuse the exact `for /f` header the script deletes by, so a change to
        # its options (eol, delims, usebackq) is caught here.
        header = re.search(r'for /f "(usebackq[^"]*)" %%K in \(', source)
        assert header, 'no allowlist read found in reset_win.bat'
        runner = tmp_path / 'read_list.bat'
        runner.write_text(
            '@echo off\r\n'
            f'for /f "{header.group(1)}" %%K in ("{list_path}") do @echo %%~K\r\n',
            encoding='ascii',
        )
        done = subprocess.run(
            ['cmd', '/c', str(runner)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert done.returncode == 0, done.stderr
        return {line.strip() for line in done.stdout.splitlines() if line.strip()}

    def test_backend_list_parses_the_same(self, tmp_path):
        path = os.path.join(PRESERVE_LIST_DIR, BACKEND_PRESERVE_LIST)
        assert self._read_via_cmd(path, tmp_path) == load_preserve_list(BACKEND_PRESERVE_LIST)

    def test_root_list_parses_the_same(self, tmp_path):
        path = os.path.join(PRESERVE_LIST_DIR, ROOT_PRESERVE_LIST)
        assert self._read_via_cmd(path, tmp_path) == load_preserve_list(ROOT_PRESERVE_LIST)
