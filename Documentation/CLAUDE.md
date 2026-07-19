# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is PINE

PINE (Private Interview & Notes Environment) is a fully local, offline Flask desktop web app for product researchers. All audio/transcript processing runs on the user's machine — no data leaves the PC. It uses WhisperX for speech-to-text and pyannote for speaker diarization.

## Running the app

```bash
# From the repo root, activate the venv first:
source .venv/bin/activate          # macOS/Linux
.\.venv\Scripts\activate           # Windows

# Then start the server:
python backend/run.py
# Opens at http://127.0.0.1:5000
```

On Windows, `WIN_Install.bat` does both steps. On macOS/Linux, double-click `MAC_Install.command` (or run `./MAC_Install.command`). Both scripts live in the repo root, create `.venv` and install dependencies on first run, then start the server on subsequent launches.

## Running tests

Tests live in `backend/tests/`. Run from the repo root with the venv active:

```bash
# All tests
cd backend && pytest

# Single test file
cd backend && pytest tests/test_api_projects.py

# Single test
cd backend && pytest tests/test_api_projects.py::test_create_project

# With coverage
cd backend && pytest --cov=app
```

The test suite uses an in-memory SQLite DB and temp directories — it never touches real data. Set `PINE_TESTING=1` to signal test mode to the app.

## Architecture

### Stack
- **Backend:** Flask 3 + Flask-SQLAlchemy + Flask-SocketIO + Flask-CORS
- **Templates:** Server-rendered Jinja2 HTML (no build step, no JS framework)
- **DB:** SQLite (`backend/data/pine.db`) via SQLAlchemy
- **Real-time:** SocketIO for transcription progress events

### Key directories
```
backend/
  app/
    api/          # Flask blueprints: onboarding_bp, projects_bp, settings_bp
    models/       # SQLAlchemy models: Project, Recording, Segment, Setting, MLModel
    services/     # Business logic: transcription, export, model_manager, annotations, pii, backup
  templates/      # Jinja2: main.html, recording.html, tags.html, settings.html, onboarding.html
  data/           # pine.db (created at runtime)
  tests/          # pytest suite with conftest.py fixtures
models/           # HuggingFace model files (configurable path, gitignored)
projects/         # Per-project folders (configurable path)
```

### Data model (hybrid)
- **SQLite** stores metadata: projects, recordings (status, paths), segments (screener groups), settings (key-value), ML model registry.
- **Filesystem JSON** stores transcripts (`<name>_transcript.json`), annotations (`<name>_annotations.json`), and attachments (`attachments.json` + `attachments/` dir) inside `projects/<folder_name>/`. This keeps all project data portable for transfer packages.

### Transcription pipeline
- `TranscriptionService` is a singleton in `services/transcription.py` that lazy-loads WhisperX + pyannote.
- A single background worker thread processes one recording at a time (GPU-bound). Worker starts in `create_app()`.
- On startup, `requeue_interrupted()` reschedules recordings stuck in `transcribing` state from a previous crash.
- Progress emitted as SocketIO `transcription_progress` events with `{ recording_id, status, progress_pct, message }`.
- Files >30 min are chunked (30-min chunks, 30s overlap).

### Model management
- `MODEL_REGISTRY` in `services/model_manager.py` defines all available models. Required: whisperx-large-v3, pyannote-diarization, pyannote-segmentation. Optional (onboarding only): gliner-pii.
- Heavy ML deps (torch, whisperx, pyannote, gliner) are **not** in `requirements.txt` — they are installed dynamically by `model_manager.py` at download time.
- Models are downloaded from HuggingFace Hub during onboarding; pyannote requires a HF token (stored in SQLite).

### Dependency files
- **`backend/requirements.txt`** — minimal base deps (Flask, huggingface-hub, pytest, etc.). Used by `MAC_Install.command` / `WIN_Install.bat` on first run. Does **not** include ML packages.
- **`backend/requirements-lock.txt`** — full pinned `pip freeze` snapshot after a complete install (base + all ML packages). Not used by launch scripts; kept for reproducibility and auditing.
- To regenerate the lock file: `pip freeze > backend/requirements-lock.txt` (run with the venv active after onboarding is complete).

### Schema migrations
- No Alembic. `_migrate_db()` in `app/__init__.py` runs `ALTER TABLE ADD COLUMN` migrations on startup. Add new migrations there.

### Export
- `services/export_service.py` generates Markdown or ODT (via `odfpy`).
- Options: `include_comments`, `include_tags`, `remove_pii`.

## Known issues / active work items

See `TODO.md` for the full list. Key remaining gaps:
- **No download cancellation** — can't stop model download once started.
- **No cross-transcript search** — can't search for a keyword across all recordings in a project.
- **No formal DB migrations** — lightweight `ALTER TABLE ADD COLUMN` works now but won't scale.

## API overview

Base URL: `http://127.0.0.1:5000`

- `GET/POST /api/onboarding/*` — setup flow, model downloads, device detection
- `GET/POST/PATCH/DELETE /api/projects/<id>` — project CRUD + archive/unarchive/transfer
- `POST /api/projects/<id>/recordings` — file upload (multipart); transcription queues automatically
- `POST /api/projects/<id>/recordings/link` — link external file without copying
- `PATCH /api/projects/<id>/recordings/<rid>` — update recording metadata (segment, notes, name)
- `DELETE /api/projects/<id>/recordings/<rid>/transcription` — cancel in-progress transcription
- `GET/PATCH /api/projects/<id>/recordings/<rid>/annotations` — tag spans, comments, speaker labels
- `GET/PATCH /api/projects/<id>/tags` — project-level tag definitions
- `GET/POST/PATCH/DELETE /api/projects/<id>/segments` — research participant segments
- `GET/POST/PATCH/DELETE /api/projects/<id>/attachments` — project file attachments
- `GET/POST /api/projects/single-transcriptions` — standalone recordings without a project
- `GET/POST /api/projects/<id>/recordings/<rid>/export` — export single recording
- `GET/POST /api/projects/<id>/export` — export multiple recordings from a project
- `GET/POST/DELETE /api/backups` — backup/restore with manifest, selective project restore
- `GET/PATCH /api/settings` — font size, theme, export defaults
- `POST /api/quit` — graceful shutdown

See `API.md` for full endpoint reference and data shapes.
