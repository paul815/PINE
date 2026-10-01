"""Version stamp for the on-disk transcript format.

Transcripts are the one PINE artefact with no schema and no owner: they are
plain JSON in the project folder, they outlive the app version that produced
them, and a transfer package moves them between installations. Until now a
file carried nothing that said which shape it was, so any future change to the
format — confidence scores, word-level overlaps, a different STT engine — had
no way to tell an old file from a new one, and no migration could be written
after the fact.

The stamp is deliberately minimal:

    schema_version   int    the format revision (this module's constant)
    engine           str    which STT engine produced it ('whisperx', 'mlx')
    model            str    the model id, e.g. 'whisperx-large-v3'
    created_at       str    UTC ISO-8601

**A file without the key is version 1**, i.e. everything written before this
existed. That is what ``schema_version_of`` returns for it, so old transcripts
keep working untouched — nothing rewrites them, and nothing needs to.

When the format changes incompatibly: bump ``TRANSCRIPT_SCHEMA_VERSION``, and
give readers a branch on ``schema_version_of``.
"""

from datetime import UTC, datetime

#: Current on-disk revision.
#: 1 — {recording_id, language, duration_seconds, speakers, segments[]},
#:     segments carrying start/end/text/speaker and optional word timings.
TRANSCRIPT_SCHEMA_VERSION = 1

SCHEMA_VERSION_KEY = 'schema_version'


def stamp(transcript, engine='', model=''):
    """Add the version stamp to *transcript* in place and return it.

    Existing keys win: re-saving a file (a find & replace, say) must not
    relabel it as having been produced by whatever engine is loaded now.
    """
    transcript.setdefault(SCHEMA_VERSION_KEY, TRANSCRIPT_SCHEMA_VERSION)
    transcript.setdefault('created_at', datetime.now(UTC).isoformat())
    if engine:
        transcript.setdefault('engine', engine)
    if model:
        transcript.setdefault('model', model)
    return transcript


def schema_version_of(transcript):
    """Revision of a loaded transcript. Unstamped files are revision 1."""
    try:
        return int(transcript.get(SCHEMA_VERSION_KEY, 1))
    except (AttributeError, TypeError, ValueError):
        return 1


def is_readable(transcript):
    """Whether this build understands the file.

    Older or equal revisions are readable. A *newer* one means the user
    downgraded PINE, or restored a backup from a newer install — the caller
    should say so rather than silently rendering a format it does not know.
    """
    return schema_version_of(transcript) <= TRANSCRIPT_SCHEMA_VERSION
