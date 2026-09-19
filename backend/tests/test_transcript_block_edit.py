"""Rewriting one speaker block the way it reads on screen.

The user sees paragraphs, not segments: a block is several segments joined,
and an edit arrives as the whole paragraph's new text. Mapping it back onto
segments is where the anchors live -- tags and comments are stored as char
offsets into a segment's text, so a change that moves words has to move them
too, or a highlight ends up on words nobody tagged.

The invariant every test here leans on: after the edit, the block reads back
exactly as the user typed it.
"""

import random

import pytest

from app.services.speaker_blocks import block_text, merge_speaker_blocks
from app.services.transcript_edit import (
    BLOCK_EDIT_EMPTY,
    BLOCK_EDIT_OK,
    BLOCK_EDIT_STALE,
    BLOCK_EDIT_UNCHANGED,
    apply_block_edit,
)


def seg(start, end, speaker, text, words=None):
    out = {'start': start, 'end': end, 'speaker': speaker, 'text': text}
    if words is not None:
        out['words'] = words
    return out


def timed(text, start, end):
    """Word tokens spread evenly over the segment, as alignment would leave them."""
    tokens = text.split()
    step = (end - start) / len(tokens)
    return [{'word': t, 'start': start + step * n, 'end': start + step * (n + 1)}
            for n, t in enumerate(tokens)]


def one_block(text_a='Привет мир', text_b='и всё остальное'):
    """Two segments of one speaker, folded into a single paragraph."""
    return [
        seg(0.0, 2.0, 'A', text_a, timed(text_a, 0.0, 2.0)),
        seg(2.0, 5.0, 'A', text_b, timed(text_b, 2.0, 5.0)),
    ]


def block_of(segments, n=0):
    return merge_speaker_blocks(segments)[n]


def edit(segments, annotations, new_text, n=0):
    block = block_of(segments, n)
    return apply_block_edit(segments, annotations, block['indices'],
                            block['text'], new_text)


def reads_as(segments, n=0):
    block = block_of(segments, n)
    return block['text']


# --------------------------------------------------------------------------
# The change lands, and the paragraph reads back as typed
# --------------------------------------------------------------------------

def test_a_typo_inside_one_segment():
    """The case the feature exists for: one word, one segment, nothing else moves."""
    segments = one_block()
    tail = segments[1]['text']

    assert edit(segments, {}, 'Привет мор и всё остальное') == BLOCK_EDIT_OK

    assert segments[0]['text'] == 'Привет мор'
    assert segments[1]['text'] == tail, 'the untouched segment was rewritten'
    assert reads_as(segments) == 'Привет мор и всё остальное'


def test_a_longer_word_does_not_disturb_the_segment_after_it():
    segments = one_block()

    assert edit(segments, {}, 'Привет огромный мир и всё остальное') == BLOCK_EDIT_OK

    assert segments[0]['text'] == 'Привет огромный мир'
    assert segments[1]['text'] == 'и всё остальное'


def test_a_shorter_word():
    segments = one_block()

    assert edit(segments, {}, 'Привет и всё остальное') == BLOCK_EDIT_OK

    assert segments[0]['text'] == 'Привет'
    assert segments[1]['text'] == 'и всё остальное'


def test_text_appended_at_the_end_of_the_paragraph():
    segments = one_block()

    assert edit(segments, {}, 'Привет мир и всё остальное тоже') == BLOCK_EDIT_OK

    assert segments[0]['text'] == 'Привет мир'
    assert segments[1]['text'] == 'и всё остальное тоже'


def test_text_inserted_at_the_very_start():
    segments = one_block()

    assert edit(segments, {}, 'Ну Привет мир и всё остальное') == BLOCK_EDIT_OK

    assert segments[0]['text'] == 'Ну Привет мир'
    assert segments[1]['text'] == 'и всё остальное'


def test_an_edit_across_the_seam_collapses_into_the_first_segment():
    """Segments are never dropped, so the ones it swallowed are left blank."""
    segments = one_block()

    assert edit(segments, {}, 'Привет всему остальному') == BLOCK_EDIT_OK

    assert segments[0]['text'] == 'Привет всему остальному'
    assert segments[1]['text'] == ''
    assert len(segments) == 2, 'segment count must not change'
    assert reads_as(segments) == 'Привет всему остальному'


