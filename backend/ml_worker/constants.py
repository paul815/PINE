"""Tunables shared across the ML pipeline."""

import os

# Chunked processing for long audio files (>30 min)
CHUNK_THRESHOLD_SEC = 1800  # switch to chunked if >= 30 min
CHUNK_SIZE_SEC = 1800       # each chunk = 30 min
CHUNK_OVERLAP_SEC = 30      # overlap to avoid cutting mid-word

# Chunked diarization. This was introduced on the assumption that pyannote's
# clustering is ~O(n²), so a long file had to be split. Measurement says
# otherwise: on a 74-min recording the eight chunks cost 124.4 / 126.2 / 125.8 /
# 125.4 / 125.6 / 126.4 / 125.9 / 101.0s — flat per unit of audio. The cost is
# the segmentation and embedding passes, which are linear; clustering a few
# thousand embeddings is seconds. So chunking bought no speed, spent an extra
# 7% re-processing the overlaps (8 x 600s of audio for a 4468s file), and cost
# accuracy: speaker identity is stitched across chunk boundaries by temporal
# overlap in ``merge_chunk_speakers``, and one bad match flips two speakers for
# the rest of the file (observed at 3420s = the 7th chunk boundary).
#
# Whole-file diarization is therefore both faster and correct, and the threshold
# now exists only as an out-of-memory guard: the decoded waveform is ~4 MB per
# audio-minute, so a 4-hour file is ~1 GB held while pyannote runs. Past that,
# accept the label-drift risk rather than the OOM.
DIARIZE_CHUNK_THRESHOLD_SEC = int(
    os.environ.get('PINE_DIARIZE_CHUNK_THRESHOLD_SEC', '14400'))  # 4 hours
DIARIZE_CHUNK_SIZE_SEC = 600         # 10-min chunks (sweet spot for pyannote)
DIARIZE_CHUNK_OVERLAP_SEC = 30       # 30s overlap for speaker continuity

# Run diarization alongside transcription instead of after it. pyannote reads
# only the audio — the transcript is needed by the speaker *assignment* step,
# which costs ~0s — so the two stages have no real dependency. Serially they
# cost STT + diarize; overlapped they cost roughly max(STT, diarize). On the
# 74-min reference recording that is 1762.8s -> ~985s.
#
# The win depends on the two stages not fighting over one accelerator: with
# mlx-whisper on Metal, diarization wants to be somewhere else. Measure the
# combinations with PINE_PARALLEL_STAGES and PINE_DIARIZE_DEVICE before
# assuming a default fits.
PARALLEL_STAGES = os.environ.get('PINE_PARALLEL_STAGES', '1').strip() != '0'

# Progress weighting: expected cost of each stage as a multiple of the audio
# duration. These only set how the single 0→100 scale is divided between
# stages, so being off by a bit costs pacing, not correctness. Measured on
# whisperx/CUDA over ~75-min recordings: STT 0.032–0.084x realtime (the spread
# is model size and GPU), diarization a steady ~0.019x.
#
# The mlx/pyannote path on Apple Silicon sits an order of magnitude higher and
# in the opposite order — measured on a 74-min recording: STT 0.174x,
# diarization 0.220x. These defaults are therefore wrong for that path, but
# only for pacing: ProgressMapper re-derives the job's pace from its own
# milestones, so the plan is a starting prior, not the ETA. Retuning them per
# platform is a separate change, and would want more than one machine.
PROGRESS_RTF_TRANSCRIBE = float(os.environ.get('PINE_PROGRESS_RTF_TRANSCRIBE', '0.045'))
PROGRESS_RTF_ALIGN = float(os.environ.get('PINE_PROGRESS_RTF_ALIGN', '0.010'))
PROGRESS_RTF_DIARIZE = float(os.environ.get('PINE_PROGRESS_RTF_DIARIZE', '0.019'))
# Flat costs that don't scale with audio length.
PROGRESS_LOAD_SEC = float(os.environ.get('PINE_PROGRESS_LOAD_SEC', '10'))
PROGRESS_FINALIZE_SEC = 2.0
# How often the interpolating ticker refreshes between real milestones.
PROGRESS_TICK_SEC = float(os.environ.get('PINE_PROGRESS_TICK_SEC', '2'))

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
