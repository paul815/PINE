"""Annotations storage: tags, comments. Stored as JSON in project folders."""

import json
import os
import re
import threading

from .file_utils import atomic_read_json as _atomic_read_json
from .file_utils import atomic_write_json as _atomic_write_json

# Per-file locks to serialize read-modify-write on annotation JSON files.
_annotation_locks = {}
_annotation_locks_lock = threading.Lock()


def _get_lock(path):
    """Get or create a Lock for a given annotation file path."""
    lock = _annotation_locks.get(path)
    if lock is not None:
        return lock
    with _annotation_locks_lock:
        lock = _annotation_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _annotation_locks[path] = lock
        return lock

DEFAULT_TAGS = [
    {'id': 'pain', 'name': 'Pain point', 'color': 'pain'},
    {'id': 'insight', 'name': 'Insight', 'color': 'ins'},
    {'id': 'delight', 'name': 'Delight', 'color': 'del'},
    {'id': 'confusion', 'name': 'Confusion', 'color': 'conf'},
    {'id': 'followup', 'name': 'Follow-up needed', 'color': 'fu'},
]

TAG_COLORS = {
    'pain': '#f04f4f',
    'ins': '#34c97a',
    'del': '#f5a623',
    'conf': '#5da8e0',
    'fu': '#a0a0b8',
}

# Named color tokens understood by the CSS (var(--tag-*)).
KNOWN_TAG_COLORS = ('pain', 'ins', 'del', 'conf', 'fu')
_HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{3,8}$')


def safe_color(value, default='fu'):
    """Return a color that is safe to interpolate into CSS / HTML attributes.

    Accepts a known token (pain/ins/del/conf/fu) or a ``#hex`` string. Anything
    else (including markup-injection attempts) collapses to ``default``. This is
    the server-side half of the XSS defense for tag/theme colors.
    """
    if isinstance(value, str):
        v = value.strip()
        if v in KNOWN_TAG_COLORS or _HEX_COLOR_RE.match(v):
            return v
    return default


def _clean_str(value, default=''):
    return value.strip() if isinstance(value, str) else default


def normalize_tag(raw):
    """Coerce one tag dict to a safe, fully-populated shape, or None if invalid.

    A tag must have a non-empty id and name; everything else is defaulted.
    """
    if not isinstance(raw, dict):
        return None
    tid = _clean_str(raw.get('id'))
    name = _clean_str(raw.get('name'))
    if not tid or not name:
        return None
    group_id = _clean_str(raw.get('group_id')) or None
    return {
        'id': tid,
        'name': name,
        'color': safe_color(raw.get('color')),
        'description': _clean_str(raw.get('description')),
        'group_id': group_id,
    }


def normalize_tags(tags):
    """Drop malformed entries and de-duplicate by id. Always returns a list."""
    if not isinstance(tags, list):
        return []
    out, seen = [], set()
    for raw in tags:
        tag = normalize_tag(raw)
        if tag and tag['id'] not in seen:
            seen.add(tag['id'])
            out.append(tag)
    return out


def normalize_theme(raw):
    """Coerce one theme (tag group) dict to a safe shape, or None if invalid."""
    if not isinstance(raw, dict):
        return None
    tid = _clean_str(raw.get('id'))
    name = _clean_str(raw.get('name'))
    if not tid or not name:
        return None
    return {
        'id': tid,
        'name': name,
        'color': safe_color(raw.get('color')),
    }


def normalize_themes(themes):
    if not isinstance(themes, list):
        return []
    out, seen = [], set()
    for raw in themes:
        theme = normalize_theme(raw)
        if theme and theme['id'] not in seen:
            seen.add(theme['id'])
            out.append(theme)
    return out


def _project_tags_path(project_dir):
    return os.path.join(project_dir, 'project_tags.json')


def _project_themes_path(project_dir):
    return os.path.join(project_dir, 'project_themes.json')


def annotation_stem(recording_ref):
    base = os.path.basename(recording_ref or '')
    if base.endswith('_transcript.json'):
        return base[:-len('_transcript.json')]
    return os.path.splitext(base)[0]


def annotation_recording_ref(recording):
    """Return a stable per-recording reference used to name annotation files."""
    transcript_path = (getattr(recording, 'transcript_path', None) or '').strip()
    if transcript_path:
        return transcript_path

    stored_name = (getattr(recording, 'stored_name', None) or '').strip()
    recording_id = getattr(recording, 'id', None)
    if recording_id is None:
        return stored_name

    base = os.path.splitext(os.path.basename(stored_name))[0] or 'recording'
    return f'{base}_{recording_id}_transcript.json'


