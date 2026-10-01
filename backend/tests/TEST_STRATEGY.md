# PINE Test Strategy

## 1. What Should Be Tested and Why

### Critical Business Logic (Priority 1)

| Area | Rationale |
|------|------------|
| **Export service** | Core output feature; incorrect timestamps, missing speakers, or broken ODT corrupt user deliverables |
| **Annotations** | Tags, comments, speaker labels are user data; corrupt JSON or race conditions cause data loss |
| **Project folder naming** | `_safe_folder_name` affects filesystem; collisions or invalid chars break project creation |
| **Settings persistence** | User preferences must survive restarts; disallowed keys must be rejected |
| **File upload validation** | Wrong formats or paths can corrupt storage or expose security issues |

### Edge Cases (Priority 2)

| Area | Rationale |
|------|------------|
| **Time formatting** | `_fmt_time(0)`, negative, very large values; export must never crash |
| **Empty/corrupt annotations** | Missing file or bad JSON → return defaults, no crash |
| **Tag quotes** | Out-of-range segment_idx, missing tag_id, empty quote text |
| **ODT content** | Empty markdown, XML escaping, special chars |

### Error Scenarios (Priority 3)

| Area | Rationale |
|------|------------|
| **Export with missing project/recording** | Return `None, 'Not found'` not crash |
| **Export with missing transcript file** | Return `None, 'Transcript not found'` |
| **PII model unavailable** | Fall through without redaction, no crash |
| **Upload rejected formats** | 400 with clear error |
| **Non-existent project ID** | 404 on all project-scoped endpoints |

### Integration Points (Priority 4)

| Area | Rationale |
|------|------------|
| **Project CRUD → filesystem** | Create/rename/delete must sync folder and DB |
| **Annotations ↔ export** | Speaker labels, tag spans, comments appear correctly in output |
| **Settings API** | PATCH only updates ALLOWED_KEYS; others ignored |

---

## 2. Test Priorities

1. **Unit tests** — Pure functions, no DB: `_fmt_time`, `_md_to_odt_content`, `_safe_folder_name`, `redact_text`
2. **Service tests** — With temp dirs: `get_annotations`, `save_annotations`, `get_project_tags`, `export_recording_markdown`
3. **API integration tests** — Full Flask client: project CRUD, upload validation, annotations, export, settings
4. **Edge/error tests** — Corrupt JSON, missing files, boundary values

---

## 4. Test Cases Summary

| Module | Test Class | Cases |
|--------|------------|-------|
| **export_service** | TestFmtTime | zero, negative, none, under 1min, 1hr, large |
| **export_service** | TestMdToOdtContent | empty, h1, h2, paragraph, comment, XML escaping |
| **export_service** | TestExportRecordingMarkdown | not found, transcript missing, success, empty segments |
| **export_service** | TestExportRecordingOdt | delegates to markdown, valid ZIP |
| **annotations** | TestProjectTags | missing file, save/get, corrupt JSON |
| **annotations** | TestAnnotations | missing file, save/get, corrupt JSON, unicode |
| **projects** | TestSafeFolderName | simple, special chars, unicode, empty, truncate, collision |
| **projects** | TestAllowedExtensions | all 8 formats |
| **pii_service** | TestRedactText | empty entities, single, multiple, overlapping |
| **pii_service** | TestRedactSegments | empty, model unavailable |
| **API projects** | TestProjectsCRUD | list, create, special chars, get 404, update, archive, delete |
| **API projects** | TestUploadValidation | no file 400, .txt rejected, nonexistent project 404 |
| **API projects** | TestAnnotationsAPI | roundtrip |
| **API projects** | TestExportAPI | 404, success |
| **API settings** | TestSettingsAPI | get, update allowed, disallowed ignored |

---

## 5. Out of Scope (for this suite)

- **Transcription pipeline** — Requires WhisperX/pyannote; mock in separate suite
- **PII detection** — GLiNER model; test `redact_text` with mocked entities only
- **Model download** — Network; mock or skip
- **SocketIO** — Real-time; manual testing
---

## 6. UI Accessibility Audit

- Run `npm run audit:contrast` from `backend/` to audit rendered WCAG AA text contrast on the live app at `http://127.0.0.1:5000`
- The audit checks `/`, `/settings`, and the first available project `tags` and `recording` pages in both light and dark themes
- The command fails with a non-zero exit code when axe-core reports contrast violations, so it can be reused as a regression check
