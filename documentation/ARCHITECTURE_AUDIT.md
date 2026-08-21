# PINE — Architectural Audit Report

**Date:** 2026-06-05
**Scope:** Full codebase — backend (~10.7K LOC Python) + frontend (~10.8K LOC templates) + supervisor/launchers.
**Method:** Every claim below was re-verified against the current tree (file:line references are current, not carried over).
**Supersedes:** the `2026-03-28` editions of this file and `backend-decomposition-audit.md`. Both were stale — see *Appendix B*.

**Status re-check 2026-08-21.** All P0 and P1 remain done. Since:

- **P2-1, P2-2, P2-3 done** — all three monoliths are split. `transcription.py` became a package plus a separate ML process; `model_manager.py` 1898 → 790; `projects.py` 1660 → a seven-module package with an unchanged URL map.
- **P2-4 half done** — the shared CSS landed and the private per-template copies are gone; no template inheritance yet.
- **P3-1 done for linting** — ruff, config in `backend/pyproject.toml`, tree clean. No type checker.
- **P3-2, P3-6, P3-8 done** — ports behind `app/ports.py`, per-installation secret key, dev tools out of the lock file.
- **Still open:** the rest of P2-4 (`base.html`, shared `api.js`), P3-3 (path idioms), P3-4 (`except Exception`, 121 of them), P3-5 (doc drift — largely addressed 2026-08-20), P3-7 (venv recovery).

A **P0-grade defect found on 2026-08-21 and fixed** is recorded as P0-2 below: concurrent uploads of one filename overwrote each other's audio.

Everything else below is as written on 2026-06-05.

**Verdict:** Architecture is sound for a local single-user desktop app. No remote-exploitable issues. The dominant problems are (1) one data-integrity bug, (2) three service/route monoliths that **grew** instead of being split after the last audit, and (3) accumulated doc/code drift and repo hygiene.

---

## How to use this document

Items are ordered for **sequential fixing** — top to bottom. Each has a stable ID (`P0-1`, `P1-2`, …), an effort estimate (**S** ≤1h · **M** few hours · **L** day+), and a checkbox. Fix one, tick it, commit, move down. Within a tier, earlier items are cheaper or unblock later ones.

> 🧭 **Каждый пункт начинается со строки «Простыми словами»** — что это значит для вас как пользователя или для сохранности ваших данных, без технических деталей. Ниже неё — `Evidence`/`Fix` для того, кто будет чинить.

> **Progress — 2026-06-05:** all of **P0** and **P1** are done and verified. 28 targeted tests pass (incl. the P0-1 cascade regression and 15 new attachment/media tests); the rest of the suite is green. Only `test_api_settings.py` has 4 failures — confirmed **pre-existing** (a Flask app-context-preservation quirk in the launcher tests, reproduces with these changes reverted) and unrelated to this work.

| Tier | Meaning |
|------|---------|
| **P0** | Correctness / data integrity — fix first |
| **P1** | High value, low effort — quick wins |
| **P2** | Structural refactors — deliberate, larger |
| **P3** | Consistency, tooling, hygiene, docs |
| **P4** | Deferred / known gaps |

---

## P0 — Correctness (fix first)

