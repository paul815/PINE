"""Transcript text editing, with annotation offset migration.

Two ways in: find & replace across the whole transcript, and rewriting one
speaker block the way it reads on screen.

The transcript is stored as ``{"segments": [{text, speaker, start, end, words}]}``.
Tag spans and comments live in a *separate* annotations file and reference text
positions as ``segment_idx`` + ``start_char``/``end_char`` (relative to the
*trimmed* segment text), with optional cross-segment ``end_segment_idx`` /
``end_seg_end_char`` and cached merged-block offsets (``merged_start`` etc.).

When find & replace changes the length of a segment's text, every annotation
offset that sits after the edit must shift, or the highlight would drift onto
the wrong words. ``migrate_annotation_offsets`` does that migration so existing
tags and comments stay anchored to the same words.
"""

import re

from .annotations import refresh_anchor_text
from .speaker_blocks import (
    block_offsets,
    block_text_and_offsets,
    merge_speaker_blocks,
)

# What apply_block_edit did, for the endpoint to turn into a status code.
BLOCK_EDIT_OK = 'ok'
BLOCK_EDIT_UNCHANGED = 'unchanged'
BLOCK_EDIT_STALE = 'stale'
BLOCK_EDIT_EMPTY = 'empty'


def _build_mapper(edits):
    """Return f(old_offset, is_end) -> new_offset for one segment's edits.

    ``edits`` is a list of ``(match_start, match_end, new_len)`` tuples, ordered
    by position and non-overlapping. Offsets after a match shift by the cumulative
    length delta. An offset that lands *inside* a replaced span is clamped to the
    edit boundary (start side collapses to the match start; end side extends to
    the end of the replacement) so spans never invert or point mid-replacement.
    """
    def mapper(offset, is_end=False):
        delta = 0
        for start, end, new_len in edits:
            if offset >= end:
                delta += new_len - (end - start)
            elif offset > start:
                # Inside this match.
                return start + delta + (new_len if is_end else 0)
            else:
                break
        return offset + delta
    return mapper


def _block_offsets(segments):
    """Char offset of each segment within its merged speaker-turn block.

    Mirrors the client's merge so cached ``merged_*`` offsets can be rebuilt
    consistently. See ``services.speaker_blocks``.
    """
    return block_offsets(segments)


def _compile(find, match_case, whole_word):
    pattern = re.escape(find)
    if whole_word:
        pattern = r'\b' + pattern + r'\b'
    flags = 0 if match_case else re.IGNORECASE
    return re.compile(pattern, flags)


def migrate_annotation_offsets(segments, annotations, mappers):
    """Shift tag and comment offsets to follow text that changed length.

    ``mappers`` maps a segment index to that segment's ``_build_mapper``
    closure; a segment missing from it was left untouched, so its offsets pass
    through. ``segments`` must already hold the *new* text — the cached
    ``merged_*`` offsets are rebuilt from it. Mutates ``annotations`` in place.

    Each anchor's ``anchor_text`` is re-taken at the end. The offsets and the
    snapshot are only guaranteed to agree at this moment, having just been made
    to; leaving the old wording behind would give every later check two answers
    and no way to tell which one was right.
    """
    boff = _block_offsets(segments)

    def remap(idx, value, is_end):
        mp = mappers.get(idx)
        return mp(value, is_end) if mp else value

    for collection in (annotations.get('tag_spans') or [],
                       annotations.get('comments') or []):
        for span in collection:
            start_idx = span.get('segment_idx')
            if start_idx is None:
                continue
            if 'start_char' in span:
                span['start_char'] = remap(start_idx, span.get('start_char') or 0, False)
            end_idx = span.get('end_segment_idx')
            if end_idx is not None:
                if 'end_seg_end_char' in span:
                    span['end_seg_end_char'] = remap(
                        end_idx, span.get('end_seg_end_char') or 0, True)
                if 'merged_start' in span:
                    span['merged_start'] = boff.get(start_idx, 0) + (span.get('start_char') or 0)
                if 'end_merged_end' in span:
                    span['end_merged_end'] = boff.get(end_idx, 0) + (span.get('end_seg_end_char') or 0)
            else:
                if 'end_char' in span:
                    span['end_char'] = remap(start_idx, span.get('end_char') or 0, True)
                if 'merged_start' in span:
                    span['merged_start'] = boff.get(start_idx, 0) + (span.get('start_char') or 0)
                if 'merged_end' in span:
                    span['merged_end'] = boff.get(start_idx, 0) + (span.get('end_char') or 0)

    refresh_anchor_text(annotations, segments)


