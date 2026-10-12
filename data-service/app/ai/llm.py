"""Minimal client for any OpenAI-compatible API (chat completions + embeddings).

Works with Google Gemini's OpenAI-compatible endpoint, Groq, OpenRouter, OpenAI,
Together, or a local Ollama server, by changing LLM_BASE_URL / LLM_MODEL only.

A deliberately thin wrapper instead of LangChain: the agent needs two calls (chat and
embeddings) and full control over prompts, retries and error handling, and keeping
it small makes it easy to test with a fake.
"""
from __future__ import annotations

import json
import math
import re
import time
from typing import Protocol

import httpx

from app.config import Settings, get_settings


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def chat(self, messages: list[dict], temperature: float = 0.0) -> str: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._http = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))
        self._send_dimensions = True

    # -- helpers -----------------------------------------------------------------
    def _post(self, base: str, key: str, path: str, payload: dict) -> dict:
        url = base.rstrip("/") + "/" + path
        last_error = None
        for attempt in range(3):
            try:
                resp = self._http.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                time.sleep(2.0 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise LLMError(last_error or "LLM request failed")

    # -- API -----------------------------------------------------------------------
    def chat(self, messages: list[dict], temperature: float = 0.0) -> str:
        if not self.s.llm_api_key:
            raise LLMError("LLM_API_KEY is not configured")
        data = self._post(self.s.llm_base_url, self.s.llm_api_key, "chat/completions",
                          {"model": self.s.llm_model, "messages": messages, "temperature": temperature})
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from None
        if isinstance(content, list):  # some providers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or ""

    def embed(self, texts: list[str]) -> list[list[float]]:
        key = self.s.embedding_api_key or self.s.llm_api_key
        base = self.s.embedding_base_url or self.s.llm_base_url
        if not key or not self.s.embedding_model:
            raise LLMError("embeddings are not configured")
        payload = {"model": self.s.embedding_model, "input": texts}
        if self._send_dimensions:
            payload["dimensions"] = self.s.embedding_dim
        try:
            data = self._post(base, key, "embeddings", payload)
        except LLMError as exc:
            if self._send_dimensions and "dimension" in str(exc).lower():
                self._send_dimensions = False          # provider doesn't support it: truncate locally
                return self.embed(texts)
            raise
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda d: d.get("index", 0))]
        return [fit_dimension(v, self.s.embedding_dim) for v in vectors]


def fit_dimension(vector: list[float], dim: int) -> list[float]:
    """Matryoshka-style truncation (or zero padding) to the column size, then L2 re-normalise."""
    v = list(vector[:dim]) + [0.0] * max(0, dim - len(vector))
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        if start == -1:
            raise LLMError("model did not return JSON")
        depth, in_str, esc = 0, False, False
        for i, ch in enumerate(text[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    break
    if candidate is None:
        raise LLMError("model returned incomplete JSON")
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned invalid JSON: {exc}") from None


_client: OpenAICompatibleClient | None = None
_override: LLM | None = None


def get_llm() -> LLM | None:
    """The configured client, or None when no API key is set (features degrade gracefully)."""
    global _client
    if _override is not None:
        return _override
    settings = get_settings()
    if not settings.llm_enabled:
        return None
    if _client is None:
        _client = OpenAICompatibleClient(settings)
    return _client


def embeddings_available() -> bool:
    return _override is not None or get_settings().embeddings_enabled


def set_llm_for_tests(fake: LLM | None) -> None:
    global _override
    _override = fake
