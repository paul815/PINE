"""The launchers are line-ending sensitive; this is the regression guard.

An LF-only WIN_Install.bat makes cmd.exe fail its label lookup: the visible
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


def test_every_launcher_script_has_the_ending_its_shell_needs():
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): problem
        for path in iter_target_files(REPO_ROOT)
        if (problem := check_file(path)) is not None
    }

    assert offenders == {}, f"wrong line endings: {offenders}"


def test_windows_installer_is_crlf():
    data = (REPO_ROOT / 'WIN_Install.bat').read_bytes()

    assert data.count(b'\n') - data.count(b'\r\n') == 0
    assert data.count(b'\r\n') > 0


def test_macos_installer_is_lf():
    data = (REPO_ROOT / 'MAC_Install.command').read_bytes()

    assert data.count(b'\r\n') == 0


def test_gitattributes_pins_both_and_stays_in_the_repo_root():
    attributes = (REPO_ROOT / '.gitattributes').read_text(encoding='utf-8')

    assert '*.bat     text eol=crlf' in attributes
    assert '*.command text eol=lf' in attributes

    # The installers tidy the root on first run. .gitattributes must not be part
    # of that sweep: without it every .bat checks out as LF again.
    windows_installer = (REPO_ROOT / 'WIN_Install.bat').read_text(encoding='utf-8')
    macos_installer = (REPO_ROOT / 'MAC_Install.command').read_text(encoding='utf-8')

    assert 'for %%F in (AGENTS.md LICENSE .editorconfig .gitignore' in windows_installer
    assert 'for f in AGENTS.md LICENSE .editorconfig .gitignore' in macos_installer
    assert 'relocate_root_file ".gitattributes"' not in windows_installer
    assert 'relocate_root_file ".gitattributes"' not in macos_installer


def test_checker_covers_both_shells():
    assert REQUIRED_ENDINGS['.bat'] == 'crlf'
    assert REQUIRED_ENDINGS['.cmd'] == 'crlf'
    assert REQUIRED_ENDINGS['.command'] == 'lf'
    assert REQUIRED_ENDINGS['.sh'] == 'lf'
