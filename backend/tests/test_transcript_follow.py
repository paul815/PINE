"""How the transcript follows playback, and why a click does not move it.

Clicking a word used to re-centre its paragraph, and in a dialogue it often
centred the wrong one: paragraphs were timed from their own start to the next
one's, but an interrupted speaker resumes in the paragraph above the
interjection, so a fifth of a dialogue's words belonged, by that clock, to the
paragraph underneath them. The text jumped down on the click and back up when
playback caught up.

The playing paragraph now comes from the segments' own times, and the text
moves only when that paragraph has left the screen.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / 'templates' / 'recording.html'


def _source():
    return TEMPLATE.read_text(encoding='utf-8')


def _function_body(source, name):
    """The source of `function <name>(...) { ... }`, by brace matching."""
    start = source.index(f'function {name}(')
    depth, i = 0, source.index('{', start)
    while i < len(source):
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1
    raise AssertionError(f'{name} has unbalanced braces')


def test_a_click_on_a_word_leaves_the_text_where_it_is():
    click = _function_body(_source(), 'onTranscriptClick')

    # The paragraph that was clicked, not whichever one the clock picks.
    assert "[...document.querySelectorAll('.utt')].indexOf(utt)" in click
    assert 'seekTo(secs, { utt: clicked, skipScroll: true })' in click


def test_the_playing_paragraph_is_timed_by_its_segments():
    source = _source()
    render = _function_body(source, 'renderTranscript')
    update = _function_body(source, 'onTimeUpdate')

    assert 'playSpans.push(' in render
    assert 'uttIndexAt(t, lastPlayingIdx)' in update
    # The rule this replaced: a paragraph runs until the next one starts.
    assert 'nextElementSibling' not in update


def test_playback_moves_the_text_only_when_the_paragraph_is_off_screen():
    source = _source()

    assert 'if (playing && !uttInView(playing)) scrollUttIntoView(playing);' in \
        _function_body(source, 'onTimeUpdate')
    assert '!uttInView(target)' in _function_body(source, 'seekTo')


def test_a_seek_settles_the_playing_paragraph_itself():
    """Otherwise the next time update sees a change and scrolls anyway."""
    seek = _function_body(_source(), 'seekTo')

    assert 'lastPlayingIdx = idx;' in seek


# ── the rule itself, run under node ──────────────────────────────────────────

# A asks a question and is cut off by B's "yes" at 4 s; A finishes the question
# in their own paragraph, which is drawn above B's; C answers from 10 s.
_SPANS = [
    {'start': 0.0, 'end': 4.0, 'utt': 0},   # A, before the interruption
    {'start': 4.0, 'end': 4.6, 'utt': 1},   # B, the interjection
    {'start': 4.8, 'end': 9.0, 'utt': 0},   # A, resuming
    {'start': 10.0, 'end': 14.0, 'utt': 2},  # C
]

_CASES = [
    # (t, current, expected)
    (-1.0, -1, -1),   # before anyone speaks
    (2.0, -1, 0),
    (4.2, 0, 1),      # the interjection takes it...
    (5.0, 1, 0),      # ...and the resumed sentence takes it back
    (4.7, 1, 1),      # the beat of silence between them stays with who spoke last
    (6.0, -1, 0),     # a word clicked after the interjection is A's, not B's
    (9.5, 0, 0),      # the pause before the answer
    (12.0, 0, 2),
    (20.0, 2, 2),     # past the last words
]


@pytest.mark.skipif(not shutil.which('node'), reason='node is not installed')
def test_the_paragraph_at_a_time_follows_whoever_is_speaking(tmp_path):
    source = _source()
    rule = _function_body(source, 'uttIndexAt')

    script = tmp_path / 'rule.js'
    script.write_text(
        f'const playSpans = {json.dumps(_SPANS)};\n{rule}\n'
        f'const cases = {json.dumps(_CASES)};\n'
        'process.stdout.write(JSON.stringify(cases.map(([t, cur]) => uttIndexAt(t, cur))));',
        encoding='utf-8')
    result = subprocess.run([shutil.which('node'), str(script)], capture_output=True,
                            text=True, encoding='utf-8', timeout=30)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [expected for _, _, expected in _CASES]


@pytest.mark.skipif(not shutil.which('node'), reason='node is not installed')
def test_two_voices_at_once_do_not_flick_the_highlight(tmp_path):
    """While both are covered, whichever holds the highlight keeps it."""
    rule = _function_body(_source(), 'uttIndexAt')
    spans = [{'start': 0, 'end': 10, 'utt': 0}, {'start': 3, 'end': 6, 'utt': 1}]

    script = tmp_path / 'overlap.js'
    script.write_text(
        f'const playSpans = {json.dumps(spans)};\n{rule}\n'
        'process.stdout.write(JSON.stringify([uttIndexAt(4, 0), uttIndexAt(4, 1), uttIndexAt(4, -1)]));',
        encoding='utf-8')
    result = subprocess.run([shutil.which('node'), str(script)], capture_output=True,
                            text=True, encoding='utf-8', timeout=30)

    assert result.returncode == 0, result.stderr
    # With nobody holding it, the voice that came in last has the floor.
    assert json.loads(result.stdout) == [0, 1, 1]
