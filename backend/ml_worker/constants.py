"""Tunables shared across the ML pipeline.

The numbers here are measured, not guessed — each one carries the observation it
came from — and they are literals on purpose. Every threshold used to also read
a ``PINE_*`` environment variable, thirty-six of them, none named anywhere else
in the repo: not in a test, not in the docs, not in a launcher. They were a
tuning session that ended, left behind as a promise the code no longer keeps, so
a reader had to check each default twice to learn it was the only value in play.
Retune by changing the value and saying what you measured.

Four overrides survive, because something outside this file knows them:

* ``PINE_PARALLEL_STAGES`` and ``PINE_DIARIZE_DEVICE`` (the latter lives in
  ``diarize.py``) — documentation/ARCHITECTURE.md tells you to measure with them.
* ``PINE_LANG_CONFIDENCE_MIN`` / ``PINE_LANG_CONFIDENCE_MARGIN`` — documented in
  documentation/API.md.
* ``PINE_DIARIZE_CHUNK_THRESHOLD_SEC`` — the out-of-memory guard below. Not a
  quality trade-off: the way out of a crash on a very long file.
"""

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
#
# A Mac running mlx-whisper looked like the exception, and is not. With pyannote
# on MPS as well (2026-07-19, the same 74-min recording) the two queued on one
# GPU: STT nearly doubled and the whole job gained 17%. With pyannote on the
# CPU instead (2026-09-24, an 88-minute interview) STT kept its pace, 1081s
# against 1051s serially, but diarization had not finished 33 minutes after it
# started — MPS does the same file in 307s after STT — and the progress bar sat
# at 99% the whole time, since a stage running underneath reports nothing. So
# the stages run in turn everywhere; measure before turning this on.
_PARALLEL_ENV = os.environ.get('PINE_PARALLEL_STAGES', '').strip()
PARALLEL_STAGES = _PARALLEL_ENV not in ('', '0')

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
PROGRESS_RTF_TRANSCRIBE = 0.045
PROGRESS_RTF_ALIGN = 0.010
PROGRESS_RTF_DIARIZE = 0.019
# Bounds on the learned correction, so one pathological run (a machine that
# went to sleep mid-job) cannot poison the estimate for every job after it.
PROGRESS_SCALE_MIN = 0.2
PROGRESS_SCALE_MAX = 5.0
# Weight of the newest measurement against the running one.
PROGRESS_SCALE_SMOOTHING = 0.4
# Below this much audio the flat costs dominate and the ratio measures disk
# cache rather than transcription speed, so nothing is learned from it.
PROGRESS_LEARN_MIN_SEC = 120.0
# Flat costs that don't scale with audio length.
PROGRESS_LOAD_SEC = 10.0
PROGRESS_FINALIZE_SEC = 2.0
# How often the interpolating ticker refreshes between real milestones.
PROGRESS_TICK_SEC = 2.0
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
VAD_FRAME_SEC = 0.02
VAD_MIN_SPEECH_SEC = 0.2
VAD_MERGE_GAP_SEC = 0.5
VAD_PAD_SEC = 0.25
# Thresholds are a fraction of each track's own quiet-to-loud range, so tracks
# recorded at different levels are each measured against themselves.
VAD_OPEN_FRACTION = 0.35
VAD_CLOSE_FRACTION = 0.20
VAD_MIN_MARGIN_DB = 6.0
# Digital silence is -inf dB; without a floor the range is unbounded and every
# threshold derived from it collapses. Also the level below which a track with
# no loud/quiet structure is read as empty rather than as speech throughout.
VAD_SILENCE_FLOOR_DB = -80.0
VAD_MIN_DYNAMIC_DB = 6.0
# How far one track must lead the others to be given a frame outright. Only
# bites on multi-channel recordings where the mics hear each other.
VAD_DOMINANCE_DB = 6.0
# Silence inserted between two spliced-together regions, so the model does not
# run a turn from minute 3 into a turn from minute 40.
VAD_COMPACT_GAP_SEC = 0.2
# Cutting the pauses out also cuts the transcript: a turn spoken across two
# speech regions comes back as two segments. Neighbours from one speaker no
# further apart than this are put back together — wide enough to close the
# breath the gate opened on, short enough to leave a real handover alone.
TRACK_JOIN_GAP_SEC = 1.5
# ...but only up to here. Someone talking steadily for ten minutes would
# otherwise arrive as one block nobody can scroll past or click into.
TRACK_JOIN_MAX_SEC = 30.0
# Per-track gate, for the multitrack path only (ml_worker/multitrack.py).
# Every region boundary there is a seam the transcript can tear along: whisperx
# reads the compacted track as continuous and the aligner spreads words over
# silence that was never spoken. The general-purpose 0.5s only closes the gaps
# inside a sentence, which cut a 40-minute interview into 330 regions; two
# seconds closes the breath between sentences as well, for ~5% more audio to
# decode. The MLX gate settled on the same pair for the same reason.
TRACK_VAD_MERGE_GAP_SEC = 2.0
TRACK_VAD_PAD_SEC = 0.5
# Cutting the pauses out also moves the words: whisperx aligns against audio
# whose seams it cannot see, and a word landing a fifth of a second the wrong
# side of one is remapped tens of seconds away from the rest of its sentence.
# A lone word this close behind the previous one was never a second utterance —
# a real one arrives after a pause — so it is kept with the words it belongs to.
# Twice the inserted gap: nothing that follows a seam can be closer than one.
TRACK_SEAM_CONTIGUOUS_SEC = 0.4

