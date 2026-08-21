# Backend Decomposition Audit

> **Retired — kept for the record.** Superseded by
> [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) (2026-06-05); see its Appendix B
> for why. Read it for the current picture, not this file.
>
> Two things below no longer exist as described: `transcription.py` was split
> into `app/services/transcription/` plus the separate `ml_worker/` process, and
> the route inventory predates multitrack, themes, transcript editing and the
> backup blueprint. Line counts are all stale.

**Date:** 2026-03-28
**Scope:** projects.py (1,358 LOC), transcription.py (1,905 LOC), model_manager.py (1,087 LOC)

---

## 1. backend/app/api/projects.py (1,358 LOC)

### Route Inventory by Domain (35 routes)

| Category | Routes | Count |
|----------|--------|-------|
| **Project CRUD** | `GET /` (list), `POST /` (create), `GET /<id>` (get), `PATCH /<id>` (update), `POST /<id>/archive`, `POST /<id>/unarchive`, `DELETE /<id>` | 7 |
| **Single Transcriptions** | `GET /single-transcriptions`, `POST /single-transcriptions`, `DELETE /single-transcriptions/<id>` | 3 |
| **Segments** | `GET /<id>/segments`, `POST /<id>/segments`, `PATCH /<id>/segments/<sid>`, `DELETE /<id>/segments/<sid>` | 4 |
| **Recordings** | `POST /<id>/recordings` (upload), `POST /<id>/recordings/link`, `GET /<id>/recordings/<rid>`, `PATCH /<id>/recordings/<rid>`, `GET /<id>/recordings/<rid>/media`, `DELETE /<id>/recordings/<rid>`, `DELETE /<id>/recordings/<rid>/transcription` (cancel) | 7 |
| **Annotations/Tags** | `GET|PATCH /<id>/tags`, `GET|PATCH /<id>/recordings/<rid>/annotations`, `GET /<id>/tags/quotes` | 3 |
| **Transcript & Export** | `GET /<id>/recordings/<rid>/transcript`, `GET|POST /<id>/recordings/<rid>/export`, `GET|POST /<id>/export` | 3 |
| **Transfer/Archive** | `GET /<id>/transfer` | 1 |
| **Attachments** | `GET /<id>/attachments`, `POST /<id>/attachments`, `PATCH /<id>/attachments/<aid>`, `DELETE /<id>/attachments/<aid>`, `GET /<id>/attachments/<aid>/download` | 5 |

**Total: 33 route functions across 35 decorator bindings** (tags and annotations each handle GET+PATCH)

### Helper Functions (non-route, module-level)

| Line | Function | Purpose |
|------|----------|---------|
| 29 | `_safe_ascii()` | Filename sanitization |
| 37 | `_content_disposition()` | HTTP header helper |
| 46 | `_write_project_readme()` | Write README.txt into project folder |
| 120 | `_projects_root()` | Get projects directory path |
| 124 | `_recording_filepath()` | Resolve recording file on disk |
| 135 | `_safe_folder_name()` | Deduplicate folder names |
| 152 | `_mp4_faststart()` | Move MP4 moov atom for streaming |
| 174 | `_probe_duration()` | ffprobe audio duration |
| 215 | `_get_or_create_system_project()` | System project for single transcriptions |
| 470 | `_segment_assigned_count()` | Count recordings in a segment |
| 820 | `_parse_bool()` | Query param bool parsing |
| 886 | `_cached_json()` | Mtime-based JSON cache |
| 904 | `_anchor_text_from_span()` | Extract tag quote text from segments |
| 1243-1262 | `_attachments_dir/meta/load/save()` | 4 attachment filesystem helpers |

### Duplication: upload_recording() vs link_recording()

Both functions share this identical pattern:
1. Look up project (404 if missing)
2. Validate file extension against `ALLOWED_EXTENSIONS`
3. Get `original_name`, `file_size`, `duration` via `_probe_duration()`
4. Ensure project directory exists
5. Create `Recording(...)` row with same fields
6. Commit, call `enqueue()`, call `_write_project_readme()`
7. Return `recording.to_dict()`, 201

**Duplicated logic:** ~30 lines of the recording creation + enqueue + response pattern. Could be extracted into a `_create_recording_row(project, original_name, stored_name, ext, file_size, duration, is_linked=False)` helper.

### Recommendation: SPLIT

projects.py is a 1,358-line monolith with 7 distinct domains. Recommended split:

