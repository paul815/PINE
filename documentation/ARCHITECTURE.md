# PINE Architecture

## Overview

PINE is a Flask-based desktop web app with a SQLite + filesystem hybrid data model. All processing runs locally; no external API calls after model download.

---

## Directory Structure

```
PINE/
├── README.md            # Project overview, setup, usage
├── documentation/       # API, architecture, design, CLAUDE guide, TODO
├── WIN_Install.bat      # Windows setup & launch script
├── MAC_Install.command  # macOS/Linux setup & launch script
├── backend/
│   ├── app/               # The web process — no ML imports live here
│   │   ├── api/           # Blueprints: onboarding, settings, utils, backup, and
│   │   │                  # projects/ — one module per domain (crud, recordings,
│   │   │                  # segments, tags, export, attachments) on one blueprint
│   │   ├── models/        # SQLAlchemy: Project, Recording, RecordingTrack, Segment, Setting, MLModel
│   │   ├── ports.py       # the two process ports, and the CORS origin list
│   │   ├── services/      # transcription/ (job_runner, worker_client), export, model_manager,
│   │   │                  # launcher_layout, pip_installer, annotations, multitrack_ingest,
│   │   │                  # pii, backup, system_check, ffmpeg_setup, transcript_format
│   │   ├── static/        # css/ (tokens, button-system, fonts), fonts/, js/, icons/, sounds/
│   │   ├── config.py
│   │   ├── extensions.py  # db, socketio, ALLOWED_ORIGINS
│   │   └── __init__.py    # create_app, routes
│   ├── ml_worker/         # The ML process — torch/whisperx/pyannote live only here
│   │   ├── engines/       # whisperx_engine (CUDA/CPU), mlx_engine (Apple Silicon)
│   │   ├── pipeline.py    # stage orchestration
│   │   ├── diarize.py     # pyannote
│   │   ├── tracks.py      # per-speaker track handling
│   │   ├── progress.py    # one 0–100 scale across all stages
│   │   └── protocol.py    # JSON messages over the pipe
│   ├── templates/         # Jinja2 HTML (main, recording, settings, tags, manage_tags, onboarding)
│   ├── tests/             # pytest suite
│   ├── tools/             # dev helpers (dev_reset, design/contrast audits)
│   ├── data/              # pine.db (SQLite), created at runtime
│   ├── run.py             # Entry point
│   ├── supervisor.py      # Process manager (backend lifecycle, browser lease, auto-shutdown)
│   ├── requirements.txt
│   ├── reset.command      # macOS/Linux data reset (run from repo root context)
│   └── reset_win.bat      # Windows data reset
├── models/                # HuggingFace models (configurable path)
└── projects/              # Per-project folders (configurable path)
```

Install/launch scripts live in the repo root. `WIN_Install.bat` and `MAC_Install.command` create the venv and install dependencies on first run, then start the server on subsequent launches.

---

## Data Model

### SQLite (pine.db)

| Table | Purpose |
|-------|---------|
| `project` | Name, folder_name, description, objective, research_questions, hypotheses, summary, is_archived |
| `recording` | project_id, original_name, stored_name, transcription_status, transcript_path, duration_seconds, segment_id, participant_notes, is_linked, source_kind |
| `recording_track` | recording_id, track_index, source_path, speaker_name, channel_index, duration_seconds — one row per speaker track of a multi-track recording |
| `segment` | project_id, name, description, screener_questions, target_count — research participant segments for screening |
| `setting` | Key-value store (paths, onboarding_complete, export defaults, etc.) |
| `ml_model` | Model registry (id, repo_id, status, size_bytes) |

### Filesystem (per project)

```
projects/<folder_name>/
├── README.md                    # Auto-generated project summary
├── project_tags.json            # Tag definitions (id, name, color)
├── attachments.json             # Attachment metadata (id, display_name, stored_name)
├── attachments/                 # Uploaded project files (PDFs, docs, etc.)
├── <recording>.mp3              # Media file
├── <recording>_transcript.json  # WhisperX output (segments, speakers)
└── <recording>_annotations.json # tag_spans, comments, speaker_labels
```

**Design decision:** Annotations live in JSON files, not SQLite, so they stay with the project folder and are easy to include in transfer packages.

---

## Key Components

### Transcription Pipeline

- **Queue:** Single-threaded worker processes one recording at a time (GPU-bound)
- **Two processes:** `services/transcription/job_runner.py` owns the queue inside
  Flask and talks to a separate `ml_worker` process through
  `worker_client.py` (JSON messages over a pipe, `ml_worker/protocol.py`). Torch,
  WhisperX and pyannote are imported **only** in that child process, so a CUDA
  crash or an OOM kills the worker, not the backend
- **Engines:** `ml_worker/engines/` — `whisperx_engine` on CUDA/CPU,
  `mlx_engine` on Apple Silicon, behind a common `base.py` interface