### ☑ P0-1 · Deleting a project orphans (or fails on) its Segments · **S** · ✅ done 2026-06-05
> 🧭 **Простыми словами:** Когда вы удаляете исследовательский проект, привязанные к нему «сегменты» (группы участников для скрининга) не удаляются вместе с ним. В итоге одно из двух: либо они остаются невидимым мусором, ссылающимся на уже несуществующий проект, либо само удаление падает с ошибкой и проект вообще не удаляется. Риск для вас: удаление проекта работает не до конца, а база данных со временем засоряется «висячими» остатками. Это единственный настоящий баг данных в списке.
- **Evidence:** [project.py:38](../backend/app/models/project.py:38) declares a cascade relationship for `recordings` **only**; there is no `segments` relationship and [segment.py:10](../backend/app/models/segment.py:10) defines `project_id` as a plain FK with no `ondelete`. [delete_project()](../backend/app/api/projects.py:478) calls `db.session.delete(project)` and never touches segments.
- **Why it matters:** `PRAGMA foreign_keys=ON` is set per-connection ([extensions.py:37](../backend/app/extensions.py:37)). With FK enforcement on and no `ON DELETE`, deleting a project that still has segments either (a) leaves orphan `segment` rows pointing at a dead `project_id`, or (b) raises an `IntegrityError` → HTTP 500. Both are bugs; which one fires depends on enforcement timing.
- **Fix:** Add `segments = db.relationship('Segment', backref='project', cascade='all, delete-orphan')` on `Project` (mirror the `recordings` line), **and** add `ondelete='CASCADE'` to the FK for DB-level safety. Add a regression test: create project + segment → delete project → assert segment gone, no error.

### ☑ P0-2 · Concurrent uploads of the same filename overwrite each other's audio · **S** · ✅ done 2026-08-21
> 🧭 **Простыми словами:** Если две записи с одинаковым именем файла загружались одновременно — например, вы перетащили в проект два разных `interview.mp3` из разных папок, — приложение записывало обе в один и тот же файл. В списке появлялись две записи, но аудио оставалось одно: вторая загрузка затирала первую, молча и без ошибки. Восстановить было нечем — исходник уже заменён.
- **Evidence:** both upload paths ([projects.py:304](../backend/app/api/projects.py:304) and [:641](../backend/app/api/projects.py:641)) picked a free name with `while os.path.exists(dest)` and then wrote it with `file.save(dest)` — a check and a write with a window between them. Racers all saw the name as free, all chose the same `dest`, and the last writer won. Two `recording` rows then referenced one file.
- **Reproduced:** the check-then-write allocator, driven by 8 threads through a barrier, collided in **20 of 20** trials — 8 uploads produced 1 file, 7 recordings' audio lost.
- **Fix:** `claim_free_path()` in [file_utils.py](../backend/app/services/file_utils.py) claims the name with `O_CREAT | O_EXCL`, so the filesystem hands it to exactly one caller and the losers retry with the next counter. Both call sites use it, which also removed the duplicated allocation logic noted in P2-2. Regression test: `test_same_filename_from_many_threads_never_collides`.

---

## P1 — Quick wins (small effort, real impact)

### ☑ P1-1 · No indexes on hot foreign keys · **S** · ✅ done 2026-06-05
> 🧭 **Простыми словами:** Чтобы найти записи проекта, база сейчас перебирает всю таблицу целиком при каждом открытии списка. Пока проектов мало — незаметно; с ростом числа записей приложение начнёт ощутимо подтормаживать. Индекс — это как оглавление в книге: позволяет найти нужное сразу, а не листать всё подряд. Никакого риска для данных, чисто скорость.
- **Evidence:** [recording.py:10,16](../backend/app/models/recording.py:10) (`project_id`, `transcription_status`) and [segment.py:10](../backend/app/models/segment.py:10) (`project_id`) — none declare `index=True`. These columns are filtered on nearly every request.
- **Fix:** `index=True` on `Recording.project_id`, `Recording.transcription_status`, `Segment.project_id`. Add the matching `ADD INDEX` to the startup migration so existing DBs get them.

### ☑ P1-2 · No global error handler — unhandled exceptions return HTML, not JSON · **S** · ✅ done 2026-06-05
> 🧭 **Простыми словами:** Когда внутри что-то ломается, приложение сейчас может показать «сырую» техническую страницу ошибки вместо понятного сообщения — экран просто зависает без объяснений. После правки вы всегда будете получать аккуратное сообщение об ошибке, а не белый экран.
- **Evidence:** no `@app.errorhandler` / `register_error_handler` anywhere in [__init__.py](../backend/app/__init__.py). The frontend `fetch()` layer expects `{error: …}` JSON; an unhandled 500 returns Flask's HTML page and breaks client parsing.
- **Fix:** register `@app.errorhandler(Exception)` and `@app.errorhandler(404)` returning `jsonify({'error': …}), code`.

