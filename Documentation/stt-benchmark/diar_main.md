# main — diarization configurations

Expected speakers: 2 (two-person interview).

| config | spk | ok | turns | <1s | switches | agree% | sec | RTF | VRAM |
|---|---|---|---|---|---|---|---|---|---|
| minmax2-4__step0.2 | 2 | ✓ | 340 | 121 | 92 | 98.4 | 100.15 | 0.083 | 1628 |
| minmax2-4__step0.1 | 4 | ✗ | 346 | 126 | 100 | 98.4 | 192.82 | 0.161 | 1628 |

`agree%` = mean timeline agreement with the other configs (labels matched optimally).
`<1s` = sub-second turns, the usual sign of boundary jitter.
