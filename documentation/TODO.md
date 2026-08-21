# PINE — Testing, Architecture Review & Roadmap

*Status checked against the code on 2026-08-21: 629 tests pass, `ruff check`
clean. What remains is listed in section 6; the release blockers there are all
manual-verification items, not code.*

---

## 1. Architecture Review

### What's solid

| Area | Verdict |
|------|---------|
| Flask factory pattern + blueprints | Great, clean separation |
| SQLite + filesystem JSON hybrid | Smart for a local desktop app |
| SocketIO for real-time transcription progress | Excellent UX |
| GPU/CPU auto-detection with fallback | Well-implemented, graceful degradation |
| ML in a separate worker process (`ml_worker/`) | A CUDA crash or OOM no longer takes the backend down |
| Background worker queue (one-at-a-time) | Correct for GPU-bound work |
| Crash recovery (requeue stuck transcriptions) | Nice touch |
| File upload security (secure_filename, extension whitelist) | Good |

### What needs fixing

#### Bugs

- [x] **Tag selection applies to wrong text span** — ~~character offset calculation in `getSelectionSegmentInfo()` needs a clean rewrite~~ *(fixed)*

#### Critical

- [x] **No tests at all** — ~~a single regression could silently corrupt transcripts or lose annotations~~ *(558 tests in `backend/tests/`, all green as of 2026-08-20; Tier 1–3 complete)*
- [x] **No cross-platform setup scripts** — ~~only `.bat` and `.ps1`~~ *(MAC_Install.command does setup on first run for Mac/Linux, then becomes `Launch Pine.command`)*
- [x] **Clarify the two dependency files** — ~~`requirements.txt` vs `requirements-lock.txt`: roles, update workflow, usage in setup~~ *(comment headers added to both files; Dependency files section added to CLAUDE.md)*
- [x] **Implicit ML dependencies** — ~~`whisperx`, `torch`, `pyannote`, `speechbrain`, `gliner` installed dynamically, not declared. If dynamic install fails, users get cryptic errors~~ *(`check_ml_deps()` in model_manager.py; called at start of `transcribe()` — missing deps now raise a clear RuntimeError shown in the UI)*
- [x] **No transcription timeout/watchdog** — ~~if WhisperX hangs (GPU crash, OOM), the worker blocks forever~~ *(watchdog timer kills stuck worker after configurable timeout)*
- [x] **Annotation file race condition** — ~~plain JSON files. Two concurrent writes~~ *(per-file locking + atomic writes)*

#### Important

- [ ] **No formal DB migrations** — lightweight `ALTER TABLE ADD COLUMN` in `_migrate_db()` works now but won't scale. Switch to Alembic before schema gets complex
- [x] **No `schema_version` in the transcript JSON** — ~~the on-disk format carries no version or engine metadata~~ *(`services/transcript_format.py`: new transcripts carry `schema_version`, `engine`, `model`, `created_at`; a file without the key reads as version 1, so nothing had to be rewritten)*
- [ ] **HuggingFace token stored unencrypted in SQLite** — low risk for local app, but worth noting
- [ ] **A test fails about once in ten full runs, and only on the first run after files change on disk.** Seen twice on 2026-08-21; six and then five consecutive re-runs were clean, so it has never been caught with a traceback. One instance was `TestConcurrentUpload`, which shared a single `FlaskClient` across threads — that one is fixed. If it reappears, run `pytest -p no:randomly --tb=long` in a loop and keep the output: the suspicion is another timing-sensitive test, not the app, but that is unproven
- [x] **Design tokens are half-extracted** — ~~five templates redeclared the palette in their own `:root`, overriding `tokens.css`~~ *(168 palette declarations and 117 `@font-face` blocks removed; bare `[data-theme="dark"]` selectors scoped to `:root`. Verified in a browser: 53/53 tokens resolve, 22 faces load)*
- [ ] **Add new setting - turning off click to text functionality** — clicking on the text in the transcription shouldn't chabge the current recording position
- [x] **`python-docx` in requirements but never imported** — ~~dead dependency. Export generates ODT, not DOCX~~ *(removed from both requirements files; ODT is the documented format everywhere)*
- [x] **CORS is `*`** — ~~any website on user's PC can call the API~~ *(`CORS(app, origins=ALLOWED_ORIGINS)` in `app/__init__.py`; SocketIO uses the same list in `app/extensions.py`)*
- [x] **No disk space check before model download** — ~~downloading 5GB with 2GB free = corrupted partial models~~ *(`shutil.disk_usage` checks in `api/onboarding.py` and `services/system_check.py`)*
- [x] **Tag quotes endpoint reads ALL transcripts every time** — ~~fine for 5 recordings, slow for 50+~~ *(mtime-based cache; only changed files are re-read)*

