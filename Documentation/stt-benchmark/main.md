# main — reference-free comparison

Consensus from 4 models (canary-1b-v2, canary-qwen-2.5b, parakeet-tdt-0.6b-v3, whisper-large-v3__fp16), 3061 words.

| model | dWER% | del% | ins% | sub% | rep% | RTF | sec | peak VRAM |
|---|---|---|---|---|---|---|---|---|
| whisper-turbo__fp16 | 1.83 | 0.1 | 1.11 | 0.62 | 0.0 | 0.016 | 19.2 | 3336 |
| parakeet-tdt-0.6b-v3· | 1.86 | 0.26 | 1.31 | 0.29 | 0.0 | 0.053 | 64.0 | 10051 |
| canary-qwen-2.5b· | 2.09 | 0.65 | 1.05 | 0.39 | 0.0 | 0.098 | 118.0 | 10005 |
| whisper-large-v3__fp16· | 2.42 | 0.0 | 2.03 | 0.39 | 0.0 | 0.099 | 118.3 | 6484 |
| canary-1b-v2· | 2.68 | 1.67 | 0.72 | 0.29 | 0.0 | 0.307 | 368.0 | 14135 |
| whisper-large-v3__int8 | 6.11 | 0.95 | 4.48 | 0.69 | 0.0 | 0.105 | 126.2 | 4814 |

`dWER%` = divergence from the majority-vote consensus, not true WER.
`del%` = consensus words the model lost; `ins%` = words it added.
`rep%` = words inside a looping 4-gram (hallucination).
`·` marks models that voted in the consensus.
