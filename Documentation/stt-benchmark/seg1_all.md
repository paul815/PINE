# seg1_intro — reference-free comparison

Consensus from 5 models (canary-1b-v2, parakeet-tdt-0.6b-v3, whisper-large-v3__cuda-fp16, whisper-large-v3__cuda-int8, whisper-turbo__cuda-fp16), 1061 words.

| model | dWER% | del% | ins% | sub% | rep% | RTF | sec | peak VRAM |
|---|---|---|---|---|---|---|---|---|
| whisper-turbo__cuda-fp16· | 1.32 | 0.75 | 0.47 | 0.09 | 0.0 | 0.017 | 7.0 | 3934 |
| whisper-large-v3__cuda-fp16· | 1.89 | 0.0 | 1.6 | 0.28 | 0.0 | 0.078 | 32.7 | 6961 |
| whisper-large-v3__cuda-int8· | 2.92 | 0.0 | 2.73 | 0.19 | 0.0 | 0.066 | 27.8 | 5393 |
| parakeet-tdt-0.6b-v3· | 3.3 | 2.36 | 0.75 | 0.19 | 0.0 | 0.128 | 53.7 | 8899 |
| canary-1b-v2· | 4.71 | 3.58 | 0.57 | 0.57 | 0.0 | 0.872 | 364.1 | 9816 |

`dWER%` = divergence from the majority-vote consensus, not true WER.
`del%` = consensus words the model lost; `ins%` = words it added.
`rep%` = words inside a looping 4-gram (hallucination).
`·` marks models that voted in the consensus.
