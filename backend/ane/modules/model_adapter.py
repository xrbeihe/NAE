"""Model Adapter — unified interface for all LLM providers.

Adding a new provider = one new class, zero changes to business logic.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from ane.config import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    ANTHROPIC_API_KEY,
    CLAUDE_BASE_URL,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    GEMINI_API_KEY,
    GEMINI_BASE_URL,
    SENSENOVA_API_KEY,
    SENSENOVA_BASE_URL,
    OLLAMA_BASE_URL,
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

# ── Token usage tracking ───────────────────────────────────────

@dataclass
class TokenUsage:
    """Token consumption + elapsed time for a single LLM call."""
    provider: str = ""
    model: str = ""
    label: str = ""           # e.g. "llm_main", "llm_modeling", "llm_summary"
    user_id: str = ""         # empty = server-side / unknown
    session_id: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_seconds: float = 0.0
    timestamp: str = ""          # ISO 时间戳（写入持久化日志时填充）

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "label": self.label,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "timestamp": self.timestamp,
        }


# In-memory accumulator (reset on server restart)
_usage_log: list[TokenUsage] = []

# 持久化日志目录：user_logs/usage/年月.jsonl（重启不丢，可回溯）
_USAGE_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "user_logs" / "usage"


def _usage_log_path() -> Path:
    """按年月分文件的 JSONL 路径。"""
    return _USAGE_LOG_DIR / f"{datetime.now().strftime('%Y%m')}.jsonl"


def log_usage(entry: TokenUsage) -> None:
    """Record a token usage entry in memory AND persist to user_logs/usage/年月.jsonl."""
    entry.timestamp = entry.timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _usage_log.append(entry)
    try:
        _USAGE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(_usage_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("usage log write failed: %s", e)


def get_persisted_usage(user_id: str = "") -> list[dict]:
    """Read usage entries from the persisted JSONL logs (all months)."""
    if not _USAGE_LOG_DIR.exists():
        return []
    out: list[dict] = []
    for path in sorted(_USAGE_LOG_DIR.glob("*.jsonl")):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not user_id or d.get("user_id") == user_id:
                        out.append(d)
        except OSError:
            continue
    return out


def get_usage(user_id: str = "") -> list[dict]:
    """Return all stored usage entries, optionally filtered by user_id."""
    if user_id:
        return [e.to_dict() for e in _usage_log if e.user_id == user_id]
    return [e.to_dict() for e in _usage_log]


def get_usage_summary(user_id: str = "") -> dict:
    """Aggregate token totals + elapsed time per label."""
    entries = _usage_log if not user_id else [e for e in _usage_log if e.user_id == user_id]
    summary: dict[str, int] = {}
    timing: dict[str, dict] = {}
    for e in entries:
        key = e.label or "unknown"
        summary.setdefault(key, 0)
        summary[key] += e.total
        timing.setdefault(key, {"count": 0, "total_seconds": 0.0})
        timing[key]["count"] += 1
        timing[key]["total_seconds"] += e.elapsed_seconds
    return {
        "total_tokens": sum(summary.values()),
        "by_label": summary,
        "timing": {k: {"count": v["count"], "total_seconds": round(v["total_seconds"], 1),
                        "avg_seconds": round(v["total_seconds"] / v["count"], 1)} for k, v in timing.items()},
    }


def get_usage_by_session(user_id: str = "") -> dict:
    """Aggregate token totals + elapsed time per session (optionally per label).

    Returns:
      {
        "total_tokens": int,
        "sessions": [
          {"session_id": "...", "label": "llm_main",
           "count": n, "total_tokens": int, "total_seconds": float, "avg_seconds": float},
          ...
        ]
      }
    Sessions without a session_id group under "(unknown)".
    """
    entries = _usage_log if not user_id else [e for e in _usage_log if e.user_id == user_id]
    agg: dict[tuple, dict] = {}
    for e in entries:
        key = (e.session_id or "(unknown)", e.label or "unknown")
        a = agg.setdefault(key, {"count": 0, "total_tokens": 0, "total_seconds": 0.0})
        a["count"] += 1
        a["total_tokens"] += e.total
        a["total_seconds"] += e.elapsed_seconds
    sessions = [
        {
            "session_id": sid,
            "label": label,
            "count": a["count"],
            "total_tokens": a["total_tokens"],
            "total_seconds": round(a["total_seconds"], 1),
            "avg_seconds": round(a["total_seconds"] / a["count"], 1),
        }
        for (sid, label), a in sorted(agg.items(), key=lambda kv: kv[1]["total_tokens"], reverse=True)
    ]
    return {
        "total_tokens": sum(a["total_tokens"] for a in agg.values()),
        "sessions": sessions,
    }

# ── Retry configuration ────────────────────────────────────────

MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 30.0  # seconds


async def _retry_with_backoff(fn, name: str = "LLM") -> str:
    """Call an async function with exponential backoff on transient errors.

    Retries on: httpx.TimeoutException, httpx.HTTPStatusError with status >= 500 or 429.
    ValueError, KeyError, TypeError (parsing errors) are never retried.
    """
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await fn()
        except httpx.TimeoutException as e:
            last_error = e
            logger.warning(f"{name} timed out (attempt {attempt + 1}/{MAX_RETRIES + 1})")
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status < 500 and status != 429:
                # Log response body for client errors — critical for diagnosing auth/quota issues
                try:
                    body = e.response.text[:1000]
                except Exception:
                    body = "(unreadable)"
                logger.error(f"{name} returned {status}: {body}")
                raise  # Client error (4xx except 429) — don't retry
            last_error = e
            logger.warning(f"{name} returned {status} (attempt {attempt + 1}/{MAX_RETRIES + 1})")
        except (ValueError, KeyError, TypeError) as e:
            # Parsing/programming errors — never retry
            logger.error(f"{name} fatal error (no retry): {type(e).__name__}: {e}")
            raise
        except Exception as e:
            last_error = e
            logger.warning(f"{name} unexpected error: {type(e).__name__}: {e} (attempt {attempt + 1}/{MAX_RETRIES + 1})")

        if attempt < MAX_RETRIES:
            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            logger.info(f"Retrying {name} in {delay:.1f}s...")
            await asyncio.sleep(delay)

    raise last_error  # type: ignore[misc]


class BaseAdapter(ABC):
    """Every LLM provider implements this."""

    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate text. kwargs may include user_id, session_id, label for token tracking."""
        ...


