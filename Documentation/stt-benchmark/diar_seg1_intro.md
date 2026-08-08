# seg1_intro — diarization configurations

Expected speakers: 2 (two-person interview).

| config | spk | ok | turns | <1s | switches | agree% | sec | RTF | VRAM |
|---|---|---|---|---|---|---|---|---|---|
| gpu__num2__step0.2 | 2 | ✓ | 121 | 41 | 47 | 99.9 | 33.91 | 0.081 | 1629 |
| gpu__minmax2-4__step0.2 | 2 | ✓ | 121 | 41 | 47 | 99.9 | 34.83 | 0.083 | 1629 |
| cpu__num2__step0.2 | 2 | ✓ | 121 | 41 | 47 | 99.9 | 585.75 | 1.395 | — |
| gpu__num2__step0.1 | 2 | ✓ | 121 | 42 | 47 | 99.8 | 68.78 | 0.164 | 1629 |

`agree%` = mean timeline agreement with the other configs (labels matched optimally).
`<1s` = sub-second turns, the usual sign of boundary jitter.
