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
#
# Default is off. On CUDA both stages land on the same card by default, and a
# 12 GB GPU running whisperx float16 (batch_size 16) next to pyannote spends
# 6+ minutes with both resident: the machine becomes unusable while the driver
# pages VRAM back to host RAM, and the first chunk's status line sits still for
# the whole of it. Sequential stages cost more wall clock and give it back in
# a responsive desktop. Set PINE_PARALLEL_STAGES=1 to overlap them again --
# worth it when diarization is on a different device (PINE_DIARIZE_DEVICE=cpu)
# or the GPU has headroom to spare.
PARALLEL_STAGES = os.environ.get('PINE_PARALLEL_STAGES', '0').strip() != '0'

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
#
# These stay as shipped. What corrects them is measurement: every finished job
# reports how far off the plan was for this machine, model and mode, and the
# next job starts from that instead (see ProgressMapper.observed_scale and
# `progress_scale:*` in Settings). So the numbers below only have to be a
# sensible starting point for the very first recording — the alternative,
# hand-tuning them per platform, needs a machine of every kind and goes stale
# the moment anyone changes model.
PROGRESS_RTF_TRANSCRIBE = float(os.environ.get('PINE_PROGRESS_RTF_TRANSCRIBE', '0.045'))
PROGRESS_RTF_ALIGN = float(os.environ.get('PINE_PROGRESS_RTF_ALIGN', '0.010'))
PROGRESS_RTF_DIARIZE = float(os.environ.get('PINE_PROGRESS_RTF_DIARIZE', '0.019'))
# Bounds on the learned correction, so one pathological run (a machine that
# went to sleep mid-job) cannot poison the estimate for every job after it.
PROGRESS_SCALE_MIN = float(os.environ.get('PINE_PROGRESS_SCALE_MIN', '0.2'))
PROGRESS_SCALE_MAX = float(os.environ.get('PINE_PROGRESS_SCALE_MAX', '5.0'))
# Weight of the newest measurement against the running one.
PROGRESS_SCALE_SMOOTHING = float(os.environ.get('PINE_PROGRESS_SCALE_SMOOTHING', '0.4'))
# Below this much audio the flat costs dominate and the ratio measures disk
# cache rather than transcription speed, so nothing is learned from it.
PROGRESS_LEARN_MIN_SEC = float(os.environ.get('PINE_PROGRESS_LEARN_MIN_SEC', '120'))
# Flat costs that don't scale with audio length.
PROGRESS_LOAD_SEC = float(os.environ.get('PINE_PROGRESS_LOAD_SEC', '10'))
PROGRESS_FINALIZE_SEC = 2.0
# How often the interpolating ticker refreshes between real milestones.
PROGRESS_TICK_SEC = float(os.environ.get('PINE_PROGRESS_TICK_SEC', '2'))
# Key the measured pace travels under inside the transcript payload. Underscored
# because it is not part of the transcript — ``_finalize`` pops it before the
# file is written. It lives here rather than beside the pipeline so the Flask
# side can read it without importing the pipeline, and through it torch: that
# import ran once with a stale ``constants`` still in ``sys.modules`` and cost
# a finished 40-minute transcript.
PROGRESS_SCALE_KEY = '_progress_scale'

# ── Per-speaker tracks (see tracks.py) ──
#
# Voice activity detection on one speaker's own track, used to cut the pauses
# out before transcription. Tuned for interviews rather than for a general VAD:
# the thing most easily lost here is back-channel — "угу", "да", "понятно" —
# which runs 0.3-0.8s and would be dropped by the 0.5s minimum a general-purpose
# gate uses. Losing it costs the moderator's half of the conversation, so the
# minimum sits at 0.2s and short gaps are closed before anything is discarded.
VAD_FRAME_SEC = float(os.environ.get('PINE_VAD_FRAME_SEC', '0.02'))
VAD_MIN_SPEECH_SEC = float(os.environ.get('PINE_VAD_MIN_SPEECH_SEC', '0.2'))
VAD_MERGE_GAP_SEC = float(os.environ.get('PINE_VAD_MERGE_GAP_SEC', '0.5'))
VAD_PAD_SEC = float(os.environ.get('PINE_VAD_PAD_SEC', '0.25'))
# Thresholds are a fraction of each track's own quiet-to-loud range, so tracks
# recorded at different levels are each measured against themselves.
VAD_OPEN_FRACTION = float(os.environ.get('PINE_VAD_OPEN_FRACTION', '0.35'))
VAD_CLOSE_FRACTION = float(os.environ.get('PINE_VAD_CLOSE_FRACTION', '0.20'))
VAD_MIN_MARGIN_DB = float(os.environ.get('PINE_VAD_MIN_MARGIN_DB', '6.0'))
# Digital silence is -inf dB; without a floor the range is unbounded and every
# threshold derived from it collapses. Also the level below which a track with
# no loud/quiet structure is read as empty rather than as speech throughout.
VAD_SILENCE_FLOOR_DB = float(os.environ.get('PINE_VAD_SILENCE_FLOOR_DB', '-80.0'))
VAD_MIN_DYNAMIC_DB = float(os.environ.get('PINE_VAD_MIN_DYNAMIC_DB', '6.0'))
# How far one track must lead the others to be given a frame outright. Only
# bites on multi-channel recordings where the mics hear each other.
VAD_DOMINANCE_DB = float(os.environ.get('PINE_VAD_DOMINANCE_DB', '6.0'))
# Silence inserted between two spliced-together regions, so the model does not
# run a turn from minute 3 into a turn from minute 40.
VAD_COMPACT_GAP_SEC = float(os.environ.get('PINE_VAD_COMPACT_GAP_SEC', '0.2'))
# Cutting the pauses out also cuts the transcript: a turn spoken across two
# speech regions comes back as two segments. Neighbours from one speaker no
# further apart than this are put back together — wide enough to close the
# breath the gate opened on, short enough to leave a real handover alone.
TRACK_JOIN_GAP_SEC = float(os.environ.get('PINE_TRACK_JOIN_GAP_SEC', '1.5'))
# ...but only up to here. Someone talking steadily for ten minutes would
# otherwise arrive as one block nobody can scroll past or click into.
TRACK_JOIN_MAX_SEC = float(os.environ.get('PINE_TRACK_JOIN_MAX_SEC', '30.0'))

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
