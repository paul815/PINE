"""The transcript version stamp and how old files are treated."""

from app.services.transcript_format import (
    TRANSCRIPT_SCHEMA_VERSION,
    is_readable,
    schema_version_of,
    stamp,
)


class TestStamp:
    def test_adds_version_engine_and_model(self):
        t = stamp({'segments': []}, engine='whisperx', model='whisperx-large-v3')
        assert t['schema_version'] == TRANSCRIPT_SCHEMA_VERSION
        assert t['engine'] == 'whisperx'
        assert t['model'] == 'whisperx-large-v3'
        assert t['created_at'].endswith('+00:00')

    def test_leaves_the_segments_alone(self):
        segments = [{'start': 0.0, 'end': 1.0, 'text': 'hi', 'speaker': 'SPEAKER_00'}]
        t = stamp({'segments': segments, 'language': 'en'})
        assert t['segments'] == segments
        assert t['language'] == 'en'

    def test_resaving_does_not_relabel_the_producer(self):
        """A find & replace must not claim the file came from today's engine."""
        original = stamp({'segments': []}, engine='mlx', model='mlx-large-v3')
        created = original['created_at']
        again = stamp(dict(original), engine='whisperx', model='whisperx-large-v3')
        assert again['engine'] == 'mlx'
        assert again['model'] == 'mlx-large-v3'
        assert again['created_at'] == created

    def test_empty_engine_and_model_are_not_written(self):
        t = stamp({'segments': []})
        assert 'engine' not in t
        assert 'model' not in t


class TestVersionOf:
    def test_unstamped_file_is_version_one(self):
        """Every transcript written before the stamp existed."""
        assert schema_version_of({'segments': [], 'language': 'en'}) == 1

    def test_reads_an_explicit_version(self):
        assert schema_version_of({'schema_version': 7}) == 7

    def test_garbage_version_falls_back_to_one(self):
        assert schema_version_of({'schema_version': 'banana'}) == 1
        assert schema_version_of({'schema_version': None}) == 1

    def test_readability(self):
        assert is_readable({})
        assert is_readable({'schema_version': TRANSCRIPT_SCHEMA_VERSION})
        assert not is_readable({'schema_version': TRANSCRIPT_SCHEMA_VERSION + 1})
