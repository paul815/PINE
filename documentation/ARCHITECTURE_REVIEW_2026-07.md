# PINE — архитектурное ревью (июль 2026)

Обзор текущей архитектуры и рекомендации с учётом развития STT-стека с момента
создания приложения (первый коммит — 2026-04-09; выбранный ML-стек — поколения
2025 года).

> **Сверено с кодом 2026-08-20.** Документ оставлен как есть — это снимок на
> июль. Что изменилось с тех пор по таблице рисков раздела 5:
>
> - **Риск 1 (ML внутри процесса Flask) — снят.** ML вынесен в отдельный процесс
>   `backend/ml_worker/`; в `app/` не импортируются ни torch, ни whisperx, ни
>   pyannote. Заодно закрыт риск 4: `transcription.py` разбит на
>   `app/services/transcription/` + `ml_worker/`.
> - **Риск 2 (нет бенчмарка качества) — частично.** Появился
>   `documentation/stt-benchmark/` с прогонами. Регрессионного прогона по
>   расписанию всё ещё нет.
> - **Риск 8 (гигиена рабочей копии) — снят.** `.gitignore` и `.gitattributes`
>   на месте, мусорные файлы вычищены.
> - **Риски 3, 5, 6, 7 — открыты:** нет `schema_version` в транскрипте, шаблоны
>   всё ещё ~12 000 строк с inline JS, чекпоинтов в конвейере нет, версии схемы
>   БД нет.
> - Версии библиотек в разделе 3 устарели ещё сильнее — сверяйте перед
>   апгрейдом, а не по этой таблице.

---

## 1. Резюме

Для трёх месяцев жизни приложение архитектурно зрелое: продуманная локальная
модель данных, супервизор процессов, onboarding с динамической установкой
ML-стека, PII-редакция, бэкапы, тесты. Главные точки роста — не «переписать»,
а **изолировать и измерять**:

1. **ML-конвейер живёт в одном процессе с веб-сервером** и обвешан
   монки-патчами совместимости — самый большой источник хрупкости.
2. **Нет измеримости качества**: решения о движках (история с Qwen3-ASR)
   принимаются без WER/DER-бенчмарка на собственных записях.
3. **Диаризация уже на актуальном поколении** (pyannote.audio 4.0.4 +
   community-1), но WhisperX отстаёт (3.8.1 против 3.8.6 со штатной
   поддержкой community-1), а по скорости экосистема ушла вперёд
   (turbo-модели, parakeet-mlx и системный SpeechAnalyzer на Mac).
4. **Монолиты**: `transcription.py` — 2 397 строк, `main.html` — 3 757 строк
   (шаблоны в сумме ~12 000 строк inline-JS/HTML).

---

## 2. Как устроено сейчас (as-is)

### Процессная модель
```
Launcher (.bat/.vbs/.lnk | .command/.app)
  └─ supervisor.py (порт 5001, token, lease/heartbeat от UI,
     авто-shutdown при потере lease; grace 45 c / 5 мин)
       └─ Flask backend run.py (порт 5000, SQLAlchemy + SocketIO)
            └─ Браузер (Jinja-шаблоны + inline JS, SocketIO-статусы)
```

### Данные
- **SQLite**: `projects`, `recordings`, `segments`, `settings`, `ml_models`;
  миграции — «слепые» `ALTER TABLE` в try/except (`_migrate_db`).
- **Файлы в папке проекта**: медиа, `*_transcript.json`,
  `*_annotations.json`, `project_tags.json` / `project_themes.json`
  (атомарная запись + per-file locks), генерируемый README, бэкапы.

### STT-конвейер (`services/transcription.py`)
- Очередь `queue.Queue` в памяти + один worker-поток + watchdog +
  `requeue_interrupted` при старте; cancel-флаги; статусы через SocketIO.
- Языковой probe первых ~30 с (порог 0.82 / margin 0.10) → при неуверенности
  блокирующий статус `awaiting_language` с выбором пользователя.
- Движки: **WhisperX** (CUDA float16, при малом VRAM/CPU — int8) и
  **mlx-whisper** (Mac). Выравнивание — wav2vec2 (per-language override,
  сейчас только `ru`). Диаризация — `whisperx.diarize.DiarizationPipeline`
  (kwargs подбираются через `inspect` под разные версии) +
  `assign_word_speakers`; ограничение спикеров per-recording (`num_speakers`).
- Спикеры захардкожены: `Moderator` + `Participant 1–5` (максимум 6).

### Модельный реестр (`services/model_manager.py`)
- STT: `whisperx-large-v3` (Systran/faster-whisper-large-v3),
  `mlx-whisper-large-v3` (mlx-community). **Turbo-варианты когда-то были** —
  сейчас их id нормализуются обратно в large-v3.
