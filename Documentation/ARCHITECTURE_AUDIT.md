# PINE — Architectural Audit Report

**Date:** 2026-06-05
**Scope:** Full codebase — backend (~10.7K LOC Python) + frontend (~10.8K LOC templates) + supervisor/launchers.
**Method:** Every claim below was re-verified against the current tree (file:line references are current, not carried over).
**Supersedes:** the `2026-03-28` editions of this file and `backend-decomposition-audit.md`. Both were stale — see *Appendix B*.

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

> The three files below were flagged for decomposition on 2026-03-28 and have since **grown**:
> `transcription.py` 1905 → **2349** · `model_manager.py` 1087 → **1830** · `projects.py` 1358 → **1439**.

### ☐ P2-1 · `model_manager.py` mixes 4 responsibilities; 387 lines of it are an embedded Windows batch script · **M**
> 🧭 **Простыми словами:** Один файл отвечает сразу за слишком многое: и за модели ИИ, и за их установку, и за скрипты запуска самого приложения. Из-за этого любая мелкая правка в одном (скажем, в кнопке запуска) рискует случайно задеть другое (загрузку моделей). Это не баг прямо сейчас, а риск на будущее: «починил одно — сломал соседнее». Разделение делает доработки безопаснее.
- **Evidence:** [model_manager.py](../backend/app/services/model_manager.py) (1830 LOC) holds: model registry/DB, pip+PyTorch install (incl. CUDA channel logic), HF Windows-symlink workarounds, **and** launcher generation. [`_windows_app_launcher_contents()`](../backend/app/services/model_manager.py:57) is a **387-line** `.bat` string literal; sibling helpers `_promote_platform_launcher`, `_move_installers_to_backend`, `_sync_platform_launcher_layout`, `_ensure_root_windows_shortcut`, `restore_default_launcher_layout_after_reset` manage installer file layout — none of which is "model management".
- **Fix (in order):** (a) extract all launcher/installer-layout code into `services/launcher_layout.py`; (b) extract pip/torch install into `services/pip_installer.py` (it has no dependency on the registry — clean cut). Leaves `model_manager.py` focused on the registry + downloads.

### ☐ P2-2 · `projects.py` is one blueprint over 7 domains, with duplicated upload/link logic · **M**
> 🧭 **Простыми словами:** Самый часто изменяемый файл свалил в кучу 7 разных тем (проекты, записи, теги, экспорт и т.д.). В нём тяжело ориентироваться — правки идут медленнее и с большим числом ошибок. Вдобавок логика загрузки файла скопирована дважды: поправят в одном месте, забудут во втором — и одинаковые на вид действия начнут вести себя по-разному. Это про скорость и надёжность будущих доработок.
- **Evidence:** [projects.py](../backend/app/api/projects.py) (1439 LOC, 33 route fns) spans project CRUD, recordings, segments, tags, annotations, export, transfer, attachments. [upload_recording()](../backend/app/api/projects.py:587) and [link_recording()](../backend/app/api/projects.py:652) share ~30 lines of recording-row creation + enqueue.
- **Fix:** extract `_create_recording_row(...)` helper first (S, isolated win), then split into sub-blueprints (`recordings`, `segments`, `annotations`, `attachments`, `export`) under a shared url-prefix.

### ☐ P2-3 · `transcription.py` is a 2349-line module with a god-class · **L**
> 🧭 **Простыми словами:** Главный файл движка расшифровки — огромный и делает всё сразу. Любая доработка в нём рискованна и медленна, потому что легко случайно задеть что-то ещё. Разбиение на части облегчит развитие (например, добавление новых языков или движков распознавания) без страха всё уронить. Срочности нет, но это инвестиция в будущее.
- **Evidence:** [transcription.py](../backend/app/services/transcription.py) — import-time monkey-patches for `torch`/`torchaudio`/`hf_hub`, a `TranscriptionService` singleton (~40 methods, two engines WhisperX+MLX, three diarization variants), plus worker/watchdog/cancellation. `transcribe()` ([:1885](../backend/app/services/transcription.py:1885)) is ~240 lines.
- **Fix:** convert to a package: `transcription/patches.py`, `transcription/audio_utils.py`, `transcription/service.py`, `transcription/worker.py`. The class itself resists splitting (shared model state) — do the cheap module separation first.

### ☐ P2-4 · Frontend: 5 standalone templates, no inheritance, ~380 duplicated lines · **M**
> 🧭 **Простыми словами:** Оформление (тема, шрифты, цвета) скопировано на каждой из 5 страниц по отдельности. Если вы захотите, например, поправить тёмную тему — менять придётся в 5 местах, и легко забыть одно, отчего страницы начнут выглядеть по-разному. Одна страница уже настроена «наоборот» относительно остальных. Это про единый внешний вид и простоту изменения оформления.
- **Evidence:** 5 `*.html`, **zero** use `{% extends %}`. Theme-init JS, `@font-face`, and CSS design tokens are copy-pasted across all of them; [recording.html:166](../backend/templates/recording.html:166) defines its theme with inverted `:root` defaults vs the others.
- **Fix:** normalize `recording.html` theme direction (S, prerequisite) → create `base.html` with `{% block %}`s → extract shared tokens/`@font-face` to `static/css/tokens.css` → unify the `api()` fetch wrapper into `static/js/api.js`.