def annotations_filename(recording_ref):
    return f'{annotation_stem(recording_ref)}_annotations.json'


def _annotations_path(project_dir, recording_ref):
    base = annotation_stem(recording_ref)
    return os.path.join(project_dir, f'{base}_annotations.json')


def get_project_tags(project_dir):
    """Load project tags, normalized. Returns empty list if none saved yet.

    Normalizing on read means a hand-edited or imported ``project_tags.json``
    can never crash consumers (e.g. the analysis screen) with a missing key.
    """
    path = _project_tags_path(project_dir)
    if os.path.isfile(path):
        try:
            return normalize_tags(_atomic_read_json(path))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_project_tags(project_dir, tags):
    path = _project_tags_path(project_dir)
    _atomic_write_json(path, normalize_tags(tags))


def get_project_themes(project_dir):
    """Load tag themes (groups), normalized. Empty list if none saved yet."""
    path = _project_themes_path(project_dir)
    if os.path.isfile(path):
        try:
            return normalize_themes(_atomic_read_json(path))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_project_themes(project_dir, themes):
    path = _project_themes_path(project_dir)
    _atomic_write_json(path, normalize_themes(themes))


def _default_annotations():
    return {
        'tag_spans': [],
        'comments': [],
        'speaker_labels': {},
        # Display name -> speaker colour class chosen in the speaker popover.
        'speaker_colors': {},
    }


def get_annotations(project_dir, recording_stored_name):
    """Read annotations under a per-file lock."""
    path = _annotations_path(project_dir, recording_stored_name)
    lock = _get_lock(path)
    with lock:
        if os.path.isfile(path):
            try:
                return _atomic_read_json(path)
            except (json.JSONDecodeError, OSError):
                pass
        return _default_annotations()


def save_annotations(project_dir, recording_stored_name, data):
    """Write annotations atomically under a per-file lock."""
    path = _annotations_path(project_dir, recording_stored_name)
    lock = _get_lock(path)
    with lock:
        _atomic_write_json(path, data)


# ── anchors ──────────────────────────────────────────────────────────────────
#
# A tag or comment is anchored by position: a segment index and character
# offsets inside it, plus the cached offsets of the merged speaker block it
# reads in. Positions are what the highlight is drawn from, and positions are
# also what an edit to the transcript can quietly invalidate.
#
# ``anchor_text`` is the words the anchor pointed at when it was made. It was
# only ever a fallback — used when the offsets failed to resolve at all — which
# is the one case where it cannot help: offsets that resolve to the *wrong*
# words resolve perfectly well. So the snapshot said one thing, the offsets said
# another, and nothing compared them.
#
# Here it becomes the check instead. ``refresh_anchor_text`` rewrites the
# snapshot whenever an edit has just moved the offsets, so it is never stale,
# and ``mark_anchor_drift`` compares the two on the way out so a quote that no
# longer matches what it was taken from can be shown as needing a look, rather
# than shown confidently wrong.


def anchor_text_from_span(span, segments, blocks=None):
    """The words ``span``'s offsets currently point at.

    Mirrors the client's ``anchorTextFromAnnotationSpan``: a span may run past
    its own segment, and even past its own speaker block, so the text is
    reconstructed from the merged blocks rather than from one segment's text.
    ``blocks`` may be prebuilt to keep a whole recording's spans from rebuilding
    them each time. Offsets are tested with ``is not None`` — an offset of 0 is
    a real offset, and the client's ``??`` treats it as one too.
    """
    from .speaker_blocks import merge_speaker_blocks

    seg_idx = span.get('segment_idx')
    if seg_idx is None or not segments:
        return ''
    if blocks is None:
        blocks = merge_speaker_blocks(segments)

    def find_block(idx):
        for block in blocks:
            if idx in block['indices']:
                return block
        return None

    start_block = find_block(seg_idx)
    if not start_block:
        return ''
    mt = start_block['text']

    merged_start = span.get('merged_start')
    merged_end = span.get('merged_end')

    end_seg_idx = span.get('end_segment_idx')
    if end_seg_idx is not None:
        end_block = find_block(end_seg_idx)
        if not end_block:
            return ''
        end_off = span.get('end_merged_end')
        if end_off is None:
            end_off = span.get('end_seg_end_char')
        ms = merged_start if merged_start is not None else 0
        if start_block is end_block:
            if end_off is not None:
                me = end_off
            elif merged_end is not None:
                me = merged_end
            else:
                me = len(mt)
            return mt[max(0, ms):min(len(mt), me)]
        # Different blocks — the whole blocks BETWEEN start and end come along,
        # otherwise a selection spanning three or more of them silently drops
        # everything in the middle.
        me_first = merged_end if merged_end is not None else len(mt)
        first = mt[max(0, ms):min(len(mt), me_first)]
        et = end_block['text']
        ec = end_off if end_off is not None else 0
        second = et[:min(len(et), ec)]
        si = next((i for i, b in enumerate(blocks) if b is start_block), -1)
        ei = next((i for i, b in enumerate(blocks) if b is end_block), -1)
        middle = [b['text'] for b in blocks[si + 1:ei]] if 0 <= si < ei else []
        return re.sub(r'\s+', ' ', ' '.join([first, *middle, second])).strip()

    if merged_start is not None and merged_end is not None:
        return mt[max(0, merged_start):min(len(mt), merged_end)]

    seg = segments[seg_idx] if 0 <= seg_idx < len(segments) else None
    if not seg:
        return ''
    st = (seg.get('text') or '').strip()
    sc = span.get('start_char')
    sc = sc if sc is not None else 0
    ec = span.get('end_char')
    ec = ec if ec is not None else len(st)
    return st[max(0, sc):min(len(st), ec)]