def apply_find_replace(segments, annotations, find, replace,
                       match_case=False, whole_word=False):
    """Replace ``find`` with ``replace`` across all segments; migrate offsets.

    Mutates ``segments`` and ``annotations`` in place and returns the number of
    replacements made. A return of 0 means nothing matched and nothing changed.
    """
    if not find:
        return 0

    pattern = _compile(find, match_case, whole_word)
    # Treat the replacement literally — never as a regex backreference template.
    repl = lambda _m: replace  # noqa: E731

    mappers = {}
    count = 0
    for i, seg in enumerate(segments):
        old = (seg.get('text') or '').strip()
        matches = list(pattern.finditer(old))
        if not matches:
            continue
        edits = [(m.start(), m.end(), len(replace)) for m in matches]
        seg['text'] = pattern.sub(repl, old)
        mappers[i] = _build_mapper(edits)
        count += len(matches)
        # Keep word-level tokens aligned for click-to-seek where the match is
        # contained in a single token (the common ASR-typo case).
        for w in seg.get('words') or []:
            if 'word' in w:
                w['word'] = pattern.sub(repl, w['word'])

    if not count:
        return 0

    migrate_annotation_offsets(segments, annotations, mappers)

    return count


def _normalize(text):
    """Collapse to single spaces what a contenteditable hands back.

    Typing in a browser produces newlines, non-breaking spaces and runs of
    ordinary ones; none of them mean anything in a transcript, and letting them
    through would make the block text on screen differ from the stored one.
    """
    return ' '.join((text or '').split())


def _changed_range(old, new):
    """The one span that differs, as ``(start, end_in_old, replacement)``.

    Common prefix and suffix are peeled off, so fixing a typo reports the typo
    rather than the paragraph -- which is what keeps every annotation outside
    the misspelt word exactly where it was.
    """
    limit = min(len(old), len(new))
    lo = 0
    while lo < limit and old[lo] == new[lo]:
        lo += 1
    tail = 0
    while tail < limit - lo and old[len(old) - 1 - tail] == new[len(new) - 1 - tail]:
        tail += 1
    return lo, len(old) - tail, new[lo:len(new) - tail]


def _token_spans(text, words):
    """Where each word token sits in ``text``, mirroring how the client finds them.

    MLX and some other engines hand back tokens whose spacing does not
    substring-match the segment text; the renderer falls forward to the running
    position in that case, and so does this.
    """
    spans = []
    pos = 0
    for w in words:
        token = (w.get('word') or '').strip()
        if not token:
            continue
        at = text.find(token, pos)
        if at < 0:
            at = pos
        spans.append((at, at + len(token), w))
        pos = at + len(token)
    return spans


def _reflow_words(seg, old_text, lo, hi, kept_tail_len):
    """Keep the timings of untouched words; interpolate the ones just typed.

    A word the user did not touch keeps the time it was aligned to. A word they
    typed has no time of its own, so it gets an even share of the gap between
    the last untouched word before it and the first one after -- close enough
    that clicking it lands in the right sentence, and marked ``approx`` so a
    made-up time can never be mistaken for an aligned one.
    """
    words = seg.get('words') or []
    if not words:
        return
    new_text = seg.get('text') or ''
    spans = _token_spans(old_text, words)

    # The typo case: the change sits inside one token and adds no word break.
    # Respelling it keeps its own timing exactly, which is the whole point.
    covering = [(s, e, w) for s, e, w in spans if s <= lo and e >= hi]
    replacement = new_text[lo:len(new_text) - kept_tail_len]
    if len(covering) == 1 and replacement and ' ' not in replacement:
        s, e, w = covering[0]
        w['word'] = old_text[s:lo] + replacement + old_text[hi:e]
        return

    before = [w for s, e, w in spans if e <= lo]
    after = [w for s, e, w in spans if s >= hi]

    start = before[-1].get('end') if before else seg.get('start')
    end = after[0].get('start') if after else seg.get('end')
    if start is None:
        start = seg.get('start')
    if end is None:
        end = seg.get('end')

    fresh = []
    tokens = replacement.split()
    if tokens and start is not None and end is not None:
        step = (end - start) / len(tokens) if end > start else 0.0
        for n, token in enumerate(tokens):
            fresh.append({
                'word': token,
                'start': start + step * n,
                'end': start + step * (n + 1),
                'approx': True,
            })
    elif tokens:
        fresh = [{'word': token, 'approx': True} for token in tokens]

    seg['words'] = before + fresh + after