def test_joining_two_words_across_the_seam():
    """Deleting the space between segments: the seam itself has to close."""
    segments = [
        seg(0.0, 2.0, 'A', 'Микро'),
        seg(2.0, 4.0, 'A', 'сервис работает.'),
    ]

    assert edit(segments, {}, 'Микросервис работает.') == BLOCK_EDIT_OK

    assert reads_as(segments) == 'Микросервис работает.'
    assert segments[0]['text'] == 'Микросервис работает.'
    assert segments[1]['text'] == ''


def test_the_whitespace_a_browser_adds_is_ignored():
    segments = one_block()

    status = edit(segments, {}, '  Привет мор   и всё\nостальное  ')

    assert status == BLOCK_EDIT_OK
    assert reads_as(segments) == 'Привет мор и всё остальное'


def test_a_non_contiguous_block_is_addressed_by_its_own_indices():
    """An interjection splits the segment list but not the paragraph.

    A "Угу." dropped into A's sentence lands between A's two segments, so the
    block A reads as owns indices [0, 2]. Addressing it as a range would edit
    the interjection instead.
    """
    segments = [
        seg(0.0, 4.0, 'A', 'Тут есть ещё, в зависимости от проекта,'),
        seg(3.5, 4.3, 'B', 'Угу.'),
        seg(4.9, 9.0, 'A', 'и желание инфлюенсера.'),
    ]
    block = block_of(segments)
    assert block['indices'] == [0, 2]

    status = apply_block_edit(
        segments, {}, block['indices'], block['text'],
        'Тут есть ещё, в зависимости от проекта, и желание блогера.')

    assert status == BLOCK_EDIT_OK
    assert segments[1]['text'] == 'Угу.', 'the interjection was edited instead'
    assert segments[2]['text'] == 'и желание блогера.'


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_text_that_moved_underneath_us_is_refused():
    segments = one_block()

    status = apply_block_edit(segments, {}, [0, 1],
                              'Совсем другой текст', 'Что угодно')

    assert status == BLOCK_EDIT_STALE
    assert segments[0]['text'] == 'Привет мир', 'a refused edit still wrote'


def test_indices_that_name_no_block_are_refused():
    segments = one_block()

    status = apply_block_edit(segments, {}, [0], reads_as(segments), 'Что угодно')

    assert status == BLOCK_EDIT_STALE
    assert segments[0]['text'] == 'Привет мир'


def test_emptying_the_paragraph_is_refused():
    """Deleting a whole turn is not a typo fix; it would lose the timings."""
    segments = one_block()

    assert edit(segments, {}, '   \n  ') == BLOCK_EDIT_EMPTY
    assert reads_as(segments) == 'Привет мир и всё остальное'


def test_the_same_text_back_is_a_no_op():
    segments = one_block()

    assert edit(segments, {}, 'Привет мир и всё остальное') == BLOCK_EDIT_UNCHANGED


# --------------------------------------------------------------------------
# Annotations follow their words
# --------------------------------------------------------------------------

def anns(**kw):
    span = {'segment_idx': 0, 'start_char': 0, 'end_char': 6, 'tag_id': 't1'}
    span.update(kw)
    return {'tag_spans': [span], 'comments': []}


def test_a_tag_before_the_edit_does_not_move():
    segments = one_block()
    annotations = anns(start_char=0, end_char=6, anchor_text='Привет')

    edit(segments, annotations, 'Привет огромный мир и всё остальное')

    span = annotations['tag_spans'][0]
    assert segments[0]['text'][span['start_char']:span['end_char']] == 'Привет'


def test_a_tag_after_the_edit_shifts_with_it():
    segments = one_block()
    # "мир" sits at 7..10 in "Привет мир".
    annotations = anns(start_char=7, end_char=10, anchor_text='мир')

    edit(segments, annotations, 'Приветствую мир и всё остальное')

    span = annotations['tag_spans'][0]
    assert segments[0]['text'][span['start_char']:span['end_char']] == 'мир'


def test_a_tag_in_a_later_segment_keeps_its_merged_offset():
    segments = one_block()
    annotations = {'tag_spans': [{
        'segment_idx': 1, 'start_char': 0, 'end_char': 1,
        'merged_start': 11, 'merged_end': 12, 'anchor_text': 'и', 'tag_id': 't1',
    }], 'comments': []}

    edit(segments, annotations, 'Приветствую мир и всё остальное')

    span = annotations['tag_spans'][0]
    text = reads_as(segments)
    assert text[span['merged_start']:span['merged_end']] == 'и'


