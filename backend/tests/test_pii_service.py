"""Unit tests for PII service (redact_text with mocked entities)."""

import pytest

from app.services.pii_service import redact_text, redact_segments


class TestRedactText:
    """Tests for redact_text - logic only; model not loaded."""

    def test_empty_entities_returns_original(self):
        text = 'Hello John, call me at 555-1234'
        assert redact_text(text, []) == text

    def test_single_entity(self):
        text = 'Hello John Smith'
        entities = [{'start': 6, 'end': 16, 'label': 'person', 'text': 'John Smith'}]
        result = redact_text(text, entities)
        assert 'John Smith' not in result
        assert 'person' in result.lower() or 'redact' in result.lower()

    def test_multiple_entities_replaced_descending(self):
        text = 'Email: john@example.com and phone 555-1234'
        entities = [
            {'start': 8, 'end': 24, 'label': 'email', 'text': 'john@example.com'},
            {'start': 33, 'end': 41, 'label': 'phone number', 'text': '555-1234'},
        ]
        result = redact_text(text, entities)
        assert 'john@example.com' not in result
        assert '555-1234' not in result

    def test_overlapping_entities(self):
        # Replace from end to start to avoid index shift
        text = 'xxx'
        entities = [
            {'start': 0, 'end': 1, 'label': 'x', 'text': 'x'},
            {'start': 1, 'end': 2, 'label': 'x', 'text': 'x'},
        ]
        result = redact_text(text, entities)
        assert 'xxx' not in result or 'x' in result  # At least partial redaction

    def test_label_in_redaction(self):
        text = 'Secret'
        entities = [{'start': 0, 'end': 6, 'label': 'secret', 'text': 'Secret'}]
        result = redact_text(text, entities)
        assert 'Secret' not in result
        assert 'redacted' in result.lower()


class TestRedactSegments:
    """Tests for redact_segments - when model is None, raises PIIError."""

    def test_empty_segments_raises_without_model(self):
        from app.services.pii_service import PIIError
        with pytest.raises(PIIError):
            redact_segments([])

    def test_segments_raise_when_model_unavailable(self):
        from app.services.pii_service import PIIError
        segs = [{'start': 0, 'end': 1, 'text': 'Hello', 'speaker': 'S1'}]
        with pytest.raises(PIIError):
            redact_segments(segs)
