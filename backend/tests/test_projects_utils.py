"""Unit tests for projects API utilities."""

import pytest

from app.api.projects import _safe_folder_name, ALLOWED_EXTENSIONS
from app.services.annotations import annotation_recording_ref


class TestSafeFolderName:
    """Tests for _safe_folder_name (filesystem-safe slug, collision handling)."""

    def test_simple_name(self):
        assert _safe_folder_name('My Project') == 'My_Project'

    def test_special_chars_stripped(self):
        assert _safe_folder_name('Audio & Video — Test!') == 'Audio_Video_Test'

    def test_unicode_handled(self):
        # Result should be filesystem-safe (no path separators, etc.)
        result = _safe_folder_name('Café Résumé')
        assert '/' not in result and '\\' not in result
        assert result

    def test_empty_after_strip_becomes_project(self):
        assert _safe_folder_name('!!!@@@###') == 'project'

    def test_whitespace_only_becomes_project(self):
        assert _safe_folder_name('   ') == 'project'

    def test_truncated_to_80_chars(self):
        long_name = 'A' * 100
        result = _safe_folder_name(long_name)
        assert len(result) <= 80

    def test_collision_append_counter(self):
        existing = {'My_Project'}
        result = _safe_folder_name('My Project', existing)
        assert result == 'My_Project_2'  # Implementation uses counter+1 first

    def test_multiple_collisions(self):
        existing = {'My_Project', 'My_Project_2', 'My_Project_3'}
        result = _safe_folder_name('My Project', existing)
        assert result == 'My_Project_4'

    def test_case_insensitive_collision(self):
        existing = {'my_project'}
        result = _safe_folder_name('My Project', existing)
        assert result == 'My_Project_2'  # Implementation uses counter+1 first

    def test_no_collision_when_unique(self):
        existing = {'Other_Project'}
        result = _safe_folder_name('My Project', existing)
        assert result == 'My_Project'

    def test_existing_none_uses_empty_set(self):
        result = _safe_folder_name('Test')
        assert result == 'Test'


class TestAllowedExtensions:
    """Tests for upload extension validation."""

    def test_all_allowed_formats(self):
        allowed = {'mp3', 'mp4', 'm4a', 'wav', 'mkv', 'webm', 'ogg', 'flac'}
        assert ALLOWED_EXTENSIONS == allowed


class TestAnnotationRecordingRef:
    """Tests for annotation key source per recording."""

    def test_uses_transcript_path_when_present(self):
        class R:
            id = 101
            transcript_path = 'interview_101_transcript.json'
            stored_name = 'interview.mp3'

        assert annotation_recording_ref(R()) == 'interview_101_transcript.json'

    def test_uses_unique_id_key_before_transcription(self):
        class R:
            id = 101
            transcript_path = None
            stored_name = 'interview.mp3'

        assert annotation_recording_ref(R()) == 'interview_101_transcript.json'

    def test_falls_back_to_stored_name_without_id(self):
        class R:
            transcript_path = None
            stored_name = 'interview.mp3'

        assert annotation_recording_ref(R()) == 'interview.mp3'
