import os
import re
import subprocess
import json
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, current_app, send_file, Response
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models.project import Project
from ..models.recording import Recording
from ..models.segment import Segment
from ..models.setting import Setting
from ..services.annotations import (
    get_project_tags,
    save_project_tags,
    get_project_themes,
    save_project_themes,
    get_annotations,
    update_annotations,
    save_annotations,
    annotations_filename,
    annotation_recording_ref,
)
from ..services.file_utils import atomic_write_text

projects_bp = Blueprint('projects', __name__)

ALLOWED_EXTENSIONS = {'mp3', 'mp4', 'm4a', 'wav', 'mkv', 'webm', 'ogg', 'flac'}


def _safe_ascii(name):
    """ASCII-only fallback name for Content-Disposition filename= param."""
    if not name:
        return 'export'
    safe = ''.join(c if ord(c) < 128 else '_' for c in str(name))
    return re.sub(r'[^\w\s\-.]', '', safe).strip() or 'export'


def _content_disposition(filename):
    """Build a Content-Disposition header value with RFC 5987 Unicode support.
    Modern browsers use filename*=UTF-8''<encoded>; old clients fall back to filename=."""
    from urllib.parse import quote
    ascii_name = _safe_ascii(filename)
    encoded = quote(str(filename).encode('utf-8'), safe=' -()')
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _build_recording_export_filename(project_name, recording_name, ext):
    """Build export filename as <project>-<recording>.<ext>."""
    project_part = (project_name or '').strip()
    recording_part = os.path.splitext(recording_name or '')[0].strip() or 'recording'
    if project_part:
        return f'{project_part}-{recording_part}{ext}'
    return f'{recording_part}{ext}'


def _write_project_readme(project):
    """Write a README.md with project metadata into the project folder."""
    root = _projects_root()
    folder = os.path.join(root, project.folder_name)
    if not os.path.isdir(folder):
        return

    lines = [f'# {project.name}', '']

    if project.description:
        lines += [project.description, '']

    if project.objective:
        lines += ['## Objective', '', project.objective, '']

    if project.methodology:
        lines += ['## Methodology', '', project.methodology, '']

    if project.interview_guide:
        lines += ['## Interview Guide', '', project.interview_guide, '']

    questions = project.get_questions()
    if questions:
        lines += ['## Research Questions', '']
        for i, q in enumerate(questions, 1):
            lines.append(f'{i}. {q}')
        lines.append('')

    hypotheses = project.get_hypotheses()
    if hypotheses:
        lines += ['## Hypotheses', '']
        for i, h in enumerate(hypotheses, 1):
            lines.append(f'{i}. {h}')
        lines.append('')

    stakeholders = project.get_stakeholders()
    if stakeholders:
        lines += ['## Stakeholders', '']
        for i, s in enumerate(stakeholders, 1):
            lines.append(f'{i}. {s}')
        lines.append('')

    summary_sections = project.get_enabled_summary_sections()
    summary_content = []
    for key in summary_sections:
        if key == 'key_findings' and project.key_findings:
            summary_content.append(('Key Findings', project.key_findings))
        elif key == 'further_steps' and project.further_steps:
            summary_content.append(('Further Steps', project.further_steps))
        elif key == 'recommendations' and project.recommendations:
            summary_content.append(('Recommendations', project.recommendations))
    if summary_content:
        lines += ['## Summary', '']
        for title, content in summary_content:
            lines += [f'### {title}', '', content, '']

    recs = project.recordings
    if recs:
        lines += ['## Recordings', '']
        for r in recs:
            status = r.transcription_status or 'pending'
            lines.append(f'- **{r.original_name}** ({status})')
        lines.append('')

    lines += [
        '---',
        f'*Created: {project.created_at.strftime("%Y-%m-%d") if project.created_at else "—"}*  ',
        f'*Updated: {project.updated_at.strftime("%Y-%m-%d") if project.updated_at else "—"}*',
    ]

    readme_path = os.path.join(folder, 'README.md')
    atomic_write_text(readme_path, '\n'.join(lines) + '\n')


def _projects_root():
    return Setting.get('projects_path', current_app.config['DEFAULT_PROJECTS_PATH'])


def _recording_filepath(project, recording):
    """Return the absolute path to the media file for a recording.

    Linked recordings store the absolute path directly in stored_name.
    Copied recordings store just the filename relative to the project folder.
    """
    if recording.is_linked:
        return recording.stored_name
    return os.path.join(_projects_root(), project.folder_name, recording.stored_name)


def _delete_recording_annotation_files(project_dir, recording):
    """Delete both current and legacy annotation files for a recording."""
    refs = {
        annotation_recording_ref(recording),
        (recording.stored_name or '').strip(),
    }
    for ref in refs:
        if not ref:
            continue
        ann_path = os.path.join(project_dir, annotations_filename(ref))
        if os.path.isfile(ann_path):
            os.remove(ann_path)