- Диаризация: `pyannote/speaker-diarization-community-1` (+
  `segmentation-3.0`, wespeaker-эмбеддинги) — т.е. модель community-1 уже
  используется, но поверх библиотеки pyannote.audio 3.3.
- `gliner-pii` (urchade/gliner_multi_pii-v1) — PII-редакция при экспорте.
- Тяжёлые пакеты ставятся динамически при onboarding'е; база пиннится
  (`requirements.txt` + `requirements-lock.txt`).

### Ключевые пины (requirements-lock.txt) и актуальные версии (июль 2026)
| Пакет | В проекте | Последняя | Разрыв |
|---|---|---|---|
| whisperx | 3.8.1 | 3.8.6 (2026-05-25) | 5 патч-релизов; в 3.8.6 community-1 стал штатным бэкендом диаризации |
| pyannote.audio | **4.0.4** | 4.0.7 (2026-06-30) | уже на 4.x; только патч-бампы |
| faster-whisper | 1.2.1 | 1.2.1 | актуален |
| mlx-whisper | (ставится на Mac) | 0.4.3 (2025-08-29) | проверить на Mac-машине |

Прочее: `ctranslate2==4.7.1`, `torch==2.8.0+cu128`, `torchaudio==2.8.0`,
`transformers==4.57.6`.

**Внимание:** комментарий в `transcription.py` («pyannote.audio 3.3 still
expects…») устарел — фактически стоит 4.0.4. Часть шимов, вероятно, уже мертва.

### Слой совместимости (симптом, не болезнь)
Три глобальных монки-патча в `transcription.py`:
- `torchaudio` API-шимы под pyannote 3.3;
- `hf_hub_download(use_auth_token→token)`;
- `torch.load → weights_only=False` (глобально, с env-переключателем).

Плюс `inspect`-адаптеры под сигнатуры разных версий WhisperX. Каждый апгрейд
зависимостей — минное поле; патч `weights_only=False` ослабляет и безопасность
всего процесса (сейчас это оправдано только тем, что модели локальные).

---

## 3. Сильные стороны (сохранить)

- Последовательная **privacy-first локальность**: телеметрия выключена,
  PII — локальным GLiNER, экспортные LLM-промпты вместо встроенного облака.
- Устойчивость: watchdog, requeue после падения, атомарные записи JSON,
  lease-модель супервизора, восстановление бэкапов.
- UX распознавания: подтверждение языка при низкой уверенности, выбор числа
  спикеров при загрузке — редкие в OSS-инструментах вещи.
- Пиннинг зависимостей + lock-файл, идемпотентные миграции, набор тестов.

---

## 4. Риски и долг (по убыванию важности)

| # | Риск | Следствие |
|---|------|-----------|
| 1 | ML в процессе Flask (GIL, VRAM, глобальные патчи) | Краш CUDA/OOM роняет весь бэкенд; нельзя перезапустить ML отдельно; патчи действуют на весь процесс |
| 2 | Нет бенчмарка качества | Смена движка/версии = спор мнений; регрессии не ловятся |
| 3 | Формат транскрипта без `schema_version` и метаданных | Блокирует эволюцию (confidence, перекрытия, другой движок) |
| 4 | `transcription.py` 2 397 строк — очередь+2 движка+align+diarize+прогресс | Дорогие изменения, высокая связность |
| 5 | Шаблоны ~12 000 строк с inline JS | Не тестируется, страшно трогать |
| 6 | Конвейер без чекпоинтов | Падение на 2-часовой записи → всё заново; смена `num_speakers` → полный повторный ASR |
| 7 | Миграции без версии схемы | Тихие расхождения схем у старых установок |
| 8 | Гигиена рабочей копии: удалены `.gitignore`/`.gitattributes`, мусор `pytest-api-quit-*`, `nul`, `__pycache__`, неигнорируемые `data/ logs/ models/ projects/ backups/` | Риск закоммитить мусор/данные |

---

## 5. Что изменилось в STT-ландшафте (к июлю 2026)

### ASR-модели
- **Whisper large-v3-turbo** — по-прежнему главный «дешёвый» апгрейд: ~8×
  быстрее large-v3 при +0.3–0.7 п.п. WER. Открытого «Whisper v4» нет;
  новинки OpenAI (gpt-4o-transcribe, GPT-Realtime-Whisper, май 2026) — только API.
- **NVIDIA Canary-Qwen 2.5B** — №1 открытого ASR-лидерборда (5.63% WER),
  но англоцентричен. **Parakeet TDT** — экстремальная скорость (RTFx ~2000),
  v3 покрывает 25 европейских языков, включая русский.
