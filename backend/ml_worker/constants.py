"""Tunables shared across the ML pipeline."""

import os

# Chunked processing for long audio files (>30 min)
CHUNK_THRESHOLD_SEC = 1800  # switch to chunked if >= 30 min
CHUNK_SIZE_SEC = 1800       # each chunk = 30 min
CHUNK_OVERLAP_SEC = 30      # overlap to avoid cutting mid-word

# Chunked diarization for long audio (pyannote clustering is ~O(n²))
DIARIZE_CHUNK_THRESHOLD_SEC = 1200   # chunk diarization if >= 20 min
DIARIZE_CHUNK_SIZE_SEC = 600         # 10-min chunks (sweet spot for pyannote)
DIARIZE_CHUNK_OVERLAP_SEC = 30       # 30s overlap for speaker continuity

SPEAKER_LABELS = [
    'Moderator',
    'Participant 1',
    'Participant 2',
    'Participant 3',
    'Participant 4',
    'Participant 5',
]

# Min probability on the first-30s language probe to skip asking the user (WhisperX: top-1 only).
LANG_CONFIDENCE_MIN = float(os.environ.get('PINE_LANG_CONFIDENCE_MIN', '0.82'))
# MLX: also require this margin between 1st and 2nd language probability.
LANG_CONFIDENCE_MARGIN = float(os.environ.get('PINE_LANG_CONFIDENCE_MARGIN', '0.10'))

# Language-specific WhisperX align-model overrides.
# These repos include tokenizer metadata needed by recent transformers/hf_hub stacks.
ALIGN_MODEL_CANDIDATES = {
    'ru': [
        'anton-l/wav2vec2-large-xlsr-53-russian',
        'jonatasgrosman/wav2vec2-large-xlsr-53-russian',
    ],
}