def _safe_folder_name(name, existing_names=None):
    """Turn a project name into a filesystem-safe folder name, avoiding collisions."""
    slug = re.sub(r'[^\w\s\-]', '', name).strip()
    slug = re.sub(r'\s+', '_', slug) or 'project'
    slug = slug[:80]

    if existing_names is None:
        existing_names = set()

    candidate = slug
    counter = 1
    while candidate.lower() in {n.lower() for n in existing_names}:
        counter += 1
        candidate = f'{slug}_{counter}'
    return candidate


def _mp4_faststart(filepath):
    """Move MP4 moov atom to front for instant browser playback."""
    import tempfile
    tmp = filepath + '.faststart.mp4'
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', filepath, '-c', 'copy',
             '-movflags', '+faststart', tmp],
            capture_output=True, timeout=120,
        )
        if result.returncode == 0 and os.path.isfile(tmp):
            os.replace(tmp, filepath)
    except Exception:
        pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _probe_duration(filepath):
    """Use ffprobe to get duration in seconds. Returns 0 on failure."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', filepath],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0


# ── List all projects ──

@projects_bp.route('', methods=['GET'])
def list_projects():
    projects = Project.query.filter(
        (Project.is_system == False) | (Project.is_system == None)  # noqa: E712
    ).order_by(Project.updated_at.desc()).all()
    raw_inc = request.args.get('include_tags') or request.args.get('includeTags') or ''
    include_tags = str(raw_inc).lower() in ('1', 'true', 'yes')
    root = _projects_root() if include_tags else None

    def to_entry(p):
        d = p.to_dict()
        if include_tags:
            project_dir = os.path.normpath(os.path.join(root, p.folder_name))
            d['tags'] = get_project_tags(project_dir)
        return d

    active = [to_entry(p) for p in projects if not p.is_archived]
    archived = [to_entry(p) for p in projects if p.is_archived]
    return jsonify({'active': active, 'archived': archived})


# ── Single transcriptions (no-project mode) ──

SYSTEM_PROJECT_NAME = '__single_transcriptions'


def _get_or_create_system_project():
    """Return the hidden system project, creating it on first use."""
    proj = Project.query.filter_by(is_system=True).first()
    if proj:
        return proj
    root = _projects_root()
    proj = Project(
        name=SYSTEM_PROJECT_NAME,
        folder_name=SYSTEM_PROJECT_NAME,
        is_system=True,
    )
    db.session.add(proj)
    db.session.flush()
    os.makedirs(os.path.join(root, SYSTEM_PROJECT_NAME), exist_ok=True)
    db.session.commit()
    return proj


@projects_bp.route('/single-transcriptions', methods=['GET'])
def list_single_transcriptions():
    proj = Project.query.filter_by(is_system=True).first()
    if not proj:
        return jsonify({'recordings': [], 'project_id': None})
    recs = Recording.query.filter_by(project_id=proj.id)\
        .order_by(Recording.created_at.desc()).all()
    return jsonify({
        'recordings': [r.to_dict() for r in recs],
        'project_id': proj.id,
    })


@projects_bp.route('/single-transcriptions', methods=['POST'])
def upload_single_transcription():
    proj = _get_or_create_system_project()

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': f'Unsupported format: .{ext}'}), 400

    original_name = file.filename
    safe_name = secure_filename(original_name)
    if not safe_name:
        safe_name = f'recording.{ext}'

    project_dir = os.path.join(_projects_root(), proj.folder_name)
    os.makedirs(project_dir, exist_ok=True)

    dest = os.path.join(project_dir, safe_name)
    base, extension = os.path.splitext(safe_name)
    counter = 1
    while os.path.exists(dest):
        counter += 1
        dest = os.path.join(project_dir, f'{base}_{counter}{extension}')
        safe_name = f'{base}_{counter}{extension}'
    file.save(dest)

    file_size = os.path.getsize(dest)
    duration = _probe_duration(dest)

    recording = Recording(
        project_id=proj.id,
        original_name=original_name,
        stored_name=safe_name,
        file_format=ext,
        file_size_bytes=file_size,
        duration_seconds=duration,
        transcription_status='pending',
    )
    db.session.add(recording)
    db.session.commit()

    from ..services.transcription import enqueue
    enqueue(recording.id)

    return jsonify(recording.to_dict()), 201


@projects_bp.route('/single-transcriptions/<int:recording_id>', methods=['DELETE'])
def delete_single_transcription(recording_id):
    proj = Project.query.filter_by(is_system=True).first()
    if not proj:
        return jsonify({'error': 'Not found'}), 404
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != proj.id:
        return jsonify({'error': 'Recording not found'}), 404

    project_dir = os.path.join(_projects_root(), proj.folder_name)
    filepath = _recording_filepath(proj, recording)
    if not recording.is_linked and os.path.isfile(filepath):
        os.remove(filepath)
    if recording.transcript_path:
        tf = os.path.join(project_dir, recording.transcript_path)
        if os.path.isfile(tf):
            os.remove(tf)
    _delete_recording_annotation_files(project_dir, recording)

    db.session.delete(recording)
    db.session.commit()
    return jsonify({'ok': True})


# ── Create project ──

@projects_bp.route('', methods=['POST'])
def create_project():
    data = request.get_json(force=True) if request.is_json else {}
    name = (data.get('name') or 'Untitled project').strip()

    root = _projects_root()
    existing = {p.folder_name for p in Project.query.all()}
    folder = _safe_folder_name(name, existing)

    project = Project(name=name, folder_name=folder)
    db.session.add(project)
    db.session.flush()

    os.makedirs(os.path.join(root, folder), exist_ok=True)

    db.session.commit()
    _write_project_readme(project)
    return jsonify(project.to_dict(include_recordings=True)), 201


# ── Get single project ──

@projects_bp.route('/<int:project_id>', methods=['GET'])
def get_project(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    data = project.to_dict(include_recordings=True)
    # Add segment names to recordings
    if data.get('recordings'):
        seg_ids = {r.segment_id for r in project.recordings if r.segment_id}
        segments = Segment.query.filter(Segment.id.in_(seg_ids)).all() if seg_ids else []
        seg_map = {s.id: s.name for s in segments}
        for rec in data['recordings']:
            rec['segment_name'] = seg_map.get(rec['segment_id']) if rec.get('segment_id') else None
    return jsonify(data)


# ── Update project fields ──

@projects_bp.route('/<int:project_id>', methods=['PATCH'])
def update_project(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.get_json(force=True)

    if 'name' in data:
        new_name = (data['name'] or '').strip() or 'Untitled project'
        if new_name != project.name:
            project.name = new_name
            root = _projects_root()
            old_folder = os.path.join(root, project.folder_name)
            existing = {p.folder_name for p in Project.query.filter(Project.id != project.id).all()}
            new_folder = _safe_folder_name(new_name, existing)
            new_path = os.path.join(root, new_folder)
            if os.path.isdir(old_folder) and old_folder != new_path:
                os.rename(old_folder, new_path)
            elif not os.path.isdir(new_path):
                os.makedirs(new_path, exist_ok=True)
            project.folder_name = new_folder

    for field in ('description', 'objective', 'summary', 'icon', 'results_recommendations', 'further_steps',
                  'methodology', 'interview_guide', 'key_findings', 'recommendations'):
        if field in data:
            setattr(project, field, (data[field] or '').strip())

    if 'enabled_summary_sections' in data:
        project.set_enabled_summary_sections(data['enabled_summary_sections'])

    if 'research_questions' in data:
        project.set_questions(data['research_questions'])

    if 'hypotheses' in data:
        project.set_hypotheses(data['hypotheses'])

    if 'stakeholders' in data:
        project.set_stakeholders(data['stakeholders'])

    if 'enabled_sections' in data:
        project.set_enabled_sections(data['enabled_sections'])

    if 'custom_sections' in data:
        project.set_custom_sections(data['custom_sections'])

    if 'custom_summary_sections' in data:
        project.set_custom_summary_sections(data['custom_summary_sections'])

    if 'default_transcription_language' in data:
        raw = (data['default_transcription_language'] or '').strip().lower().replace('-', '_')
        if raw == '':
            project.default_transcription_language = ''
        elif re.match(r'^[a-z][a-z0-9_]{0,15}$', raw):
            project.default_transcription_language = raw
        else:
            return jsonify({'error': 'Invalid default_transcription_language'}), 400

    db.session.commit()
    _write_project_readme(project)
    return jsonify(project.to_dict())


# ── Archive / unarchive ──

@projects_bp.route('/<int:project_id>/archive', methods=['POST'])
def archive_project(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project.is_archived = True
    project.archived_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(project.to_dict())


@projects_bp.route('/<int:project_id>/unarchive', methods=['POST'])
def unarchive_project(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project.is_archived = False
    project.archived_at = None
    db.session.commit()
    return jsonify(project.to_dict())


# ── Delete project (removes folder too) ──

@projects_bp.route('/<int:project_id>', methods=['DELETE'])
def delete_project(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    if project.is_system:
        return jsonify({'error': 'Cannot delete system project'}), 403

    from ..services.backup_service import create_safety_snapshot

    safety_backup = create_safety_snapshot(
        current_app._get_current_object(),
        reason=f'delete_project_{project.folder_name}',
        include_audio=True,
        project_folders=[project.folder_name],
    )

    import shutil
    folder_path = os.path.join(_projects_root(), project.folder_name)
    if os.path.isdir(folder_path):
        shutil.rmtree(folder_path, ignore_errors=True)

    lp = db.session.get(Setting, 'last_open_project_id')
    if lp and lp.value == str(project_id):
        lp.value = ''
    db.session.delete(project)
    db.session.commit()
    return jsonify({'ok': True, 'safety_backup': safety_backup})


# ── Segments ──

def _segment_assigned_count(segment_id):
    return Recording.query.filter_by(segment_id=segment_id).count()


def _touch_project(project):
    if project is None:
        return
    project.updated_at = datetime.now(timezone.utc)
    db.session.add(project)


@projects_bp.route('/<int:project_id>/segments', methods=['GET'])
def list_segments(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    segments = Segment.query.filter_by(project_id=project_id).order_by(Segment.created_at).all()
    return jsonify([s.to_dict(assigned_count=_segment_assigned_count(s.id)) for s in segments])


@projects_bp.route('/<int:project_id>/segments', methods=['POST'])
def create_segment(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.get_json(force=True) or {}
    segment = Segment(
        project_id=project_id,
        name=(data.get('name') or '').strip(),
        description=(data.get('description') or '').strip(),
        screener_questions=(data.get('screener_questions') or '').strip(),
        target_count=int(data.get('target_count') or 0),
    )
    db.session.add(segment)
    _touch_project(project)
    db.session.commit()
    return jsonify(segment.to_dict(assigned_count=0)), 201


@projects_bp.route('/<int:project_id>/segments/<int:segment_id>', methods=['PATCH'])
def update_segment(project_id, segment_id):
    segment = db.session.get(Segment, segment_id)
    if not segment or segment.project_id != project_id:
        return jsonify({'error': 'Segment not found'}), 404

    data = request.get_json(force=True) or {}
    if 'name' in data:
        segment.name = (data['name'] or '').strip()
    if 'description' in data:
        segment.description = (data['description'] or '').strip()
    if 'screener_questions' in data:
        segment.screener_questions = (data['screener_questions'] or '').strip()
    if 'target_count' in data:
        segment.target_count = int(data['target_count'] or 0)

    _touch_project(db.session.get(Project, project_id))
    db.session.commit()
    return jsonify(segment.to_dict(assigned_count=_segment_assigned_count(segment.id)))


@projects_bp.route('/<int:project_id>/segments/<int:segment_id>', methods=['DELETE'])
def delete_segment(project_id, segment_id):
    segment = db.session.get(Segment, segment_id)
    if not segment or segment.project_id != project_id:
        return jsonify({'error': 'Segment not found'}), 404

    # Nullify segment assignment on affected recordings
    Recording.query.filter_by(segment_id=segment_id).update({'segment_id': None})
    db.session.delete(segment)
    _touch_project(db.session.get(Project, project_id))
    db.session.commit()
    return jsonify({'ok': True})


def _parse_num_speakers(raw):
    """Clamp a user-supplied speaker count to 1..10, or None when unset/invalid.

    None means "no explicit count" — diarization falls back to its default
    speaker-range heuristic.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return min(n, 10)


