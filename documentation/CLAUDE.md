# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is PINE

PINE (Private Interview & Notes Environment) is a fully local, offline Flask desktop web app for product researchers. All audio/transcript processing runs on the user's machine — no data leaves the PC. Speech-to-text is WhisperX; speaker diarization is pyannote.

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
  app/            # Web process. Never import torch/whisperx/pyannote here
    api/          # onboarding_bp, settings_bp, utils_bp, backup_bp, and projects/ —
                  # a package (common, crud, recordings, segments, tags, export,
                  # attachments) whose modules all register on one projects_bp
    models/       # SQLAlchemy: Project, Recording, RecordingTrack, Segment, Setting, MLModel
    ports.py      # backend/supervisor ports and the CORS origin list, in one place
    services/     # transcription/ (job_runner + worker_client), export, model_manager,
                  # launcher_layout, pip_installer, annotations, multitrack_ingest, pii,
                  # backup, system_check, ffmpeg_setup, transcript_format
  ml_worker/      # ML process. torch/whisperx/pyannote/mlx live only here
    engines/      # whisperx_engine (CUDA/CPU), mlx_engine (Apple Silicon)
  templates/      # Jinja2: main.html, recording.html, tags.html, manage_tags.html,
                  # settings.html, onboarding.html
  data/           # pine.db (created at runtime)
  tests/          # pytest suite with conftest.py fixtures
  tools/          # dev helpers: dev_reset.*, design/contrast audits
models/           # HuggingFace model files (configurable path, gitignored)
projects/         # Per-project folders (configurable path)
```

### Data model (hybrid)
- **SQLite** stores metadata: projects, recordings (status, paths), segments (screener groups), settings (key-value), ML model registry.
- **Filesystem JSON** stores transcripts (`<name>_transcript.json`), annotations (`<name>_annotations.json`), and attachments (`attachments.json` + `attachments/` dir) inside `projects/<folder_name>/`. This keeps all project data portable for transfer packages.

### Transcription pipeline
- **Two processes.** `services/transcription/job_runner.py` owns the queue inside Flask; `worker_client.py` spawns and talks to the `ml_worker` process over a pipe (JSON messages, see `ml_worker/protocol.py`). **Never import torch/whisperx/pyannote from `app/`** — that's the whole point of the split: an OOM or CUDA crash must not take the backend down.
- A single background worker thread processes one recording at a time (GPU-bound). Worker starts in `create_app()`.
- Engines live in `ml_worker/engines/`: `whisperx_engine` (CUDA/CPU), `mlx_engine` (Apple Silicon), behind `base.py`.
- On startup, `requeue_interrupted()` reschedules recordings stuck in `transcribing` state from a previous crash.
- Progress emitted as SocketIO `transcription:status` events with `{ recording_id, status, stage, message, percent? }` (see API.md for the full event list — every event name uses the `namespace:event` form). `ml_worker/progress.py` folds every stage into one 0–100 scale and learns per-machine throughput — the correction is stored per model and mode as `progress_scale:<model>:<single|multi>` in Settings.
- ASR chunks files ≥30 min (30-min chunks, 30s overlap). Diarization runs whole-file and overlaps the ASR pass; its chunking path is parked behind a 4-hour threshold because chunked pyannote flipped speakers across seams. All thresholds are in `ml_worker/constants.py`.
- Multi-track recordings (Zoom folders, multi-channel files) skip diarization entirely — see `services/multitrack_ingest.py` and `ml_worker/tracks.py`, and the ARCHITECTURE.md section on per-speaker tracks.

### Model management
- `MODEL_REGISTRY` in `services/model_manager.py` defines all available models. Required: pyannote-diarization, pyannote-segmentation, and one transcription model. Optional: gliner-pii — offered during onboarding, and installable or removable afterwards from Settings.
- **One transcription model per platform**, offered in onboarding and in Settings: `whisperx-large-v3` (`mlx-whisper-large-v3` on Mac), which runs on the accelerator. `supported_stt_models()` lists what this platform may offer and `normalize_stt_model_id()` rejects everything else.
- Heavy ML deps (torch, whisperx, pyannote, gliner) are **not** in `requirements.txt` — they are installed dynamically by `model_manager.py` at download time.
- Models are downloaded from HuggingFace Hub during onboarding; pyannote requires a HF token (stored in SQLite).

### Dependency files
- **`backend/requirements.txt`** — minimal base deps (Flask, huggingface-hub, pytest, etc.). Used by `MAC_Install.command` / `WIN_Install.bat` on first run. Does **not** include ML packages.
- **`backend/requirements-lock.txt`** — full pinned `pip freeze` snapshot after a complete install (base + all ML packages). Not used by launch scripts; kept for reproducibility and auditing.
- To regenerate the lock file: `pip freeze > backend/requirements-lock.txt` (run with the venv active after onboarding is complete).

### Schema migrations
- No Alembic. `_migrate_db()` in `app/__init__.py` runs `ALTER TABLE ADD COLUMN` migrations on startup. Add new migrations there.

### Frontend
- No build step, no framework: six standalone Jinja2 templates with inline `<style>` and `<script>`. None uses `{% extends %}`.
- Shared CSS lives in `app/static/css/`: `tokens.css` (palette, radius scale, z-index ladder), `button-system.css`, `fonts.css`, `save-status.css`. All six templates link them.
- A colour or font lives in exactly one place now — the private per-template copies were removed. What is left in a template's `:root` is page-only (layout metrics, player colours).
- Never write a raw `z-index` — use the `--z-*` ladder. Scope theme rules to `:root[data-theme="dark"]`, never a bare `[data-theme="dark"]`: the latter also matches the theme-picker buttons, which carry that attribute.

### Export
- `services/export_service.py` generates Markdown or ODT (via `odfpy`).
- Options: `include_comments`, `include_tags`, `remove_pii`.

## Known issues / active work items

See `TODO.md` for the full list. Key remaining gaps:
- **No download cancellation** — can't stop model download once started.
- **No cross-transcript search** — can't search for a keyword across all recordings in a project.
- **No formal DB migrations** — lightweight `ALTER TABLE ADD COLUMN` works now but won't scale.
- **No `schema_version` in transcript JSON** — the on-disk format carries no version, so changing it later has no migration path.

## API overview

Base URL: `http://127.0.0.1:5000`

