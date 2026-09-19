"""Where one speaker turn ends and the next begins.

An interrupted speaker keeps the floor so a "хорошо" dropped into a question
does not cut the question in half. The claim is bounded by silence: a speaker
who comes back seconds later is answering, not finishing a sentence, and
folding those words back into the block above the interruption prints the
answer before the question that prompted it.

The rule lives in services/speaker_blocks.py and is mirrored in JS in
recording.html. A boundary that differs between the two is an annotation
landing on the wrong words, so the fixtures below are run through both.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import speaker_blocks
from app.services.speaker_blocks import (
    block_offsets,
    block_text,
    merge_speaker_blocks,
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


def _js_source():
    """The mirrored rule, lifted out of the template to run on its own."""
    source = TEMPLATE.read_text(encoding='utf-8')
    start = source.index('const SENTENCE_END_RE')
    end = source.index('function seedSpeakerColors')
    return source[start:end]


def test_the_template_still_carries_the_rule():
    js = _js_source()

    assert 'RESUME_MAX_SILENCE' in js
    assert 'function mergeSpeakerBlocks' in js


def test_the_two_copies_agree_on_the_silence_limit():
    match = re.search(r'const RESUME_MAX_SILENCE = ([\d.]+);', _js_source())

    assert match, 'the template lost its RESUME_MAX_SILENCE'
    assert float(match.group(1)) == speaker_blocks.RESUME_MAX_SILENCE


@pytest.mark.skipif(not shutil.which('node'), reason='node is not installed')
def test_js_and_python_draw_the_same_boundaries(tmp_path):
    """Every fixture, folded by both copies of the rule, block for block."""
    script = tmp_path / 'blocks.js'
    script.write_text(
        _js_source()
        + '\nconst cases = ' + json.dumps(CASES, ensure_ascii=False) + ';'
        + '\nconst out = {};'
        + '\nfor (const k of Object.keys(cases)) {'
        + '\n  out[k] = mergeSpeakerBlocks(cases[k]).map('
        + '\n    b => ({speaker: b.speaker, text: b.text, indices: b.indices}));'
        + '\n}'
        + '\nprocess.stdout.write(JSON.stringify(out));',
        encoding='utf-8')

    result = subprocess.run([shutil.which('node'), str(script)], capture_output=True,
                            text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr

    from_js = json.loads(result.stdout)
    for name, segments in CASES.items():
        expected = [{'speaker': b['speaker'], 'text': b['text'], 'indices': b['indices']}
                    for b in merge_speaker_blocks(segments)]
        assert from_js[name] == expected, name


@pytest.mark.skipif(not shutil.which('node'), reason='node is not installed')
def test_js_and_python_measure_the_same_offsets(tmp_path):
    """Anchor offsets from both copies -- a drift here misplaces a highlight.

    The template computes these while rendering; the server computes them when
    an edit shifts the text underneath them. The two have to agree char for
    char or a tag saved by one is drawn on the wrong words by the other.
    """
    script = tmp_path / 'offsets.js'
    script.write_text(
        _js_source()
        + '\nconst cases = ' + json.dumps(CASES, ensure_ascii=False) + ';'
        + '\nconst out = {};'
        + '\nfor (const k of Object.keys(cases)) {'
        + '\n  const segs = cases[k];'
        + '\n  const acc = {};'
        + '\n  for (const b of mergeSpeakerBlocks(segs)) {'
        + '\n    const r = blockTextAndOffsets(segs, b.indices);'
        + '\n    if (r.text !== b.text) {'
        + '\n      throw new Error("block text drift in " + k + ": " + r.text);'
        + '\n    }'
        + '\n    Object.assign(acc, r.offsets);'
        + '\n  }'
        + '\n  out[k] = acc;'
        + '\n}'
        + '\nprocess.stdout.write(JSON.stringify(out));',
        encoding='utf-8')

    result = subprocess.run([shutil.which('node'), str(script)], capture_output=True,
                            text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr

    from_js = json.loads(result.stdout)
    for name, segments in CASES.items():
        # JSON object keys are strings on the way back from node.
        expected = {str(k): v for k, v in block_offsets(segments).items()}
        assert from_js[name] == expected, name
