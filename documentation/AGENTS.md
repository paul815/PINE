# AGENTS.md

Last updated: 2026-08-20
Scope: Repository-wide guidance for Claude Code, Codex, and Cursor.

## 1) Mission

PINE is a local-first research tool for interview transcription and analysis.
The top engineering goal is data integrity for user research assets:

- Transcripts (`*_transcript.json`)
- Annotations (`*_annotations.json`, including tags/comments/speaker labels)
- Project files (audio/video/uploads and exported files)

When speed conflicts with safety, choose safety.

## 2) Applies To

This policy applies to any coding agent working in this repository.
Examples include Claude Code, Codex, Cursor, and future agent tools.

All agents must follow this file even if their platform defaults differ.

## 3) Priority Rules (highest -> lowest)

1. System/tooling safety constraints
2. This `documentation/AGENTS.md`
3. User request in the current task
4. Agent preferences/defaults

If rules conflict, the agent must state which rule won and why.

## 4) Project Context (Do Not Assume Generic Web App)

- Backend: Flask 3, Flask-SQLAlchemy, Flask-SocketIO, Flask-CORS
- Data model: SQLite + filesystem JSON hybrid
- Database path: `backend/data/pine.db`
- User project storage: `projects/` (configurable at runtime)
- Models storage: `models/` (configurable at runtime)
- Models: SQLAlchemy — Project, Recording, RecordingTrack, Segment, Setting, MLModel
- Templates: server-rendered Jinja2 under `backend/templates/`
- Tests: `pytest` in `backend/tests/` (558 tests)
- ML runs in a **separate process**: `backend/ml_worker/`, driven by `app/services/transcription/worker_client.py`

Important architecture: metadata is in SQLite, while transcript and annotation source-of-truth files live in per-project folders. Breaking file consistency is a production data-loss event.

Process boundary: `backend/app/` must never import `torch`, `whisperx`, `pyannote` or `mlx`. Those belong to `backend/ml_worker/` alone, so that a CUDA crash or an OOM kills the worker instead of the backend. An import that crosses this line looks harmless and undoes the split.

## 5) Data Safety Requirements (Critical)

Treat every change that can affect transcript/tag/file persistence as high risk.

Never do these without explicit user request:

- Delete/overwrite project directories or recording files
- Mass rewrite files under `projects/`
- Run reset scripts (`backend/reset_win.bat`, `backend/reset.command`)
- Execute destructive git/file operations

For risky operations, require all of:

1. Explicit user confirmation
2. Minimal scope
3. A rollback path (backup/snapshot or reversible write strategy)

When editing code that writes transcript/tag/annotation files:

- Prefer atomic write patterns (write temp file, then replace)
- Preserve unknown fields when reading/updating JSON
- Keep backward compatibility for existing JSON schema
- Fail closed on corruption (do not silently truncate or drop user content)

## 6) Files And Areas Requiring Extra Care

High-risk modules (read carefully before edits):

- `backend/app/api/projects.py`
- `backend/app/services/annotations.py`
- `backend/app/services/export_service.py`
- `backend/app/services/backup_service.py`
- `backend/app/services/file_utils.py`
- `backend/app/services/transcript_edit.py`
- `backend/app/services/multitrack_ingest.py` (registers files by absolute path — never copies, never moves user media)
- `backend/app/models/` (schema implications)

Data-bearing runtime directories (do not modify casually):

- `projects/`
- `backend/data/`
- `backups/`

## 7) Change Strategy

- Make the smallest change that solves the user request.
- Do not refactor unrelated code in the same PR/task.
- Keep API contracts stable unless user asked for breaking changes.
- Preserve existing behavior for export formatting and annotation semantics unless explicitly requested.
- If a migration is required, include forward-safe behavior and explicit fallback handling.

## 8) Testing And Validation

Run tests from `backend/` with active venv.

Standard commands:

```bash
cd backend && pytest
cd backend && pytest tests/test_api_projects.py
cd backend && pytest tests/test_annotations.py tests/test_export_service.py
cd backend && pytest --cov=app
```

Minimum expectation per change:

- Data-layer/API changes: run impacted test modules
- Annotation/export/project-file logic: run targeted tests plus one integration-style API test file
- If anything could affect data durability, prioritize durability/annotation/export tests

If tests cannot be run, report:

- Exact reason
- What was not validated
- Concrete manual checks required

## 9) Communication Contract

Use concise progress updates while working.
Final response must include:

1. What changed
2. Files touched
3. Validation run and results
4. Data safety impact statement (why transcripts/tags/files remain safe)
5. Any residual risk

If uncertainty can cause data loss, stop and ask before continuing.

## 10) Git And Workspace Rules

- Do not commit/push unless user asks.
- Do not rewrite history unless user asks.
- Do not revert unrelated local changes.
- Assume worktree may be dirty; isolate your edits.

Forbidden without explicit user request:

- `git reset --hard`
- `git checkout -- <path>`
- Recursive deletes affecting user data paths

## 11) Security And Privacy

- Never expose tokens/secrets in logs or responses.
- Never send local user transcript or project content to external services.
- Keep local-first privacy model intact.
- Network behavior changes require explicit mention in final report.

## 12) Definition Of Done

A task is done only when:

- Requested behavior is implemented
- Impacted tests/checks are run or explicitly documented as not run
- No silent data-destructive path was introduced
- Final report clearly states validation and data safety outcome
