"""The launchers are line-ending sensitive; this is the regression guard.

An LF-only Setup_WIN.bat makes cmd.exe fail its label lookup: the visible
window dies at "call :open_browser", closes without a message, and the browser
never opens. A CRLF .command breaks the macOS launcher the same way.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / 'backend' / 'scripts'))

from check_line_endings import (  # noqa: E402
    REQUIRED_ENDINGS,
    check_file,
    iter_target_files,
)

sys.path.insert(0, str(REPO_ROOT / 'backend'))

from app.services.launcher_layout import RELOCATED_ROOT_FILES  # noqa: E402


def test_every_launcher_script_has_the_ending_its_shell_needs():
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): problem
        for path in iter_target_files(REPO_ROOT)
        if (problem := check_file(path)) is not None
    }

    assert offenders == {}, f"wrong line endings: {offenders}"


def test_windows_installer_is_crlf():
    data = (REPO_ROOT / 'Setup_WIN.bat').read_bytes()

    assert data.count(b'\n') - data.count(b'\r\n') == 0
    assert data.count(b'\r\n') > 0


def test_macos_installer_is_lf():
    data = (REPO_ROOT / 'Setup_MAC.command').read_bytes()

    assert data.count(b'\r\n') == 0


def test_gitattributes_pins_both_and_stays_in_the_repo_root():
    attributes = (REPO_ROOT / '.gitattributes').read_text(encoding='utf-8')

    assert '*.bat     text eol=crlf' in attributes
    assert '*.command text eol=lf' in attributes

    # The finished install sweeps dev files out of the root. .gitattributes must
    # not be part of that sweep: without it every .bat checks out as LF again.
    # Read the sweep list rather than pinning its names -- it grows as root files
    # are added, and only one thing about it is load-bearing here.
    swept = {name for name, _destination in RELOCATED_ROOT_FILES}
    assert 'LICENSE' in swept
    assert '.gitattributes' not in swept


def test_checker_covers_both_shells():
    assert REQUIRED_ENDINGS['.bat'] == 'crlf'
    assert REQUIRED_ENDINGS['.cmd'] == 'crlf'
    assert REQUIRED_ENDINGS['.command'] == 'lf'
    assert REQUIRED_ENDINGS['.sh'] == 'lf'