- **`projects_crud.py`** -- Project CRUD + segments (11 routes, ~250 LOC)
- **`recordings.py`** -- Upload, link, get, update, delete, media, cancel (7 routes, ~300 LOC)
- **`annotations.py`** -- Tags, annotations, tag_quotes (3 routes, ~300 LOC including `_anchor_text_from_span` + `_cached_json`)
- **`export.py`** -- Recording export, project export, transfer (3 routes, ~200 LOC)
- **`attachments.py`** -- All attachment routes + 4 helpers (5 routes, ~130 LOC)
- **`_helpers.py`** -- Shared helpers (`_projects_root`, `_recording_filepath`, `_probe_duration`, `_mp4_faststart`, `_safe_folder_name`, `_content_disposition`, `_safe_ascii`, `_write_project_readme`, `_parse_bool`)

Single-transcriptions routes (3) can stay with project CRUD since they use the system project pattern.

---

## 2. backend/app/services/transcription.py (1,905 LOC)

### Function Inventory by Concern

#### Monkey-patching / Compatibility Shims (lines 21-137, ~120 LOC)
| Line | Function | Purpose |
|------|----------|---------|
| 21 | `_patch_torchaudio_for_pyannote()` | Shim removed torchaudio 2.9+ APIs |
| 60 | `_patch_hf_hub_legacy_use_auth_token()` | Patch deprecated HF auth kwarg |
| 88 | `_patch_torch_load_for_trusted_checkpoints()` | Suppress torch.load weights_only warning |

#### Audio Processing / FFmpeg Utilities (lines 148-252, ~105 LOC)
| Line | Function | Purpose |
|------|----------|---------|
| 148 | `_get_duration_secs()` | ffprobe duration |
| 161 | `_load_audio_range()` | Load audio slice via ffmpeg subprocess |
| 176 | `_write_wav()` | Write float32 tensor to WAV |
| 194 | `_merge_speech_intervals()` | Merge adjacent VAD intervals |
| 208 | `_build_vad_chunks()` | Split audio by VAD for chunked transcription |
| 253 | `_clear_mlx_cache()` | Free MLX memory cache |

#### TranscriptionService Class (lines 262-1690, ~1,430 LOC)

**Model Loading (lines 262-730)**
| Line | Method | Concern |
|------|--------|---------|
| 268 | `__new__()` | Singleton pattern |
| 276 | `__init__()` | State initialization |
| 295 | `_whisperx_align_device()` | Device selection |
| 308 | `_get_whisperx_align_model()` | Load alignment model |
| 331 | `_diarization_to_dataframe()` | Convert pyannote annotation |
| 345 | `_detect_device()` | CUDA/MPS/CPU detection |
| 400 | `_get_models_path()` | Path helper |
| 404 | `_get_hf_token()` | Token helper |
| 408 | `_load_whisper()` | Load whisper/MLX model |
| 446 | `_stub_torchcodec()` | Stub out torchcodec for pyannote |
| 620 | `_apply_hf_offline_after_onboarding()` | Set HF_HUB_OFFLINE env |
| 641 | `_load_diarize()` | Load diarization pipeline |
| 699 | `_load_pyannote_pipeline_native()` | Native pyannote pipeline loading |
| 811 | `_ensure_models()` | Orchestrate all model loading |

**Diarization (lines 731-1012)**
| Line | Method | Concern |
|------|--------|---------|
| 731 | `_pyannote_audio_file_input()` | Format audio for pyannote |
| 762 | `_synchronize_mlx_segments_for_ui()` | Format segments for frontend |
| 783 | `_instantiate_whisperx_diarization_pipeline()` | WhisperX diarization setup |
| 818 | `_normalize_diarize_audio_input()` | Normalize diarization input |
| 835 | `_run_diarization()` | Run WhisperX diarization |
| 900 | `_unwrap_pyannote_diarization_annotation()` | Extract annotation from result |
| 913 | `_run_diarization_mlx_native()` | Run native pyannote diarization (Mac) |
| 962 | `_assign_speakers_simple()` | Map diarization to segments |

**VAD (lines 1012-1110)**
| Line | Method | Concern |
|------|--------|---------|
| 1012 | `_load_vad()` | Load Silero VAD model |
| 1037 | `_run_silero_vad()` | Run VAD inference |

**Transcription Execution (lines 1110-1398)**
| Line | Method | Concern |
|------|--------|---------|
| 1110 | `_transcribe_mlx()` | MLX whisper transcription (Mac) |
| 1130 | `_transcribe_chunked_mlx()` | Chunked MLX transcription |
| 1217 | `_transcribe_chunked_mlx_vad()` | VAD-aware chunked MLX |
| 1297 | `_transcribe_chunked()` | Chunked WhisperX transcription |
| 1379 | `_map_speakers()` | Normalize speaker labels |
| 1398 | `transcribe()` | Main orchestrator (~294 lines) |