# ── Gating the audio for mlx-whisper (see engines/mlx_engine.py) ──
#
# whisperx drops non-speech before the model sees it; mlx-whisper has no VAD of
# its own, so the same gate runs on the single mixed track for the MLX path. The
# job here is narrower than on per-speaker tracks: not to cut every pause, but to
# keep long stretches of non-speech away from the model. A phone interview opens
# with ~45s of ringback, Whisper writes it down as "Звук колокола.", and
# condition_on_previous_text hands that sentence to the next window as context —
# one 38-minute recording lost its first 2.5 minutes, the consent question
# included, exactly that way.
#
# Every value below is therefore biased toward keeping audio. Dropping a pause
# saves seconds; dropping speech loses an answer the interview was recorded for.
# A wider merge gap than the per-track VAD, so only real dead air is cut and the
# pauses inside a sentence stay where they are.
MLX_VAD_MERGE_GAP_SEC = 2.0
# Double the per-track padding: one mixed track has no second track to catch a
# word this one clipped, so the gate errs wide on both sides of every region.
MLX_VAD_PAD_SEC = 0.5
# Below this share of the recording surviving the gate, the audio goes to the
# model untouched. Not a tuning knob — a dead-man's switch for the case where the
# thresholds misread the take, where the old behaviour is the safe one.
MLX_VAD_MIN_KEEP_FRACTION = 0.5
# The shortest cut worth making, applied to each cut rather than to their sum:
# a gap under this is closed and the audio inside it kept. What separates the two
# cases is not delicate. The ringback this gate exists for is one block of ~45s;
# the pauses in an 88-minute interview that it wrongly shaved were 18 cuts of 1-2s
# each, 29s in total. Anything between them divides the two, so the value is set
# well clear of both — and a cut left in costs only the seconds spent decoding it.
MLX_VAD_MIN_CUT_SEC = 10.0
# Ringback is loud, so an energy gate hands it to the model as speech — which is
# how it got transcribed in the first place. Spectral flatness is what separates
# them: the geometric mean of the power spectrum over its arithmetic mean, near
# zero for a sine that puts everything in one bin, high for a voice carrying a
# whole harmonic stack.
#
# Measured on synthetic tone and voice through a 300-3400 Hz telephone band (see
# tests/test_transcription_pipeline.py), a voice holds 4.3e-2 whatever the noise
# is doing, while the tone climbs off the floor as noise fills the band in: 1.5e-5
# at 40 dB SNR, 8.3e-4 at 20 dB, 6.8e-3 at 10 dB. This sits far below the voice
# rather than close under the noisiest tone it could catch, and so gives up on
# ringback recorded below ~20 dB SNR. That trade is the whole point: missing a
# tone leaves today's behaviour in place, while taking a voice for one deletes an
# answer the interview was recorded for.
MLX_VAD_MIN_FLATNESS = 0.001
# Regions shorter than this are kept whatever they are made of. Low on purpose:
# ringback is often a second of tone every five, so each ring arrives as its own
# short region, and a threshold set to leave "short" audio alone would wave the
# whole ring pattern through. Half a second is ~30 frames, plenty of spectrum to
# read, and still above the back-channel — "угу", "да" — worth protecting.
MLX_VAD_TONE_MIN_SEC = 0.5

