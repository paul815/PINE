# ONNX port vs NeMo reference — same weights, different runtime

Reference: `main__parakeet-tdt-0.6b-v3.json` — NeMo, the run the model choice rests on.
Hypothesis: `main__parakeet-onnx.json` — onnx-asr.

## Cost

| | NeMo | onnx-asr |
|---|---|---|
| device | cuda | cuda |
| precision | float32 | fp32 |
| load, s | 24.05 | 3.07 |
| transcribe, s | 64.03 | 43.33 |
| peak VRAM, MB | 10051 | 3863 |
| segments | 180 | 200 |
| words | 3093 | 3108 |

onnx-asr is **0.68x** the NeMo transcribe time.

## Does it say the same words?

- divergence from the NeMo run: **2.44%** of 2997 reference words
- del: 0.57%
- ins: 0.87%
- sub: 1.00%
- repetition (looping 4-grams): NeMo 0.00%, onnx 0.00%

## Do the words land at the same time?

- matched words compared: 2950
- median drift: **0.036 s**
- p90 drift: 0.132 s
- worst: 1.036 s
- drifting past 0.5 s: 1 (0.03%)

A diarization turn is rarely shorter than ~1 s, so drift under 0.5 s leaves speaker attribution intact; the share above it is the share of words that risk landing on the wrong speaker.

- non-monotonic word starts: NeMo 0, onnx 0
- timestamp mode reported by the runner: ['relative']