---

## P3 — Consistency, tooling, hygiene, docs

### ☐ P3-1 · No linter or type checker · **S**
> 🧭 **Простыми словами:** Нет автоматического «корректора», который ловит опечатки и типичные ошибки в коде ещё до того, как они дойдут до вас. Это дешёвая страховка качества — компьютер проверяет код за разработчика.
- **Evidence:** [.pre-commit-config.yaml](../.pre-commit-config.yaml) has a single `mojibake-check` hook — no `ruff`, `mypy`, `flake8`, or `black`; no `pyproject.toml`. Type annotations are sparse in the service layer.
- **Fix:** add `ruff` (lint + format) as a pre-commit hook and a minimal config; optionally `mypy` in non-strict mode on the API layer.

### ☐ P3-2 · Hardcoded ports/URLs (5000, 5001) scattered across the codebase · **S–M**
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
- `AGENTS.md` exists in **both** the repo root and `Documentation/` with **divergent** content — pick one source of truth, make the other a pointer.
- Design-audit tooling is **triplicated and divergent**: [backend/design-audit.js](../backend/design-audit.js) and [backend/tools/design-audit.js](../backend/tools/design-audit.js) differ (different md5), plus `tools/design_audit.py`. Keep one, delete the rest.
- `backend-decomposition-audit.md` is stale (wrong line numbers; references removed functions). Update or delete — this file now covers it.

### ☐ P3-6 · Hardcoded `SECRET_KEY` fallback · **S**
> 🧭 **Простыми словами:** Ключ, которым приложение подписывает сессии в браузере, сейчас одинаковый «заводской» у всех установок. Для локального приложения без входа по паролю риск низкий, но правильнее генерировать уникальный ключ на вашем компьютере при первом запуске.
- **Evidence:** [config.py:8](../backend/app/config.py:8) → `'port-dev-key-change-in-prod'`. Low risk (localhost, no auth) but signs session cookies.
- **Fix:** generate a random key on first run and persist it in `Setting`.

### ☐ P3-7 · Launch scripts can't recover a broken venv · **S**
> 🧭 **Простыми словами:** Если установка приложения один раз прервалась на полпути (например, пропал интернет), при следующем запуске оно может решить, что всё уже установлено, и работать «наполовину сломанным» — без понятной причины. Исправление заставит его в таком случае доустановиться, а не делать вид, что всё в порядке.
- **Evidence:** both installers skip setup if `.venv/` exists; a pip failure mid-install leaves a present-but-broken venv that never retries.
- **Fix:** write a `.venv/.pine-setup-complete` sentinel only after a fully successful install; re-run setup if it's missing.

### ☐ P3-8 · Lock file carries stray dev tools · **S**
> 🧭 **Простыми словами:** В списке зафиксированных зависимостей затесались лишние инструменты разработчика, не нужные для работы приложения. Не вредно — просто лишний балласт, который стоит убрать для чистоты.
- **Evidence:** `requirements-lock.txt` includes `twine`, `readme_renderer` — packaging/publish tools unrelated to running PINE.
- **Fix:** regenerate the lock from a clean post-onboarding venv.

---

## P4 — Deferred / known gaps

> 🧭 **Простыми словами:** Это известные ограничения «пока не делаем» — не поломки, а отсутствующие удобства или защиты, которые можно добавить позже. Перечислены, чтобы о них помнить, но они не мешают повседневной работе.

Carried from `TODO.md` / `Documentation/CLAUDE.md`; intentionally out of scope for the fix pass:
- **No formal migrations** — startup `ALTER TABLE ADD COLUMN` loop works but can't rename/drop/retype. Evaluate Alembic only when a non-additive change is needed.
- **No download cancellation**, **no cross-transcript search** — feature gaps, not defects.
- **No JSON-schema validation** on transcript/annotation files — a hand-edited file propagates silently. Defensive, low urgency.
- **Watchdog can't kill a hung worker thread** — CPython limitation; document the GIL-dependent assumption rather than fix.

---

## Appendix A — Resolved since the 2026-03-28 audit

For an honest record (these were real then, verified fixed now — do **not** re-file them):
- ✅ `subprocess.run` timeouts — previously absent, now present on essentially all calls (lone exception tracked as **P1-5**).
- ✅ `_safe_emit()` duplication — now a single definition ([model_manager.py:635](../backend/app/services/model_manager.py:635)).
- ✅ Migration code — refactored into a data-driven `ADD COLUMN` loop with `log.info`/integrity-check logging (the old "migrations are invisible" finding no longer applies).

## Appendix B — Why the prior audit was retired

Both 2026-03-28 documents drifted from the code within ~10 weeks: line numbers were off by hundreds (e.g. `transcribe()` listed at 1398, actually 1885), and they described functions that were since removed (`_build_vad_chunks`, `_merge_speech_intervals`, `_transcribe_chunked_mlx_vad` — 0 definitions today). **Lesson:** keep audits claim-light and re-verify on each pass; prefer stable IDs + file:line over prose that rots.