# ── Carrying the prompt between windows (mlx only; see mlx_engine.py) ──
#
# Whisper's only memory across its 30 s windows is the prompt, and the prompt is
# what its punctuation and casing are built on. mlx-whisper will chain it for us,
# but chains a mistake just as faithfully, and there is no way in from outside to
# stop one. So the chain is cut into windows we hand it ourselves: each window is
# decoded with the tail of the last accepted one as its prompt, and a window that
# comes back looping is thrown away and re-decoded with no prompt at all.
#
# The window length trades the two against each other. Longer carries context
# further and costs fewer calls; shorter bounds what one bad prompt can take with
# it. Two minutes is ~4 of Whisper's own windows — long enough for the prompt to
# earn its keep, short enough that the worst case is two minutes re-decoded.
MLX_PROMPT_WINDOW_SEC = 120.0
# How much of the previous window's text is handed on. Whisper truncates the
# prompt to half its context on its own; this keeps what we send to the part that
# still describes how the speaker is writing.
MLX_PROMPT_CHARS = 200
# Identical segments in a row that mean the decoder is looping rather than
# transcribing. Three is still reachable by someone saying "да. да. да."; the
# runaway this catches ran for minutes.
MLX_PROMPT_REPEAT_LIMIT = 4
# The other shape a loop takes: one segment going round a handful of words —
# "и т.д. и т.д. .д. и т т.д." for twenty seconds, which Whisper writes when its
# own fallback gives up and samples the loop at a high temperature. No two
# segments match, so the count above never sees it. What does not survive it is
# the vocabulary: that loop had 3 different words in any 16 in a row. Over ~33,000
# words of Whisper transcripts, Russian and English, no 16 in a row held fewer than
# 8 — "yeah definitely yeah definitely" included.
MLX_LOOP_WINDOW_WORDS = 16
MLX_LOOP_MAX_DISTINCT = 4
# A window ends where its last finished segment ended, not at the length it was
# cut to, so the sentence that was still going is read again with the audio that
# finishes it. Below this much progress the tail is kept instead: re-reading a
# window almost from its start buys nothing and costs the whole window again.
# One of Whisper's own 30s windows is the natural floor.
MLX_WINDOW_MIN_PROGRESS_SEC = 30.0
# A window writing less than this share of the recording's own punctuation has
# copied a broken prompt rather than the audio, and is read again without one.
# Half is not a close call: measured over one 88-minute interview, windows that
# read correctly never fell below 70% of the median and the collapsed ones sat
# near 10%.
MLX_SENTENCE_RATE_FRACTION = 0.5
# And nothing is judged until there are this many windows to take a median from.
MLX_SENTENCE_RATE_WINDOWS = 3
# The temperatures mlx-whisper may fall back to when a 30s piece fails its own
# compression or log-probability check. Its default ladder climbs to 1.0, and
# near the top the decoder samples rather than reads: an 88-minute interview came
# back with "generatedți repentance Actinghesiaọнальных" in the middle of a
# sentence and its last 25 seconds as "тысячи DER terugивает ушко понятно…".
# whisperx never samples — it runs beam search and keeps what it finds — and the
# same recording on Windows has none of it. At 0.2 a token the model gave one
# chance in a thousand is left about one in 10^15, so the fallback can still
# nudge a piece out of a loop without writing in another alphabet. A loop that
# survives it is ``is_runaway``'s to catch, and that window is read again with
# nothing carried in.
MLX_TEMPERATURES = (0.0, 0.2)
# Languages written in Cyrillic, where a letter from any script but Cyrillic or
# Latin is not a word the speaker said. Latin is kept because Russian speech is
# full of it — YouTube, FIFA, Forbes, "Prime Time" all came out of one interview
# — and so is Latin-1 (Citroën, café). What is left over is what the sampler
# made up: "ọn", "ọn坐", "generatedți". Serbian is not here: its Latin alphabet
# needs letters (č, ć, đ) past Latin-1.
MLX_CYRILLIC_LANGUAGES = frozenset(
    {'ru', 'uk', 'be', 'bg', 'mk', 'kk', 'tg', 'mn', 'tt', 'ba'})
