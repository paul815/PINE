# PINE Architecture

## Overview

PINE is a Flask-based desktop web app with a SQLite + filesystem hybrid data model. All processing runs locally; no external API calls after model download.

---

## Directory Structure

```
PINE/
├── README.md            # Project overview, setup, usage
├── Documentation/       # API, architecture, design, CLAUDE guide, TODO
├── WIN_Install.bat      # Windows setup & launch script
├── MAC_Install.command  # macOS/Linux setup & launch script
├── backend/
│   ├── app/
│   │   ├── api/           # Blueprints: onboarding, projects, settings, backup
│   │   ├── models/        # SQLAlchemy: Project, Recording, Segment, Setting, MLModel
│   │   ├── services/      # transcription, export, model_manager, annotations, pii, backup
│   │   ├── config.py
│   │   ├── extensions.py  # db, socketio
│   │   └── __init__.py    # create_app, routes
│   ├── templates/         # Jinja2 HTML (main, recording, settings, tags, onboarding)
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
| `recording` | project_id, original_name, stored_name, transcription_status, transcript_path, duration_seconds, segment_id, participant_notes, is_linked |
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
- **Service:** `TranscriptionService` singleton, lazy-loads WhisperX + pyannote
- **Device:** Auto-detects CUDA; falls back to CPU with int8
- **Progress:** SocketIO emits `transcription_progress` events
- **Chunking:** Files >30 min use 30-min chunks with 30s overlap
- **Recovery:** `requeue_interrupted()` on startup for stuck `transcribing` recordings

### Model Management

- **Registry:** `MODEL_REGISTRY` in `model_manager.py` defines available models
- **Required:** whisperx-large-v3, pyannote-diarization, pyannote-segmentation
- **Optional:** gliner-pii (PII removal)
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
| Project-per-folder | Easy backup, transfer, and manual inspection |
| SocketIO for progress | Real-time updates without polling |
| `static_ffmpeg` | Bundles FFmpeg; no user install required |

---

## Security Notes

- **CORS:** `*` (local app; consider restricting to `127.0.0.1` for multi-user)
- **HF token:** Stored unencrypted in SQLite
- **Upload:** `secure_filename`, extension whitelist, max 4 GB
- **No auth:** Single-user local app; no login
