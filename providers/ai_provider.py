"""
ai_provider.py - thin, uniform wrapper over several FREE-tier LLM APIs so
the hypothesis engine always has somewhere to fall back to. Same rule as
every other provider in this project: a missing key means "skip this
source", never a crash.

Fallback order (as of Sep 2026, all have a free tier - verify current
limits/model names on each provider's site before relying on them in
production, free-tier terms change often):
    1. Groq        - fastest inference, generous free tier, Llama models
    2. OpenRouter  - free-tagged models (":free" suffix), wide model choice
    3. Google Gemini - free tier via generativelanguage.googleapis.com

generate_text() is the single function the rest of the app should call.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from config import CONFIG

DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.1-8b-instruct:free"
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"


def _call_groq(prompt: str, system: Optional[str], max_tokens: int) -> Optional[str]:
    key = CONFIG.ai.groq_key
    if not key:
        return None
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": DEFAULT_GROQ_MODEL, "messages": messages,
                  "max_tokens": max_tokens, "temperature": 0.3},
            timeout=CONFIG.http_timeout_sec,
        )
        if resp.status_code >= 400:
            return None
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def _call_openrouter(prompt: str, system: Optional[str], max_tokens: int) -> Optional[str]:
    key = CONFIG.ai.openrouter_key
    if not key:
        return None
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": DEFAULT_OPENROUTER_MODEL, "messages": messages,
                  "max_tokens": max_tokens, "temperature": 0.3},
            timeout=CONFIG.http_timeout_sec,
        )
        if resp.status_code >= 400:
            return None
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def _call_gemini(prompt: str, system: Optional[str], max_tokens: int) -> Optional[str]:
    key = CONFIG.ai.gemini_key
    if not key:
        return None
    try:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{DEFAULT_GEMINI_MODEL}:generateContent?key={key}",
            json={
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3},
            },
            timeout=CONFIG.http_timeout_sec,
        )
        if resp.status_code >= 400:
            return None
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def generate_text(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 200,
) -> Dict[str, Any]:
    """Tries Groq -> OpenRouter -> Gemini in order. Returns:
        {"ok": True, "text": "...", "source": "Groq"/"OpenRouter"/"Gemini"}
        {"ok": False, "reason": "no_ai_source_available"}
    Never raises - caller (hypothesis_engine) must have its own
    non-AI fallback for when this returns ok=False, since a trading
    dashboard cannot silently show nothing just because every LLM
    quota is exhausted."""
    for name, fn in (
        ("Groq", _call_groq),
        ("OpenRouter", _call_openrouter),
        ("Gemini", _call_gemini),
    ):
        text = fn(prompt, system, max_tokens)
        if text:
            return {"ok": True, "text": text, "source": name}
    return {"ok": False, "reason": "no_ai_source_available", "text": None, "source": None}