def test_a_comment_migrates_the_same_way_as_a_tag():
    segments = one_block()
    annotations = {'tag_spans': [], 'comments': [
        {'segment_idx': 0, 'start_char': 7, 'end_char': 10, 'body': 'какой мир?'},
    ]}

    edit(segments, annotations, 'Приветствую мир и всё остальное')

    span = annotations['comments'][0]
    assert segments[0]['text'][span['start_char']:span['end_char']] == 'мир'


def test_a_tag_whose_words_are_gone_is_dropped():
    segments = one_block()
    annotations = anns(start_char=7, end_char=10, anchor_text='мир')

    edit(segments, annotations, 'Привет и всё остальное')

    assert annotations['tag_spans'] == [], 'an empty highlight was left behind'


def test_a_comment_pinned_without_a_range_survives():
    """Not every annotation is a highlight; a pinned one has nothing to collapse."""
    segments = one_block()
    annotations = {'tag_spans': [], 'comments': [
        {'segment_idx': 0, 'body': 'про эту реплику целиком'},
    ]}

    edit(segments, annotations, 'Привет мор и всё остальное')

    assert len(annotations['comments']) == 1


# --------------------------------------------------------------------------
# Word timings
# --------------------------------------------------------------------------

def test_respelling_one_word_keeps_its_own_timing():
    """The typo case: the word is still the word it was aligned to."""
    segments = one_block()
    was = dict(segments[0]['words'][1])

    edit(segments, {}, 'Привет мор и всё остальное')

    now = segments[0]['words'][1]
    assert now['word'] == 'мор'
    assert now['start'] == was['start'] and now['end'] == was['end']
    assert 'approx' not in now


def test_a_typed_word_is_timed_between_its_neighbours():
    """A word inserted on a seam joins the segment that starts there.

    The bounds admit exactly one segment for a zero-width insertion, so there
    is nothing to choose between: the text goes in ahead of the words it was
    typed in front of, and is timed into the gap before them.
    """
    segments = one_block()

    edit(segments, {}, 'Привет мир огромный и всё остальное')

    words = segments[1]['words']
    assert [w['word'] for w in words] == ['огромный', 'и', 'всё', 'остальное']
    fresh = words[0]
    assert fresh['approx'] is True
    assert segments[1]['start'] <= fresh['start'] <= fresh['end'] <= words[1]['start']
    assert not any(w.get('approx') for w in words[1:]), 'a real timing was replaced'


def test_untouched_words_keep_their_timings_when_one_is_typed():
    segments = one_block()
    was = [dict(w) for w in segments[0]['words']]

    edit(segments, {}, 'Привет большой мир и всё остальное')

    kept = [w for w in segments[0]['words'] if not w.get('approx')]
    assert [(w['word'], w['start']) for w in kept] == \
           [(w['word'], w['start']) for w in was]


def test_closing_a_seam_gives_up_the_word_timings():
    """Merging two segments moves words; a moved word's time is a lie.

    Click-to-seek falls back to the paragraph, which the renderer handles --
    a kept-but-wrong timing would send the user somewhere wrong instead.
    """
    segments = [
        seg(0.0, 2.0, 'A', 'Микро', timed('Микро', 0.0, 2.0)),
        seg(2.0, 4.0, 'A', 'сервис работает.', timed('сервис работает.', 2.0, 4.0)),
    ]

    assert edit(segments, {}, 'Микросервис работает.') == BLOCK_EDIT_OK

    assert segments[0]['words'] == []
    assert segments[1]['words'] == []


def test_an_edit_across_the_seam_keeps_the_words_it_did_not_touch():
    """Rewriting the tail of a paragraph is not merging it.

    The seam survives, so each segment is edited on its own and the words
    before the change keep the times they were aligned to.
    """
    segments = one_block()

    edit(segments, {}, 'Привет всему остальному')

    assert [w['word'] for w in segments[0]['words']][0] == 'Привет'
    assert not segments[0]['words'][0].get('approx')
    assert segments[1]['words'] == [], 'the emptied segment kept phantom words'


def test_segment_timings_are_never_touched():
    """Playback, export timestamps and the block rule all read these."""
    segments = one_block()
    was = [(s['start'], s['end']) for s in segments]

    edit(segments, {}, 'Привет всему остальному совершенно другому')

    assert [(s['start'], s['end']) for s in segments] == was


