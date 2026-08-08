# seg1_intro — reference-free comparison

Consensus from 3 models (canary-1b-v2, parakeet-tdt-0.6b-v3, whisper-large-v3__cuda-fp16), 1058 words.

| model | dWER% | del% | ins% | sub% | rep% | RTF | sec | peak VRAM |
|---|---|---|---|---|---|---|---|---|
| whisper-turbo__cuda-fp16 | 1.98 | 0.95 | 0.95 | 0.09 | 0.0 | 0.017 | 7.0 | 3934 |
| whisper-large-v3__cuda-fp16· | 2.17 | 0.0 | 1.89 | 0.28 | 0.0 | 0.078 | 32.7 | 6961 |
| parakeet-tdt-0.6b-v3· | 2.65 | 1.89 | 0.57 | 0.19 | 0.0 | 0.128 | 53.7 | 8899 |
| whisper-large-v3__cuda-int8 | 3.59 | 0.19 | 3.21 | 0.19 | 0.0 | 0.066 | 27.8 | 5393 |
| canary-1b-v2· | 4.06 | 3.12 | 0.38 | 0.57 | 0.0 | 0.872 | 364.1 | 9816 |
| canary-qwen-2.5b | 90.17 | 89.98 | 0.19 | 0.0 | 0.0 | 2581.3 | 2581.3 | 16270 |

`dWER%` = divergence from the majority-vote consensus, not true WER.
`del%` = consensus words the model lost; `ins%` = words it added.
`rep%` = words inside a looping 4-gram (hallucination).
`·` marks models that voted in the consensus.
