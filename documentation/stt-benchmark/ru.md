# ru — reference-free comparison

Consensus from 2 models (parakeet-tdt-0.6b-v3, whisper-large-v3__fp16), 2567 words.

| model | dWER% | del% | ins% | sub% | rep% | RTF | sec | peak VRAM |
|---|---|---|---|---|---|---|---|---|
| parakeet-tdt-0.6b-v3· | 6.93 | 0.0 | 6.93 | 0.0 | 0.0 | 0.01 | 12.0 | 10051 |
| whisper-large-v3__fp16· | 7.67 | 0.0 | 7.67 | 0.0 | 0.0 | 0.09 | 107.4 | 7197 |
| whisper-turbo__fp16 | 8.38 | 0.12 | 7.87 | 0.39 | 0.0 | 0.035 | 41.7 | 4294 |
| whisper-large-v3__int8 | 9.47 | 0.39 | 8.73 | 0.35 | 0.0 | 0.096 | 115.2 | 5658 |

`dWER%` = divergence from the majority-vote consensus, not true WER.
`del%` = consensus words the model lost; `ins%` = words it added.
`rep%` = words inside a looping 4-gram (hallucination).
`·` marks models that voted in the consensus.
