"""Research participant segments — the screening groups inside a project."""

from flask import jsonify, request
from ...extensions import db
from ...models.project import Project
from ...models.recording import Recording
from ...models.segment import Segment
from .common import _segment_assigned_count, _touch_project, projects_bp

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