#### Worker / Queue / Cancellation (lines 1692-1905, ~215 LOC)
| Line | Function | Concern |
|------|----------|---------|
| 1692 | `_fmt_elapsed()` | Format duration string |
| 1704 | `_touch_heartbeat()` | Update worker heartbeat |
| 1710 | `_emit_status()` | SocketIO progress emission |
| 1726 | `_register_cancel()` | Register cancellation flag |
| 1733 | `_unregister_cancel()` | Remove cancellation flag |
| 1738 | `_check_cancel()` | Check if cancelled |
| 1746 | `cancel_transcription()` | Cancel a transcription |
| 1756 | `enqueue()` | Add recording to queue |
| 1762 | `_worker_loop()` | Main worker loop |
| 1811 | `start_worker()` | Start worker thread |
| 1823 | `_get_watchdog_timeout()` | Get timeout setting |
| 1833 | `_watchdog_loop()` | Watchdog thread loop |
| 1884 | `start_watchdog()` | Start watchdog thread |
| 1896 | `requeue_interrupted()` | Requeue stuck transcriptions |

### Duplication Found

- `_transcribe_chunked_mlx()` (line 1130) and `_transcribe_chunked_mlx_vad()` (line 1217) share ~60% of their structure (chunk iteration, progress emission, segment accumulation). They differ only in how chunks are determined (fixed-size vs VAD-based).
- `_run_diarization()` (WhisperX) and `_run_diarization_mlx_native()` share the same error handling and progress reporting pattern.

### Coupling Assessment

- **TranscriptionService** is a 1,430-line singleton class that mixes model loading, inference, diarization, VAD, and audio processing. The `transcribe()` method at line 1398 is ~294 lines -- a very long orchestrator.
- The monkey-patching functions are only called from within this file but affect global state.
- Worker/queue/cancellation functions at the bottom are logically independent of the TranscriptionService class.

### Recommendation: SPLIT into 3-4 modules

- **`transcription/patches.py`** -- All 3 monkey-patching functions (~120 LOC). Called once at import, cleanly separable.
- **`transcription/audio_utils.py`** -- `_get_duration_secs`, `_load_audio_range`, `_write_wav`, `_merge_speech_intervals`, `_build_vad_chunks`, `_clear_mlx_cache` (~105 LOC). Pure utility, no class coupling.
- **`transcription/service.py`** -- The TranscriptionService class (~1,430 LOC). Could be further split but the singleton pattern and shared model state make internal splitting harder without refactoring.
- **`transcription/worker.py`** -- `enqueue`, `_worker_loop`, `start_worker`, `_watchdog_loop`, `start_watchdog`, `requeue_interrupted`, cancellation functions, `_emit_status`, `_fmt_elapsed` (~215 LOC). Only dependency on service is calling `TranscriptionService().transcribe()`.

The TranscriptionService class itself is the hardest to split because model state (`self.whisper_model`, `self.diarize_pipeline`, `self.vad_model`) is shared across methods. However, the chunked transcription variants could be consolidated.

---

## 3. backend/app/services/model_manager.py (1,087 LOC)

### Function Inventory by Concern

#### Windows HF Symlink Workaround (lines 16-54, ~40 LOC)
| Line | Function | Purpose |
|------|----------|---------|
| 16 | `_begin_windows_hf_hub_no_symlinks()` | Patch HF to avoid symlinks |
| 39 | `_end_windows_hf_hub_no_symlinks()` | Restore original behavior |
| 49 | `_safe_emit()` | SocketIO error-safe emit |

#### Model Registry & DB (lines 61-286, ~225 LOC)
| Line | Function | Purpose |
|------|----------|---------|
| -- | `MODEL_REGISTRY` (dict) | Static model definitions |
| 152 | `_model_for_platform()` | Platform filter |
| 163 | `init_model_registry()` | Populate DB from registry |
| 188 | `pyannote_hub_cache_root()` | Cache path helper |
| 193 | `_hub_cache_marker_file()` | HF cache probe helper |
| 199 | `snapshot_dir_if_repo_cached()` | Check HF cache |
| 210 | `model_storage_dir()` | Resolve model path on disk |
| 226 | `reconcile_model_statuses()` | Startup status reconciliation |
| 266 | `validate_hf_token()` | HF API token validation |

#### Model Selection API (lines 277-320, ~45 LOC)
| Line | Function | Purpose |
|------|----------|---------|
| 289 | `get_default_stt_model()` | Platform-default STT model |
| 294 | `get_models_for_setup()` | Models needed for onboarding |

