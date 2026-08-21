# seg2_mid — diarization configurations

Expected speakers: 2 (two-person interview).

| config | spk | ok | turns | <1s | switches | agree% | sec | RTF | VRAM |
|---|---|---|---|---|---|---|---|---|---|
| gpu__num2__step0.2 | 2 | ✓ | 118 | 43 | 27 | 99.8 | 33.63 | 0.08 | 1629 |
| gpu__minmax2-4__step0.2 | 2 | ✓ | 118 | 43 | 27 | 99.8 | 35.23 | 0.084 | 1629 |
| cpu__num2__step0.2 | 2 | ✓ | 118 | 43 | 27 | 99.8 | 582.52 | 1.387 | — |
| gpu__num2__step0.1 | 2 | ✓ | 104 | 32 | 9 | 99.3 | 69.83 | 0.166 | 1629 |

`agree%` = mean timeline agreement with the other configs (labels matched optimally).
`<1s` = sub-second turns, the usual sign of boundary jitter.
