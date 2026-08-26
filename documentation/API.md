# PINE API Reference

Base URL: `http://127.0.0.1:5000`

All JSON responses use `Content-Type: application/json`. Errors return `{ "error": "message" }` with appropriate HTTP status.

---

## Onboarding (`/api/onboarding`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | `completed`, `modules`, `models_path`, `projects_path` |
| GET | `/device` | Transcription device (cuda/cpu), GPU name, VRAM |
| GET | `/system-check` | Python, FFmpeg, torch, disk checks |
| GET | `/defaults` | Default paths, disk usage |
| POST | `/modules` | Set modules; body `{ "modules": ["pii"] }` |
| POST | `/storage` | Set paths; body `{ "models_path", "projects_path" }` |
| POST | `/hf-token` | Validate and store HF token |
| POST | `/download/start` | Start model download |
| GET | `/download/status` | Model download status per model |
| POST | `/stt-model` | Pick the transcription model to download; body `{ "stt_model_id" }`. Unknown ids fall back to the platform default |
| GET | `/browse-folder` | Browse the filesystem for a storage path |
| POST | `/handoff/prepare` | Hand the finished onboarding over to the main app |
| POST | `/play-install-sound` | Play the install-complete chime |

---

## Projects (`/api/projects`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `` | List active + archived projects |
| POST | `` | Create project; body `{ "name" }` |
| GET | `/<id>` | Get project with recordings |
| PATCH | `/<id>` | Update name, description, objective, research_questions, hypotheses, summary |
| DELETE | `/<id>` | Delete project and folder |
| POST | `/<id>/archive` | Archive project |
| POST | `/<id>/unarchive` | Unarchive project |
| GET | `/<id>/transfer` | Download project as ZIP (no audio) |

### Recordings

`num_speakers` is optional on every endpoint that accepts it. Without it,
diarization uses the `PINE_MIN_SPEAKERS`/`PINE_MAX_SPEAKERS` range, which
defaults to **2..4** — PINE's material is interviews with two to four
participants. A monologue or a group of five and up falls outside that
range and has to pass an explicit count (or run with different env values).

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/<id>/recordings` | Upload file (multipart `file`, optional `num_speakers` 1–10) |
| POST | `/<id>/recordings/link` | Link external file; body `{ "path": "/absolute/path", "num_speakers": 2 }` (count optional) |
| POST | `/<id>/recordings/multitrack` | Register material with one track per speaker; body `{ "folder": "/path/to/zoom/meeting" }` or `{ "path": "/file/with/one/channel/per/speaker" }`. Never copies — paths are stored as-is. Diarization is skipped for these. |
| GET | `/<id>/recordings/<rid>` | Recording + transcript + tags + annotations |
| PATCH | `/<id>/recordings/<rid>` | Update recording; body `{ "segment_id", "participant_notes", "original_name", "num_speakers" }` |
| DELETE | `/<id>/recordings/<rid>` | Delete recording and files |
| DELETE | `/<id>/recordings/<rid>/transcription` | Cancel in-progress transcription (409 if not transcribing) |
| GET | `/<id>/recordings/<rid>/media` | Stream media file |
| GET | `/<id>/recordings/<rid>/transcript` | Raw transcript JSON |
| POST | `/<id>/recordings/<rid>/transcript/replace` | Find & replace across the transcript text. Tag spans and comments live in a separate file and reference character offsets, so `services/transcript_edit.py` remaps every offset when a replacement changes a segment's length |
| GET/POST | `/<id>/recordings/<rid>/export` | Export single recording; body `{ "format": "markdown" \| "odt", "include_comments", "include_tags", "remove_pii" }` |
| GET/POST | `/<id>/export` | Export project (multiple recordings); body `{ "recording_ids", "format", "include_comments", "include_tags", "include_participant_details", "include_project_details", "include_prompt", "remove_pii", "separate_files" }`; with `separate_files: true` the response is a ZIP holding one document per recording |

### Tags & Annotations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/<id>/tags` | Project tags |
| PATCH | `/<id>/tags` | Update tags; body `{ "tags": [...] }` |
| GET | `/<id>/tags/quotes` | Tagged quotes grouped by recording (cached by transcript mtime — only changed files are re-read) |
| GET/PATCH | `/<id>/themes` | Tag themes: a two-level theme → tags hierarchy, stored per project |
| GET | `/<id>/recordings/<rid>/annotations` | Annotations (tag_spans, comments, speaker_labels) |
| PATCH | `/<id>/recordings/<rid>/annotations` | Update annotations |

### Segments

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/<id>/segments` | List segments for project |
| POST | `/<id>/segments` | Create segment; body `{ "name", "description?", "screener_questions?", "target_count?" }` |
| PATCH | `/<id>/segments/<sid>` | Update segment; body `{ "name?", "description?", "screener_questions?", "target_count?" }` |
| DELETE | `/<id>/segments/<sid>` | Delete segment |

### Attachments

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/<id>/attachments` | List attachments for project |
| POST | `/<id>/attachments` | Upload attachment (multipart `file`) |
| PATCH | `/<id>/attachments/<aid>` | Rename attachment; body `{ "display_name" }` |
| DELETE | `/<id>/attachments/<aid>` | Delete attachment |
| GET | `/<id>/attachments/<aid>/download` | Download attachment file |

### Single Transcriptions (`/api/projects/single-transcriptions`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/single-transcriptions` | List standalone recordings (no project) |
| POST | `/single-transcriptions` | Upload file for standalone transcription (multipart `file`) |
| DELETE | `/single-transcriptions/<rid>` | Delete standalone recording |

