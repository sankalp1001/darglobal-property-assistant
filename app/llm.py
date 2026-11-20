"""OpenRouter client (OpenAI-compatible) with a model fallback chain: cheap paid models first, a free one last."""
from __future__ import annotations

import logging
import os
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

log = logging.getLogger(__name__)


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a local .env (git/docker-ignored) without overriding real env vars."""
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

DEFAULT_MODELS = "deepseek/deepseek-v4-flash-0731,openai/gpt-6-luna,nvidia/nemotron-3.5-super-vl:free"


class LLMUnavailable(RuntimeError):
    pass


def models() -> list[str]:
    return [m.strip() for m in os.getenv("OPENROUTER_MODELS", DEFAULT_MODELS).split(",") if m.strip()]


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise LLMUnavailable("OPENROUTER_API_KEY is not set")
    if _client is None:
        _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, timeout=45, max_retries=0)
    return _client


_calls = {"day": "", "n": 0}


def _spend_guard() -> None:
    """Cap LLM calls per UTC day (LLM_DAILY_LIMIT, default 600, roughly $0.15 on the default models)
    so a public demo link can't run up the bill. Past the cap the bot uses its no-LLM fallback."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if _calls["day"] != today:
        _calls.update(day=today, n=0)
    if _calls["n"] >= int(os.getenv("LLM_DAILY_LIMIT", "600")):
        raise LLMUnavailable("daily LLM call limit for this demo reached")
    _calls["n"] += 1


def chat(messages: list[dict], temperature: float = 0.2, max_tokens: int = 2000) -> tuple[str, str]:
    """Return (text, model_used). Tries each model in order on rate-limit/timeout/server errors.

    If every model was rate-limited, waits briefly and walks the chain once more
    (free models share a per-minute quota)."""
    _spend_guard()
    errors = []
    for round_ in range(2):
        rate_limited_only = True
        for model in models():
            try:
                r = client().chat.completions.create(
                    model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
                    # many models reason by default: hidden reasoning tokens made answers ~15x slower
                    # and sometimes used the whole token budget (empty reply). These tasks don't need it.
                    extra_body={"reasoning": {"enabled": False}},
                    extra_headers={"X-Title": "Property Assistant"},
                )
                text = (r.choices[0].message.content or "").strip()
                if text:
                    return text, model
                errors.append(f"{model}: empty response (finish={r.choices[0].finish_reason})")
                rate_limited_only = False
            except RateLimitError as e:
                if "per-day" in str(e):  # daily free quota is account-wide: no other model will work
                    raise LLMUnavailable(f"{model}: daily free-model quota exhausted") from e
                errors.append(f"{model}: rate limited")
            except (APITimeoutError, APIConnectionError) as e:
                errors.append(f"{model}: {type(e).__name__}")
                rate_limited_only = False
            except APIStatusError as e:  # 404 model gone, 5xx, provider errors -> next model
                errors.append(f"{model}: HTTP {e.status_code}")
                rate_limited_only = False
            log.warning("LLM fallback: %s", errors[-1])
        if not (round_ == 0 and rate_limited_only):
            break
        time.sleep(float(os.getenv("LLM_RETRY_WAIT", "12")))
    raise LLMUnavailable("; ".join(errors))