# ── OpenAI-compatible adapter (covers OpenAI, DeepSeek, Kimi, and any /v1 API) ──

class OpenAICompatibleAdapter(BaseAdapter):
    """Works with any API that follows the OpenAI /v1/chat/completions format."""

    def __init__(self, api_key: str, base_url: str, default_model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

    async def generate(self, prompt: str, **kwargs) -> str:
        import time
        start = time.monotonic()
        model = kwargs.get("model", self.default_model)
        temperature = kwargs.get("temperature", LLM_TEMPERATURE)
        max_tokens = kwargs.get("max_tokens", LLM_MAX_TOKENS)
        label = kwargs.get("label", "")
        user_id = kwargs.get("user_id", "")
        session_id = kwargs.get("session_id", "")

        async def _call():
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                response.raise_for_status()
                data = response.json()
                elapsed = time.monotonic() - start
                # Log usage from API response
                usage = data.get("usage", {})
                if usage:
                    pt = usage.get("prompt_tokens", 0)
                    ct = usage.get("completion_tokens", 0)
                    log_usage(TokenUsage(
                        provider="openai",
                        model=model,
                        label=label,
                        user_id=user_id,
                        session_id=session_id,
                        prompt_tokens=pt,
                        completion_tokens=ct,
                        elapsed_seconds=elapsed,
                    ))
                    logger.info(f"[{label}] user={user_id or '-'} model={model} "
                                f"prompt={pt} completion={ct} total={pt+ct} elapsed={elapsed:.1f}s")
                msg = data["choices"][0]["message"]
                content = msg.get("content") or msg.get("reasoning") or ""
                if not content:
                    logger.warning("LLM response has no content field: %s", list(msg.keys()))
                    content = str(msg)
                return content

        return await _retry_with_backoff(_call, name=f"OpenAI({model})")


# ── Anthropic / Claude adapter ────────────────────────────────

class ClaudeAdapter(BaseAdapter):
    """Anthropic Messages API with adaptive thinking + prompt caching."""

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    async def generate(self, prompt: str, **kwargs) -> str:
        model = kwargs.get("model", "claude-sonnet-5")
        max_tokens = kwargs.get("max_tokens", LLM_MAX_TOKENS)

        async def _call():
            import logging as _log
            _log.getLogger(__name__).debug(f"Calling Claude: model={model}, tokens={max_tokens}")
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "anthropic-beta": "prompt-caching-2024-07-31",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "max_tokens": max_tokens,
                        "thinking": {"type": "adaptive"},
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                response.raise_for_status()
                data = response.json()
                # Collect text blocks; Claude may return thinking-only when
                # adaptive thinking finishes without producing visible text.
                text_blocks = [
                    block.get("text", "")
                    for block in data.get("content", [])
                    if block.get("type") == "text" and block.get("text")
                ]
                if text_blocks:
                    return "".join(text_blocks)
                # Fallback: return whatever is in the first content block
                first = data["content"][0] if data.get("content") else {}
                content = first.get("text", "")
                if not content:
                    logger.warning(
                        "Claude returned no text content — blocks=%s",
                        [b.get("type") for b in data.get("content", [])],
                    )
                return content

        return await _retry_with_backoff(_call, name=f"Claude({model})")


# ── Google Gemini adapter ─────────────────────────────────────

class GeminiAdapter(BaseAdapter):
    """Google Gemini API (generateContent).

    Supports both the official Google endpoint and custom proxies/gateways
    by making the base URL configurable.

    For proxy gateways (e.g. tokeness.io), set GEMINI_BASE_URL to the
    proxy base URL and GEMINI_API_KEY to the proxy API key.
    """

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def generate(self, prompt: str, **kwargs) -> str:
        model = kwargs.get("model", "gemini-3.5-flash")

        async def _call():
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.base_url}/v1beta/models/{model}:generateContent",
                    params={"key": self.api_key},
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                    },
                )
                # Check for non-200 responses with error detail
                if response.status_code >= 400:
                    try:
                        error_body = response.json()
                    except Exception:
                        error_body = response.text
                    logger.error(
                        "Gemini API error %d: %s",
                        response.status_code,
                        error_body,
                    )
                    response.raise_for_status()

                data = response.json()

                # Validate response structure
                if not isinstance(data, dict):
                    raise ValueError(
                        f"Gemini returned non-JSON response: {type(data).__name__}"
                    )

                candidates = data.get("candidates")
                if not candidates:
                    # Safety filter may have blocked the response
                    safety = data.get("safetyRatings", data.get("promptFeedback", {}))
                    logger.warning(
                        "Gemini returned no candidates — possibly blocked by "
                        "safety filter. safetyRatings=%s", safety
                    )
                    raise ValueError(
                        f"Gemini response blocked or empty (no candidates). "
                        f"safetyRatings={safety}"
                    )

                try:
                    content = candidates[0].get("content")
                    if not content:
                        finish_reason = candidates[0].get("finishReason", "unknown")
                        raise KeyError(f"content is missing (finishReason={finish_reason})")
                    parts = content.get("parts")
                    if not parts:
                        raise KeyError("content.parts is missing or empty")
                    return parts[0]["text"]
                except (KeyError, IndexError, TypeError) as e:
                    logger.error("Gemini unexpected response structure: %s", data)
                    raise ValueError(
                        f"Gemini response missing expected fields: {e}"
                    ) from e

        return await _retry_with_backoff(_call, name=f"Gemini({model})")