# ── Upload recording to project ──

@projects_bp.route('/<int:project_id>/recordings', methods=['POST'])
def upload_recording(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': f'Unsupported format: .{ext}'}), 400

    original_name = file.filename
    safe_name = secure_filename(original_name)
    if not safe_name:
        safe_name = f'recording.{ext}'

    project_dir = os.path.join(_projects_root(), project.folder_name)
    os.makedirs(project_dir, exist_ok=True)

    # Avoid overwriting — append counter if needed
    dest = os.path.join(project_dir, safe_name)
    base, extension = os.path.splitext(safe_name)
    counter = 1
    while os.path.exists(dest):
        counter += 1
        dest = os.path.join(project_dir, f'{base}_{counter}{extension}')
        safe_name = f'{base}_{counter}{extension}'

    file.save(dest)

    # Move MP4 moov atom to front for instant browser playback
    if ext == 'mp4':
        _mp4_faststart(dest)

    file_size = os.path.getsize(dest)
    duration = _probe_duration(dest)

    recording = Recording(
        project_id=project.id,
        original_name=original_name,
        stored_name=safe_name,
        file_format=ext,
        file_size_bytes=file_size,
        duration_seconds=duration,
        transcription_status='pending',
        num_speakers=_parse_num_speakers(request.form.get('num_speakers')),
    )
    db.session.add(recording)
    _touch_project(project)
    db.session.commit()

    from ..services.transcription import enqueue
    enqueue(recording.id)

    _write_project_readme(project)
    return jsonify(recording.to_dict()), 201


# ── Link existing recording by path (no file copy) ──

@projects_bp.route('/<int:project_id>/recordings/link', methods=['POST'])
def link_recording(project_id):
    """Register an existing file as a recording without copying it.

    The file stays at its original path on disk. PINE stores that absolute
    path in stored_name and sets is_linked=True so the path is used directly
    when serving media or starting transcription.
    """
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.get_json(force=True) or {}
    file_path = (data.get('path') or '').strip()
    if not file_path:
        return jsonify({'error': 'path is required'}), 400

    if not os.path.isfile(file_path):
        return jsonify({'error': 'File not found at the given path'}), 400

    ext = file_path.rsplit('.', 1)[-1].lower() if '.' in file_path else ''
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': f'Unsupported format: .{ext}'}), 400

    original_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    duration = _probe_duration(file_path)

    os.makedirs(os.path.join(_projects_root(), project.folder_name), exist_ok=True)

    recording = Recording(
        project_id=project.id,
        original_name=original_name,
        stored_name=file_path,          # absolute path IS the stored reference
        file_format=ext,
        file_size_bytes=file_size,
        duration_seconds=duration,
        transcription_status='pending',
        is_linked=True,
        num_speakers=_parse_num_speakers(data.get('num_speakers')),
    )
    db.session.add(recording)
    _touch_project(project)
    db.session.commit()

    from ..services.transcription import enqueue
    enqueue(recording.id)

    _write_project_readme(project)
    return jsonify(recording.to_dict()), 201


# ── Get single recording (for recording page) ──

@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>', methods=['GET'])
def get_recording(project_id, recording_id):
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404

    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = {
        'recording': recording.to_dict(),
        'project': project.to_dict(),
    }

    project_dir = os.path.join(_projects_root(), project.folder_name)

    if recording.transcription_status == 'transcribed' and recording.transcript_path:
        transcript_file = os.path.join(project_dir, recording.transcript_path)
        if os.path.isfile(transcript_file):
            with open(transcript_file, 'r', encoding='utf-8') as f:
                data['transcript'] = json.load(f)

    data['tags'] = get_project_tags(project_dir)
    data['themes'] = get_project_themes(project_dir)
    ann = get_annotations(project_dir, annotation_recording_ref(recording))
    data['annotations'] = ann

    # Include segment info if assigned
    if recording.segment_id:
        seg = db.session.get(Segment, recording.segment_id)
        if seg:
            data['recording']['segment'] = seg.to_dict(
                assigned_count=_segment_assigned_count(seg.id)
            )

    return jsonify(data)


# ── Update recording metadata (segment, participant notes) ──

@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>', methods=['PATCH'])
def update_recording(project_id, recording_id):
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404

    data = request.get_json(force=True, silent=True) or {}

    if 'segment_id' in data:
        sid = data['segment_id']
        if sid is None:
            recording.segment_id = None
        else:
            segment = db.session.get(Segment, int(sid))
            if not segment or segment.project_id != project_id:
                return jsonify({'error': 'Segment not found'}), 404
            recording.segment_id = segment.id

    if 'participant_notes' in data:
        recording.participant_notes = (data['participant_notes'] or '').strip()

    if 'num_speakers' in data:
        recording.num_speakers = _parse_num_speakers(data.get('num_speakers'))

    if 'original_name' in data:
        new_name = (data['original_name'] or '').strip()
        if new_name:
            # Preserve extension if the new name doesn't have one
            ext = ''
            if recording.original_name and '.' in recording.original_name:
                ext = '.' + recording.original_name.rsplit('.', 1)[-1]
            if ext and not new_name.lower().endswith(ext.lower()):
                new_name = new_name + ext
            recording.original_name = new_name[:500]

    _touch_project(db.session.get(Project, project_id))
    db.session.commit()

    result = recording.to_dict()
    if recording.segment_id:
        seg = db.session.get(Segment, recording.segment_id)
        if seg:
            result['segment'] = seg.to_dict(assigned_count=_segment_assigned_count(seg.id))
    return jsonify(result)


# ── Stream media file ──

@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>/media', methods=['GET'])
def stream_media(project_id, recording_id):
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404

    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    filepath = _recording_filepath(project, recording)
    if not os.path.isfile(filepath):
        return jsonify({'error': 'Media file not found'}), 404

    from flask import send_file as flask_send_file
    return flask_send_file(filepath, conditional=True)


# ── Annotations (tags, comments) ──

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
    for key in ('tag_spans', 'comments', 'speaker_labels'):
        if key in data:
            updates[key] = data[key]
    ann = update_annotations(project_dir, ann_ref, updates)
    _touch_project(project)
    db.session.commit()
    return jsonify(ann)


# ── Get transcript ──

@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>/transcript', methods=['GET'])
def get_transcript(project_id, recording_id):
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404

    if recording.transcription_status != 'transcribed' or not recording.transcript_path:
        return jsonify({'error': 'Transcript not available', 'status': recording.transcription_status}), 404

    project = db.session.get(Project, project_id)
    transcript_file = os.path.join(_projects_root(), project.folder_name, recording.transcript_path)
    if not os.path.isfile(transcript_file):
        return jsonify({'error': 'Transcript file missing'}), 404

    import json as _json
    with open(transcript_file, 'r', encoding='utf-8') as f:
        data = _json.load(f)
    return jsonify(data)


@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>/transcript/replace', methods=['POST'])
def replace_transcript_text(project_id, recording_id):
    """Find & replace across a recording's transcript, migrating tag/comment offsets.

    Used to mass-fix recurring ASR errors (names, jargon). Existing annotations
    stay anchored: their char offsets are shifted to follow the edited text.
    """
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404
    if recording.transcription_status != 'transcribed' or not recording.transcript_path:
        return jsonify({'error': 'Transcript not available'}), 404

    body = request.get_json(force=True, silent=True) or {}
    find = str(body.get('find') or '')
    replace = str(body.get('replace') or '')
    if not find:
        return jsonify({'error': 'Nothing to find'}), 400
    match_case = _parse_bool(body.get('match_case'), False)
    whole_word = _parse_bool(body.get('whole_word'), False)

    project = db.session.get(Project, project_id)
    transcript_file = os.path.join(_projects_root(), project.folder_name, recording.transcript_path)
    if not os.path.isfile(transcript_file):
        return jsonify({'error': 'Transcript file missing'}), 404

    with open(transcript_file, 'r', encoding='utf-8') as f:
        transcript = json.load(f)

    project_dir = os.path.join(_projects_root(), project.folder_name)
    ann_ref = annotation_recording_ref(recording)
    annotations = get_annotations(project_dir, ann_ref)

    from ..services.transcript_edit import apply_find_replace
    count = apply_find_replace(
        transcript.get('segments') or [], annotations,
        find, replace, match_case=match_case, whole_word=whole_word,
    )

    if count:
        atomic_write_text(transcript_file, json.dumps(transcript, ensure_ascii=False, indent=2))
        save_annotations(project_dir, ann_ref, annotations)
        _touch_project(project)
        db.session.commit()

    return jsonify({'count': count, 'transcript': transcript, 'annotations': annotations})


# ── Export recording ──

def _parse_bool(val, default):
    """Parse bool from JSON or query string (true/false/1/0)."""
    if val is None or val == '':
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() in ('true', '1', 'yes')


@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>/export', methods=['GET', 'POST'])
def export_recording(project_id, recording_id):
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
    else:
        data = request.args
    fmt = (data.get('format') or 'markdown').strip().lower()
    opts = {
        'include_comments': _parse_bool(data.get('include_comments'), True),
        'include_tags': _parse_bool(data.get('include_tags'), True),
        'include_participant_details': _parse_bool(data.get('include_participant_details'), True),
        'include_project_details': _parse_bool(data.get('include_project_details'), True),
        'include_prompt': _parse_bool(data.get('include_prompt'), True),
        'remove_pii': _parse_bool(data.get('remove_pii'), False),
        'use_recording_screen_prompt': _parse_bool(data.get('use_recording_screen_prompt'), False),
    }

    from ..services.export_service import export_recording_markdown, export_recording_odt
    from ..services.pii_service import PIIError

    try:
        if fmt == 'odt':
            content, err = export_recording_odt(current_app, project_id, recording_id, opts)
        else:
            content, err = export_recording_markdown(current_app, project_id, recording_id, opts)
    except PIIError as exc:
        return jsonify({'error': str(exc)}), 422

    if err:
        return jsonify({'error': err}), 404

    project_name = (recording.project.name if recording.project else '').strip()
    if fmt == 'odt':
        name = _build_recording_export_filename(project_name, recording.original_name, '.odt')
        return Response(
            content,
            mimetype='application/vnd.oasis.opendocument.text',
            headers={'Content-Disposition': _content_disposition(name)},
        )

    name = _build_recording_export_filename(project_name, recording.original_name, '.md')
    return Response(
        content,
        mimetype='text/markdown; charset=utf-8',
        headers={'Content-Disposition': _content_disposition(name)},
    )


# ── Tag quotes aggregation ──

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
        with open(path, 'r', encoding='utf-8') as f:
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
    """Merge consecutive same-speaker segments into blocks (built once per
    recording so anchor reconstruction is not quadratic in spans×segments)."""
    blocks = []
    for i, seg in enumerate(segments):
        spk = (seg.get('speaker') or '').strip()
        if blocks and (blocks[-1]['speaker'] or '').strip() == spk:
            blocks[-1]['text'] = (blocks[-1]['text'] + ' ' + (seg.get('text') or '').strip()).strip()
            blocks[-1]['indices'].append(i)
        else:
            blocks.append({
                'speaker': seg.get('speaker', ''),
                'indices': [i],
                'text': (seg.get('text') or '').strip(),
            })
    return blocks


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


# ── Delete recording ──

@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>', methods=['DELETE'])
def delete_recording(project_id, recording_id):
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404

    project = db.session.get(Project, project_id)
    if project:
        project_dir = os.path.join(_projects_root(), project.folder_name)
        filepath = _recording_filepath(project, recording)
        # Only delete the media file if it was copied into the project folder
        if not recording.is_linked and os.path.isfile(filepath):
            os.remove(filepath)
        if recording.transcript_path:
            transcript_file = os.path.join(project_dir, recording.transcript_path)
            if os.path.isfile(transcript_file):
                os.remove(transcript_file)
        _delete_recording_annotation_files(project_dir, recording)

    db.session.delete(recording)
    _touch_project(project)
    db.session.commit()

    if project:
        _write_project_readme(project)
    return jsonify({'ok': True})


@projects_bp.route('/<int:project_id>/recordings/<int:recording_id>/transcription', methods=['DELETE'])
def cancel_transcription_route(project_id, recording_id):
    """Cancel an in-progress transcription job."""
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404
    if recording.transcription_status not in (
            'transcribing', 'cancelling', 'awaiting_language'):
        return jsonify({'error': 'Recording is not being transcribed'}), 409

    from ..services.transcription import cancel_transcription
    found = cancel_transcription(recording_id)
    if found:
        recording.transcription_status = 'cancelling'
        _touch_project(db.session.get(Project, project_id))
        db.session.commit()
        return jsonify({'ok': True, 'status': 'cancelling'})
    return jsonify({'error': 'No active job found for this recording'}), 404


@projects_bp.route(
    '/<int:project_id>/recordings/<int:recording_id>/transcription/language',
    methods=['POST'])
def submit_transcription_language(project_id, recording_id):
    """Resume transcription after the worker asked for a spoken language (low detector confidence)."""
    recording = db.session.get(Recording, recording_id)
    if not recording or recording.project_id != project_id:
        return jsonify({'error': 'Recording not found'}), 404
    if recording.transcription_status != 'awaiting_language':
        return jsonify({'error': 'This recording is not waiting for a language'}), 409

    data = request.get_json(silent=True) or {}
    lang = (data.get('language') or '').strip().lower()
    if not lang or not re.match(r'^[a-z][a-z0-9_]{0,15}$', lang):
        return jsonify({'error': 'Invalid language code (e.g. en, ru, de)'}), 400

    from ..services.transcription import notify_language_chosen
    if not notify_language_chosen(recording_id, lang):
        return jsonify({'error': 'No active language prompt for this recording'}), 404
    return jsonify({'ok': True, 'language': lang})


# ── Project export (multiple recordings as single document) ──

@projects_bp.route('/<int:project_id>/export', methods=['GET', 'POST'])
def export_project(project_id):
    """Export selected recordings as a single Markdown or ODT document."""
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or {}
    else:
        data = request.args
    recording_ids_raw = data.get('recording_ids', [])
    if isinstance(recording_ids_raw, str):
        recording_ids = [int(x.strip()) for x in recording_ids_raw.split(',') if x.strip()]
    else:
        recording_ids = recording_ids_raw
    fmt = (data.get('format') or 'markdown').strip().lower()
    opts = {
        'include_comments': _parse_bool(data.get('include_comments'), True),
        'include_tags': _parse_bool(data.get('include_tags'), True),
        'include_participant_details': _parse_bool(data.get('include_participant_details'), True),
        'include_project_details': _parse_bool(data.get('include_project_details'), True),
        'include_prompt': _parse_bool(data.get('include_prompt'), True),
        'remove_pii': _parse_bool(data.get('remove_pii'), False),
    }

    if not recording_ids:
        return jsonify({'error': 'No recordings selected'}), 400

    from ..services.export_service import export_project_markdown, export_project_odt
    from ..services.pii_service import PIIError

    try:
        if fmt == 'odt':
            content, err = export_project_odt(current_app, project_id, recording_ids, opts)
        else:
            content, err = export_project_markdown(current_app, project_id, recording_ids, opts)
    except PIIError as exc:
        return jsonify({'error': str(exc)}), 422

    if err:
        return jsonify({'error': err}), 400

    ext = 'odt' if fmt == 'odt' else 'md'
    export_name = project.name.replace(' ', '_')[:50] + f'_export.{ext}'
    mimetype = 'application/vnd.oasis.opendocument.text' if fmt == 'odt' else 'text/markdown; charset=utf-8'

    return Response(
        content,
        mimetype=mimetype,
        headers={'Content-Disposition': _content_disposition(export_name)},
    )


# ── Project transfer (export as ZIP) ──

@projects_bp.route('/<int:project_id>/transfer', methods=['GET'])
def transfer_project(project_id):
    """Package a project as a ZIP file (transcripts + annotations + metadata, no audio)."""
    import zipfile
    import tempfile

    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    project_dir = os.path.join(_projects_root(), project.folder_name)

    # Build project metadata
    meta = project.to_dict(include_recordings=True)
    tags = get_project_tags(project_dir)
    themes = get_project_themes(project_dir)

    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        tmp_path = tmp.name

    with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Project metadata
        zf.writestr('project.json', json.dumps(meta, ensure_ascii=False, indent=2))
        zf.writestr('project_tags.json', json.dumps(tags, ensure_ascii=False, indent=2))
        zf.writestr('project_themes.json', json.dumps(themes, ensure_ascii=False, indent=2))

        # Each recording's transcript + annotations
        for rec in project.recordings:
            # Transcript JSON
            if rec.transcript_path:
                tx_file = os.path.join(project_dir, rec.transcript_path)
                if os.path.isfile(tx_file):
                    zf.write(tx_file, f'transcripts/{rec.transcript_path}')

            # Annotations JSON
            ann_name = annotations_filename(annotation_recording_ref(rec))
            ann_file = os.path.join(project_dir, ann_name)
            if os.path.isfile(ann_file):
                zf.write(ann_file, f'annotations/{ann_name}')

        # README
        readme_path = os.path.join(project_dir, 'README.md')
        if os.path.isfile(readme_path):
            zf.write(readme_path, 'README.md')

    safe_name = _safe_ascii(project.name).replace(' ', '_')[:50]
    return send_file(
        tmp_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{safe_name}_transfer.zip',
    )


# ── Attachments ──

def _attachments_dir(project_dir):
    return os.path.join(project_dir, 'attachments')


def _attachments_meta_path(project_dir):
    return os.path.join(project_dir, 'attachments.json')


def _load_attachments(project_dir):
    path = _attachments_meta_path(project_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _save_attachments(project_dir, data):
    atomic_write_text(_attachments_meta_path(project_dir),
                      json.dumps(data, ensure_ascii=False, indent=2))


@projects_bp.route('/<int:project_id>/attachments', methods=['GET'])
def list_attachments(project_id):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project_dir = os.path.join(_projects_root(), project.folder_name)
    return jsonify(_load_attachments(project_dir))


@projects_bp.route('/<int:project_id>/attachments', methods=['POST'])
def upload_attachment(project_id):
    import uuid
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    original_name = file.filename
    _, ext = os.path.splitext(original_name)
    aid = str(uuid.uuid4())[:8]
    stored_name = aid + (ext.lower() if ext else '')

    project_dir = os.path.join(_projects_root(), project.folder_name)
    att_dir = _attachments_dir(project_dir)
    os.makedirs(att_dir, exist_ok=True)
    file.save(os.path.join(att_dir, stored_name))

    attachments = _load_attachments(project_dir)
    entry = {
        'id': aid,
        'display_name': original_name,
        'stored_name': stored_name,
        'uploaded_at': datetime.now(timezone.utc).isoformat(),
    }
    attachments.append(entry)
    _save_attachments(project_dir, attachments)
    return jsonify(entry), 201


@projects_bp.route('/<int:project_id>/attachments/<aid>', methods=['PATCH'])
def rename_attachment(project_id, aid):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    data = request.get_json(silent=True) or {}
    new_name = (data.get('display_name') or '').strip()
    if not new_name:
        return jsonify({'error': 'display_name required'}), 400
    project_dir = os.path.join(_projects_root(), project.folder_name)
    attachments = _load_attachments(project_dir)
    for att in attachments:
        if att['id'] == aid:
            att['display_name'] = new_name
            _save_attachments(project_dir, attachments)
            return jsonify(att)
    return jsonify({'error': 'Attachment not found'}), 404


@projects_bp.route('/<int:project_id>/attachments/<aid>', methods=['DELETE'])
def delete_attachment(project_id, aid):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project_dir = os.path.join(_projects_root(), project.folder_name)
    attachments = _load_attachments(project_dir)
    found = next((a for a in attachments if a['id'] == aid), None)
    if not found:
        return jsonify({'error': 'Attachment not found'}), 404
    file_path = os.path.join(_attachments_dir(project_dir), found['stored_name'])
    if os.path.isfile(file_path):
        os.remove(file_path)
    _save_attachments(project_dir, [a for a in attachments if a['id'] != aid])
    return jsonify({'ok': True})


@projects_bp.route('/<int:project_id>/attachments/<aid>/download', methods=['GET'])
def download_attachment(project_id, aid):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project_dir = os.path.join(_projects_root(), project.folder_name)
    found = next((a for a in _load_attachments(project_dir) if a['id'] == aid), None)
    if not found:
        return jsonify({'error': 'Attachment not found'}), 404
    file_path = os.path.join(_attachments_dir(project_dir), found['stored_name'])
    if not os.path.isfile(file_path):
        return jsonify({'error': 'File not found on disk'}), 404
    return send_file(file_path, as_attachment=True, download_name=found['display_name'])
