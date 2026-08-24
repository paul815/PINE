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

