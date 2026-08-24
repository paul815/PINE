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

### 2.1 torchvision — оказался нужен (первоначальный вывод был неверен)
Прямых `import torchvision` в коде PINE нет, и по этому признаку он выглядел лишним.
Но `whisperx 3.8.6` объявляет `torchvision~=0.23.0` жёсткой зависимостью (проверено
по метаданным PyPI), а фазы 4-5 в `install_pip_packages()` ставят whisperx через
`--no-deps` и добирают его зависимости вручную — то есть torchvision там стоит
намеренно, а не по недосмотру. Убирать нельзя.

Что при этом было исправлено:
* Комментарий фазы 3 объяснял torchvision через `transformers` — неверно:
  у transformers он только в extras `vision`/`all`/`dev`. Теперь комментарий
  ссылается на реальный пин whisperx.
* `_torch_companion_channels_aligned()` сравнивал канал колеса torch **только**
  с torchvision и вовсе не смотрел на torchaudio — при том что импортирует PINE
  именно torchaudio. CPU-torchaudio рядом с CUDA-torch проходил проверку молча
  и падал позже undefined-symbol'ом. Теперь проверяются оба спутника.

### 2.2 pyannoteai-sdk + opentelemetry (13 пакетов) — мёртвый груз
`pyannote-audio 4.x` тянет облачный SDK: `pyannoteai-sdk`, 8× `opentelemetry-*`,
`grpcio`, `protobuf`, `googleapis-common-protos`. В коде не используется, телеметрия
уже заглушена в `app/__init__.py:21-34`. Убрать через pip нельзя (объявлено
зависимостью pyannote-audio), но стоит зафиксировать в документации как известный
шум — для локального приватного приложения это ~25 МБ и gRPC-стек ни за чем.

### 2.3 Оправданно (тянет pyannote/whisperx, убрать нельзя)
lightning, pytorch-lightning, torchmetrics, optuna+alembic+Mako+colorlog,
matplotlib-стек, scikit-learn, scipy, torch-audiomentations, torch_pitch_shift,
julius, asteroid-filterbanks, nltk, ctranslate2, faster-whisper, onnxruntime
(VAD внутри faster-whisper), transformers, tokenizers, av, torchcodec,
triton-windows.
`pandas` вдобавок используется напрямую — `ml_worker/diarize.py:126`.

### 2.4 Сделано правильно
`gliner` (только под PII), `mlx-whisper` (только Mac) — в `MODEL_OPTIONAL_PACKAGES`,
а не в обязательных: установка, которая этих моделей не использует, за них не платит.

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
Обязательные: STT под платформу + pyannote community-1 + wespeaker.
Опциональные: gliner-pii.

### 7.1 pyannote/segmentation-3.0 — убран (на проверке)
Кэшированный конфиг пайплайна на диске
(`models/pyannote_cache/models--pyannote--speaker-diarization-community-1/.../config.yaml`)
показывает, что community-1 адресует веса внутрь собственного репозитория:

    segmentation: $model/segmentation
    embedding:    $model/embedding
    plda:         $model/plda

Отдельные репозитории `pyannote/segmentation-3.0` и
`pyannote/wespeaker-voxceleb-resnet34-LM` в конфиге не упоминаются, и в
`models/pyannote_cache` на рабочей машине лежит только community-1 — при том
что диаризация работает. Отката на пайплайн 3.1 в коде нет:
`DEFAULT_DIARIZATION_MODEL` в `ml_worker/diarize.py:34` — всегда community-1.

Удалено: запись в `MODEL_REGISTRY`, шаг лицензии в онбординге (шагов стало 3),
`pyannote-segmentation` из `downloadModelIds`. Экономия — один gated-репозиторий
в онбординге и один пункт лицензии, который пользователю больше не нужно принимать.

Заодно `init_model_registry()` теперь удаляет строки `ml_models` с неизвестными
registry id. Без этого запись о снятой модели навсегда оставалась бы в списках
онбординга и настроек на всех уже существующих установках — обе страницы читают
таблицу напрямую (`MLModel.query.all()`).

**Статус: ждёт проверки реальной диаризацией.** Если сломается — вернуть запись
в `MODEL_REGISTRY`, шаг лицензии и id в `downloadModelIds`.

### 7.2 wespeaker — тот же случай, не тронут
`pyannote/wespeaker-voxceleb-resnet34-LM` по тем же признакам выглядит таким же
мёртвым: конфиг берёт `embedding` из собственного репозитория. Оставлен намеренно,
чтобы проверка 7.1 дала чистый сигнал. Снимать следующим шагом, отдельно.
