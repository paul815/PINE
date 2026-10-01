"""Where one speaker turn ends and the next begins.

An interrupted speaker keeps the floor so a "хорошо" dropped into a question
does not cut the question in half. The claim is bounded by silence: a speaker
who comes back seconds later is answering, not finishing a sentence, and
folding those words back into the block above the interruption prints the
answer before the question that prompted it.

The rule lives in services/speaker_blocks.py, and it used to be mirrored in JS
in recording.html so the browser could draw the transcript. A boundary that
differed between the two was an annotation landing on the wrong words, so the
fixtures below were run through both — they agreed, and the JS copy was then
deleted rather than kept in step forever: the page is served its blocks now.
The tests at the bottom of this file are what holds that arrangement in place.
"""

from pathlib import Path

import pytest

from app.services import speaker_blocks
from app.services.speaker_blocks import (
    block_offsets,
    block_text,
    merge_speaker_blocks,
    with_speaker_blocks,
)

TEMPLATE = Path(__file__).resolve().parents[1] / 'templates' / 'recording.html'


def seg(start, end, speaker, text):
    return {'start': start, 'end': end, 'speaker': speaker, 'text': text}


# A backchannel: "Угу." lands inside A's sentence and A carries straight on.
BACKCHANNEL = [
    seg(0.0, 4.0, 'A', 'Тут просто есть еще, в зависимости от проекта,'),
    seg(3.5, 4.3, 'B', 'Угу.'),
    seg(4.9, 9.0, 'A', 'и как раз-таки там желание инфлюенсера.'),
]

# A real exchange, from the transcript that found this: A trails off without a
# full stop, B asks a whole question in the gap, A answers 1.7s later.
EXCHANGE = [
    seg(25.7, 40.7, 'A', 'канал, который добавляется при создании подписок, верно? И немножко'),
    seg(41.6, 45.3, 'B', 'Давайте поговорим вообще обо всей, не знаю, экосистеме.'),
    seg(47.0, 51.9, 'A', 'У нас глобальный продукт, это White Label VPN.'),
]

# The same shape stretched out: B holds the floor for a minute and a half, and
# A comes back with a new question of their own.
LONG_HANDOVER = [
    seg(0.0, 20.0, 'B', 'сервисы, которые включены в работу'),
    seg(21.0, 115.0, 'A', 'Мы отвечаем на это так, и еще вот так.'),
    seg(115.5, 130.0, 'B', 'Спасибо. И расскажите, пожалуйста, что дальше?'),
]

# An edit that ran across a segment boundary leaves the segments it swallowed
# with no text. They keep their place in the list so annotation indices stay
# valid, which means the offsets have to step over them.
BLANKED = [
    seg(0.0, 3.0, 'A', 'Первая часть реплики,'),
    seg(3.0, 6.0, 'A', ''),
    seg(6.0, 9.0, 'A', 'и её продолжение.'),
]

# The same, where the blank is the first thing in the block: nothing precedes
# the words, so they start the block rather than sitting one space into it.
BLANKED_HEAD = [
    seg(0.0, 3.0, 'A', ''),
    seg(3.0, 6.0, 'A', 'Реплика целиком здесь.'),
]

CASES = {
    'backchannel': BACKCHANNEL,
    'exchange': EXCHANGE,
    'long_handover': LONG_HANDOVER,
    'blanked': BLANKED,
    'blanked_head': BLANKED_HEAD,
    'untimed': [
        {'speaker': 'A', 'text': 'первая половина вопроса'},
        {'speaker': 'B', 'text': 'Угу.'},
        {'speaker': 'A', 'text': 'вторая половина.'},
    ],
    'finished_sentence': [
        seg(0.0, 4.0, 'A', 'Готово.'),
        seg(4.1, 4.9, 'B', 'Угу.'),
        seg(5.2, 9.0, 'A', 'Теперь другое.'),
    ],
    # A closing quote rides along after the full stop; the sentence is still over.
    'quoted_ender': [
        seg(0.0, 2.0, 'A', 'Он сказал «всё готово».'),
        seg(2.0, 2.3, 'B', 'ага'),
        seg(2.3, 4.0, 'A', 'И ушёл.'),
    ],
    # A colon and an ellipsis end a sentence as far as the reader is concerned.
    'colon_and_ellipsis': [
        seg(0.0, 2.0, 'A', 'Смотрите:'),
        seg(2.0, 2.3, 'B', 'да'),
        seg(2.3, 4.0, 'A', 'вот так…'),
    ],
    # Two people cut in one after the other: only one claim on the floor is
    # held, so the first speaker's sentence does not get it back.
    'stacked_interruptions': [
        seg(0.0, 2.0, 'A', 'Я хотел сказать что'),
        seg(2.0, 2.3, 'B', 'извините'),
        seg(2.3, 2.6, 'C', 'секунду'),
        seg(2.6, 4.0, 'A', 'это уже не важно.'),
    ],
    # Diarisation found nobody: every segment reads as the same speaker.
    'unlabelled': [
        seg(0.0, 2.0, '', 'Первое предложение'),
        seg(2.0, 4.0, '', 'и второе.'),
    ],
    'empty': [],
}


