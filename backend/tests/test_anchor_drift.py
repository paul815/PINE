"""Anchors that can be checked against the words they were taken from.

A tag or comment is anchored by position — segment index plus character offsets
— and the words it covered are stored beside it as ``anchor_text``. That
snapshot used to be read only when the offsets failed to resolve, which is the
one case it cannot help with: offsets that point at the *wrong* words resolve
perfectly well, and the highlight was drawn over them without a murmur.

So the snapshot is a check now. An edit that goes through the supported path
re-takes it, and anything that moved the text without migrating offsets is
reported instead of being displayed as though it were still true.
"""

from app.services.annotations import (
    anchor_text_from_span,
    mark_anchor_drift,
    refresh_anchor_text,
)
from app.services.transcript_edit import apply_find_replace


def _segments():
    return [
        {'start': 0.0, 'end': 4.0, 'speaker': 'A',
         'text': 'Мы пользуемся Джирой каждый день.'},
        {'start': 4.0, 'end': 9.0, 'speaker': 'B',
         'text': 'Джира у нас с самого начала проекта.'},
    ]


def _span(seg_idx, start, end, segments, **extra):
    """A span anchored the way the client anchors one, snapshot included."""
    span = {'id': 's1', 'tag_id': 'pain', 'segment_idx': seg_idx,
            'start_char': start, 'end_char': end, **extra}
    span['anchor_text'] = anchor_text_from_span(span, segments)
    return span


def test_a_fresh_anchor_matches_the_words_it_covers():
    segments = _segments()
    span = _span(0, 14, 20, segments)

    assert span['anchor_text'] == 'Джирой'
    assert mark_anchor_drift({'tag_spans': [span]}, segments)['tag_spans'][0] \
        .get('anchor_drifted') is None


def test_find_and_replace_moves_the_snapshot_with_the_offsets():
    """The edit that renames a word renames it inside the quote too."""
    segments = _segments()
    annotations = {'tag_spans': [_span(0, 14, 20, segments)], 'comments': []}

    count = apply_find_replace(segments, annotations, 'Джирой', 'Jira')

    assert count == 1
    span = annotations['tag_spans'][0]
    assert span['anchor_text'] == 'Jira'
    # ...and the offsets agree with it, so nothing is reported as drifted.
    assert mark_anchor_drift(annotations, segments)['tag_spans'][0] \
        .get('anchor_drifted') is None


def test_text_that_moved_without_migrating_offsets_is_reported():
    """The failure this whole mechanism exists for."""
    segments = _segments()
    annotations = {'tag_spans': [_span(0, 14, 20, segments)], 'comments': []}

    # An edit from somewhere that does not know about annotations: a hand-edited
    # transcript file, an import, a tool. The offsets now cover other words.
    segments[0]['text'] = 'Мы давно пользуемся Джирой каждый день.'

    span = mark_anchor_drift(annotations, segments)['tag_spans'][0]

    assert span['anchor_drifted'] is True
    assert span['anchor_text'] == 'Джирой'      # what it was coded as
    assert span['anchor_text_now'] != 'Джирой'  # what it covers today


def test_the_stored_annotations_are_not_changed_by_the_check():
    """Marking is for the response; the file keeps saying what it said."""
    segments = _segments()
    annotations = {'tag_spans': [_span(0, 14, 20, segments)], 'comments': []}
    segments[0]['text'] = 'Мы давно пользуемся Джирой каждый день.'

    mark_anchor_drift(annotations, segments)

    assert 'anchor_drifted' not in annotations['tag_spans'][0]


def test_an_anchor_with_no_snapshot_is_left_alone():
    """Nothing to compare against, so no warning nobody could act on."""
    segments = _segments()
    span = {'id': 's9', 'tag_id': 'pain', 'segment_idx': 0,
            'start_char': 0, 'end_char': 2}

    marked = mark_anchor_drift({'tag_spans': [span]}, segments)['tag_spans'][0]

    assert 'anchor_drifted' not in marked


def test_comments_are_checked_like_tag_spans():
    segments = _segments()
    comment = _span(1, 0, 5, segments)
    comment['text'] = 'спросить про миграцию'
    annotations = {'tag_spans': [], 'comments': [comment]}

    segments[1]['text'] = 'Кстати, Джира у нас с самого начала проекта.'

    assert mark_anchor_drift(annotations, segments)['comments'][0]['anchor_drifted'] is True


def test_whitespace_alone_is_not_drift():
    """A run of spaces is not what an anchor is about."""
    segments = _segments()
    span = _span(0, 0, 13, segments)
    span['anchor_text'] = span['anchor_text'].replace(' ', '  ')

    marked = mark_anchor_drift({'tag_spans': [span]}, segments)['tag_spans'][0]

    assert 'anchor_drifted' not in marked


def test_refresh_reports_how_many_quotes_the_edit_reworded():
    segments = _segments()
    annotations = {
        'tag_spans': [_span(0, 14, 20, segments)],
        'comments': [_span(1, 0, 5, segments)],
    }
    segments[0]['text'] = 'Мы пользуемся Jira каждый день.'

    changed = refresh_anchor_text(annotations, segments)

    assert changed == 1
    # Re-taken from where the offsets land now, whatever that turns out to be:
    # the word is shorter, so the same span reaches into what follows it.
    assert annotations['tag_spans'][0]['anchor_text'] == 'Jira к'
    # The untouched segment's anchor is unchanged, and not counted.
    assert annotations['comments'][0]['anchor_text'] == 'Джира'
