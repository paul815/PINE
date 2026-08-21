# VAD sweep — ONNX port scored against the NeMo run

Reference: `main__parakeet-tdt-0.6b-v3.json`, 2997 words.

| config | div% | del | ins | sub | median drift | p90 | >0.5s | sec | VRAM | RSS | segs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| vad-xlong | **1.50** | 0.20 | 0.67 | 0.63 | 0.024s | 0.072s | 2 | 41.05 | 3874 | 1233 | 93 |
| vad-xxlong | **1.57** | 0.30 | 0.67 | 0.60 | 0.024s | 0.072s | 3 | 38.68 | 3818 | 1232 | 91 |
| vad-long-pad | **1.77** | 0.23 | 0.80 | 0.73 | 0.024s | 0.072s | 2 | 41.2 | 3885 | None | 101 |
| vad-long | **2.30** | 0.53 | 0.83 | 0.93 | 0.036s | 0.132s | 1 | 47.34 | 3804 | None | 194 |
| vad-pad | **2.37** | 0.33 | 1.07 | 0.97 | 0.036s | 0.076s | 2 | 41.33 | 3830 | None | 112 |
| vad-base | **2.44** | 0.57 | 0.87 | 1.00 | 0.036s | 0.132s | 1 | 48.8 | 3823 | None | 200 |
| vad-tight | **2.50** | 0.57 | 0.87 | 1.07 | 0.036s | 0.140s | 1 | 52.94 | 3707 | None | 240 |

Lowest divergence: **vad-xlong** at 1.50%, median word drift 0.024s, 2 of 2972 words past 0.5s.

`div%` is divergence from the NeMo run on the same weights — the cost of the port, not of the model. Drift is the gap between word start times, which is what speaker attribution rides on.