# Аудит зависимостей PINE — 2026-08-21

Что реально ставится на машину пользователя: базовый слой (`backend/requirements.txt`,
7 пакетов, ставят лаунчеры), ML-слой (`pip_installer.py`, ставится в онбординге),
опциональные пакеты под конкретные модели, и модели с HF.

## 1. Базовый слой — всё нужно

| Пакет | Где используется |
|---|---|
| flask, flask-sqlalchemy | ядро приложения |
| flask-socketio | `extensions.py:48`, прогресс установки/транскрипции |
| flask-cors | `app/__init__.py:317` — страница и supervisor на разных портах |
| huggingface-hub | `model_manager.py` — скачивание моделей, проверка токена |
| psutil | мониторинг процессов |
| static-ffmpeg | `services/ffmpeg_setup.py`, ~190 МБ качается лениво |

## 2. ML-слой — что можно убрать

### 2.1 torchvision — лишний (главная находка)
Ни одного `import torchvision` в коде PINE. Он попадает в venv только потому, что
`_run_torchruntime_install()` (`pip_installer.py:225`) вызывает `torchruntime install`
без списка пакетов, а torchruntime по умолчанию ставит torch+torchvision+torchaudio.

Цена: ~200 строк машинерии выравнивания каналов колёс —
`_realign_torchaudio_torchvision`, `repair_torch_companion_wheels_if_needed`,
`_torch_companion_channels_aligned`, `_companion_pip_extra_args`,
плюс ветка ошибки в `job_runner.py:475`, плюс целый класс отказов
`operator torchvision::nms does not exist`.

Предлагаемая правка: `torchruntime install torch torchaudio`.
Проверить перед коммитом: `transformers` подтягивает torchvision лениво только для
image-моделей — путь WhisperX его не трогает, но нужен прогон транскрипции.

### 2.2 pyannoteai-sdk + opentelemetry (13 пакетов) — мёртвый груз
`pyannote-audio 4.x` тянет облачный SDK: `pyannoteai-sdk`, 8× `opentelemetry-*`,
`grpcio`, `protobuf`, `googleapis-common-protos`. В коде не используется, телеметрия
уже заглушена в `app/__init__.py:21-34`. Убрать через pip нельзя (объявлено
зависимостью pyannote-audio), но стоит зафиксировать в документации как известный
шум — для локального приватного приложения это ~25 МБ и gRPC-стек ни за чем.

### 2.3 Оправданно (тянет pyannote/whisperx, убрать нельзя)
lightning, pytorch-lightning, torchmetrics, optuna+alembic+Mako+colorlog,
matplotlib-стек, scikit-learn, scipy, torch-audiomentations, torch_pitch_shift,
julius, asteroid-filterbanks, nltk, ctranslate2, faster-whisper, transformers,
tokenizers, av, torchcodec, triton-windows.
`pandas` вдобавок используется напрямую — `ml_worker/diarize.py:126`.

### 2.4 Сделано правильно
`onnxruntime`/`onnx-asr` (только под Parakeet, намеренно CPU-сборка — чтобы не держать
два CUDA-рантайма в одном процессе), `gliner` (только под PII), `mlx-whisper` (только Mac).
Всё это в `MODEL_OPTIONAL_PACKAGES`, а не в обязательных.

## 3. requirements-lock.txt загрязнён (чинится за минуту)
В lock попал издательский тулчейн с машины разработчика — он никогда не должен
оказаться у пользователя:

`twine`, `keyring`, `readme_renderer`, `docutils`, `nh3`, `rich`, `Pygments`,
`markdown-it-py`, `mdurl`, `id`, `jaraco.classes`, `jaraco.context`,
`jaraco.functools`, `more-itertools`, `requests-toolbelt`, `rfc3986`,
`pywin32-ctypes` — 17 пакетов.

Плюс `pluggy` и `iniconfig` — остатки pytest, при том что самого pytest в списке нет.

Lock лаунчерами не используется (только как справка), но справка сейчас врёт.
Перегенерировать из чистого venv.

## 4. Устаревшие комментарии в requirements.txt
* Шапка перечисляет `speechbrain` среди тяжёлых пакетов — его в дереве больше нет
  (pyannote 4 использует собственный wespeaker).
* `gliner` там же назван обязательным ML-пакетом — он давно опционален.
* Ссылка на `requirements-dev.txt` — такого файла в репозитории нет.

## 5. Node (backend/package.json)
`playwright` + `axe-core` под два dev-скрипта (`tools/contrast-audit.js`,
`tools/design-audit.js`). Установщиков не касается, node_modules не в репозитории.
Но лежат в `dependencies`, а не в `devDependencies` — стоит переставить:
`npm install` у playwright тянет ~500 МБ браузеров.

## 6. Фронтенд
Полностью вендорится (`app/static/css|js|fonts`), ни одной ссылки на CDN. Хорошо.

## 7. Модели
Обязательные: STT под платформу + 3 pyannote (community-1, segmentation-3.0,
wespeaker). Опциональные: silero-vad, gliner-pii, parakeet. Состав разумный.
Проверить отдельно: нужен ли `pyannote/segmentation-3.0` отдельной записью, если
пайплайн community-1 несёт собственный сегментатор.