- **Voxtral Transcribe 2** (Mistral, февраль 2026, Apache 2.0) — 4B,
  нативный стриминг, 5.9% против 7.4% WER Whisper на FLEURS, но ~13 языков.
- **Qwen3-ASR** (январь 2026, 52 языка) — в топах, но в PINE уже пробовался и
  был осознанно откачен. Не возвращать без бенч-харнесса (см. P0-3).
- Whisper остаётся лучшим по языковому покрытию (99+) — выбор WhisperX как
  «центра» конвейера по-прежнему обоснован.

### Диаризация
- **pyannote.audio 4.0 + community-1**: заметно точнее 3.x и специально
  улучшена стыковка с STT-таймстампами (**exclusive diarization**). PINE уже
  на pyannote.audio 4.0.4 и уже использует модель community-1 — этот апгрейд
  фактически сделан; осталось убедиться, что exclusive-режим реально
  задействован при привязке слов к спикерам.
- **WhisperX ≥3.8.6** официально перешёл на community-1 (PINE на 3.8.1 делает
  это вручную через inspect-хаки — апгрейд до 3.8.6 уберёт их).
- **NVIDIA Sortformer** — end-to-end/стриминг, но деградирует при >4 спикеров;
  для интервью-сценария PINE (до 6) community-1 предпочтительнее.

### Mac (репозиторий буквально называется *Mac_Improvements*)
- **parakeet-mlx** — Parakeet на MLX: реалтайм, стриминг, sub-100ms на M3;
  на порядок быстрее mlx-whisper-large-v3.
- **Apple SpeechAnalyzer / SpeechTranscriber (macOS 26)** — системный
  on-device STT, очень быстрый и точный на европейских языках; доступен
  через небольшой Swift-хелпер.
- **FluidAudio** — Parakeet + диаризация на CoreML/ANE (энергоэффективно).

---

## 6. Рекомендации

### P0 — фундамент (не меняет качества, делает изменения дешёвыми)

1. **Вынести ML-конвейер в отдельный worker-процесс** (subprocess c
   JSON-протоколом по stdio или локальному сокету).
   Выигрыш: краш/OOM не роняет UI; перезапуск ML без рестарта приложения;
   монки-патчи (включая `weights_only=False`) заперты в дочернем процессе;
   watchdog из «лечим зависший поток» превращается в честный supervisor
   процесса. Это же — точка подключения альтернативных движков.
2. **Интерфейс `EngineAdapter`**: `transcribe(audio, lang, opts) → segments`
   в едином формате + декларация возможностей (word_timestamps, diarization,
   streaming). Первые адаптеры — текущие WhisperX и MLX.
3. **Бенч-харнесс** (`backend/tools/bench.py`): golden set из 3–5 реальных
   записей (ru/en, 2–6 спикеров) с эталонными транскриптами; метрики WER
   (jiwer) и DER (pyannote.metrics); результаты — в documentation.
   Прямой ответ на историю Qwen3-ASR: любые будущие споры о движках
   закрываются цифрами на *ваших* данных.
4. **Версионировать формат транскрипта**: `schema_version`, `engine`,
   `model_id`, per-segment `confidence`, флаг перекрытия, тайминги этапов.
   Писать тайминги этапов (asr/align/diarize) в БД — база для сравнения.
5. **Распил монолитов**: `transcription.py` → `queue.py`,
   `engines/whisperx.py`, `engines/mlx.py`, `align.py`, `diarize.py`,
   `progress.py`; JS из шаблонов → `static/js/*` (ES-модули, без фреймворка).

### P1 — качество и скорость на текущей архитектуре

6. **Апгрейд whisperx 3.8.1 → 3.8.6 (+ pyannote 4.0.4 → 4.0.7) и аудит шимов.**
   pyannote 4.x уже стоит (комментарии в коде про «3.3» устарели), поэтому
   реальная работа — поднять WhisperX до версии со штатным community-1,
   проверить использование exclusive diarization и удалить мёртвые
   монки-патчи/inspect-хаки. Обязателен прогон бенча (P0-3) до/после и
   пересборка lock-файла.
7. **Вернуть large-v3-turbo как пресет «Быстро»** (CT2 + mlx-community
   turbo) рядом с large-v3 «Качество». Turbo уже был в реестре и был убран —
   возвращать через бенч; для CPU-установок и Mac разница драматическая.