def indices(segments):
    return [block['indices'] for block in merge_speaker_blocks(segments)]


def test_backchannel_leaves_the_interrupted_sentence_whole():
    """The case the floor rule exists for: A's sentence must not be cut in two."""
    assert indices(BACKCHANNEL) == [[0, 2], [1]]


def test_a_question_asked_in_the_gap_is_not_an_interjection():
    """B's question stands on its own and A's answer follows it, in that order."""
    blocks = merge_speaker_blocks(EXCHANGE)

    assert [b['indices'] for b in blocks] == [[0], [1], [2]]
    assert [b['speaker'] for b in blocks] == ['A', 'B', 'A']
    # Read top to bottom, the blocks stay in time order.
    assert [b['start'] for b in blocks] == sorted(b['start'] for b in blocks)


def test_the_floor_does_not_survive_a_long_answer():
    assert indices(LONG_HANDOVER) == [[0], [1], [2]]


def test_resuming_within_the_window_still_merges():
    """The limit is silence, not the presence of an interruption."""
    late = list(EXCHANGE)
    late[2] = seg(43.0, 48.0, 'A', 'У нас глобальный продукт, это White Label VPN.')

    assert speaker_blocks._silence_before({'end': 40.7}, late[2]) <= \
        speaker_blocks.RESUME_MAX_SILENCE
    assert indices(late) == [[0, 2], [1]]


def test_segments_without_timings_fall_back_to_the_text():
    """Nothing to measure means the rule reads as it did before the clock."""
    assert indices(CASES['untimed']) == [[0, 2], [1]]


def test_a_finished_sentence_has_no_claim_on_the_floor():
    assert indices(CASES['finished_sentence']) == [[0], [1], [2]]
    assert indices(CASES['quoted_ender']) == [[0], [1], [2]]
    assert indices(CASES['colon_and_ellipsis']) == [[0], [1], [2]]


def test_only_one_speaker_at_a_time_can_hold_the_floor():
    """Two interruptions in a row, and the first speaker's sentence is let go.

    Recorded rather than argued for: one slot is what the rule has, and a
    transcript that reads oddly here reads oddly the same way in both panes.
    """
    assert indices(CASES['stacked_interruptions']) == [[0], [1], [2], [3]]


def test_an_unlabelled_transcript_is_one_speaker():
    assert indices(CASES['unlabelled']) == [[0, 1]]


def test_block_offsets_follow_the_new_boundaries():
    """Annotation anchors are offsets into the block a segment landed in."""
    assert block_offsets(EXCHANGE) == {0: 0, 1: 0, 2: 0}
    assert block_offsets(BACKCHANNEL)[2] == len(BACKCHANNEL[0]['text']) + 1


def test_an_emptied_segment_takes_no_room_in_the_block():
    """A segment an edit blanked adds no text, so it must add no offset either.

    The merge joins what is left with single spaces and never writes the blank,
    so counting a separator for it pushes every later anchor one char right --
    a tag highlighting the last word of the block lands past the end of it.
    """
    block = merge_speaker_blocks(BLANKED)[0]
    offsets = block_offsets(BLANKED)

    assert block['text'] == 'Первая часть реплики, и её продолжение.'
    assert block['text'][offsets[2]:] == BLANKED[2]['text']
    # The blank sits at the seam it was edited out of, not past the next words.
    assert offsets[1] == len(BLANKED[0]['text'])