def test_a_segment_without_words_is_edited_all_the_same():
    segments = [seg(0.0, 2.0, 'A', 'Привет мир')]

    assert edit(segments, {}, 'Привет мор') == BLOCK_EDIT_OK
    assert segments[0]['text'] == 'Привет мор'
    assert 'words' not in segments[0]


# --------------------------------------------------------------------------
# The invariant, over a lot of edits at once
# --------------------------------------------------------------------------

WORDS = ['мир', 'дом', 'очень', 'длинное', 'слово', 'ещё', 'и', 'да']


def random_edit(rng, text):
    """A plausible edit: retype, delete or insert a run somewhere in the text."""
    if len(text) < 2:
        return text + ' ещё'
    lo = rng.randrange(0, len(text) - 1)
    hi = rng.randrange(lo, len(text))
    what = rng.choice(['delete', 'insert', 'replace'])
    if what == 'delete':
        return text[:lo] + text[hi:]
    piece = ' '.join(rng.choice(WORDS) for _ in range(rng.randrange(1, 3)))
    if what == 'insert':
        return text[:lo] + piece + text[lo:]
    return text[:lo] + piece + text[hi:]


def three_segments():
    parts = ['Первая часть реплики,', 'вторая её часть', 'и совсем хвост.']
    return [seg(n * 2.0, n * 2.0 + 2.0, 'A', p, timed(p, n * 2.0, n * 2.0 + 2.0))
            for n, p in enumerate(parts)]


def with_a_blank():
    """A block that already carries the blank a previous edit left behind."""
    return [
        seg(0.0, 2.0, 'A', 'Начало реплики'),
        seg(2.0, 4.0, 'A', ''),
        seg(4.0, 6.0, 'A', 'и её конец.'),
    ]


def interrupted():
    """A block whose indices have a hole in them: [0, 2]."""
    return [
        seg(0.0, 4.0, 'A', 'Тут есть ещё, в зависимости от проекта,'),
        seg(3.5, 4.3, 'B', 'Угу.'),
        seg(4.9, 9.0, 'A', 'и желание инфлюенсера.'),
    ]


SHAPES = {
    'two': one_block,
    'three': three_segments,
    'blank': with_a_blank,
    'interrupted': interrupted,
    'single': lambda: [seg(0.0, 2.0, 'A', 'Одна реплика целиком.')],
}


@pytest.mark.parametrize('shape', sorted(SHAPES))
@pytest.mark.parametrize('seed', range(120))
def test_any_block_shape_reads_back_as_it_was_typed(seed, shape):
    """The invariant again, over blocks of every shape the folder produces.

    A three-segment turn, one that already carries a blank, and one whose
    indices skip an interjection -- each is a different way for the mapping
    back onto segments to go wrong.
    """
    rng = random.Random(seed)
    segments = SHAPES[shape]()
    block = block_of(segments)
    count = len(segments)
    typed = ' '.join(random_edit(rng, block['text']).split())
    if not typed:
        pytest.skip('an empty paragraph is refused, covered elsewhere')

    status = apply_block_edit(segments, {}, block['indices'], block['text'], typed)

    assert status in (BLOCK_EDIT_OK, BLOCK_EDIT_UNCHANGED)
    assert block_text(segments, block['indices']) == typed
    assert len(segments) == count, 'segment count changed'


@pytest.mark.parametrize('seed', range(150))
def test_a_tag_outside_the_edited_segment_is_untouched(seed):
    """Editing one segment must not move an anchor in another one."""
    rng = random.Random(seed)
    segments = three_segments()
    tail = segments[2]['text']
    at = tail.index('хвост')
    annotations = {'tag_spans': [{
        'segment_idx': 2, 'start_char': at, 'end_char': at + 5,
        'anchor_text': 'хвост', 'tag_id': 't1',
    }], 'comments': []}

    block = block_of(segments)
    text = block['text']
    # Confine the change to the interior of the first segment, so no seam and
    # no later segment is involved.
    limit = len(segments[0]['text']) - 1
    lo = rng.randrange(1, limit)
    hi = rng.randrange(lo, limit)
    piece = rng.choice(['', rng.choice(WORDS), ' ' + rng.choice(WORDS) + ' '])
    typed = ' '.join((text[:lo] + piece + text[hi:]).split())

    status = apply_block_edit(segments, annotations, block['indices'], text, typed)
    assert status in (BLOCK_EDIT_OK, BLOCK_EDIT_UNCHANGED)

    span = annotations['tag_spans'][0]
    assert span['segment_idx'] == 2
    assert segments[2]['text'][span['start_char']:span['end_char']] == 'хвост'


