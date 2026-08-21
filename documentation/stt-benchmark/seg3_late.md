# seg3_late — reference-free comparison

Consensus built from 3 models, 1368 words.

| model | dWER% | rep% | missed% | RTF | sec | peak VRAM |
|---|---|---|---|---|---|---|
| whisper-large-v3__cuda-fp16 | 3.65 | 0.0 | 7.18 | 0.104 | 43.9 | 6877 |
| whisper-turbo__cuda-fp16 | 3.65 | 0.0 | 1.86 | 0.017 | 7.3 | 3836 |
| whisper-large-v3__cuda-int8 | 9.8 | 0.0 | 5.81 | 0.145 | 60.9 | 5298 |

`dWER%` = divergence from the majority-vote consensus, not true WER.
`rep%` = words inside a looping 4-gram (hallucination).
`missed%` = VAD speech time with no words (deletion).
