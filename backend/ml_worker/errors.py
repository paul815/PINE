"""Exceptions shared between the pipeline and its callers."""


class TranscriptionCancelled(Exception):
    """Raised when a transcription job is signalled to stop."""
