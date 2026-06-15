# Diagnosis scratchpad

Run the practice simulator, read YOUR telemetry, and note what you find.
Fault classes to hunt: error_spike · latency_spike · cost_blowup · quality_drift ·
infinite_loop · tool_failure · pii_leak.

| symptom (from telemetry) | which requests | suspected cause | config fix? | wrapper fix? |
|---|---|---|---|---|
| `tool_error_rate=0.18` in config → 18% tool failures | tất cả request | `tool_error_rate` cố tình cao + `retry.enabled=false` | `tool_error_rate=0.0`, `retry.enabled=true` max 3 | retry loop trong `mitigate()` |
| `timeout_ms=0` → không có giới hạn thời gian | latency không giới hạn | `verbose_system=true` phình prompt + không có timeout | `timeout_ms=30000`, `verbose_system=false` | log `wall_ms` để phát hiện outlier |
| `max_completion_tokens=2000` + `model_price_tier=premium` → chi phí cao | mọi request | token cap quá cao + tier premium không cần | `max_completion_tokens=512`, `model_price_tier=standard` | log `cost_usd` mỗi call |
| `session_drift_rate=0.06` + `context_reset_every=0` → quality xuống cuối session | turn > 6 | corruption tích lũy, không reset | `session_drift_rate=0.0`, `context_reset_every=6` | session reset trong wrapper |
| `loop_guard=false` + `max_steps=12` → vòng lặp tool | request phức tạp | không có phát hiện lặp | `loop_guard=true`, `max_steps=8` | log `trace` phát hiện repeated actions |
| `normalize_unicode=false` → city tên có dấu bị lỗi | giao Hà Nội / Đà Nẵng | tool không nhận unicode | `normalize_unicode=true` | — |
| `catalog_override={macbook:{in_stock:false}}` → MacBook luôn hết hàng | mua MacBook | override sai | `catalog_override={}` | — |
| `redact_pii=false` + prompt gốc không cấm echo PII | request chứa email/sđt | không redact | `redact_pii=true` | PII scan + redact trong wrapper |
| Prompt gốc không có grounding → bịa tổng tiền | sản phẩm hết hàng/không có | prompt không ràng buộc LLM | — | prompt rewrite: grounding rule |
| `temperature=1.6` → tính toán sai ngẫu nhiên | mọi request toán | temperature cực cao | `temperature=0.2` | log `answer` để verify tổng |
| `tool_budget=0` → gọi tool không giới hạn | mọi request | không có tool budget cap | `tool_budget=4` | log `tool_count` phát hiện overuse |

**Tóm tắt fixes đã thực hiện:**
- config.json: sửa 21 knob (toàn bộ fault class)
- prompt.txt: viết lại hoàn toàn (8 pillars)
- wrapper.py: observability đầy đủ + retry + cache + PII redact + injection sanitize + prompt routing
- findings.json: 10 fault class với evidence + root cause + fix
