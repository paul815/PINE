# seg1_intro — reference-free comparison

Consensus built from 5 models, 1061 words.

| model | dWER% | rep% | missed% | RTF | sec | peak VRAM |
|---|---|---|---|---|---|---|
| whisper-turbo__cuda-fp16 | 1.32 | 0.0 | 7.5 | 0.017 | 7.0 | 3934 |
| whisper-large-v3__cuda-fp16 | 1.89 | 0.0 | 3.78 | 0.078 | 32.7 | 6961 |
| whisper-large-v3__cuda-int8 | 2.92 | 0.0 | 4.1 | 0.066 | 27.8 | 5393 |
| parakeet-tdt-0.6b-v3 | 3.3 | 0.0 | 8.27 | 0.128 | 53.7 | 8899 |
| canary-1b-v2 | 4.71 | 0.0 | 23.71 | 0.872 | 364.1 | 9816 |

`dWER%` = divergence from the majority-vote consensus, not true WER.
`rep%` = words inside a looping 4-gram (hallucination).
`missed%` = VAD speech time with no words (deletion).
