"""Unit tests for export_service."""

import json
import os

import pytest

from app.services.export_service import (
    _fmt_time,
    _md_to_odt_content,
    _merge_consecutive_speakers,
    _render_transcript_blocks,
    export_project_markdown,
    export_project_odt,
    export_recording_markdown,
    export_recording_odt,
)


class TestFmtTime:
    """Tests for _fmt_time (boundary and edge cases)."""

    def test_zero(self):
        assert _fmt_time(0) == '0:00'

    def test_negative(self):
        assert _fmt_time(-1) == '0:00'
        assert _fmt_time(-100) == '0:00'

    def test_none_and_falsy(self):
        assert _fmt_time(None) == '0:00'

    def test_under_one_minute(self):
        assert _fmt_time(30) == '0:30'
        assert _fmt_time(59) == '0:59'

    def test_exactly_one_minute(self):
        assert _fmt_time(60) == '1:00'

    def test_minutes_and_seconds(self):
        assert _fmt_time(90) == '1:30'
        assert _fmt_time(125) == '2:05'

    def test_one_hour(self):
        assert _fmt_time(3600) == '1:00:00'

    def test_hours_minutes_seconds(self):
        assert _fmt_time(3661) == '1:01:01'
        assert _fmt_time(7325) == '2:02:05'

    def test_large_value(self):
        assert _fmt_time(36000) == '10:00:00'


class TestMdToOdtContent:
    """Tests for _md_to_odt_content."""

    def test_empty_markdown(self):
        result = _md_to_odt_content('')
        assert 'office:document-content' in result
        assert 'office:body' in result

    def test_h1(self):
        result = _md_to_odt_content('# Title')
        assert 'text:outline-level="1"' in result
        assert 'Title' in result

    def test_h2(self):
        result = _md_to_odt_content('## Section')
        assert 'text:outline-level="2"' in result
        assert 'Section' in result

    def test_paragraph(self):
        result = _md_to_odt_content('Hello world')
        assert '<text:p>Hello world</text:p>' in result

    def test_comment_line(self):
        result = _md_to_odt_content('  > A comment')
        assert 'Comment' in result or 'A comment' in result

    def test_empty_line(self):
        result = _md_to_odt_content('\n\n')
        assert '<text:p/>' in result

    def test_xml_escaping(self):
        result = _md_to_odt_content('A & B < C > D "quoted"')
        assert '&amp;' in result
        assert '&lt;' in result
        assert '&gt;' in result
        assert '&quot;' in result