- **Device:** Auto-detects CUDA; falls back to CPU with int8
- **Progress:** SocketIO emits `transcription:status` — `{ recording_id, status, stage, message, percent? }`
- **Chunking:** ASR splits files ≥30 min into 30-min chunks with 30s overlap
  (`CHUNK_*` in `ml_worker/constants.py`). Diarization runs on the whole file:
  its chunking path still exists but its threshold sits at 4 hours
  (`DIARIZE_CHUNK_THRESHOLD_SEC`), because matching speakers across chunk seams
  flipped labels. Diarization overlaps the ASR pass rather than following it
- **Recovery:** `requeue_interrupted()` on startup for stuck `transcribing` recordings
- **ETA:** `ml_worker/progress.py` folds every stage into one 0–100 scale. The
  shipped cost model in `constants.py` is only the first guess — each finished
  job reports how far off it was, and the figure is stored per model and mode
  under `progress_scale:<model>:<single|multi>` in Settings for the next job to
  start from (`_learn_progress_scale` in `services/transcription/job_runner.py`)

### Per-speaker tracks (multi-track recordings)

A recording whose speakers each have their own track skips diarization entirely
— pyannote is not even loaded. Two sources qualify: a Zoom meeting folder with
an `Audio Record` subfolder (one file per participant), and a single file whose
channels are the speakers. Both are recognised by
`services/multitrack_ingest.py` and stored as `RecordingTrack` rows against a
recording with `source_kind = 'multitrack'`.

- **Nothing is mixed.** Each track is transcribed on its own; only the resulting
  segments are merged, so simultaneous speech survives instead of being
  arbitrated. A mixdown is built only when the folder has nothing playable, and
  it is used for the player alone.
- **Silence is cut first.** `ml_worker/tracks.py` finds the speech, splices it
  into a short file, and maps the returned timestamps back to the original
  timeline. Without this, N tracks would cost N full passes and Whisper would
  invent text over the long silences on a listener's track. VAD thresholds are
  tuned for interview back-channel ("угу", "да") — see the constants in
  `ml_worker/constants.py`.
- **Names come from the files.** Zoom puts the participant in the filename;
  `speaker_name_from_filename()` recovers it, so the transcript shows real names
  instead of "Participant 1". Renaming afterwards works as it always did.
- **Never copied.** Multi-track material is registered by absolute path — a Zoom
  meeting folder can be several gigabytes.

The single-file path is untouched: no tracks means the pyannote flow, unchanged.

### Model Management

- **Registry:** `MODEL_REGISTRY` in `model_manager.py` defines available models
- **Required:** pyannote-diarization, pyannote-segmentation, and the transcription
  model for the platform — whisperx-large-v3 (mlx-whisper-large-v3 on Mac)
- **Transcription model:** downloaded during onboarding, re-installable from
  Settings (`/api/settings/stt-model/install`) if that download never finished;
  it runs on the accelerator
- **Optional:** gliner-pii (PII removal) — installable and removable after
  onboarding from Settings (`/api/settings/pii-model/install` | `/remove`)
- **Download:** HuggingFace Hub; pip installs torch/whisperx/pyannote on first run

### Export

- **Markdown:** Plain text, LLM-ready, includes project context
- **ODT:** OpenDocument via `odfpy`
- **Options:** include_comments, include_tags, remove_pii
- **Scope:** Per-recording or per-project (multiple recordings in one export)

### Backup & Restore

- **Service:** `backup_service.py` creates ZIP archives with manifest, project data, and optionally audio
- **Restore:** Per-project selective restore with conflict strategies (skip/overwrite/rename)
- **Upload:** Users can upload previously exported backup ZIPs

### Supervisor

- **Script:** `backend/supervisor.py` manages the Flask backend lifecycle
- **Browser lease:** Auto-shutdown when no browser tabs are connected (heartbeat-based)
- **Restart:** Internal HTTP API for restart/shutdown signals

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Flask + server-rendered HTML | Simple, no build step; templates inject `project_id` etc. |
| SQLite + JSON hybrid | Metadata in DB; transcripts/annotations in files for portability |
| Single transcription worker | GPU memory limits; one model at a time |
| ML in a child process | A CUDA crash, an OOM or a library's global patches can't take the web process with them |
| Project-per-folder | Easy backup, transfer, and manual inspection |
| SocketIO for progress | Real-time updates without polling |
| `static_ffmpeg` | Bundles FFmpeg; no user install required |

---

## Security Notes

- **CORS:** restricted to `ALLOWED_ORIGINS` in `app/extensions.py` —
  `http://127.0.0.1:<port>` and `http://pine.localhost:<port>`, where the port
  comes from `PINE_BACKEND_PORT`. SocketIO uses the same list
- **HF token:** Stored unencrypted in SQLite
- **Upload:** `secure_filename`, extension whitelist, max 4 GB
- **No auth:** Single-user local app; no login