### Cross-platform compatibility

| Config | Status | Issues |
|--------|--------|--------|
| Windows + NVIDIA GPU | Works | Primary target |
| Windows + no GPU | Works | Falls back to CPU/int8, 10x slower |
| Windows + AMD GPU | Partial | ROCm limited on Windows, falls back to CPU |
| Mac (Apple Silicon) | Works | `MAC_Install.command` handles setup. Uses mlx-whisper (Apple Silicon optimised) |
| Mac (Intel) | Partial | `MAC_Install.command` handles setup. No GPU acceleration, CPU fallback |
| Linux + NVIDIA GPU | Partial | `MAC_Install.command` works on Linux. Should work once set up manually |
| Linux + no GPU | Partial | Same + CPU fallback |

Python code is cross-platform (`os.path.join`, `sys.platform` checks). Gap is setup automation.

---

## 2. Testing Plan

### Phase A — Testing infrastructure

- [x] Create `backend/tests/conftest.py` with Flask test client, temp DB, temp project dir fixtures
- [x] Add `pytest>=8.0` and `pytest-cov` to requirements
- [x] Create `backend/pytest.ini` or `pyproject.toml` with test config
- [x] Use SQLite `:memory:` or `tmp_path` — never touch real DB

### Phase B — Automated tests by priority

#### Tier 1: Must test before shipping

- [x] **Project CRUD** — POST/GET/PATCH/DELETE `/api/projects`. Folder creation, renaming, deletion. Special chars in names (`"Audio & Video — Test"`)
- [x] **File upload validation** — all 8 allowed formats. Reject `.txt` (400). Unicode filenames. Non-existent project (404)
- [x] **Annotation read/write** — write tags/comments/speaker labels to JSON. Read back. Unicode. Empty arrays. Missing file returns defaults
- [x] **Export correctness** — export with known transcript + tags + comments. Verify markdown contains correct speakers, timestamps, tags. Verify ODT is valid. Test PII on/off
- [x] **Settings persistence** — set values, recreate Flask client, verify retained. Disallowed keys rejected
- [x] **Archive/unarchive/delete** — archive project with recordings, verify in archived list. Unarchive. Delete removes folder + DB

#### Tier 2: Important before v1.0

- [x] **Transcription pipeline (mocked)** — `test_transcription_pipeline.py` (segment collapsing, diarization merge, transcript JSON structure)
- [x] **Model registry** — `TestModelRegistry` in `test_tier2.py` + `test_model_manager.py`
- [x] **System check** — `TestSystemCheck` / `TestGPUDetection` in `test_tier2.py`
- [x] **Tag quotes aggregation** — `TestTagQuotesAggregation` in `test_tier2.py`
- [x] **Project transfer ZIP** — `TestTransferZipExtended` in `test_tier2.py`
- [x] **Concurrent upload** — `TestConcurrentUpload` in `test_tier2.py`

Also covered beyond the original plan: multitrack ingest and track merging
(`test_multitrack*.py`, `test_tracks.py`), the ML worker subprocess and its exit
guards (`test_ml_worker_process.py`, `test_process_exit_guard.py`), the launcher
and supervisor (`test_launcher_flow.py`, `test_supervisor.py`), progress
learning (`test_progress_learning.py`), transcript editing and replacement.

#### Tier 3: Edge cases

- [x] Disk full during annotation save — `TestDiskFullAnnotationSave`
- [x] Corrupted annotation JSON — `TestCorruptedAnnotationJson`
- [x] Very long project names (500 chars) — `TestVeryLongProjectName`
- [x] Delete project while transcription running — `TestDeleteProjectWithPendingTranscription`
- [x] 0-second audio file — `TestZeroDurationRecording`

### Phase C — Manual testing checklist