### ☑ P1-3 · Zero logging in the entire API layer · **S–M** · ✅ done 2026-06-05
> 🧭 **Простыми словами:** Сейчас, если у вас что-то не сработало (не загрузилась запись, не сохранилось изменение), нигде не остаётся следов — ни мне, ни любому, кто помогает, нечего посмотреть, чтобы понять причину. Логи — это «бортовой самописец»: когда что-то идёт не так, остаётся запись, по которой видно, что именно сломалось. Это сильно ускоряет починку ваших проблем.
- **Evidence:** 0 `getLogger`/`log.*` references across all five blueprints — `projects.py`, `settings.py`, `onboarding.py`, `backup.py`, `utils.py`. Failed requests leave no trace. (Services log fine; the API layer is the blind spot.)
- **Fix:** module-level `log = logging.getLogger(__name__)` per blueprint; log at least every 4xx/5xx response and every caught exception. Pairs naturally with P1-2.

### ☑ P1-4 · Recently-added routes have zero test coverage · **S** · ✅ done 2026-06-05
> 🧭 **Простыми словами:** Функции «вложений» (файлы, прикреплённые к проекту) и проигрывания аудио никак не проверяются автоматически. Значит, любая будущая доработка может незаметно их сломать — и никто не заметит, пока вы не столкнётесь с этим вживую. Тесты — это страховка, что эти функции продолжают работать после изменений.
- **Evidence:** no test file references `attachment` — all 5 attachment routes ([projects.py:1348+](../backend/app/api/projects.py:1348)) and the media-streaming route ([projects.py:789](../backend/app/api/projects.py:789)) are untested.
- **Fix:** add `tests/test_api_attachments.py` (list/add/patch/delete/download) and a media-stream smoke test.

### ☑ P1-5 · One `subprocess.run` still has no timeout · **S** · ✅ done 2026-06-05
> 🧭 **Простыми словами:** В одном месте приложение запускает вспомогательную программу и может ждать её бесконечно, если та зависнет — тогда соответствующее действие «повиснет» навсегда, и придётся перезапускать приложение. Ограничение по времени гарантирует, что оно либо отработает, либо честно прервётся с ошибкой.
- **Evidence:** most calls now pass `timeout=` (good — fixed since last audit), but [settings.py:681](../backend/app/api/settings.py:681) appears to omit it. A hung child there blocks the request.
- **Fix:** add a bounded `timeout=` and handle `subprocess.TimeoutExpired`.

