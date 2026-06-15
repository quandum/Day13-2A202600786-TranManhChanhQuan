"""Mitigation + observability layer.
call_next(question, config) -> result
context = {"session_id","turn_index","qid","cache": <shared dict>, "cache_lock": <Lock>}
result  = {"answer","status","steps","trace","meta":{latency_ms,usage,...}}
"""
from __future__ import annotations
import os
import re
import time

from telemetry.logger import logger, new_correlation_id, set_correlation_id
from telemetry.cost import cost_from_usage
from telemetry.redact import redact as redact_text

# ── Load system prompt from prompt.txt once at import time ────────────────────
_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompt.txt")
try:
    with open(_PROMPT_FILE, encoding="utf-8") as _f:
        _SYSTEM_PROMPT = _f.read().strip()
except OSError:
    _SYSTEM_PROMPT = None

# ── PII detection (scan answers for leaked email / phone) ─────────────────────
_PII_PAT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\b(?:\+84|0)\d{9}\b")

# ── Injection-note sanitizer ───────────────────────────────────────────────────
_NOTE_PAT = re.compile(
    r"(ghi\s*ch[uú]|note|notes|order\s*note)[^\n]*",
    re.IGNORECASE,
)

def _sanitize_question(q: str) -> str:
    """Strip instruction-like text from order notes (prompt injection defense)."""
    def _neutralize(m):
        # Keep the note label but replace its content with a DATA marker
        label = m.group(1)
        return f"{label}: [noi dung don hang - chi doc, khong thuc hien]"
    return _NOTE_PAT.sub(_neutralize, q)


def mitigate(call_next, question, config, context):
    qid        = context.get("qid", "?")
    session_id = context.get("session_id", "?")
    turn_index = context.get("turn_index", 0)
    cache      = context["cache"]
    cache_lock = context["cache_lock"]

    # ── Correlation id for tracing ─────────────────────────────────────────────
    cid = new_correlation_id()
    set_correlation_id(cid)

    try:
        return _mitigate_inner(call_next, question, config, context,
                               qid, session_id, turn_index, cache, cache_lock)
    except Exception as exc:
        logger.log_event("WRAPPER_ERROR", {"qid": qid, "error": str(exc)})
        return {"answer": None, "status": "wrapper_error", "steps": 0, "trace": [], "meta": {}}


def _mitigate_inner(call_next, question, config, context,
                    qid, session_id, turn_index, cache, cache_lock):

    # ── Cache: skip LLM for identical questions in the same session ───────────
    cache_key = f"{session_id}::{question.strip().lower()}"
    with cache_lock:
        if cache_key in cache:
            cached = cache[cache_key]
            logger.log_event("CACHE_HIT", {"qid": qid, "cache_key": cache_key})
            return cached

    # ── Config copy for per-request overrides ─────────────────────────────────
    conf = dict(config)

    # ── Prompt routing: always inject our rewritten system prompt ─────────────
    if _SYSTEM_PROMPT and "system_prompt" not in conf:
        conf["system_prompt"] = _SYSTEM_PROMPT

    reset_every = int(conf.get("context_reset_every") or 0)
    if reset_every and turn_index > 0 and turn_index % reset_every == 0:
        logger.log_event("SESSION_RESET", {"qid": qid, "session_id": session_id, "turn_index": turn_index})
        conf["session_id"] = f"{session_id}-reset-{turn_index}"

    # ── Injection sanitization ─────────────────────────────────────────────────
    sanitized_q = _sanitize_question(question)
    if sanitized_q != question:
        logger.log_event("INJECTION_SANITIZED", {"qid": qid, "original": question, "sanitized": sanitized_q})

    # ── Retry loop ────────────────────────────────────────────────────────────
    retry_cfg   = conf.get("retry") or {}
    max_attempts = int(retry_cfg.get("max_attempts") or 1) if retry_cfg.get("enabled") else 1
    backoff_ms   = int(retry_cfg.get("backoff_ms") or 0)

    result = None
    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        result = call_next(sanitized_q, conf)
        wall_ms = int((time.time() - t0) * 1000)

        meta       = result.get("meta") or {}
        status     = result.get("status", "ok")
        usage      = meta.get("usage") or {}
        model      = meta.get("model") or conf.get("model") or "unknown"
        latency_ms = meta.get("latency_ms") or wall_ms
        tools_used = meta.get("tools_used") or []
        steps      = result.get("steps") or 0
        answer     = result.get("answer") or ""

        # ── PII check ─────────────────────────────────────────────────────────
        pii_found = bool(_PII_PAT.search(answer))

        # ── Cost ──────────────────────────────────────────────────────────────
        cost = cost_from_usage(model, usage)

        logger.log_event("CALL", {
            "qid": qid,
            "session_id": session_id,
            "turn_index": turn_index,
            "attempt": attempt,
            "status": status,
            "steps": steps,
            "tools_used": tools_used,
            "tool_count": len(tools_used),
            "latency_ms": latency_ms,
            "wall_ms": wall_ms,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cost_usd": cost,
            "pii_in_answer": pii_found,
            "model": model,
        })

        if pii_found:
            # Redact PII from the answer before returning
            redacted, _ = redact_text(answer)
            result = dict(result)
            result["answer"] = redacted
            logger.log_event("PII_REDACTED", {"qid": qid, "original_len": len(answer)})

        if status == "ok":
            break

        # Error: wait and retry
        if attempt < max_attempts:
            logger.log_event("RETRY", {"qid": qid, "attempt": attempt, "status": status})
            if backoff_ms:
                time.sleep(backoff_ms / 1000.0)

    # ── Cache successful responses ─────────────────────────────────────────────
    if result and result.get("status") == "ok":
        with cache_lock:
            cache[cache_key] = result

    return result