# Speech the gate heard and no word covers, for at least this long, was skipped
# by the decoder rather than left silent by the speaker: greedy decoding can jump
# a timestamp past what it did not manage to read. Three passages of one Mac
# interview went that way, 7 to 19 seconds each, and whisperx — which decodes
# every stretch of speech on its own — has all three. Measured against the
# Windows transcript of the same recording, the energy gate found 10 uncovered
# stretches of 4s or more, all of them intro music, jingles and closing credits,
# and 4 of 6s or more. So 6s catches every skip seen and costs about four short
# decodes a recording.
MLX_HOLE_MIN_SEC = 6.0
# The most of a hole one word is allowed to cover. The decoder that jumps a
# passage often stretches the word before the jump across it, so a word's own
# end time would hide the very skip this looks for. Two seconds is longer than
# any word anyone says.
MLX_HOLE_WORD_CAP_SEC = 2.0
# Audio kept either side of a hole when it is read on its own, so the words at
# its edges are heard whole. Only the words that fall inside the hole are kept.
MLX_HOLE_PAD_SEC = 1.0

# ── Dropping what Whisper wrote over non-speech (see pipeline.py) ──
#
# Whisper learned from YouTube subtitles, and those end on a credit over the
# closing silence. So where a recording holds a pause, noise or speech too
# garbled to read, the model sometimes writes the credit down. One whisperx
# interview came back with "Субтитры создавал DimaTorzok" in a five-minute gap
# no speaker was found in, and "Продолжение следует." twice — once stretched
# over 20s, once over the 7s the moderator then said had been inaudible. The
# VAD passes noise as speech, so neither engine is spared.
#
# Matched against the whole segment, after lowercasing, ё→е and stripping
# punctuation — never as a substring, so an answer that quotes one survives.
#
# Credits: nobody says these outside a subtitle file, so they always go.
HALLUCINATION_SIGNATURES = (
    r'субтитры (создавал|создал|сделал|делал|подготовил|подогнал)\b.*',
    r'(редактор|корректор) субтитров\b.*',
    r'субтитры (by|от)\b.*',
    r'.*\b(amara org|dimatorzok)\b.*',
)
# Phrases a person can actually say. These go only with a second sign that
# nobody said them: no speaker found under them, or words stretched over the
# silence — see HALLUCINATION_STRETCH_SEC_PER_WORD.
HALLUCINATION_PHRASES = frozenset({
    'продолжение следует',
    'спасибо за просмотр',
    'подписывайтесь на канал',
    'звук колокола',
    'thanks for watching',
})
# Speech runs ~0.3s a word. The two stretched credits above ran 10 and 3.6s;
# five times normal pace is far from both.
HALLUCINATION_STRETCH_SEC_PER_WORD = 1.5
# How far into the file to look for speech to identify the language on. The probe
# used to read the first 30s whatever was there, which on a phone call is the
# ringback — hence `p(en)=0.29` on a Russian interview, and a needless question to
# the user. Long enough to clear an intro; the whole window is decoded once.
MLX_LANG_PROBE_SEARCH_SEC = 300.0

SPEAKER_LABELS = [
    'Moderator',
    'Participant 1',
    'Participant 2',
    'Participant 3',
    'Participant 4',
    'Participant 5',
]

# Min probability on the language probe to skip asking the user (WhisperX: top-1 only).
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