8. **Mac-производительность**: добавить **parakeet-mlx** как адаптер
   (en + 25 языков, вкл. ru; стриминг «бесплатно»); как углубление —
   FluidAudio (ANE) или системный SpeechAnalyzer через Swift-хелпер для
   macOS 26+. Всё — за `EngineAdapter`, выбор по бенчу.
9. **Антигаллюцинационная пост-обработка**: фильтр по `no_speech_prob`,
   детектор зацикленных повторов, отсечение сегментов вне VAD-окон.
   Дёшево и лечит типичную боль Whisper на тишине/музыке в интервью.
10. **Снять хардкод 6 спикеров** (`SPEAKER_LABELS`) — конфигурируемый список.

### P2 — новые возможности

11. **Live-черновик во время записи**: стриминговый адаптер
    (Mac — parakeet-mlx streaming; Windows — chunked faster-whisper или
    Voxtral realtime), финальный батч-проход заменяет черновик.
    SocketIO-инфраструктура прогресса уже готова.
12. **Чекпоинты этапов конвейера** (`asr.json → aligned.json → diar.rttm`):
    падение не обнуляет часы работы; «пере-диаризация с другим числом
    спикеров» перестаёт требовать повторного ASR.
13. **Опциональная локальная LLM-постобработка** (Ollama/llama.cpp):
    саммари проекта, автоимена спикеров по содержимому, подсказки тегов для
    двухуровневой системы тем. Экспортные промпты уже есть — это их
    оффлайн-продолжение, privacy-модель не ломается.

### P3 — гигиена

14. Восстановить `.gitignore`/`.gitattributes`; вычистить
    `pytest-api-quit-*`, `nul`, `__pycache__`; игнорировать
    `data/ logs/ models/ projects/ backups/`.
15. Миграции: `PRAGMA user_version` + упорядоченный список миграций
    (без тяжёлого Alembic).
16. Launcher/shortcut-логику из `api/settings.py` (сотни строк) —
    в `services/platform_integration.py`.

### Пресеты движков (целевая картина)

| Пресет | Windows/CUDA | Mac | Комментарий |
|---|---|---|---|
| Качество (дефолт) | WhisperX large-v3 + pyannote 4 | mlx-whisper large-v3 | как сейчас, после апгрейда библиотек |
| Быстро | WhisperX large-v3-turbo | mlx turbo / parakeet-mlx | ~5–8× быстрее, чуть выше WER |
| Live-черновик | chunked fw / Voxtral rt | parakeet-mlx streaming | P2 |
| English-max (опц.) | Canary-Qwen 2.5B | — | только англоязычные исследования |

---

## 7. Чего сознательно НЕ делать

- **Не возвращать Qwen3-ASR** — решение зафиксировано; пересмотр только
  через бенч-харнесс на своих данных.
- **Не добавлять облачные STT** (gpt-4o-transcribe, ElevenLabs и т.п.) как
  дефолт — ломает ключевую ценность локальности. Не строить in-app
  «блокировку сети» (уже решено: это ложное чувство безопасности).
- **Не переписывать стек** (React/Postgres/Celery): для локального
  single-user приложения Flask + SQLite + файлы — правильный размер.
  Слабое место не стек, а границы модулей.

---

## 8. Источники

- [Gladia — Best open-source STT models 2026](https://www.gladia.io/blog/best-open-source-speech-to-text-models)
- [Northflank — Open-source STT benchmarks 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Voxtral vs Whisper 2026 — WER/стриминг](https://weesperneonflow.ai/en/blog/2026-03-31-voxtral-whisper-open-source-speech-models-comparison-2026/)
- [pyannoteAI — Community-1](https://www.pyannote.ai/blog/community-1) · [HF: speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) · [pyannote-audio (GitHub)](https://github.com/pyannote/pyannote-audio)
- [AssemblyAI — диаризация: библиотеки 2026](https://www.assemblyai.com/blog/top-speaker-diarization-libraries-and-apis)
- [WhisperX (GitHub)](https://github.com/m-bain/whisperX) · [WhisperX 3.8.6 → community-1](https://www.qwe.edu.pl/ai-tools/whisperx-speaker-diarization-install/)
- [Whisper large-v3-turbo — бенчмарк на Mac](https://whispernotes.app/blog/introducing-whisper-large-v3-turbo) · [openai/whisper releases](https://github.com/openai/whisper/releases)
- [Dictato — Apple vs Whisper vs Parakeet vs Qwen3 на 13 000 записей](https://dicta.to/blog/speech-to-text-engine-comparison-mac-2026/)
- [parakeet-mlx (GitHub)](https://github.com/EliFuzz/parakeet-mlx) · [mlx-audio](https://github.com/Blaizzy/mlx-audio) · [MacParakeet](https://github.com/moona3k/macparakeet)
