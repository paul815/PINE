"""How a paragraph is opened for editing.

A double-click on the text opens it. That replaced a pencil button beside the
speaker name, and the switch has two halves that have to stay together: the
handler that opens the editor, and the suppression of everything else a
double-click would otherwise set off — the browser selects a word, which used
to raise the tag popup, and the first of the two clicks used to seek the audio.

These are structural checks on the template. The behaviour itself is a browser
interaction and is verified by opening the page.
"""

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / 'templates' / 'recording.html'


def _source():
    return TEMPLATE.read_text(encoding='utf-8')


def test_the_transcript_panel_listens_for_a_double_click():
    source = _source()

    panel = re.search(r'<div class="transcript-panel"[^>]*>', source)

    assert panel, 'the transcript panel is gone'
    assert 'ondblclick="onTranscriptDblClick(event)"' in panel.group(0)
    # The click count is read on the way down; by pointerup it is no longer there.
    assert 'onpointerdown="onTranscriptPointerDown(event)"' in panel.group(0)


def test_the_double_click_opens_the_editor_where_it_was_aimed():
    source = _source()

    assert 'function onTranscriptDblClick(' in source
    assert 'startBlockEdit(textEl, caret)' in source, \
        'the editor must open at the clicked character, not at the end'
    assert 'function caretOffsetFromPoint(' in source


def test_a_double_click_does_not_tag_or_seek():
    """Both were what a double-click used to do, and both are wrong now."""
    source = _source()

    # The word the browser selected must not raise the tag popup...
    assert 'if (openingEditorByDoubleClick)' in source
    # ...and the first click must not move the audio.
    assert re.search(r'if \(e\.detail >= 2\) return;', source)


def test_the_pencil_is_gone():
    """One way in, not two: the button, its handler wiring and its styles."""
    source = _source()

    assert 'utt-edit' not in source
    assert 'Edit this paragraph' not in source
    assert 'onclick="startBlockEdit(' not in source