@pytest.mark.parametrize('seed', range(150))
def test_a_tag_after_a_change_in_its_own_segment_follows_its_word(seed):
    """The drift case: the anchor has to shift by exactly what the edit moved."""
    rng = random.Random(seed)
    head = 'Мы обсуждали мир и погоду вчера'
    segments = [
        seg(0.0, 3.0, 'A', head, timed(head, 0.0, 3.0)),
        seg(3.0, 6.0, 'A', 'и продолжили сегодня.'),
    ]
    at = head.index('мир')
    annotations = {'tag_spans': [{
        'segment_idx': 0, 'start_char': at, 'end_char': at + 3,
        'anchor_text': 'мир', 'tag_id': 't1',
    }], 'comments': []}

    block = block_of(segments)
    text = block['text']
    # Strictly before the tagged word, never reaching the space in front of it.
    lo = rng.randrange(0, at - 1)
    hi = rng.randrange(lo, at - 1)
    piece = rng.choice(['', rng.choice(WORDS), ' ' + rng.choice(WORDS)])
    typed = ' '.join((text[:lo] + piece + text[hi:]).split())
    if not typed:
        pytest.skip('an empty paragraph is refused, covered elsewhere')

    status = apply_block_edit(segments, annotations, block['indices'], text, typed)
    assert status in (BLOCK_EDIT_OK, BLOCK_EDIT_UNCHANGED)

    spans = annotations['tag_spans']
    assert spans, 'a tag the edit never reached was dropped'
    span = spans[0]
    idx = span['segment_idx']
    assert segments[idx]['text'][span['start_char']:span['end_char']] == 'мир'


@pytest.mark.parametrize('seed', range(300))
def test_the_paragraph_reads_back_exactly_as_it_was_typed(seed):
    """The one thing that must always hold, over a few hundred edits.

    Whatever the change was and however it split across segments, folding the
    segments back into a paragraph has to return the text the user left on
    screen -- otherwise their edit silently half-applied.
    """
    rng = random.Random(seed)
    segments = one_block()
    block = block_of(segments)
    typed = ' '.join(random_edit(rng, block['text']).split())
    if not typed:
        pytest.skip('an empty paragraph is refused, covered elsewhere')

    status = apply_block_edit(segments, {}, block['indices'], block['text'], typed)

    assert status in (BLOCK_EDIT_OK, BLOCK_EDIT_UNCHANGED)
    assert reads_as(segments) == typed
    assert len(segments) == 2, 'segment count changed'
    assert block_text(segments, [0, 1]) == typed


@pytest.mark.parametrize('seed', range(200))
def test_an_annotation_stays_on_its_own_words_through_a_random_edit(seed):
    """Every tag that survives still covers the text it was anchored to.

    A tag whose words the edit removed is dropped; one that survives must not
    have drifted, which is the failure nobody notices until export.
    """
    rng = random.Random(seed)
    segments = one_block()
    block = block_of(segments)

    # Tag a real word in the first segment, then edit somewhere at random.
    head = segments[0]['text']
    start = head.index('мир')
    annotations = {'tag_spans': [{
        'segment_idx': 0, 'start_char': start, 'end_char': start + 3,
        'anchor_text': 'мир', 'tag_id': 't1',
    }], 'comments': []}

    typed = ' '.join(random_edit(rng, block['text']).split())
    if not typed:
        pytest.skip('an empty paragraph is refused, covered elsewhere')
    status = apply_block_edit(segments, annotations, block['indices'],
                              block['text'], typed)
    assert status in (BLOCK_EDIT_OK, BLOCK_EDIT_UNCHANGED)

    spans = annotations['tag_spans']
    if not spans:
        return  # its words are gone; dropping it is the documented behaviour
    span = spans[0]
    idx = span['segment_idx']
    text = (segments[idx].get('text') or '')
    assert 0 <= span['start_char'] <= span['end_char'] <= len(text), \
        'the anchor points outside its own segment'
