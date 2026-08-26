"""Project attachments — files a researcher keeps alongside a project."""

import json
import os
from datetime import UTC, datetime
from flask import jsonify, request, send_file
from ...extensions import db
from ...models.project import Project
from ...services.file_utils import atomic_read_json, atomic_write_text
from .common import _projects_root, projects_bp

def _attachments_dir(project_dir):
    return os.path.join(project_dir, 'attachments')

def _attachments_meta_path(project_dir):
    return os.path.join(project_dir, 'attachments.json')

def _attachment_file(project_dir, entry):
    """Resolve one attachment's file, or None if it points outside the folder.

    ``stored_name`` is written by the upload route, but it is *read back* out of
    attachments.json — a file that travels with the project folder. Projects get
    shared, restored from someone else's backup and edited by hand, so the name
    on disk is not ours to trust: joined blind, a ``../../..`` in there is an
    arbitrary file read on the download route and an arbitrary delete on the
    other one. realpath resolves any symlink hop before the containment test.
    """
    stored = entry.get('stored_name') or ''
    if not stored:
        return None
    att_dir = os.path.realpath(_attachments_dir(project_dir))
    resolved = os.path.realpath(os.path.join(att_dir, stored))
    # Strictly inside: a bare "." resolves to att_dir itself, which is a
    # directory no caller here has any business opening or deleting.
    if not resolved.startswith(att_dir + os.sep):
        return None
    return resolved

def _find_attachment(project_dir, aid):
    """The entry with this id, tolerating malformed rows in a hand-edited file."""
    return next(
        (a for a in _load_attachments(project_dir)
         if isinstance(a, dict) and a.get('id') == aid),
        None,
    )

def _load_attachments(project_dir):
    path = _attachments_meta_path(project_dir)
    if not os.path.isfile(path):
        return []
    try:
        return atomic_read_json(path)
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
        'uploaded_at': datetime.now(UTC).isoformat(),
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
        if isinstance(att, dict) and att.get('id') == aid:
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
    found = _find_attachment(project_dir, aid)
    if not found:
        return jsonify({'error': 'Attachment not found'}), 404
    file_path = _attachment_file(project_dir, found)
    if file_path and os.path.isfile(file_path):
        os.remove(file_path)
    # The metadata row goes either way: an entry whose file we refuse to touch
    # is exactly the one the project should stop carrying.
    _save_attachments(project_dir, [
        a for a in attachments
        if not (isinstance(a, dict) and a.get('id') == aid)
    ])
    return jsonify({'ok': True})

@projects_bp.route('/<int:project_id>/attachments/<aid>/download', methods=['GET'])
def download_attachment(project_id, aid):
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    project_dir = os.path.join(_projects_root(), project.folder_name)
    found = _find_attachment(project_dir, aid)
    if not found:
        return jsonify({'error': 'Attachment not found'}), 404
    file_path = _attachment_file(project_dir, found)
    if not file_path or not os.path.isfile(file_path):
        return jsonify({'error': 'File not found on disk'}), 404
    return send_file(file_path, as_attachment=True,
                     download_name=found.get('display_name') or 'attachment')
