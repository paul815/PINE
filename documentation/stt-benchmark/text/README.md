# Расшифровки — `main`

Один и тот же 20-минутный кусок, расшифрованный шестью моделями.
Сгенерировано `dump_text.py`; править руками бессмысленно, перезатрётся.

| модель | слов | сегментов | пословные тайминги | текст | для diff |
|---|---|---|---|---|---|
| whisper-large-v3__int8 | 3097 | 255 | 3108 | [whisper-large-v3__int8.md](whisper-large-v3__int8.md) | [flat](whisper-large-v3__int8.flat.txt) |
| parakeet-tdt-0.6b-v3 | 3093 | 180 | 3093 | [parakeet-tdt-0.6b-v3.md](parakeet-tdt-0.6b-v3.md) | [flat](parakeet-tdt-0.6b-v3.flat.txt) |
| whisper-turbo__fp16 | 3087 | 162 | 3089 | [whisper-turbo__fp16.md](whisper-turbo__fp16.md) | [flat](whisper-turbo__fp16.flat.txt) |
| whisper-large-v3__fp16 | 3066 | 225 | 3071 | [whisper-large-v3__fp16.md](whisper-large-v3__fp16.md) | [flat](whisper-large-v3__fp16.flat.txt) |
| canary-qwen-2.5b | 2996 | 40 | **нет** | [canary-qwen-2.5b.md](canary-qwen-2.5b.md) | [flat](canary-qwen-2.5b.flat.txt) |
| canary-1b-v2 | 2944 | 165 | 2950 | [canary-1b-v2.md](canary-1b-v2.md) | [flat](canary-1b-v2.flat.txt) |

Разброс между самой многословной и самой скупой моделью — 153 слов на 20 минутах. Это и есть та разница, которую меряет `reports/main.md`.

## Сравнить две модели

Из каталога с этим файлом (путь намеренно относительный: отчёты переезжают, а команда должна оставаться рабочей):

```bash
diff -u whisper-turbo__fp16.flat.txt whisper-large-v3__int8.flat.txt
```

Разница в числе слов сама по себе ничего не говорит о качестве: verbatim-модель пишет «uh» и «like» и выглядит многословной, а Whisper их молча выбрасывает.