- [ ] Full onboarding flow: fresh install, model download, first transcription
- [ ] Real transcription quality: 5-min interview, check accuracy
- [ ] Speaker diarization: 2-person interview, verify speakers labeled correctly
- [ ] Tag workflow: select text, apply tag, see in sidebar, export, verify in markdown
- [ ] Theme toggle: dark/light, all screens look correct
- [ ] Font size setting: change to 16px, verify UI scales
- [ ] Export round-trip: export markdown, feed to ChatGPT, verify usable
- [ ] Large file: 2-hour recording, verify progress updates, memory usage
- [ ] CPU-only transcription: disable GPU, short clip, verify completion
- [ ] App restart: close/reopen, all data persists
- [ ] Tags view: filter by tag, jump to recording
- [ ] Multiple projects: 5+ projects, list/search works

### Before release — dev-only test shortcuts

These exist purely to make the local test loop fast. **They must be off for
release verification**, because each one skips something a real user goes
through. This file survives a reset, so the reminder survives with it.

| Shortcut | What it skips |
|---|---|
| `backend/reset_win.bat --keep-venv` / `backend/reset.command --keep-venv` | Preserves `.venv`, so onboarding reuses the installed ML stack instead of pip-installing torch/whisperx/pyannote. Also skips the installer's first-time setup block entirely (it only runs when `.venv` is missing) — including the move of dev files into `documentation/dev-config` and `finalize_install_layout`. |
| `backend/tools/dev_reset.bat` / `dev_reset.command` | Clears only `data/` and `projects/`. Never touches the venv, models or the installed file layout, so nothing about install/reset behaviour is exercised. |

- [ ] Run a full `reset` with **no flags**, then a fresh install from a clean
      checkout — on both platforms. This is the only path that exercises venv
      creation, the ML install during onboarding, and the file layout move.
- [ ] Verify the post-reset tree matches the preserve lists: nothing extra
      deleted (a bug here already ate `README.md` and `.github` once), nothing
      stale left behind.
- [ ] Decide whether `dev_reset.*` ships. If it does, it stays in
      `backend/tools/` (preserved by reset); if not, delete both files and drop
      the `--keep-venv` blocks from the two reset scripts.
- [x] **Keep the phase timers in `whisperx_engine.py`** — decided 2026-08-21,
      no longer a release blocker. The `_perf` context manager and its eight
      call sites (`decode`, `whisper`, `align model`, `align`, and the four
      `chunk N …` variants) stay in as analytics: they are the only per-phase
      timing the app records, and they are what surfaced the second-unit
      slowdown — chunk 2 of recording 1 ran at 0.16x realtime against chunk 1's
      0.033x, and multitrack track 1 cost 111.9s for 790s of speech against
      track 0's 128.0s for 1787s. The cost is one line per phase per chunk in
      `backend/logs/<date>/app-*.log`, never shown in the UI. If that turns out
      to be noisy on long recordings, drop them to DEBUG rather than deleting
      them.

---

## 3. What's missing for a great app

### Functional gaps

- [x] **No `setup.sh`** — Mac/Linux users can't set up the app *(MAC_Install.command does setup on first run; `backend/reset.command` for clean reset)*
- [x] **ODT vs DOCX confusion** — standardized on ODT everywhere. Removed unused `python-docx`. Added per-format descriptions in export dialogs
- [ ] **No download cancellation** — can't stop model download once started
- [x] **No transcription cancellation** — ~~2-hour file on CPU = 10+ hours with no abort~~ *(cancel button in main.html, DELETE endpoint)*
- [x] **No frontend error display** — added toast notifications + error handling in all templates *(main.html, recording.html, settings.html)*
- [x] **Tags view not wired** — ~~`tags-view.html` is a mockup~~ *(tags.html fully connected to `/tags/quotes`)*
- [ ] **No cross-transcript search** — can't search for a keyword across all interviews in a project
- [ ] **Inconsistent service naming** — перепроверить, что название сервиса (PINE) используется единообразно везде: в UI, шаблонах, скриптах, документации
- [x] **No post-onboarding optional model install** — ~~GLiNER PII can only be selected during onboarding~~ *(Settings has install/remove buttons wired to `POST /api/settings/pii-model/install` and `/remove`)*
- [ ] **Evaluate Parakeet TDT migration** — NVIDIA Parakeet TDT 0.6b (CC-BY-4.0, 600MB, WER ~3.5%) is SOTA for English STT. Requires NeMo runtime (CUDA-only, no Mac), new `_transcribe_nemo()` pipeline (~300-500 LOC), and careful dependency management (NeMo vs whisperx conflicts). Not a drop-in replacement — monitor for CTranslate2/faster-whisper ports or lighter inference APIs before committing.
- [ ] **Dependency version audit** — проверить обновления: FFmpeg (сейчас 8.0.1 bundled via static-ffmpeg), pip-пакеты в requirements.txt / requirements-lock.txt, доступность Python 3.14+ (сейчас максимум 3.13). Обновить где нужно, прогнать тесты.
- [ ] **ML package update check** — pyannote-audio, whisperx, mlx-whisper, torch are installed unpinned and can receive breaking API changes on new user installs. Periodically test new major versions (especially pyannote-audio) and update `_load_diarize()` / install targets in `model_manager.py` accordingly.