def test_a_block_that_opens_with_a_blank_starts_at_zero():
    """No words precede the text, so there is no separator to count."""
    block = merge_speaker_blocks(BLANKED_HEAD)[0]
    offsets = block_offsets(BLANKED_HEAD)

    assert block['text'] == BLANKED_HEAD[1]['text']
    assert offsets == {0: 0, 1: 0}


@pytest.mark.parametrize('name', sorted(CASES))
def test_block_text_is_the_text_the_merge_built(name):
    """One definition of a block's text -- the offsets are measured into it."""
    segments = CASES[name]

    for block in merge_speaker_blocks(segments):
        assert block_text(segments, block['indices']) == block['text']


@pytest.mark.parametrize('name', sorted(CASES))
def test_every_offset_lands_on_its_own_words(name):
    """Slice the block at a segment's offset and its own text is what is there.

    This is what an annotation anchor means, so it has to hold for every
    fixture rather than the hand-checked ones.
    """
    segments = CASES[name]
    offsets = block_offsets(segments)

    for block in merge_speaker_blocks(segments):
        for idx in block['indices']:
            text = (segments[idx].get('text') or '').strip()
            if not text:
                continue
            assert block['text'][offsets[idx]:offsets[idx] + len(text)] == text


# ── one implementation, and the arrangement that keeps it that way ──────────


def test_a_served_transcript_carries_its_blocks_and_their_offsets():
    """What the page draws from, answered once, by the rule above."""
    transcript = {'segments': BACKCHANNEL}

    served = with_speaker_blocks(transcript)

    assert [b['indices'] for b in served['blocks']] == [[0, 2], [1]]
    # A block's offsets cover its own segments, measured into its own text.
    first = served['blocks'][0]
    assert sorted(first['offsets']) == first['indices']
    for idx, offset in first['offsets'].items():
        text = BACKCHANNEL[idx]['text']
        assert first['text'][offset:offset + len(text)] == text
    # The copy on disk stays as it was: segments, no blocks.
    assert 'blocks' not in transcript


def test_the_browser_does_not_fold_blocks_of_its_own():
    """The JS mirror is gone; it must not come back unnoticed.

    A page that folds for itself is a page whose boundaries can disagree with
    the offsets its annotations are already stored against — and the two copies
    were only ever kept in step by somebody remembering to.
    """
    source = TEMPLATE.read_text(encoding='utf-8')

    for gone in ('function mergeSpeakerBlocks', 'function blockTextAndOffsets',
                 'RESUME_MAX_SILENCE'):
        assert gone not in source, f'recording.html is deciding {gone} for itself again'
    assert 'function speakerBlocks()' in source, \
        'the page has to read its turns from the transcript it was served'


def test_every_endpoint_that_returns_a_transcript_includes_blocks(client, project_with_recording):
    """The page renders nothing without them, so no response may leave them out."""
    project_id, recording_id, _ = project_with_recording(
        transcript_data={'segments': BACKCHANNEL})
    base = f'/api/projects/{project_id}/recordings/{recording_id}'

    detail = client.get(base).get_json()
    transcript = client.get(f'{base}/transcript').get_json()
    replaced = client.post(f'{base}/transcript/replace',
                           json={'find': 'Угу.', 'replace': 'Ага.'}).get_json()

    assert detail['transcript']['blocks']
    assert transcript['blocks']
    assert replaced['count'] == 1
    assert replaced['transcript']['blocks'], 'an edit must hand back fresh turns'


def test_a_block_edit_hands_back_fresh_turns(client, project_with_recording):
    """The editor re-reads the block it just wrote from this response."""
    project_id, recording_id, _ = project_with_recording(
        transcript_data={'segments': BACKCHANNEL})
    base = f'/api/projects/{project_id}/recordings/{recording_id}'
    typed = 'Тут просто есть еще, смотря по проекту, и желание инфлюенсера.'

    body = client.post(f'{base}/transcript/block', json={
        'indices': [0, 2],
        'original_text': block_text(BACKCHANNEL, [0, 2]),
        'new_text': typed,
    }).get_json()

    assert body['transcript']['blocks'][0]['text'] == typed
    assert body['transcript']['blocks'][0]['offsets']