def _comparable(text):
    """Whitespace is not what an anchor is about; a run of it is one space."""
    return ' '.join((text or '').split())


def annotation_spans(annotations):
    """Every tag span and comment in ``annotations``, in one pass."""
    for key in ('tag_spans', 'comments'):
        for span in annotations.get(key) or []:
            if isinstance(span, dict):
                yield span


def refresh_anchor_text(annotations, segments):
    """Re-take every anchor's snapshot from where its offsets now point.

    Call this straight after migrating offsets through an edit, while the two
    still agree by construction. Mutates ``annotations`` in place; returns the
    number of snapshots that changed, which is how many quotes the edit reworded.
    """
    from .speaker_blocks import merge_speaker_blocks

    blocks = merge_speaker_blocks(segments)
    changed = 0
    for span in annotation_spans(annotations):
        if span.get('segment_idx') is None:
            continue
        current = anchor_text_from_span(span, segments, blocks)
        if not current:
            # Nothing resolvable to snapshot: leave whatever is there, so a span
            # that cannot be placed keeps the words it remembers.
            continue
        if _comparable(span.get('anchor_text')) != _comparable(current):
            changed += 1
        span['anchor_text'] = current
    return changed


def mark_anchor_drift(annotations, segments):
    """A copy of ``annotations`` where anchors that moved say so.

    A span carries ``anchor_drifted`` when it remembers words that its offsets
    no longer point at — the transcript was edited by something that did not
    migrate them, or an import brought anchors from a different revision of the
    text. The flag is advisory: the span is still returned, still placed where
    its offsets say, and the reader is told not to trust it.

    Spans with no snapshot are left alone. There is nothing to compare them
    against, and a warning nobody can act on is worse than silence.
    """
    from .speaker_blocks import merge_speaker_blocks

    if not segments:
        return annotations
    blocks = merge_speaker_blocks(segments)
    out = dict(annotations)
    for key in ('tag_spans', 'comments'):
        source = annotations.get(key) or []
        marked = []
        for span in source:
            if not isinstance(span, dict):
                marked.append(span)
                continue
            remembered = span.get('anchor_text')
            if not remembered or span.get('segment_idx') is None:
                marked.append(span)
                continue
            current = anchor_text_from_span(span, segments, blocks)
            if current and _comparable(current) != _comparable(remembered):
                span = dict(span, anchor_drifted=True, anchor_text_now=current)
            marked.append(span)
        out[key] = marked
    return out


def update_annotations(project_dir, recording_stored_name, updates):
    """Atomically read, merge updates, and write annotations.

    ``updates`` is a dict of keys to set (e.g. {'speaker_labels': {...}}).
    Returns the merged annotations dict.
    """
    path = _annotations_path(project_dir, recording_stored_name)
    lock = _get_lock(path)
    with lock:
        if os.path.isfile(path):
            try:
                ann = _atomic_read_json(path)
            except (json.JSONDecodeError, OSError):
                ann = _default_annotations()
        else:
            ann = _default_annotations()
        for key, value in updates.items():
            ann[key] = value
        _atomic_write_json(path, ann)
        return ann

