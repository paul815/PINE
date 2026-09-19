"""Folding the segment list into the speaker turns the transcript is read as.

A turn is not "everything one speaker said in a row". Two people talking take
the floor from each other mid-sentence, and per-track transcription surfaces
every bit of that: a "хорошо" dropped into a question arrives as its own
segment, timestamped inside the question it interrupted.

Merging on speaker changes alone would then cut the question in half — the half
before the interjection and the half after — which is what the reader notices
first and what nobody says happened. So a speaker who was cut off *mid-sentence*
keeps the floor: the interjection becomes its own block, and when the
interrupted speaker resumes, the words go back into the block they started in.
A speaker who was interrupted after finishing a sentence has no claim on the
floor, and the next thing they say opens a new block.

The claim also expires with silence. A backchannel hands the floor back within
a beat, so a speaker who resumes on top of the interjection is still finishing
their sentence; one who comes back after seconds of quiet is starting again,
whatever the punctuation says. Without that limit a full question asked in a
gap counted as an interjection, and the answer to it was folded back into the
sentence above the question -- putting the question after the answer.

This module is the single definition of that rule. ``recording.html`` mirrors it
in JS for rendering; every server-side consumer — anchor reconstruction,
find/replace offsets, exports — imports from here, because a block boundary that
differs between the two is an annotation landing on the wrong words.
"""

import re

# How long the interrupted speaker may stay quiet and still be read as resuming.
# Measured from the end of their own last words, so it covers the interruption
# and the pause after it together: that is the silence the reader hears.
RESUME_MAX_SILENCE = 3.0

# Sentence enders, plus any closing quote or bracket riding along after them.
_SENTENCE_END = re.compile(r'[.!?…:;]["\'»”’)\]]*$')


def ends_sentence(text):
    """True if ``text`` closes a sentence — an empty block counts as closed."""
    stripped = (text or '').strip()
    return not stripped or bool(_SENTENCE_END.search(stripped))


def _silence_before(block, seg):
    """Seconds ``block``'s speaker stayed quiet before ``seg``.

    A segment without timings reads as no silence, leaving the decision to the
    text alone -- what the rule did before it consulted the clock.
    """
    start = seg.get('start')
    end = block.get('end')
    if start is None or end is None:
        return 0.0
    return start - end


def merge_speaker_blocks(segments):
    """Fold ``segments`` into speaker turns.

    Returns ``[{speaker, start, end, text, indices}]`` in the order the blocks
    are read, where ``indices`` are the positions in ``segments`` that landed in
    the block — not necessarily contiguous, since an interrupted turn resumes in
    the block it opened.
    """
    blocks = []
    # The block that last took words, which is not always the last one in
    # reading order: an interrupted turn resumes above the interruption.
    cur = -1
    # The block whose speaker was cut off mid-sentence and can still resume.
    hold = -1

    def append_to(idx, i, seg, txt):
        block = blocks[idx]
        block['text'] = (block['text'] + ' ' + txt).strip()
        block['end'] = max(block.get('end') or 0, seg.get('end') or 0)
        block['indices'].append(i)

    for i, seg in enumerate(segments or []):
        spk = (seg.get('speaker') or '').strip()
        txt = (seg.get('text') or '').strip()
        current = blocks[cur] if cur >= 0 else None

        # The same speaker carrying on. A block that has been overtaken in
        # reading order takes only the rest of its unfinished sentence: append
        # a *new* sentence to it and the reader gets words dated later than the
        # block below them.
        if (current is not None and (current['speaker'] or '').strip() == spk
                and (cur == len(blocks) - 1 or not ends_sentence(current['text']))):
            append_to(cur, i, seg, txt)
            continue

        # The interrupted speaker resuming, into the block they opened -- as
        # long as they are picking the sentence back up rather than answering
        # after a silence.
        if (hold >= 0 and (blocks[hold]['speaker'] or '').strip() == spk
                and not ends_sentence(blocks[hold]['text'])
                and _silence_before(blocks[hold], seg) <= RESUME_MAX_SILENCE):
            # Whoever just held the floor may themselves have been cut off.
            hold, cur = (cur if not ends_sentence(current['text']) else -1), hold
            append_to(cur, i, seg, txt)
            continue

        # Opening a block hands the floor to whoever just got cut off, if they
        # were cut off. Anyone else's claim expires here.
        hold = (cur if current is not None
                and not ends_sentence(current['text']) else -1)
        blocks.append({
            'speaker': spk,
            'start': seg.get('start', 0),
            'end': seg.get('end', 0),
            'text': txt,
            'indices': [i],
        })
        cur = len(blocks) - 1

    return blocks


def with_speaker_blocks(transcript):
    """``transcript`` plus the speaker turns its segments fold into.

    Each block also carries ``offsets``: where each of its segments starts
    inside the block's own text. That is the coordinate system every annotation
    is stored in, so it is answered here rather than recomputed by whoever is
    drawing.

    The browser used to do both for itself, from a second copy of the rules
    above written in JavaScript. Two implementations of one rule is one too many
    when annotations are anchored by character offsets inside the folded block:
    a boundary or a separator that moved in one and not the other puts every
    highlight after it on the wrong words. So the page is served its turns
    instead of deriving them, and this is the only place they are made.

    Returns a copy — the transcript on disk holds segments, not blocks.
    """
    if not isinstance(transcript, dict):
        return transcript
    segments = transcript.get('segments') or []
    blocks = merge_speaker_blocks(segments)
    for block in blocks:
        block['offsets'] = block_text_and_offsets(segments, block['indices'])[1]
    return dict(transcript, blocks=blocks)


def block_text_and_offsets(segments, indices):
    """The text ``indices`` read as, and where each segment sits inside it.

    The single definition of how segment texts join into what a reader sees:
    the non-empty ones, separated by one space -- exactly what ``append_to``
    builds above. A segment with no text writes nothing, so it must not be
    charged for a separator either; it takes the offset of the seam it sits
    on. Editing across a segment boundary leaves such blanks behind (the
    segments stay in the list so annotation indices keep pointing at the same
    things), and counting a space for each of them walks every later anchor off
    its own words.
    """
    parts = []
    offsets = {}
    off = 0
    for i in indices:
        text = (segments[i].get('text') or '').strip()
        if not text:
            offsets[i] = off
            continue
        offsets[i] = off + (1 if off else 0)
        off = offsets[i] + len(text)
        parts.append(text)
    return ' '.join(parts), offsets


def block_text(segments, indices):
    """The text the block spanning ``indices`` reads as."""
    return block_text_and_offsets(segments, indices)[0]


def block_offsets(segments):
    """Char offset of each segment within its merged block's text.

    Keyed by index into ``segments``.
    """
    offsets = {}
    for block in merge_speaker_blocks(segments):
        offsets.update(block_text_and_offsets(segments, block['indices'])[1])
    return offsets
