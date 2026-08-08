# seg2_mid — reference-free comparison

Consensus built from 3 models, 991 words.

| model | dWER% | rep% | missed% | RTF | sec | peak VRAM |
|---|---|---|---|---|---|---|
| whisper-turbo__cuda-fp16 | 2.93 | 0.0 | 5.7 | 0.016 | 6.7 | 3840 |
| whisper-large-v3__cuda-int8 | 4.84 | 0.0 | 7.59 | 0.136 | 57.0 | 5309 |
| whisper-large-v3__cuda-fp16 | 6.86 | 0.0 | 9.76 | 0.157 | 65.8 | 7087 |

`dWER%` = divergence from the majority-vote consensus, not true WER.
`rep%` = words inside a looping 4-gram (hallucination).
`missed%` = VAD speech time with no words (deletion).