### ☑ P1-6 · 394 stray `*.pyc.<n>` files clutter the tree · **S** · ✅ done 2026-06-05
> 🧭 **Простыми словами:** В папках проекта накопилось 394 технических файла-мусора (побочный эффект работы Python на Windows). Они не опасны и не влияют на работу, но засоряют папку, замедляют поиск и попадают в резервные копии, раздувая их размер. Достаточно один раз почистить.
- **Evidence:** `find backend -name "*.pyc.*"` → **394** files (Windows can't atomically replace a loaded `.pyc`, so it renames the old one with a numeric suffix; they accumulate). Git-ignored (they live under `__pycache__/`) but they pollute search, Glob, and backups.
- **Fix:** `Get-ChildItem backend -Recurse -Filter *.pyc.* | Remove-Item -Force`. Consider setting `PYTHONPYCACHEPREFIX` to a temp dir so bytecode never lands in the source tree.

---

## P2 — Structural refactors (deliberate, larger)

> The three files below were flagged for decomposition on 2026-03-28 and had since **grown**:
> `transcription.py` 1905 → **2349** · `model_manager.py` 1087 → **1830** · `projects.py` 1358 → **1439**.
>
> **Re-checked 2026-08-20:** `transcription.py` is gone — split into
> `app/services/transcription/` plus the separate `ml_worker/` process (P2-3, done).
> The other two kept growing: `model_manager.py` **1897**, `projects.py` **1660**.

### ☑ P2-1 · `model_manager.py` mixes 4 responsibilities; 387 lines of it are an embedded Windows batch script · **M** · ✅ done 2026-08-21
> 🧭 **Простыми словами:** Один файл отвечает сразу за слишком многое: и за модели ИИ, и за их установку, и за скрипты запуска самого приложения. Из-за этого любая мелкая правка в одном (скажем, в кнопке запуска) рискует случайно задеть другое (загрузку моделей). Это не баг прямо сейчас, а риск на будущее: «починил одно — сломал соседнее». Разделение делает доработки безопаснее.
- **Evidence:** [model_manager.py](../backend/app/services/model_manager.py) (1830 LOC) holds: model registry/DB, pip+PyTorch install (incl. CUDA channel logic), HF Windows-symlink workarounds, **and** launcher generation. [`_windows_app_launcher_contents()`](../backend/app/services/model_manager.py:57) is a **387-line** `.bat` string literal; sibling helpers `_promote_platform_launcher`, `_move_installers_to_backend`, `_sync_platform_launcher_layout`, `_ensure_root_windows_shortcut`, `restore_default_launcher_layout_after_reset` manage installer file layout — none of which is "model management".
- **Fix (in order):** (a) extract all launcher/installer-layout code into `services/launcher_layout.py`; (b) extract pip/torch install into `services/pip_installer.py` (it has no dependency on the registry — clean cut). Leaves `model_manager.py` focused on the registry + downloads.
- **Done exactly that.** `model_manager.py` 1898 → **790**; `launcher_layout.py` **602** (the .bat template and the installer shuffle); `pip_installer.py` **520** (torch channel logic, the pip runner, the install log). The seams were clean — neither new module references the other, and the only thing they shared, a socket-emit helper, moved to `extensions.safe_emit`. Every top-level definition is accounted for.

### ☑ P2-2 · `projects.py` is one blueprint over 7 domains, with duplicated upload/link logic · **M** · ✅ done 2026-08-21
> 🧭 **Простыми словами:** Самый часто изменяемый файл свалил в кучу 7 разных тем (проекты, записи, теги, экспорт и т.д.). В нём тяжело ориентироваться — правки идут медленнее и с большим числом ошибок. Вдобавок логика загрузки файла скопирована дважды: поправят в одном месте, забудут во втором — и одинаковые на вид действия начнут вести себя по-разному. Это про скорость и надёжность будущих доработок.
- **Evidence:** [projects.py](../backend/app/api/projects.py) (1439 LOC, 33 route fns) spans project CRUD, recordings, segments, tags, annotations, export, transfer, attachments. [upload_recording()](../backend/app/api/projects.py:587) and [link_recording()](../backend/app/api/projects.py:652) share ~30 lines of recording-row creation + enqueue.
- **Fix:** extract `_create_recording_row(...)` helper first (S, isolated win), then split into sub-blueprints (`recordings`, `segments`, `annotations`, `attachments`, `export`) under a shared url-prefix.
- **Done as a package, one blueprint.** `app/api/projects/` — `common.py` (the blueprint and shared helpers, 219), `recordings.py` (569), `tags.py` (308), `export.py` (202), `crud.py` (192), `attachments.py` (119), `segments.py` (68). Sub-blueprints were not needed: the modules register on the same `projects_bp`, so **the URL map is byte-identical** — verified by dumping `app.url_map` before and after (86 routes, 37 under `/api/projects`). The duplicated upload/link allocation went earlier, with the P0-2 fix.

### ☑ P2-3 · `transcription.py` is a 2349-line module with a god-class · **L** · ✅ done 2026-08
> 🧭 **Простыми словами:** Главный файл движка расшифровки — огромный и делает всё сразу. Любая доработка в нём рискованна и медленна, потому что легко случайно задеть что-то ещё. Разбиение на части облегчит развитие (например, добавление новых языков или движков распознавания) без страха всё уронить. Срочности нет, но это инвестиция в будущее.
- **Evidence (at the time):** `transcription.py` — import-time monkey-patches for `torch`/`torchaudio`/`hf_hub`, a `TranscriptionService` singleton (~40 methods, two engines WhisperX+MLX, three diarization variants), plus worker/watchdog/cancellation. `transcribe()` was ~240 lines.
- **Resolved beyond the proposed fix.** The module didn't just become a package — the ML half moved out of the Flask process entirely:
  - `app/services/transcription/` — `job_runner.py` (queue, watchdog, cancellation, progress learning) and `worker_client.py` (spawns the child, speaks the pipe protocol).
  - `backend/ml_worker/` — `pipeline.py`, `diarize.py`, `tracks.py`, `multitrack.py`, `audio.py`, `progress.py`, `protocol.py`, `compat.py` (the monkey-patches), and `engines/` with `whisperx_engine` + `mlx_engine` behind `base.py`.
  - This also closes risk #1 of `ARCHITECTURE_REVIEW_2026-07.md`: a CUDA crash or OOM now kills the worker, not the backend.

### ☐ P2-4 · Frontend: 5 standalone templates, no inheritance, ~380 duplicated lines · **M**
> 🧭 **Простыми словами:** Оформление (тема, шрифты, цвета) скопировано на каждой из 5 страниц по отдельности. Если вы захотите, например, поправить тёмную тему — менять придётся в 5 местах, и легко забыть одно, отчего страницы начнут выглядеть по-разному. Одна страница уже настроена «наоборот» относительно остальных. Это про единый внешний вид и простоту изменения оформления.
- **Evidence:** 5 `*.html`, **zero** use `{% extends %}`. Theme-init JS, `@font-face`, and CSS design tokens are copy-pasted across all of them; [recording.html:166](../backend/templates/recording.html:166) defines its theme with inverted `:root` defaults vs the others.
- **Fix:** normalize `recording.html` theme direction (S, prerequisite) → create `base.html` with `{% block %}`s → extract shared tokens/`@font-face` to `static/css/tokens.css` → unify the `api()` fetch wrapper into `static/js/api.js`.
- **Progress 2026-08-21 — the CSS half is done.** `app/static/css/` holds `tokens.css` (light-first palette, tag scale, radius scale, z-index ladder), `button-system.css`, `fonts.css` and `save-status.css`, linked by all six templates. The private copies are gone: **168 palette declarations and 117 `@font-face` blocks removed, 342 lines**, leaving each template only what it alone uses. Verified by resolving every token in a browser against the shared sheets — 53/53 resolve, 22 faces load, light and dark both correct.
  - Also fixed while there: `main.html`, `manage_tags.html`, `onboarding.html` and `recording.html` declared their dark palette on a bare `[data-theme="dark"]`, which matches any element with the attribute — e.g. the Settings theme picker's own "Dark" button, on a light page. All are `:root`-scoped now.
  - **Still open in this item:** no template uses `{% extends %}`, there is no `base.html`, and `static/js/` has only `save-status.js` and vendored `socket.io.min.js` — no shared `api.js`.

---

## P3 — Consistency, tooling, hygiene, docs

### ☑ P3-1 · No linter or type checker · **S** · ✅ ruff done 2026-08-21 (no type checker yet)
> 🧭 **Простыми словами:** Нет автоматического «корректора», который ловит опечатки и типичные ошибки в коде ещё до того, как они дойдут до вас. Это дешёвая страховка качества — компьютер проверяет код за разработчика.
- **Evidence:** [.pre-commit-config.yaml](../.pre-commit-config.yaml) has a single `mojibake-check` hook — no `ruff`, `mypy`, `flake8`, or `black`; no `pyproject.toml`. Type annotations are sparse in the service layer.
- **Fix:** add `ruff` (lint + format) as a pre-commit hook and a minimal config; optionally `mypy` in non-strict mode on the API layer.

### ☑ P3-2 · Hardcoded ports/URLs (5000, 5001) scattered across the codebase · **S–M** · ✅ done 2026-08-21
> 🧭 **Простыми словами:** Номер «двери» (порт), через которую открывается приложение, прописан вручную в десятке мест. Если поменять его в одном — остальные перестанут совпадать, и часть функций отвалится. Сведение к одному месту убирает этот риск при настройке.
- **Evidence:** literal `127.0.0.1:5000` / `:5001` in [onboarding.py:31](../backend/app/api/onboarding.py:31), [settings.py:199,239](../backend/app/api/settings.py:199), [extensions.py:45-46](../backend/app/extensions.py:45), [transcription.py:1896](../backend/app/services/transcription.py:1896), [__init__.py:353](../backend/app/__init__.py:353), and many `model_manager.py` script strings. `PINE_BACKEND_PORT` is only honoured in some of them → changing the port desyncs the rest.
- **Fix:** single source in `config.py` (backend + supervisor port), import everywhere; build URLs from it.

### ☐ P3-3 · Mixed path idioms — `os.path` (14 files) vs `pathlib` (4 files) · **S–M**
> 🧭 **Простыми словами:** Внутри используются два разных стиля работы с путями к файлам. Само по себе не баг, но повышает шанс ошибок с путями — особенно при переносе между Windows и Mac. Единый стиль = надёжнее работа с вашими файлами на разных системах.
- **Fix:** standardize on `pathlib.Path`; migrate opportunistically when touching a file.

### ☐ P3-4 · Over-broad `except Exception` · **M**
> 🧭 **Простыми словами:** Местами код «глотает» ошибки молча: если что-то пошло не так, оно может просто тихо пропуститься — и вы получите неполный результат, не зная, что часть не сработала. Нужно, чтобы такие случаи хотя бы записывались в журнал, а не исчезали бесследно.
- **Evidence:** 32 in `transcription.py`, 30 in `model_manager.py`. Many are legitimately defensive (CUDA probing, temp cleanup), but some swallow the root cause silently.
- **Fix:** narrow to specific exceptions where possible; guarantee `log.exception(...)` on every catch that continues.

### ☐ P3-5 · Documentation drift / duplication · **S**
> 🧭 **Простыми словами:** В документации есть устаревшие и противоречивые места — например, написано, что приложение открыто всем сетям, хотя на деле оно доступно только локально на вашем ПК; и существуют два разных файла-инструкции с одинаковым именем. Это вводит в заблуждение и вас, и любого, кто помогает с проектом. Нужно привести в порядок и оставить один источник правды.
- `ARCHITECTURE.md` security note says **"CORS: `*`"** ([ARCHITECTURE.md:122](ARCHITECTURE.md)) — **wrong**: CORS is restricted to localhost in both [__init__.py:296](../backend/app/__init__.py:296) and [extensions.py:49](../backend/app/extensions.py:49). Fix the note.
- `AGENTS.md` exists in **both** the repo root and `documentation/` with **divergent** content — pick one source of truth, make the other a pointer.
- Design-audit tooling is **triplicated and divergent**: [backend/design-audit.js](../backend/design-audit.js) and [backend/tools/design-audit.js](../backend/tools/design-audit.js) differ (different md5), plus `tools/design_audit.py`. Keep one, delete the rest.
- `backend-decomposition-audit.md` is stale (wrong line numbers; references removed functions). Update or delete — this file now covers it.

### ☑ P3-6 · Hardcoded `SECRET_KEY` fallback · **S** · ✅ done 2026-08-21
> 🧭 **Простыми словами:** Ключ, которым приложение подписывает сессии в браузере, сейчас одинаковый «заводской» у всех установок. Для локального приложения без входа по паролю риск низкий, но правильнее генерировать уникальный ключ на вашем компьютере при первом запуске.
- **Evidence:** [config.py:8](../backend/app/config.py:8) → `'port-dev-key-change-in-prod'`. Low risk (localhost, no auth) but signs session cookies.
- **Fix:** generate a random key on first run and persist it in `Setting`.

### ☐ P3-7 · Launch scripts can't recover a broken venv · **S**
> 🧭 **Простыми словами:** Если установка приложения один раз прервалась на полпути (например, пропал интернет), при следующем запуске оно может решить, что всё уже установлено, и работать «наполовину сломанным» — без понятной причины. Исправление заставит его в таком случае доустановиться, а не делать вид, что всё в порядке.
- **Evidence:** both installers skip setup if `.venv/` exists; a pip failure mid-install leaves a present-but-broken venv that never retries.
- **Fix:** write a `.venv/.pine-setup-complete` sentinel only after a fully successful install; re-run setup if it's missing.

### ☑ P3-8 · Lock file carries stray dev tools · **S** · ✅ done 2026-08-21
> 🧭 **Простыми словами:** В списке зафиксированных зависимостей затесались лишние инструменты разработчика, не нужные для работы приложения. Не вредно — просто лишний балласт, который стоит убрать для чистоты.
- **Evidence:** `requirements-lock.txt` includes `twine`, `readme_renderer` — packaging/publish tools unrelated to running PINE.
- **Fix:** regenerate the lock from a clean post-onboarding venv.

---

## P4 — Deferred / known gaps

> 🧭 **Простыми словами:** Это известные ограничения «пока не делаем» — не поломки, а отсутствующие удобства или защиты, которые можно добавить позже. Перечислены, чтобы о них помнить, но они не мешают повседневной работе.

Carried from `TODO.md` / `documentation/CLAUDE.md`; intentionally out of scope for the fix pass:
- **No formal migrations** — startup `ALTER TABLE ADD COLUMN` loop works but can't rename/drop/retype. Evaluate Alembic only when a non-additive change is needed.
- **No download cancellation**, **no cross-transcript search** — feature gaps, not defects.
- **No JSON-schema validation** on transcript/annotation files — a hand-edited file propagates silently. Defensive, low urgency.
- ~~**Watchdog can't kill a hung worker thread** — CPython limitation; document the GIL-dependent assumption rather than fix.~~ **Resolved 2026-08** by P2-3: the worker is a separate process now, so the watchdog kills it outright (`worker_client.kill()`, `taskkill /T /F` on Windows to take the whole tree).

---

## Appendix A — Resolved since the 2026-03-28 audit

For an honest record (these were real then, verified fixed now — do **not** re-file them):
- ✅ `subprocess.run` timeouts — previously absent, now present on essentially all calls (lone exception tracked as **P1-5**).
- ✅ `_safe_emit()` duplication — now a single definition ([model_manager.py:635](../backend/app/services/model_manager.py:635)).
- ✅ Migration code — refactored into a data-driven `ADD COLUMN` loop with `log.info`/integrity-check logging (the old "migrations are invisible" finding no longer applies).

## Appendix B — Why the prior audit was retired

Both 2026-03-28 documents drifted from the code within ~10 weeks: line numbers were off by hundreds (e.g. `transcribe()` listed at 1398, actually 1885), and they described functions that were since removed (`_build_vad_chunks`, `_merge_speech_intervals`, `_transcribe_chunked_mlx_vad` — 0 definitions today). **Lesson:** keep audits claim-light and re-verify on each pass; prefer stable IDs + file:line over prose that rots.