def apply_block_edit(segments, annotations, indices, original_text, new_text):
    """Rewrite one speaker block's text; migrate annotations and word timings.

    ``indices`` names the block the way ``merge_speaker_blocks`` folded it --
    not a range, since an interrupted speaker resumes into the block they
    opened and their indices come back with a hole in them. ``original_text``
    is the block as the client had it; a mismatch means someone else edited
    this transcript in between, and rather than clobber their work we say so.

    The change is mapped back onto the segments it covers. One segment is the
    common case and stays one segment. An edit that runs across a boundary
    collapses into the first segment it touched and leaves the rest blank:
    segments are never dropped or inserted, so ``segment_idx`` in the
    annotations file keeps pointing at the same thing.

    Mutates ``segments`` and ``annotations`` in place. Returns one of the
    ``BLOCK_EDIT_*`` constants.
    """
    indices = list(indices)
    block = next((b for b in merge_speaker_blocks(segments)
                  if b['indices'] == indices), None)
    if block is None:
        return BLOCK_EDIT_STALE

    old, offsets = block_text_and_offsets(segments, indices)
    if _normalize(original_text) != _normalize(old):
        return BLOCK_EDIT_STALE

    new = _normalize(new_text)
    if not new:
        return BLOCK_EDIT_EMPTY
    if new == old:
        return BLOCK_EDIT_UNCHANGED

    lo, hi, repl = _changed_range(old, new)

    spans = {}
    for i in indices:
        text = (segments[i].get('text') or '').strip()
        if text:
            spans[i] = (offsets[i], offsets[i] + len(text), text)

    # Ends included: a zero-width insertion sits on a boundary rather than
    # inside anything, and a deletion that swallowed a seam has to reach the
    # segments either side of it.
    touched = [i for i, (s, e, _t) in spans.items() if s <= hi and e >= lo]
    if not touched:
        return BLOCK_EDIT_STALE

    # Give each segment the part of the change that fell inside it. The typed
    # text itself goes to the first segment the change reached -- for an
    # insertion on a seam that is the one segment the bounds above admitted, so
    # there is nothing to choose between.
    owner = touched[0]
    rewritten = {}
    for i in touched:
        start, end, text = spans[i]
        cut_lo = min(max(lo, start), end) - start
        cut_hi = min(max(hi, start), end) - start
        piece = repl if i == owner else ''
        rewritten[i] = (text[:cut_lo] + piece + text[cut_hi:]).strip()

    collapsed = _would_read_as(segments, indices, rewritten) != new
    if collapsed:
        # The change closed a seam. The space between two segments is written
        # by the join and belongs to neither of them, so the only way to lose
        # it is to merge what it separated: everything the change reached
        # collapses into the first segment, and the rest keep their place in
        # the list with nothing in them.
        last = touched[-1]
        head_end = min(max(lo, spans[owner][0]), spans[owner][1]) - spans[owner][0]
        tail_start = min(max(hi, spans[last][0]), spans[last][1]) - spans[last][0]
        rewritten = dict.fromkeys(touched, '')
        rewritten[owner] = (spans[owner][2][:head_end] + repl
                            + spans[last][2][tail_start:]).strip()

    edits = {}
    for i in touched:
        was, text = spans[i][2], rewritten[i]
        if text == was:
            continue
        cut_lo, cut_hi, piece = _changed_range(was, text)
        segments[i]['text'] = text
        edits[i] = [(cut_lo, cut_hi, len(piece))]
        if collapsed and segments[i].get('words'):
            # Words either moved into another segment or describe text that is
            # gone. Either way their times no longer say where their words
            # are, and a wrong time sends the user somewhere wrong; dropping
            # them falls back to seeking by paragraph, which is honest.
            segments[i]['words'] = []
        elif not collapsed:
            _reflow_words(segments[i], was, cut_lo, cut_hi, len(was) - cut_hi)

    mappers = {i: _build_mapper(e) for i, e in edits.items()}
    migrate_annotation_offsets(segments, annotations, mappers)
    _drop_collapsed_spans(annotations)

    return BLOCK_EDIT_OK


def _would_read_as(segments, indices, rewritten):
    """The block text these proposed segment texts would fold back into.

    Mirrors ``block_text_and_offsets``: the non-empty ones, single-spaced.
    """
    parts = []
    for i in indices:
        text = rewritten.get(i)
        if text is None:
            text = (segments[i].get('text') or '').strip()
        if text:
            parts.append(text)
    return ' '.join(parts)


def _drop_collapsed_spans(annotations):
    """Forget tags and comments whose words the edit removed outright.

    ``_build_mapper`` clamps an offset that lands inside replaced text to the
    edit boundary, so a span whose every word is gone comes out zero-length. It
    no longer marks anything; leaving it in would draw an empty highlight and
    keep the tag counted against the recording.
    """
    def collapsed(span):
        # Only a span that says where it starts *and* ends can be judged
        # empty. A comment pinned to a segment without a range is not a
        # highlight and has nothing to lose.
        if 'start_char' not in span or 'end_char' not in span:
            return False
        end_idx = span.get('end_segment_idx')
        if end_idx is not None and end_idx != span.get('segment_idx'):
            return False
        return (span.get('end_char') or 0) <= (span.get('start_char') or 0)

    for key in ('tag_spans', 'comments'):
        collection = annotations.get(key)
        if not collection:
            continue
        annotations[key] = [s for s in collection if not collapsed(s)]
