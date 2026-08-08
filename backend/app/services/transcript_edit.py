"""Transcript text editing (find & replace) with annotation offset migration.

The transcript is stored as ``{"segments": [{text, speaker, start, end, words}]}``.
Tag spans and comments live in a *separate* annotations file and reference text
positions as ``segment_idx`` + ``start_char``/``end_char`` (relative to the
*trimmed* segment text), with optional cross-segment ``end_segment_idx`` /
``end_seg_end_char`` and cached merged-block offsets (``merged_start`` etc.).

When find & replace changes the length of a segment's text, every annotation
offset that sits after the edit must shift, or the highlight would drift onto
the wrong words. ``apply_find_replace`` does that migration so existing tags and
comments stay anchored to the same words.
"""

import re

from .speaker_blocks import block_offsets


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

    return count