class TestExportRecordingMarkdown:
    """Tests for export_recording_markdown."""

    def test_project_not_found(self, app):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording

            db.create_all()
            proj = Project(name='P', folder_name='p')
            rec = Recording(project_id=1, original_name='r.mp3', stored_name='r.mp3')
            db.session.add(proj)
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

        content, err = export_recording_markdown(app, 99999, rid, {})
        assert content is None
        assert err == 'Not found'

        content, err = export_recording_markdown(app, pid, 99999, {})
        assert content is None
        assert err == 'Not found'

    def test_transcript_not_found(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()
            proj = Project(name='P', folder_name='p')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='r.mp3',
                stored_name='r.mp3',
                transcript_path='nonexistent.json',
                duration_seconds=60,
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id
            os.makedirs(os.path.join(projects_path, 'p'), exist_ok=True)

        content, err = export_recording_markdown(app, pid, rid, {})
        assert content is None
        assert err == 'Transcript not found'

    def test_success_minimal_transcript(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='Test Project', folder_name='test_proj')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='interview.mp3',
                stored_name='interview.mp3',
                transcript_path='interview_transcript.json',
                duration_seconds=120,
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'test_proj')
            os.makedirs(proj_dir, exist_ok=True)
            transcript = {
                'segments': [
                    {'start': 0, 'end': 2, 'text': 'Hello', 'speaker': 'SPEAKER_00'},
                    {'start': 2, 'end': 5, 'text': 'Hi there', 'speaker': 'SPEAKER_01'},
                ],
                'duration_seconds': 5,
            }
            with open(os.path.join(proj_dir, 'interview_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump(transcript, f, ensure_ascii=False)

        content, err = export_recording_markdown(app, pid, rid, {})
        assert err is None
        assert content is not None
        assert '# Test Project' in content
        assert '# interview.mp3 (Transcript)' in content
        assert 'Hello' in content
        assert 'Hi there' in content

    def test_uses_recording_specific_annotations_not_legacy_name_based(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting
            from app.services.annotations import (
                annotation_recording_ref,
                annotations_filename,
            )

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='Ann Isolated Export', folder_name='ann_isolated_export')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='same.mp3',
                stored_name='same.mp3',
                transcript_path='same_42_transcript.json',
                duration_seconds=30,
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, proj.folder_name)
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, rec.transcript_path), 'w', encoding='utf-8') as f:
                json.dump({
                    'segments': [{'start': 0, 'end': 2, 'text': 'Hello', 'speaker': 'SPEAKER_00'}],
                    'duration_seconds': 2,
                }, f)

            # Legacy file has a wrong name, recording-specific file has the right one.
            with open(
                os.path.join(proj_dir, annotations_filename(rec.stored_name)),
                'w',
                encoding='utf-8',
            ) as f:
                json.dump({'speaker_labels': {'SPEAKER_00': 'Legacy Name'}}, f)
            with open(
                os.path.join(proj_dir, annotations_filename(annotation_recording_ref(rec))),
                'w',
                encoding='utf-8',
            ) as f:
                json.dump({'speaker_labels': {'SPEAKER_00': 'Current Name'}}, f)

        content, err = export_recording_markdown(app, pid, rid, {})
        assert err is None
        assert 'Current Name' in content
        assert 'Legacy Name' not in content

    def test_empty_segments(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='Empty', folder_name='empty')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='e.mp3',
                stored_name='e.mp3',
                transcript_path='e_transcript.json',
                duration_seconds=0,
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'empty')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'e_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump({'segments': [], 'duration_seconds': 0}, f)

        content, err = export_recording_markdown(app, pid, rid, {})
        assert err is None
        assert '(Transcript)' in content

    def test_merged_speaker_turns_chronological(self, app, temp_dir):
        """Consecutive same-speaker segments are merged; order is P1, Mod, P1 (not P1,P1,P1,Mod)."""
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='Merge Test', folder_name='merge_test')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='m.mp3',
                stored_name='m.mp3',
                transcript_path='m_transcript.json',
                duration_seconds=30,
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'merge_test')
            os.makedirs(proj_dir, exist_ok=True)
            transcript = {
                'segments': [
                    {'start': 0, 'end': 2, 'text': 'Hello.', 'speaker': 'Participant 1'},
                    {'start': 2, 'end': 4, 'text': 'How are you?', 'speaker': 'Participant 1'},
                    {'start': 4, 'end': 6, 'text': 'Good thanks.', 'speaker': 'Moderator'},
                    {'start': 6, 'end': 8, 'text': "I'm fine.", 'speaker': 'Participant 1'},
                ],
                'duration_seconds': 8,
            }
            with open(os.path.join(proj_dir, 'm_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump(transcript, f, ensure_ascii=False)

        content, err = export_recording_markdown(app, pid, rid, {})
        assert err is None
        assert content is not None
        # Order must be: Participant 1 (merged), Moderator, Participant 1
        transcript_section = content.split('(Transcript)')[1].split('---')[0]
        assert '**Participant 1**' in transcript_section
        assert '**Moderator**' in transcript_section
        assert 'Hello. How are you?' in transcript_section
        assert 'Good thanks.' in transcript_section
        assert "I'm fine." in transcript_section
        # Participant 1 must appear before Moderator (first block)
        p1_pos = transcript_section.find('**Participant 1**')
        mod_pos = transcript_section.find('**Moderator**')
        assert p1_pos < mod_pos
        # Moderator must appear before second Participant 1
        p1_second = transcript_section.find('**Participant 1**', p1_pos + 1)
        assert mod_pos < p1_second

    def test_merged_blocks_with_tags_and_comments(self, app, temp_dir):
        """Tags and comments align correctly when segments are merged."""
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='Tag Test', folder_name='tag_test')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='t.mp3',
                stored_name='t.mp3',
                transcript_path='t_transcript.json',
                duration_seconds=10,
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'tag_test')
            os.makedirs(proj_dir, exist_ok=True)
            # Segments: P1 "Hello world", Mod "Hi" - tag "world" in segment 0, comment on segment 1
            transcript = {
                'segments': [
                    {'start': 0, 'end': 2, 'text': 'Hello', 'speaker': 'P1'},
                    {'start': 2, 'end': 4, 'text': 'world', 'speaker': 'P1'},
                    {'start': 4, 'end': 6, 'text': 'Hi', 'speaker': 'Mod'},
                ],
                'duration_seconds': 6,
            }
            with open(os.path.join(proj_dir, 't_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump(transcript, f, ensure_ascii=False)
            # Tags definition (project_tags.json)
            with open(os.path.join(proj_dir, 'project_tags.json'), 'w', encoding='utf-8') as f:
                json.dump([{'id': 'greet', 'name': 'greeting', 'color': '#aaa'}], f, ensure_ascii=False)
            # Annotations: tag "world" (segment 1, chars 0-5), comment on segment 2
            ann = {
                'tag_spans': [{'segment_idx': 1, 'start_char': 0, 'end_char': 5, 'tag_id': 'greet'}],
                'comments': [{'segment_idx': 2, 'text': 'Moderator said hi'}],
            }
            with open(os.path.join(proj_dir, 't_annotations.json'), 'w', encoding='utf-8') as f:
                json.dump(ann, f, ensure_ascii=False)

        content, err = export_recording_markdown(app, pid, rid, {'include_tags': True, 'include_comments': True})
        assert err is None
        # Merged P1 block: "Hello world" with "world" tagged as **world** [greeting]
        assert '**world** [greeting]' in content
        assert 'Hello' in content
        assert 'Moderator said hi' in content
        assert '**P1**' in content
        assert '**Mod**' in content


class TestMergeConsecutiveSpeakers:
    """Tests for _merge_consecutive_speakers."""

    def test_merge_same_speaker(self):
        segments = [
            {'speaker': 'P1', 'text': 'A.', 'start': 0, 'end': 1},
            {'speaker': 'P1', 'text': 'B.', 'start': 1, 'end': 2},
            {'speaker': 'Mod', 'text': 'C.', 'start': 2, 'end': 3},
            {'speaker': 'P1', 'text': 'D.', 'start': 3, 'end': 4},
        ]
        merged = _merge_consecutive_speakers(segments)
        assert len(merged) == 3
        assert merged[0]['speaker'] == 'P1'
        assert merged[0]['text'] == 'A. B.'
        assert merged[0]['indices'] == [0, 1]
        assert merged[1]['speaker'] == 'Mod'
        assert merged[1]['text'] == 'C.'
        assert merged[1]['indices'] == [2]
        assert merged[2]['speaker'] == 'P1'
        assert merged[2]['text'] == 'D.'
        assert merged[2]['indices'] == [3]

    def test_interjection_does_not_split_a_sentence(self):
        """P1 is cut off mid-sentence: the turn resumes in the block it opened."""
        segments = [
            {'speaker': 'P1', 'text': 'How long have you', 'start': 0, 'end': 2},
            {'speaker': 'Mod', 'text': 'Mhm.', 'start': 2, 'end': 3},
            {'speaker': 'P1', 'text': 'worked here?', 'start': 3, 'end': 4},
            {'speaker': 'P1', 'text': 'And before that?', 'start': 4, 'end': 5},
        ]
        merged = _merge_consecutive_speakers(segments)
        assert [m['speaker'] for m in merged] == ['P1', 'Mod', 'P1']
        assert merged[0]['text'] == 'How long have you worked here?'
        assert merged[0]['indices'] == [0, 2]
        assert merged[1]['text'] == 'Mhm.'
        # The sentence that was interrupted goes home; the next one does not,
        # or it would read above the interjection it came after.
        assert merged[2]['text'] == 'And before that?'

    def test_interjector_does_not_reopen_a_finished_block(self):
        """Mod's later answer is its own block, not glued to the earlier 'Mhm.'."""
        segments = [
            {'speaker': 'P1', 'text': 'How long have you', 'start': 0, 'end': 2},
            {'speaker': 'Mod', 'text': 'Mhm.', 'start': 2, 'end': 3},
            {'speaker': 'P1', 'text': 'worked here?', 'start': 3, 'end': 4},
            {'speaker': 'Mod', 'text': 'Seven months.', 'start': 4, 'end': 5},
        ]
        merged = _merge_consecutive_speakers(segments)
        assert [m['text'] for m in merged] == [
            'How long have you worked here?', 'Mhm.', 'Seven months.']
        assert [m['indices'] for m in merged] == [[0, 2], [1], [3]]


class TestRenderTranscriptBlocks:
    """Tests for _render_transcript_blocks (multi-segment tag export)."""

    def test_cross_block_tag_same_speaker_end_merged_end_preserves_tail(self):
        """Tag start+end in same merged block: ec from end_merged_end; text after tag remains."""
        segments = [
            {'speaker': 'Mod', 'text': 'aa', 'start': 0, 'end': 1},
            {'speaker': 'Mod', 'text': 'bb cc', 'start': 1, 'end': 5},
        ]
        merged = _merge_consecutive_speakers(segments)
        assert len(merged) == 1
        assert merged[0]['text'] == 'aa bb cc'

        tag_spans = [{
            'segment_idx': 0,
            'end_segment_idx': 1,
            'start_char': 0,
            'end_merged_end': 5,
            'merged_start': 0,
            'anchor_text': 'aa bb',
            'tag_id': 't1',
        }]
        tag_map = {'t1': {'id': 't1', 'name': 'Tag Test 2'}}

        lines = _render_transcript_blocks(segments, merged, tag_spans, [], tag_map, True, False)
        body = '\n'.join(lines)
        assert '**aa bb** [Tag Test 2]' in body
        assert ' cc' in body

    def test_merged_coordinates_without_end_segment_idx(self):
        """Without end_segment_idx, merged_start/merged_end override clipped start_char/end_char."""
        segments = [
            {'speaker': 'Mod', 'text': 'aa', 'start': 0, 'end': 1},
            {'speaker': 'Mod', 'text': 'bb cc', 'start': 1, 'end': 5},
        ]
        merged = _merge_consecutive_speakers(segments)
        tag_spans = [{
            'segment_idx': 0,
            'start_char': 0,
            'end_char': 2,
            'merged_start': 0,
            'merged_end': 5,
            'tag_id': 't1',
        }]
        tag_map = {'t1': {'id': 't1', 'name': 'M'}}

        lines = _render_transcript_blocks(segments, merged, tag_spans, [], tag_map, True, False)
        body = '\n'.join(lines)
        assert '**aa bb** [M]' in body
        assert ' cc' in body

    def test_cross_utt_end_block_uses_transcript_slice_not_anchor_text(self):
        """Cross-utterance tag: moderator line must keep tail; do not dump full anchor_text."""
        segments = [
            {'speaker': 'Participant 1', 'text': 'Hello NAME', 'start': 0, 'end': 1},
            {'speaker': 'Moderator', 'text': 'Hi.', 'start': 1, 'end': 2},
            {'speaker': 'Moderator', 'text': 'Bye there.', 'start': 2, 'end': 3},
        ]
        merged = _merge_consecutive_speakers(segments)
        tag_spans = [{
            'segment_idx': 0,
            'end_segment_idx': 1,
            'start_char': 6,
            'end_merged_end': 3,
            'anchor_text': 'NAME\n0:09\nModerator\nHi.',
            'tag_id': 't1',
        }]
        tag_map = {'t1': {'id': 't1', 'name': 'T'}}

        lines = _render_transcript_blocks(segments, merged, tag_spans, [], tag_map, True, False)
        body = '\n'.join(lines)
        assert '**Hi.** [T]' in body
        assert ' Bye there.' in body
        assert '0:09' not in body

    def test_nested_tags_outer_only_no_duplicate_suffix(self):
        """Inner tag fully inside outer: export once, no repeated trailing phrase."""
        segments = [{'speaker': 'M', 'text': 'And do you like your major?', 'start': 0, 'end': 1}]
        merged = _merge_consecutive_speakers(segments)
        tag_spans = [
            {'segment_idx': 0, 'start_char': 0, 'end_char': 28, 'tag_id': 'outer'},
            {'segment_idx': 0, 'start_char': 11, 'end_char': 28, 'tag_id': 'inner'},
        ]
        tag_map = {
            'outer': {'id': 'outer', 'name': 'Outer'},
            'inner': {'id': 'inner', 'name': 'Inner'},
        }
        lines = _render_transcript_blocks(segments, merged, tag_spans, [], tag_map, True, False)
        body = '\n'.join(lines)
        assert body.count('like your major?') == 1
        assert 'And do you **like your major?** [Inner] [Outer]' in body

    def test_same_range_two_tags_separate_brackets(self):
        segments = [{'speaker': 'M', 'text': 'Hello', 'start': 0, 'end': 1}]
        merged = _merge_consecutive_speakers(segments)
        tag_spans = [
            {'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 'a'},
            {'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 'b'},
        ]
        tag_map = {'a': {'id': 'a', 'name': 'One'}, 'b': {'id': 'b', 'name': 'Two'}}
        lines = _render_transcript_blocks(segments, merged, tag_spans, [], tag_map, True, False)
        assert '**Hello** [One] [Two]' in '\n'.join(lines)

    def test_cross_block_end_only_missing_end_merged_end_covers_block(self):
        """end_merged_end absent → use full merged line (avoid ec==0 skip / zero-width)."""
        segments = [
            {'speaker': 'P1', 'text': 'Hello.', 'start': 0, 'end': 1},
            {'speaker': 'Mod', 'text': 'Okay. Tail here.', 'start': 1, 'end': 2},
        ]
        merged = _merge_consecutive_speakers(segments)
        tag_spans = [{
            'segment_idx': 0,
            'end_segment_idx': 1,
            'start_char': 0,
            'end_char': 6,
            'merged_start': 0,
            'merged_end': 6,
            'tag_id': 't1',
            'anchor_text': 'Hello.',
            # no end_merged_end
        }]
        tag_map = {'t1': {'id': 't1', 'name': 'T'}}
        lines = _render_transcript_blocks(segments, merged, tag_spans, [], tag_map, True, False)
        body = '\n'.join(lines)
        assert '**Okay. Tail here.** [T]' in body
        assert 'Tail here' in body

    def test_cross_utterance_comment_on_both_speaker_lines(self):
        """Comment spanning two merged blocks: each line gets inline **slice** [Comment: …]."""
        segments = [
            {'speaker': 'P1', 'text': 'So here we go.', 'start': 0, 'end': 1},
            {'speaker': 'P2', 'text': 'Welcome. Welcome.', 'start': 1, 'end': 2},
        ]
        merged = _merge_consecutive_speakers(segments)
        assert len(merged) == 2
        comments = [{
            'segment_idx': 0,
            'end_segment_idx': 1,
            'start_char': 11,
            'end_char': 14,
            'merged_start': 11,
            'end_seg_end_char': 8,
            'text': 'Interview note',
            'anchor_text': 'go. Welcome.',
        }]
        tag_map = {}
        lines = _render_transcript_blocks(segments, merged, [], comments, tag_map, False, True)
        body = '\n'.join(lines)
        assert '**go.** [Comment: Interview note]' in body
        assert '**Welcome.** [Comment: Interview note]' in body


class TestExportRecordingOdt:
    """Tests for export_recording_odt."""

    def test_delegates_to_markdown_and_converts(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='ODT Test', folder_name='odt_test')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='a.mp3',
                stored_name='a.mp3',
                transcript_path='a_transcript.json',
                duration_seconds=10,
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'odt_test')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'a_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'segments': [{'start': 0, 'end': 5, 'text': 'Test', 'speaker': 'Moderator'}],
                    'duration_seconds': 5,
                }, f)

        content, err = export_recording_odt(app, pid, rid, {})
        assert err is None
        assert content is not None
        assert isinstance(content, bytes)
        # ODT is a ZIP file
        import zipfile
        import io
        zf = zipfile.ZipFile(io.BytesIO(content), 'r')
        names = zf.namelist()
        assert 'content.xml' in names
        assert 'mimetype' in names


class TestExportProjectMarkdown:
    """Tests for export_project_markdown."""

    def test_export_project_markdown_single_recording(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='Single Rec Project', folder_name='single_rec')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='interview.mp3',
                stored_name='interview.mp3',
                transcript_path='interview_transcript.json',
                duration_seconds=30,
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'single_rec')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'interview_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'segments': [
                        {'start': 0, 'end': 10, 'text': 'Hello world', 'speaker': 'Moderator'},
                        {'start': 10, 'end': 20, 'text': 'Good morning', 'speaker': 'Participant'},
                    ],
                    'duration_seconds': 20,
                }, f)

        content, err = export_project_markdown(app, pid, [rid], {})
        assert err is None
        assert content is not None
        assert 'Single Rec Project' in content
        assert 'Hello world' in content
        assert 'Good morning' in content

    def test_export_project_markdown_with_tags_comments(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='Tags Project', folder_name='tags_proj')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='rec.mp3',
                stored_name='rec.mp3',
                transcript_path='rec_transcript.json',
                duration_seconds=30,
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'tags_proj')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'rec_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'segments': [
                        {'start': 0, 'end': 10, 'text': 'Some transcript text', 'speaker': 'Moderator'},
                    ],
                    'duration_seconds': 10,
                }, f)

            with open(os.path.join(proj_dir, 'project_tags.json'), 'w', encoding='utf-8') as f:
                json.dump([
                    {'id': 'imp', 'name': 'ImportantTag', 'color': '#ff0000'},
                ], f)

            with open(os.path.join(proj_dir, 'rec_annotations.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'tag_spans': [
                        {'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 'imp'},
                    ],
                    'comments': [
                        {'segment_idx': 0, 'text': 'This is a test comment'},
                    ],
                }, f)

        content, err = export_project_markdown(app, pid, [rid], {
            'include_tags': True,
            'include_comments': True,
        })
        assert err is None
        assert content is not None
        assert 'ImportantTag' in content
        assert 'This is a test comment' in content

    def test_export_project_markdown_exclude_options(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='Exclude Project', folder_name='exclude_proj')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='rec.mp3',
                stored_name='rec.mp3',
                transcript_path='rec_transcript.json',
                duration_seconds=30,
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'exclude_proj')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'rec_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'segments': [
                        {'start': 0, 'end': 10, 'text': 'Some transcript text', 'speaker': 'Moderator'},
                    ],
                    'duration_seconds': 10,
                }, f)

            with open(os.path.join(proj_dir, 'project_tags.json'), 'w', encoding='utf-8') as f:
                json.dump([
                    {'id': 'hid', 'name': 'HiddenTag', 'color': '#00ff00'},
                ], f)

            with open(os.path.join(proj_dir, 'rec_annotations.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'tag_spans': [
                        {'segment_idx': 0, 'start_char': 0, 'end_char': 5, 'tag_id': 'hid'},
                    ],
                    'comments': [
                        {'segment_idx': 0, 'text': 'Hidden comment content'},
                    ],
                }, f)

        content, err = export_project_markdown(app, pid, [rid], {
            'include_tags': False,
            'include_comments': False,
        })
        assert err is None
        assert content is not None
        assert 'HiddenTag' not in content
        assert 'Hidden comment content' not in content

    def test_export_project_markdown_nonexistent(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

        content, err = export_project_markdown(app, 99999, [1], {})
        assert content is None
        assert err is not None

    def test_export_project_odt_returns_bytes(self, app, temp_dir):
        with app.app_context():
            from app.extensions import db
            from app.models.project import Project
            from app.models.recording import Recording
            from app.models.setting import Setting

            projects_path = os.path.join(temp_dir, 'projects')
            os.makedirs(projects_path, exist_ok=True)
            Setting.set('projects_path', projects_path)
            db.create_all()

            proj = Project(name='ODT Project', folder_name='odt_proj')
            db.session.add(proj)
            db.session.flush()
            rec = Recording(
                project_id=proj.id,
                original_name='interview.mp3',
                stored_name='interview.mp3',
                transcript_path='interview_transcript.json',
                duration_seconds=30,
                transcription_status='transcribed',
            )
            db.session.add(rec)
            db.session.commit()
            pid, rid = proj.id, rec.id

            proj_dir = os.path.join(projects_path, 'odt_proj')
            os.makedirs(proj_dir, exist_ok=True)
            with open(os.path.join(proj_dir, 'interview_transcript.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'segments': [
                        {'start': 0, 'end': 10, 'text': 'Hello world', 'speaker': 'Moderator'},
                    ],
                    'duration_seconds': 10,
                }, f)

        content, err = export_project_odt(app, pid, [rid], {})
        assert err is None
        assert content is not None
        assert isinstance(content, bytes)
        # ODT is a ZIP file
        import zipfile
        import io
        zf = zipfile.ZipFile(io.BytesIO(content), 'r')
        names = zf.namelist()
        assert 'content.xml' in names
        assert 'mimetype' in names
