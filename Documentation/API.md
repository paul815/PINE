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

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/<id>/recordings` | Upload file (multipart `file`) |
| POST | `/<id>/recordings/link` | Link external file; body `{ "path": "/absolute/path" }` |
| GET | `/<id>/recordings/<rid>` | Recording + transcript + tags + annotations |
| PATCH | `/<id>/recordings/<rid>` | Update recording; body `{ "segment_id", "participant_notes", "original_name" }` |
| DELETE | `/<id>/recordings/<rid>` | Delete recording and files |
| DELETE | `/<id>/recordings/<rid>/transcription` | Cancel in-progress transcription (409 if not transcribing) |
| GET | `/<id>/recordings/<rid>/media` | Stream media file |
| GET | `/<id>/recordings/<rid>/transcript` | Raw transcript JSON |
| GET/POST | `/<id>/recordings/<rid>/export` | Export single recording; body `{ "format": "markdown" \| "odt", "include_comments", "include_tags", "remove_pii" }` |
| GET/POST | `/<id>/export` | Export project (multiple recordings); body `{ "recording_ids", "format", "include_comments", "include_tags", "include_participant_details", "include_project_details", "include_prompt", "remove_pii" }` |

### Tags & Annotations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/<id>/tags` | Project tags |
| PATCH | `/<id>/tags` | Update tags; body `{ "tags": [...] }` |
| GET | `/<id>/tags/quotes` | Tagged quotes grouped by recording |
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

## Backups (`/api/backups`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `` | Create backup; body `{ "include_audio?" }` |
| GET | `` | List available backups |
| GET | `/<filename>/manifest` | Get backup manifest (project list) |
| POST | `/restore` | Restore from backup; body `{ "filename", "project_folders?", "restore_settings?", "conflict_strategy?" }` |
| POST | `/upload` | Upload backup ZIP (multipart `file`) |
| DELETE | `/<filename>` | Delete backup file |

---

## Settings (`/api/settings`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `` | All settings + `hf_token_masked`, `models` |
| PATCH | `` | Update; body `{ "font_size", "theme", "export_default_*" }` |

---

## Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/quit` | Graceful server shutdown |

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
  "segment_id": null,
  "participant_notes": "",
  "is_linked": false
}
```

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

| Event | Direction | Payload |
|-------|-----------|---------|
| `transcription_progress` | Server → Client | `{ "recording_id", "status", "progress_pct", "message" }` |
| `model_download_progress` | Server → Client | `{ "model_id", "status", "progress_pct" }` |
