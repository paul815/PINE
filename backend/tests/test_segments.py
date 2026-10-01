"""Tier 2 tests: Segments CRUD API."""



class TestSegmentsCRUD:
    """Tests for POST/GET/PATCH/DELETE /<project_id>/segments."""

    def _create_project(self, client):
        r = client.post('/api/projects', json={'name': 'Seg Test'})
        assert r.status_code == 201
        return r.get_json()['id']

    def test_list_segments_empty(self, client):
        pid = self._create_project(client)
        r = client.get(f'/api/projects/{pid}/segments')
        assert r.status_code == 200
        assert r.get_json() == []

    def test_create_segment(self, client):
        pid = self._create_project(client)
        r = client.post(f'/api/projects/{pid}/segments', json={
            'name': 'Power Users',
            'description': 'Users with 100+ sessions',
            'screener_questions': 'How often do you use the app?',
            'target_count': 5,
        })
        assert r.status_code == 201
        data = r.get_json()
        assert data['name'] == 'Power Users'
        assert data['description'] == 'Users with 100+ sessions'
        assert data['screener_questions'] == 'How often do you use the app?'
        assert data['target_count'] == 5
        assert data['assigned_count'] == 0
        assert 'id' in data

    def test_create_segment_minimal(self, client):
        """Creating a segment with no optional fields should work."""
        pid = self._create_project(client)
        r = client.post(f'/api/projects/{pid}/segments', json={'name': 'Minimal'})
        assert r.status_code == 201
        data = r.get_json()
        assert data['name'] == 'Minimal'
        assert data['target_count'] == 0
        assert data['description'] == ''

    def test_create_segment_nonexistent_project(self, client):
        r = client.post('/api/projects/99999/segments', json={'name': 'Ghost'})
        assert r.status_code == 404

    def test_list_segments_ordered(self, client):
        pid = self._create_project(client)
        for name in ['Alpha', 'Beta', 'Gamma']:
            client.post(f'/api/projects/{pid}/segments', json={'name': name})

        r = client.get(f'/api/projects/{pid}/segments')
        assert r.status_code == 200
        names = [s['name'] for s in r.get_json()]
        assert names == ['Alpha', 'Beta', 'Gamma']

    def test_update_segment_name(self, client):
        pid = self._create_project(client)
        cr = client.post(f'/api/projects/{pid}/segments', json={
            'name': 'Old Name', 'description': 'Keep me', 'target_count': 3,
        })
        sid = cr.get_json()['id']

        r = client.patch(f'/api/projects/{pid}/segments/{sid}',
                         json={'name': 'New Name'})
        assert r.status_code == 200
        data = r.get_json()
        assert data['name'] == 'New Name'
        assert data['description'] == 'Keep me'  # unchanged
        assert data['target_count'] == 3          # unchanged

    def test_update_segment_target_count(self, client):
        pid = self._create_project(client)
        cr = client.post(f'/api/projects/{pid}/segments', json={
            'name': 'Seg', 'target_count': 2,
        })
        sid = cr.get_json()['id']

        r = client.patch(f'/api/projects/{pid}/segments/{sid}',
                         json={'target_count': 10})
        assert r.status_code == 200
        assert r.get_json()['target_count'] == 10

    def test_delete_segment(self, client):
        pid = self._create_project(client)
        cr = client.post(f'/api/projects/{pid}/segments', json={'name': 'Doomed'})
        sid = cr.get_json()['id']

        r = client.delete(f'/api/projects/{pid}/segments/{sid}')
        assert r.status_code == 200
        assert r.get_json()['ok'] is True

        # Verify gone
        r2 = client.get(f'/api/projects/{pid}/segments')
        assert len(r2.get_json()) == 0

    def test_delete_segment_nullifies_recordings(self, client, app):
        """Deleting a segment sets segment_id=None on assigned recordings."""
        pid = self._create_project(client)
        cr = client.post(f'/api/projects/{pid}/segments', json={'name': 'Temp'})
        sid = cr.get_json()['id']

        # Create a recording and assign it to the segment
        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording

            rec = Recording(
                project_id=pid, original_name='r.mp3', stored_name='r.mp3',
                transcription_status='pending', segment_id=sid,
            )
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

        # Delete the segment
        client.delete(f'/api/projects/{pid}/segments/{sid}')

        # Recording should now have segment_id=None
        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            rec = db.session.get(Recording, rid)
            assert rec is not None
            assert rec.segment_id is None

    def test_segment_not_found(self, client):
        pid = self._create_project(client)
        r = client.patch(f'/api/projects/{pid}/segments/99999',
                         json={'name': 'Nope'})
        assert r.status_code == 404

        r2 = client.delete(f'/api/projects/{pid}/segments/99999')
        assert r2.status_code == 404

    def test_segment_wrong_project(self, client):
        """Segment from project A cannot be accessed via project B."""
        pid_a = self._create_project(client)
        pid_b = self._create_project(client)

        cr = client.post(f'/api/projects/{pid_a}/segments', json={'name': 'A-Seg'})
        sid = cr.get_json()['id']

        r = client.patch(f'/api/projects/{pid_b}/segments/{sid}',
                         json={'name': 'Hijack'})
        assert r.status_code == 404

        r2 = client.delete(f'/api/projects/{pid_b}/segments/{sid}')
        assert r2.status_code == 404

    def test_segment_assigned_count(self, client, app):
        """assigned_count reflects the number of recordings linked to the segment."""
        pid = self._create_project(client)
        cr = client.post(f'/api/projects/{pid}/segments', json={'name': 'Counted'})
        sid = cr.get_json()['id']

        # Add 2 recordings assigned to this segment
        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording

            for i in range(2):
                rec = Recording(
                    project_id=pid, original_name=f'r{i}.mp3',
                    stored_name=f'r{i}.mp3', transcription_status='pending',
                    segment_id=sid,
                )
                db.session.add(rec)
            db.session.commit()

        # List segments and check assigned_count
        r = client.get(f'/api/projects/{pid}/segments')
        segs = r.get_json()
        assert len(segs) == 1
        assert segs[0]['assigned_count'] == 2

    def test_delete_project_cascades_segments(self, client, app):
        """Deleting a project removes its segments (and recordings) with no orphan
        rows and no FK error — regression for the cascade-delete bug (audit P0-1)."""
        pid = self._create_project(client)
        cr = client.post(f'/api/projects/{pid}/segments', json={'name': 'Doomed'})
        sid = cr.get_json()['id']

        # A recording assigned to that segment is the worst case for delete ordering.
        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            rec = Recording(
                project_id=pid, original_name='r.mp3', stored_name='r.mp3',
                transcription_status='pending', segment_id=sid,
            )
            db.session.add(rec)
            db.session.commit()
            rid = rec.id

        r = client.delete(f'/api/projects/{pid}')
        assert r.status_code == 200

        # Segment and recording rows are gone — not orphaned, no IntegrityError.
        with app.app_context():
            from app.extensions import db
            from app.models.recording import Recording
            from app.models.segment import Segment
            assert db.session.get(Segment, sid) is None
            assert db.session.get(Recording, rid) is None