### Quality-of-life gaps

- [x] **No CPU transcription progress** — ~~user sees "Transcribing..." with no ETA~~ *(`ml_worker/progress.py` learns per-machine throughput and reports remaining time; guards against an inflated percent skewing the estimate)*
- [ ] **No keyboard shortcuts** — tagging, navigating segments, play/pause
- [ ] **No auto-save for project fields** — easy to forget "Save" button and lose edits
- [x] **No backup/restore** — ~~if SQLite corrupts, all project metadata is lost~~ *(backup_service.py: full backup/restore with manifest, per-project selective restore, conflict strategies, upload support)*

### Performance gaps

- [x] **Tag quotes endpoint is O(n)** — added mtime-based in-memory cache; only re-reads changed files
- [x] **No transcript caching** — not needed; single-file read is instant for a local app. Tag quotes endpoint (multi-file) already cached
- [ ] **Full audio loaded into memory** — 2-hour WAV ≈ 1.4 GB RAM. ASR *is* chunked above 30 min (`CHUNK_THRESHOLD_SEC` in `ml_worker/constants.py`), but the file is still decoded whole before chunking. Diarization chunking exists too, with its threshold parked at 4 hours (`DIARIZE_CHUNK_THRESHOLD_SEC`) because chunked pyannote flipped speakers across chunk seams — leave it off until that's solved

### Also
- [ ] Run `npm audit` to find vulnerable installed packages (Playwright/audit tooling only)
- [ ] Run `npm audit fix` to apply updates
- [ ] Run tests to verify the updates didn't break anything

---

## 5. Future ideas

### AMD GPU (ROCm) support

Currently PINE supports NVIDIA GPUs (via CUDA) and Apple Silicon (via MPS/mlx-whisper). AMD GPU users fall back to CPU mode. Investigate ROCm-enabled PyTorch builds to support AMD Radeon GPUs. Challenges: ROCm availability is Linux-only as of 2025; Windows ROCm support is experimental. Would need to update `_detect_nvidia_gpu()` to also detect AMD GPUs (via `rocm-smi`), add ROCm PyTorch install path in `model_manager.py`, and update onboarding UI to guide AMD users.

### Voice events detection

Previously implemented and subsequently removed. The feature detected four types of voice events inline in the transcript and exposed them in the UI, settings, and export.

**How it worked:**

Four detection strategies ran as **Stage 4** of the transcription pipeline (after transcript save), then results were stored in `voice_events: []` inside each recording's `_annotations.json` file.

1. **Silence (VAD-based)** — used `SpeechBrain VAD` (`speechbrain/vad-crdnn-libriparty`, ~200 MB, optional). The model identified speech boundaries in the audio; gaps ≥ a configurable threshold (default 2 s) between boundaries were tagged as `{type: "silence", label: "silence Xs"}`. If the model was absent, a text-based fallback (`derive_voice_events`) detected silences from gaps between transcript segments instead.

2. **Laugh (energy-based)** — ran on the raw audio waveform (no extra model). Computed RMS energy over short gaps (0.3–4 s) between speech segments. If the gap RMS exceeded 1.5× the overall RMS, it was classified as `{type: "laugh", label: "laugh"}`.

3. **Filler words (regex on text)** — matched a fixed set of patterns (uh, um, uhm, erm, ah, hmm, you know, i mean, sort of, kind of) against each transcribed segment. First match per segment → `{type: "filler", label: "filler: <word>"}`.

4. **Emotion cues (regex on text)** — matched four emotion categories (frustration, enthusiasm, confusion, hesitation) against segment text. First match per segment → `{type: "emotion", label: "<emotion>"}`.

