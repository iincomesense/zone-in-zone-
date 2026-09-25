"""
config.py - Central configuration for the algo-zone-terminal data pipeline.

Design rule (important): NOTHING in this file, and nothing that reads from
it, may raise on import just because an API key is missing. A missing key
means "this source is unavailable" -> the provider layer must skip it and
fall through to the next free source. This is what keeps the whole pipeline
from crashing when a paid/optional key hasn't been set yet.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()  # loads a local .env file if present, silently no-ops otherwise
except ImportError:
    pass


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(key, default)
    if val is not None:
        val = val.strip()
    return val or None


@dataclass
class DhanConfig:
    client_id: Optional[str] = field(default_factory=lambda: _env("DHAN_CLIENT_ID"))
    access_token: Optional[str] = field(default_factory=lambda: _env("DHAN_ACCESS_TOKEN"))
    base_url: str = "https://api.dhan.co/v2"

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.access_token)


@dataclass
class NewsConfig:
    # All optional/free-tier keys. Any subset can be empty - the news
    # provider fans out only across the ones that are actually set.
    newsapi_key: Optional[str] = field(default_factory=lambda: _env("NEWSAPI_KEY"))
    gnews_key: Optional[str] = field(default_factory=lambda: _env("GNEWS_KEY"))
    marketaux_key: Optional[str] = field(default_factory=lambda: _env("MARKETAUX_KEY"))


@dataclass
class AIConfig:
    # Free/low-cost LLM endpoints for hypothesis generation. Any of these
    # can be blank; ai_provider.py (next phase) will skip missing ones.
    openrouter_key: Optional[str] = field(default_factory=lambda: _env("OPENROUTER_KEY"))
    groq_key: Optional[str] = field(default_factory=lambda: _env("GROQ_KEY"))
    gemini_key: Optional[str] = field(default_factory=lambda: _env("GEMINI_API_KEY"))


@dataclass
class AppConfig:
    dhan: DhanConfig = field(default_factory=DhanConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    ai: AIConfig = field(default_factory=AIConfig)

    # Request behaviour
    http_timeout_sec: float = float(_env("HTTP_TIMEOUT_SEC", "8") or 8)
    http_retries: int = int(_env("HTTP_RETRIES", "2") or 2)
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # Cache TTLs (seconds) - keeps small timeframes from hammering sources.
    cache_ttl_price_sec: int = int(_env("CACHE_TTL_PRICE_SEC", "5") or 5)
    cache_ttl_oi_sec: int = int(_env("CACHE_TTL_OI_SEC", "60") or 60)
    cache_ttl_fii_dii_sec: int = int(_env("CACHE_TTL_FII_DII_SEC", "1800") or 1800)
    cache_ttl_news_sec: int = int(_env("CACHE_TTL_NEWS_SEC", "600") or 600)


CONFIG = AppConfig()


def status_report() -> dict:
    """Human-readable snapshot of what's configured - safe to expose on a
    /health or Settings page so the user can see which sources are live
    vs skipped, without ever printing the actual secret values."""
    return {
        "dhan_configured": CONFIG.dhan.is_configured,
        "newsapi_configured": bool(CONFIG.news.newsapi_key),
        "gnews_configured": bool(CONFIG.news.gnews_key),
        "marketaux_configured": bool(CONFIG.news.marketaux_key),
        "openrouter_configured": bool(CONFIG.ai.openrouter_key),
        "groq_configured": bool(CONFIG.ai.groq_key),
        "gemini_configured": bool(CONFIG.ai.gemini_key),
    }