# ── Ollama adapter (local) ────────────────────────────────────

class OllamaAdapter(BaseAdapter):
    """Local Ollama server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def generate(self, prompt: str, **kwargs) -> str:
        model = kwargs.get("model", "llama3")

        async def _call():
            # trust_env=False avoids system proxy (e.g. Clash on :7897)
            # hijacking localhost requests to Ollama.
            async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["response"]

        return await _retry_with_backoff(_call, name=f"Ollama({model})")


# ── Registry & factory ────────────────────────────────────────

class ModelAdapter:
    """Unified interface that routes to the correct backend.

    Usage:
        adapter = ModelAdapter()
        response = await adapter.generate(prompt, model="deepseek-v4-flash")
    """

    def __init__(self):
        self._adapters: dict[str, BaseAdapter] = {}

        # Register available backends based on configured API keys
        if OPENAI_API_KEY:
            self._adapters["openai"] = OpenAICompatibleAdapter(
                OPENAI_API_KEY, OPENAI_BASE_URL, "gpt-4o"
            )
        if DEEPSEEK_API_KEY:
            self._adapters["deepseek"] = OpenAICompatibleAdapter(
                DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, "deepseek-v4-flash"
            )
        if SENSENOVA_API_KEY:
            self._adapters["sensenova"] = OpenAICompatibleAdapter(
                SENSENOVA_API_KEY, SENSENOVA_BASE_URL, "sensenova-6.7-flash-lite"
            )
        if ANTHROPIC_API_KEY:
            self._adapters["claude"] = ClaudeAdapter(ANTHROPIC_API_KEY, CLAUDE_BASE_URL)
        if GEMINI_API_KEY:
            self._adapters["gemini"] = GeminiAdapter(GEMINI_API_KEY, GEMINI_BASE_URL)
        # Ollama is always registered (local)
        self._adapters["ollama"] = OllamaAdapter(OLLAMA_BASE_URL)

        logger.info(f"Model adapters registered: {list(self._adapters.keys())}")

    async def generate(self, prompt: str, model: str | None = None, **kwargs) -> str:
        """Route to correct adapter based on model name prefix.

        Model naming convention: "provider:model-name"
          e.g., "openai:gpt-4o", "deepseek:deepseek-v4-flash", "claude:claude-sonnet-4-20250514"
        If no colon, tries to guess from the model name.
        """
        from ane.config import DEFAULT_MODEL

        model = model or DEFAULT_MODEL

        # Determine provider
        if ":" in model:
            provider, actual_model = model.split(":", 1)
        else:
            # Guess provider from model name
            provider = self._guess_provider(model)
            actual_model = model

        adapter = self._adapters.get(provider)
        if not adapter:
            available = list(self._adapters.keys())
            raise ValueError(
                f"No adapter for provider '{provider}'. "
                f"Available: {available}. Set the corresponding API key."
            )

        logger.info(f"Calling {provider} model {actual_model}...")
        return await adapter.generate(prompt, model=actual_model, **kwargs)

    def _guess_provider(self, model_name: str) -> str:
        """Guess provider from model name when no explicit prefix."""
        model_lower = model_name.lower()
        if "claude" in model_lower:
            return "claude"
        if "gemini" in model_lower:
            return "gemini"
        if "deepseek" in model_lower:
            return "deepseek"
        if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            return "openai"
        if any(x in model_lower for x in ["llama", "mistral", "qwen", "phi"]):
            return "ollama"
        return "ollama"  # default fallback


# Singleton
model_adapter = ModelAdapter()
