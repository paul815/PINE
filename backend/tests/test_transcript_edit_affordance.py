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
    # ...the second click must not seek again...
    assert re.search(r'if \(e\.detail >= 2\) return;', source)
    # ...and the first one, which already did, is taken back.
    assert 'function undoAccidentalSeek(' in source
    assert 'undoAccidentalSeek();' in source


def test_the_undone_seek_restores_position_and_pause():
    """seekTo() also starts playing, so putting the time back is not enough."""
    source = _source()

    snapshot = re.search(r'playbackBeforeEdit = mediaEl\s*\?\s*\{([^}]*)\}', source)

    assert snapshot, 'nothing remembers what playback was doing before the seek'
    for field in ('time:', 'paused:', 'at:'):
        assert field in snapshot.group(1)
    assert 'if (before.paused && !mediaEl.paused) togglePlay();' in source


def test_the_pencil_is_gone_from_every_paragraph():
    """One way in per paragraph, not two: the button, its wiring and its styles.

    The pencil itself is not gone from the screen -- it moved to the toolbar as
    one control for the whole transcript.
    """
    source = _source()

    assert 'utt-edit' not in source
    assert 'Edit this paragraph' not in source
    assert 'onclick="startBlockEdit(' not in source


# ── edit mode ────────────────────────────────────────────────────────────────


def test_the_toolbar_carries_the_edit_toggle():
    source = _source()

    button = re.search(r'<button[^>]*id="editModeBtn".*?</button>', source, re.S)

    assert button, 'the toolbar has no edit toggle'
    assert 'onclick="toggleEditMode()"' in button.group(0)
    # A toggle says which way it is set, to a reader and to a screen reader.
    assert 'aria-pressed' in button.group(0)
    assert 'Ctrl twice' in button.group(0), 'the shortcut is only discoverable from here'


def test_find_and_replace_belongs_to_edit_mode():
    """It is a correction tool, so it is out only while corrections are being
    made — under the Edit button, not beside it in the toolbar."""
    source = _source()

    tools = re.search(r'<div class="edit-tools".*?</div>', source, re.S)

    assert tools, 'the Edit button has no group to drop anything out of'
    assert 'id="editModeBtn"' in tools.group(0)
    assert 'id="findReplaceBtn"' in tools.group(0)
    assert 'Find&amp;Replace' in tools.group(0)
    assert 'openFindReplace()' in tools.group(0)
    # Hidden until the mode is on, and positioned below the button.
    assert '.edit-tools .edit-subaction {\n  display: none;' in source
    assert '.edit-tools.open .edit-subaction { display: inline-flex; }' in source
    assert re.search(r'\.edit-tools \.edit-subaction \{[^}]*top: calc\(100% \+ 6px\)', source, re.S)
    assert "getElementById('editTools').classList.toggle('open', editMode)" in source


def test_edit_mode_shows_its_state():
    source = _source()

    assert "btn.classList.toggle('on', editMode)" in source
    assert "classList.toggle('editing', editMode)" in source
    assert '.transcript-panel.editing' in source
    # The cursor stops offering to navigate before anything is clicked.
    assert re.search(r'\.transcript-panel\.editing[^{]*\.word \{[^}]*cursor: text', source)


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


def test_a_click_in_edit_mode_opens_the_paragraph_instead_of_seeking():
    click = _function_body(_source(), 'onTranscriptClick')

    branch = re.search(r'if \(editMode\) \{(.*?)\n  \}', click, re.S)

    assert branch, 'onTranscriptClick does not branch on the mode'
    assert 'startBlockEdit(textEl, aim.caret)' in branch.group(1)
    # And it leaves before the seek below it can run.
    assert 'return;' in branch.group(1)


def test_the_aim_survives_the_transcript_being_rebuilt():
    """Closing a paragraph can run a refresh that replaces every node, so the
    target is remembered by the segments it holds, not by the element."""
    source = _source()
    down = _function_body(source, 'onTranscriptPointerDown')
    click = _function_body(source, 'onTranscriptClick')

    assert 'indices: utt.dataset.indices' in down
    assert 'uttByIndices(aim.indices)' in click


def test_the_caret_is_measured_on_the_way_down():
    """The open paragraph blurs between pointerdown and click.

    Closing it puts its markup back and reflows everything under it, so
    coordinates read at click time point at different words — the caret kept
    landing at the start of the next paragraph instead of where it was aimed.
    """
    source = _source()
    down = _function_body(source, 'onTranscriptPointerDown')
    click = _function_body(source, 'onTranscriptClick')

    assert 'caretOffsetFromPoint' in down, 'the aim is not taken on pointerdown'
    assert 'caretOffsetFromPoint' not in click, 'measuring again at click time is too late'
    assert 'aimedAtOnPointerDown' in click


def test_the_transcript_is_not_rebuilt_under_an_open_paragraph():
    """Saving one paragraph refreshes the transcript, and in edit mode that
    refresh lands while the next paragraph is already open."""
    refresh = _function_body(_source(), 'refreshTranscript')

    assert 'if (editingBlock) {' in refresh
    assert 'transcriptRefreshPending = true;' in refresh
    # ...and it is not simply dropped: it runs when the caret leaves.
    close = _function_body(_source(), 'closeBlockEdit')
    assert 'if (transcriptRefreshPending) refreshTranscript();' in close


def test_moving_between_paragraphs_is_one_click():
    """The click that closes one paragraph goes on to open the next."""
    source = _source()

    assert 'if (!editMode) return;' in source


def test_the_mode_does_not_offer_codes():
    source = _source()

    assert 'if (editingBlock || editMode) return;' in source


def test_ctrl_twice_toggles_the_mode_without_swallowing_real_shortcuts():
    source = _source()

    assert 'modifierUsedWithAnotherKey' in source, 'nothing separates a tap from a combo'
    assert 'if (e.ctrlKey || e.metaKey) modifierUsedWithAnotherKey = true;' in source
    assert "e.key === 'Meta'" in source, 'a Mac types the chord on Command'
    assert 'toggleEditMode();' in source


def test_escape_leaves_the_paragraph_before_the_mode():
    """Two presses, two steps out -- not both on the first."""
    source = _source()

    block_escape = re.search(r"function onBlockKeydown.*?\n\}", source, re.S).group(0)

    assert 'e.stopPropagation();' in block_escape
    assert re.search(r'if \(editMode\) \{\s*setEditMode\(false\);', source)


def _player_keydown_handler(source):
    """The document keydown listener that owns Space and the arrows."""
    for match in re.finditer(r"document\.addEventListener\('keydown'", source):
        depth, i, start = 0, source.index('{', match.start()), match.start()
        while i < len(source):
            if source[i] == '{':
                depth += 1
            elif source[i] == '}':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = source[start:i + 1]
        if 'togglePlay()' in body:
            return body
    raise AssertionError('no keydown listener drives the player')


def test_play_pause_survives_inside_an_open_paragraph():
    """Space types there, so the player keeps a copy under the modifier."""
    handler = _player_keydown_handler(_source())

    modified = re.search(
        r"if \(\(e\.ctrlKey \|\| e\.metaKey\)[^{]*Space'\)\) \{(.*?)\n  \}", handler, re.S)

    assert modified, 'play/pause has no shortcut that reaches into an open paragraph'
    assert 'togglePlay();' in modified.group(1)
    # ...and it is read before the handler hands the plain keys to typing.
    assert handler.index(modified.group(0)) < handler.index('isContentEditable')
    # Ctrl+Arrow stays with the text, where it moves by word.
    assert not re.search(r"(?:ctrlKey|metaKey)[^\n]*ArrowLeft", handler)