**UI integration:**
- Recording view: "Events" toggle button in the topbar (on/off), plus a chevron to open a per-type filter popover (Silence / Laugh / Filler / Emotion toggles + silence duration input). Event badges (e.g. `⏸ silence 4.2s`, `😆 laugh`) were rendered inline in the transcript.
- Settings: an "Events" card with per-type toggles and a "Silence duration" field. An "Include voice events" toggle in Export defaults.
- Export: events were interleaved with transcript segments in temporal order and rendered as `**[0:12]** *silence 4.2s*` lines. A `include_voice_events` option controlled inclusion.
- Onboarding: a "Voice events" module item (`~200 MB`) that triggered `speechbrain-vad` download if selected.

**Reason removed:** The text-based detectors (laugh/filler/emotion) produced too many false positives and added noise without clear research value. The VAD-based silence detection added a large optional model dependency. The feature was removed to simplify the codebase while the core workflow (transcription, tagging, export) is stabilized.

**To re-implement:** Add `speechbrain-vad` back to `MODEL_REGISTRY` in `model_manager.py`; restore `derive_voice_events` in `annotations.py`; add a `_detect_voice_events` method back to `TranscriptionService`; wire it into Stage 4 of `transcribe()`; restore the UI elements in `recording.html`, `settings.html`, and `onboarding.html`; restore the settings keys in `settings.py`; and restore `voice_events` in annotation API handling and export options.

---

## 6. Recommended next steps (priority order)

Items 1–8 are done: Mac/Linux setup script, the pytest suite, the transcription
watchdog, annotation locking, frontend toasts, the DOCX/ODT decision,
transcription cancellation, and the wired-up tags view.

**Blocking the release** — nothing here has been exercised end to end yet:

1. Run a full `reset` with no flags, then a clean install, on **both** platforms.
   This is the only path that covers venv creation, the ML install during
   onboarding, and the file-layout move. See the *dev-only test shortcuts*
   section above for why the fast loop doesn't cover it.
2. Decide whether `dev_reset.*` ships (see the same section).
3. Walk the Phase C manual checklist — none of it is ticked.

**Next real features / debt**, in order:

4. Cross-transcript search — no `/search` endpoint exists at all; the most
   visible remaining gap for users.
5. Download cancellation.
6. `schema_version` in the transcript JSON, then Alembic.
7. Memory use on long recordings — ASR already chunks at 30 min
   (`CHUNK_THRESHOLD_SEC`), but the file is still decoded whole first.
8. ML dependency version audit — whisperx 3.8.1 → 3.8.6, pyannote.audio
   4.0.4 → 4.0.7; the ML stack installs unpinned, so new user installs can pick
   up breaking API changes.

---

## 7. LLM Transcript Chat (Ollama)

Allow users to discuss a recording's transcript with a local LLM via a persistent chat sidebar in the recording view. All inference runs locally through Ollama — no data leaves the machine.

### Backend

- [ ] `services/chat_service.py` — Ollama streaming client, transcript context formatter, `_chat.json` read/write (atomic)
- [ ] `chat_handlers.py` — `@socketio.on('chat:message')` handler; emits `chat:token`, `chat:done`, `chat:error`
- [ ] Register chat SocketIO handlers in `create_app()` (`app/__init__.py`)
- [ ] `GET /api/projects/<id>/recordings/<rid>/chat` — return saved chat history
- [ ] `DELETE /api/projects/<id>/recordings/<rid>/chat` — clear chat history (`_chat.json`)
- [ ] `GET /api/utils/llm-check` — ping Ollama, return `{ok, model}`
- [ ] Add `llm_base_url` (default `http://localhost:11434`) and `llm_model` (default `llama3.2`) to settings

### Frontend

- [ ] `settings.html` — LLM Chat card: Ollama URL field, model name field, "Test connection" button
- [ ] `recording.html` — persistent right-side chat sidebar:
  - Toggle button in topbar
  - Load history from `/chat` on open
  - Streaming token rendering via `chat:token` SocketIO events
  - "Clear conversation" button
  - Inline warning + link to settings if Ollama is unreachable

### Data

- Conversation stored as `<basename>_chat.json` per recording (same folder as `_transcript.json`)
- Transcript injected as `system` message each turn (not persisted); full transcript fits in Llama 3.2's 128K context window