- `GET/POST /api/onboarding/*` — setup flow, model downloads, device detection
- `GET/POST/PATCH/DELETE /api/projects/<id>` — project CRUD + archive/unarchive/transfer
- `POST /api/projects/<id>/recordings` — file upload (multipart); transcription queues automatically
- `POST /api/projects/<id>/recordings/link` — link external file without copying
- `POST /api/projects/<id>/recordings/multitrack` — register a Zoom folder or multi-channel file as per-speaker tracks (never copied)
- `GET /api/projects/<id>/recordings/<rid>/transcript` — transcript JSON; `POST .../transcript/replace` swaps it wholesale
- `GET /api/projects/<id>/recordings/<rid>/media` — audio stream for the player
- `GET /api/projects/<id>/tags/quotes` — every tagged quote in the project (mtime-cached)
- `GET/PATCH /api/projects/<id>/themes` — project themes
- `PATCH /api/projects/<id>/recordings/<rid>` — update recording metadata (segment, notes, name)
- `DELETE /api/projects/<id>/recordings/<rid>/transcription` — cancel in-progress transcription
- `GET/PATCH /api/projects/<id>/recordings/<rid>/annotations` — tag spans, comments, speaker labels
- `GET/PATCH /api/projects/<id>/tags` — project-level tag definitions
- `GET/POST/PATCH/DELETE /api/projects/<id>/segments` — research participant segments
- `GET/POST/PATCH/DELETE /api/projects/<id>/attachments` — project file attachments
- `GET/POST /api/projects/single-transcriptions` — standalone recordings without a project
- `GET/POST /api/projects/<id>/recordings/<rid>/export` — export single recording
- `GET/POST /api/projects/<id>/export` — export multiple recordings from a project
- `GET/POST/DELETE /api/backup` — backup/restore with manifest, selective project restore
- `GET/PATCH /api/settings` — font size, theme, export defaults; `POST /api/settings/stt-model/install` re-downloads the transcription model (switching to one that is not installed returns 409); `POST /api/settings/pii-model/install|remove` manages the optional GLiNER model
- `GET/POST /api/utils/*` — native file/folder pickers, desktop and Start-menu shortcuts, `inspect-multitrack`, update check, runtime status
- `POST /api/quit` — graceful shutdown

See `API.md` for full endpoint reference and data shapes.