#### Model Removal (lines 322-344, ~25 LOC)
| Line | Function | Purpose |
|------|----------|---------|
| 322 | `remove_model()` | Delete model from disk + DB |

#### Pip / Package Installation (lines 347-685, ~340 LOC)
| Line | Function | Purpose |
|------|----------|---------|
| 378 | `_is_package_installed()` | Check importability |
| 395 | `_check_packages()` | Status of all required packages |
| 422 | `check_ml_deps()` | Check transcription deps |
| 432 | `ensure_transcription_dependencies()` | Auto-install missing deps |
| 445 | `_install_package_if_missing()` | Single pip install |
| 467 | `_install_model_specific_packages()` | Optional model deps |
| 482 | `_base_python_torch_version()` | Check base Python torch |
| 500 | `_is_torch_cuda_available()` | CUDA availability check |
| 512 | `_run_torchruntime_install()` | GPU detection + PyTorch install |
| 547 | `_pytorch_wheel_index_url()` | Determine correct wheel URL |
| 567 | `_companion_pip_extra_args()` | Pip args for torch companions |
| 578 | `_torch_companion_channels_aligned()` | Check torch/torchvision alignment |
| 597 | `_evict_torch_companion_modules()` | Clear torch modules from sys.modules |
| 608 | `repair_torch_companion_wheels_if_needed()` | Fix mismatched torch variants |
| 637 | `_realign_torchaudio_torchvision()` | Force reinstall companions |
| 657 | `_run_pip()` | Run pip subprocess |
| 686 | `install_pip_packages()` | Full pip install orchestrator |

#### Download & Progress (lines 747-898, ~150 LOC)
| Line | Function | Purpose |
|------|----------|---------|
| 747 | `_dir_size()` | Recursive directory size |
| 758 | `_model_already_on_disk()` | Check if model fully downloaded |
| 765 | `_monitor_progress()` | Background download progress thread |
| 795 | `play_install_complete_sound()` | Play completion sound |
| 873 | `_preload_alignment_model()` | Preload WhisperX alignment model |
| 889 | `download_models()` | Main download orchestrator (~200 LOC) |

### Coupling Assessment

- **Pip installation (~340 LOC)** is tightly coupled to PyTorch ecosystem specifics (torchruntime, CUDA detection, wheel URL resolution, companion package alignment). This is a self-contained concern with no dependency on model registry or DB.
- **MODEL_REGISTRY** is referenced by registry functions, download, and removal -- central coupling point but acceptable.
- `_safe_emit()` is duplicated here and could be shared from a utility module.
- `download_models()` is a 200-line function that mixes pip installation, model downloading, progress monitoring, and onboarding completion -- too many concerns.

### Duplication Found

- `_safe_emit()` is defined identically in both `model_manager.py` (line 49) and likely used similarly in transcription.py's `_emit_status()`.
- The "already on disk" check pattern appears 4 times in `download_models._run()` (lines for use_pyc and non-pyc paths, both checking `_model_already_on_disk` with identical skip logic).

### Recommendation: SPLIT into 2 modules

- **`model_manager.py`** (keep, ~550 LOC) -- Registry, DB operations, selection API, removal, download orchestration, progress monitoring. This is the cohesive "model lifecycle" concern.
- **`pip_installer.py`** (extract, ~340 LOC) -- Everything from `_is_package_installed()` through `install_pip_packages()`, plus the torch companion alignment functions. Zero dependency on MODEL_REGISTRY or DB models. Only called from `download_models()` and `ensure_transcription_dependencies()`.

The `_safe_emit()` helper should move to a shared utility (e.g., `extensions.py` or a new `utils.py`).

---

## Summary Table

| File | LOC | Concerns Mixed | Recommended Action | Priority |
|------|-----|----------------|-------------------|----------|
| `projects.py` | 1,358 | 7 domains in one file | Split into 5-6 route modules + helpers | Medium |
| `transcription.py` | 1,905 | Patches, audio utils, service class, worker | Split into 4 modules (package) | Low |
| `model_manager.py` | 1,087 | Registry, pip install, download | Extract pip_installer.py (~340 LOC) | Low |

### Key Risk: projects.py

The highest-value split is `projects.py`. At 35 routes across 7 domains, it has the most developer contention risk. The attachments routes (added recently) and tag_quotes (140+ lines of quote aggregation logic) are the most self-contained extraction targets.

### Low-Risk Quick Wins

1. Extract `_create_recording_row()` helper to deduplicate upload/link (~30 LOC saved)
2. Extract `_safe_emit()` to shared utility (used in 2 files)
3. Move attachment routes + helpers to `attachments.py` (fully self-contained, ~130 LOC)
