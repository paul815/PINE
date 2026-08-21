"""Tags, themes, annotations, and the cross-recording quote index.

`tag_quotes` reads every transcript in the project, so it is cached on
file mtimes — see `_cached_json`.
"""

import json
import os
import re
from flask import jsonify, request
from ...extensions import db
from ...models.project import Project
from ...models.recording import Recording
from ...models.segment import Segment
from ...services.annotations import (
    annotation_recording_ref,
    annotations_filename,
    get_annotations,
    get_project_tags,
    get_project_themes,
    save_project_tags,
    save_project_themes,
    update_annotations,
)
from ...services.speaker_blocks import merge_speaker_blocks
from .common import _projects_root, _touch_project, projects_bp

@projects_bp.route('/<int:project_id>/tags', methods=['GET', 'PATCH'])
def project_tags(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project_dir = os.path.join(_projects_root(), project.folder_name)

    if request.method == 'GET':
        return jsonify(get_project_tags(project_dir))

    data = request.get_json(force=True)
    tags = data.get('tags')
    if tags is not None:
        save_project_tags(project_dir, tags)
        _touch_project(project)
        db.session.commit()
    return jsonify(get_project_tags(project_dir))

@projects_bp.route('/<int:project_id>/themes', methods=['GET', 'PATCH'])
def project_themes(project_id):
    """Tag themes (two-level hierarchy: theme → tags). Stored per-project."""
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project_dir = os.path.join(_projects_root(), project.folder_name)

    if request.method == 'GET':
        return jsonify(get_project_themes(project_dir))

    data = request.get_json(force=True)
    themes = data.get('themes')
    if themes is not None:
        save_project_themes(project_dir, themes)
        _touch_project(project)
        db.session.commit()
    return jsonify(get_project_themes(project_dir))

@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>/annotations', methods=['GET', 'PATCH'])
def recording_annotations(project_id, recording_id):
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project_dir = os.path.join(_projects_root(), project.folder_name)
    ann_ref = annotation_recording_ref(recording)

    if request.method == 'GET':
        return jsonify(get_annotations(project_dir, ann_ref))

    data = request.get_json(force=True)
    updates = {}
    for key in ('tag_spans', 'comments', 'speaker_labels', 'speaker_colors'):
        if key in data:
            updates[key] = data[key]
    ann = update_annotations(project_dir, ann_ref, updates)
    _touch_project(project)
    db.session.commit()
    return jsonify(ann)

# ── Tag quotes cache (mtime-based) ──
# Keyed by file path → (mtime, parsed_data)
_tq_cache = {}

_TQ_CACHE_MAX = 256  # bound memory: transcripts/annotations across all projects

def _cached_json(path):
    """Read JSON file with mtime-based caching. Returns parsed data or None."""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    cached = _tq_cache.get(path)
    if cached and cached[0] == mt:
        return cached[1]
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        # Simple bounded cache: drop everything once it grows too large rather
        # than leak one entry per file touched over the server's lifetime.
        if len(_tq_cache) >= _TQ_CACHE_MAX:
            _tq_cache.clear()
        _tq_cache[path] = (mt, data)
        return data
    except (OSError, json.JSONDecodeError):
        return None

def _merged_speaker_blocks(segments):
    """Speaker turns for anchor reconstruction (built once per recording so it
    is not quadratic in spans×segments). See ``services.speaker_blocks``."""
    return merge_speaker_blocks(segments)

def _anchor_text_from_span(span, segments, blocks=None):
    """Port of JS anchorTextFromAnnotationSpan — reconstruct full quote text
    for spans that may cross multiple segments / merged speaker blocks.

    ``blocks`` may be prebuilt via ``_merged_speaker_blocks`` to avoid rebuilding
    them for every span. Offsets use ``is not None`` (not truthiness) so a
    legitimate offset of 0 matches the JS ``??`` semantics exactly.
    """
    seg_idx = span.get('segment_idx')
    if seg_idx is None or not segments:
        return ''
    if blocks is None:
        blocks = _merged_speaker_blocks(segments)

    def find_block(idx):
        for b in blocks:
            if idx in b['indices']:
                return b
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
        # Different blocks — include whole blocks BETWEEN start and end, otherwise
        # a selection spanning 3+ speaker blocks silently drops the middle ones.
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

    # Fallback: single segment
    seg = segments[seg_idx] if 0 <= seg_idx < len(segments) else None
    if not seg:
        return ''
    st = (seg.get('text') or '').strip()
    sc = span.get('start_char')
    sc = sc if sc is not None else 0
    ec = span.get('end_char')
    ec = ec if ec is not None else len(st)
    return st[max(0, sc):min(len(st), ec)]

@projects_bp.route('/<int:project_id>/tags/quotes', methods=['GET'])
def tag_quotes(project_id):
    """Return all tagged quotes across all transcribed recordings, grouped by recording."""
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    project_dir = os.path.join(_projects_root(), project.folder_name)
    tags = get_project_tags(project_dir)      # normalized — never missing id/name
    themes = get_project_themes(project_dir)
    tag_map = {t['id']: t for t in tags}

    # Counters for each tag
    tag_quote_count = {t['id']: 0 for t in tags}
    tag_rec_sets = {t['id']: set() for t in tags}
    total_quotes = 0

    project_segments = Segment.query.filter_by(project_id=project_id).order_by(Segment.created_at).all()
    segment_map = {s.id: s.name for s in project_segments}

    recordings_out = []

    for rec in project.recordings:
        if rec.transcription_status != 'transcribed' or not rec.transcript_path:
            continue

        transcript_file = os.path.join(project_dir, rec.transcript_path)
        transcript = _cached_json(transcript_file)
        if transcript is None:
            continue

        segments = transcript.get('segments', [])
        blocks = _merged_speaker_blocks(segments)  # build once, reuse per span
        ann_file = os.path.join(
            project_dir,
            annotations_filename(annotation_recording_ref(rec)),
        )
        ann = _cached_json(ann_file) or {'tag_spans': [], 'comments': [], 'speaker_labels': {}}
        tag_spans = ann.get('tag_spans', [])
        speaker_labels = ann.get('speaker_labels', {})
        comments_list = ann.get('comments', [])
        comment_map = {c['segment_idx']: c['text'] for c in comments_list if 'segment_idx' in c}

        if not tag_spans:
            continue

        quotes = []
        for span in tag_spans:
            seg_idx = span.get('segment_idx')
            tag_id = span.get('tag_id')
            if seg_idx is None or tag_id is None:
                continue
            if seg_idx < 0 or seg_idx >= len(segments):
                continue

            seg = segments[seg_idx]
            quote_text = _anchor_text_from_span(span, segments, blocks).strip()
            if not quote_text:
                continue

            raw_speaker = seg.get('speaker', '')
            speaker = speaker_labels.get(raw_speaker, raw_speaker) or 'Speaker'
            timestamp = seg.get('start', 0)

            tag_info = tag_map.get(tag_id, {'id': tag_id, 'name': tag_id, 'color': 'fu'})

            quotes.append({
                'text': quote_text,
                'speaker': speaker,
                'timestamp': timestamp,
                'tag_id': tag_id,
                'tag_name': tag_info.get('name', tag_id),
                'tag_color': tag_info.get('color', 'fu'),
                'comment': comment_map.get(seg_idx),
            })

            if tag_id in tag_quote_count:
                tag_quote_count[tag_id] += 1
                tag_rec_sets[tag_id].add(rec.id)
            total_quotes += 1

        if quotes:
            recordings_out.append({
                'id': rec.id,
                'name': rec.original_name,
                'duration_seconds': rec.duration_seconds or 0,
                'segment_id': rec.segment_id,
                'segment_name': segment_map.get(rec.segment_id) if rec.segment_id else None,
                'quotes': quotes,
            })

    tags_out = []
    for t in tags:
        tid = t['id']
        tags_out.append({
            'id': tid,
            'name': t['name'],
            'color': t.get('color', 'fu'),
            'description': t.get('description', ''),
            'group_id': t.get('group_id'),
            'count': tag_quote_count.get(tid, 0),
            'recording_count': len(tag_rec_sets.get(tid, set())),
        })

    return jsonify({
        'project': {
            'id': project.id,
            'name': project.name,
            'icon': project.icon or '📄',
        },
        'segments': [{'id': s.id, 'name': s.name} for s in project_segments],
        'themes': themes,
        'tags': tags_out,
        'recordings': recordings_out,
        'total_quotes': total_quotes,
    })