---

## Backups (`/api/backup`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `` | Create backup; body `{ "include_audio?" }` |
| GET | `` | List available backups |
| GET | `/<filename>/manifest` | Get backup manifest (project list) |
| POST | `/restore` | Restore from backup; body `{ "filename", "project_folders?", "restore_settings?", "conflict_strategy?" }` |
| POST | `/upload` | Upload backup ZIP (multipart `file`) |
| POST | `/open-folder` | Reveal the backup folder in Explorer/Finder |
| DELETE | `/<filename>` | Delete backup file |

---

## Settings (`/api/settings`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `` | All settings + `hf_token_masked`, `models`, `stt_models` |
| PATCH | `` | Update; body `{ "font_size", "theme", "export_default_*" }`. `stt_model_id` returns **409** when that model is not installed |
| POST | `/stt-model/install` | Download the transcription model; body `{ "model_id" }` |
| POST | `/pii-model/install` | Download the optional GLiNER PII model after onboarding |
| POST | `/pii-model/remove` | Delete it and free the disk space |
| POST | `/check-update` | Ask GitHub whether a newer PINE release exists |
| POST | `/reset` | Reset settings to defaults |
| GET | `/app-launch` | Both shortcut states in one call: `{ "prompt_dismissed", "start_menu": { "supported", "added", "label", "description" }, "desktop": {…} }`. `prompt_dismissed` drives the first-run offer in the sidebar; adding either shortcut sets it |
| GET/POST | `/start-menu`, `/start-menu/add`, `/start-menu/remove` | Windows Start-menu shortcut |
| GET/POST | `/desktop`, `/desktop/add`, `/desktop/remove` | Desktop shortcut |

---

## Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/quit` | Graceful server shutdown |
| POST | `/api/utils/pick-file` | Native file picker; returns `{ "path" }` |
| POST | `/api/utils/pick-files` | Native multi-file picker; returns `{ "paths" }` |
| POST | `/api/utils/pick-folder` | Native folder picker; returns `{ "path" }` |
| POST | `/api/utils/inspect-multitrack` | Report per-speaker tracks without importing; body `{ "folder" }` or `{ "path" }`, returns `{ "kind", "tracks": [{ "path", "speaker_name", "channel"? }], "media" }` |
| GET | `/api/utils/runtime-status` | Backend runtime state for the launcher |
| POST | `/api/utils/restart` | Restart the backend process |
| POST | `/api/internal/quit-backend` | Internal shutdown signal used by the supervisor — not for UI use |

---

## Data Shapes

### Project

```json
{
  "id": 1,
  "name": "Project Name",
  "folder_name": "Project_Name",
  "description": "",
  "objective": "",
  "research_questions": "",
  "hypotheses": "",
  "summary": "",
  "icon": "📄",
  "is_archived": false,
  "recordings": [...],
  "created_at": "...",
  "updated_at": "..."
}
```

### Recording

```json
{
  "id": 1,
  "project_id": 1,
  "original_name": "interview.mp3",
  "stored_name": "interview.mp3",
  "transcription_status": "transcribed",
  "transcript_path": "interview_transcript.json",
  "duration_seconds": 3600,
  "file_format": "mp3",
  "file_size_bytes": 45000000,
  "language": "en",
  "error_message": null,
  "segment_id": null,
  "participant_notes": "",
  "num_speakers": 2,
  "is_linked": false,
  "source_kind": "single",
  "track_count": 0,
  "created_at": "..."
}
```

`source_kind` is `"single"` or `"multitrack"`; `track_count` is 0 for single-file
recordings and the number of speaker tracks otherwise.

### Segment

```json
{
  "id": 1,
  "project_id": 1,
  "name": "Power Users",
  "description": "Daily active users with 6+ months tenure",
  "screener_questions": "How often do you use the product?",
  "target_count": 5,
  "assigned_count": 2,
  "created_at": "..."
}
```

### Attachment

```json
{
  "id": "a1b2c3d4",
  "display_name": "research_brief.pdf",
  "stored_name": "a1b2c3d4.pdf",
  "uploaded_at": "2025-04-12T10:30:00.000000+00:00"
}
```

### Transcript segment

```json
{
  "start": 0.5,
  "end": 3.2,
  "text": "Hello world",
  "speaker": "SPEAKER_00"
}
```

### Tag span

```json
{
  "segment_idx": 0,
  "tag_id": "pain",
  "start_char": 0,
  "end_char": 5
}
```

---

## SocketIO Events

All events are server → client. Names use a `namespace:event` form.

| Event | Payload |
|-------|---------|
| `transcription:status` | `{ "recording_id", "status", "stage", "message", "percent"? }` — plus any extra keys the emitting stage adds. `status` is the recording's state (`pending`, `transcribing`, `transcribed`, `failed`), `stage` the step within it (`queued`, `asr`, `diarize`, …). Queue-position pings reuse this event with `stage: "queued"` and a `#k of N` message |
| `download:model_start` / `download:progress` / `download:model_complete` | Per-model download of weights |
| `download:all_complete` / `download:error` | End of the whole download batch |
| `install:start` / `install:log` / `install:complete` / `install:error` | pip install of the ML stack during onboarding — `install:log` carries raw output lines |
| `backup:progress` / `backup:complete` / `backup:error` | Backup creation |
| `backup:restore_progress` / `backup:restore_complete` / `backup:restore_error` | Restore |
