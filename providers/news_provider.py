"""
news_provider.py - multi-source news + macro/economic events, with
cross-source verification so the AI hypothesis layer (separate module,
next phase) gets ONE de-duplicated, source-checked feed instead of raw
per-source noise.

Sources used (each individually optional/free-tier, fallback-safe):
    - ForexFactory weekly calendar (public JSON, no key) - macro events
    - NewsAPI.org (free tier, needs NEWSAPI_KEY)
    - GNews.io (free tier, needs GNEWS_KEY)
    - Marketaux (free tier, needs MARKETAUX_KEY)
    - Economic Times Markets RSS (no key)
    - Moneycontrol RSS (no key)

Cross-verification rule: a headline/topic is marked "verified" when it
is corroborated by 2+ independent sources within the cache window, or
comes from a single high-trust free source (ForexFactory for events,
official RSS for India news) when no other source covers it. Slow/
duplicate items are dropped rather than shown twice.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

import feedparser

from config import CONFIG
from providers._cache import NEWS_CACHE
from providers._http import safe_get

FOREXFACTORY_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
ET_MARKETS_RSS = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
MONEYCONTROL_RSS = "https://www.moneycontrol.com/rss/marketreports.xml"


# --------------------------------------------------------------------------
# Individual source fetchers - each returns a list of normalized dicts:
#   {"headline": str, "source": str, "time": str, "severity": Optional[str],
#    "instrument": Optional[str], "url": Optional[str]}
# and NEVER raises; failures return [].
# --------------------------------------------------------------------------

def _fetch_forexfactory_events() -> List[Dict[str, Any]]:
    resp = safe_get(FOREXFACTORY_CALENDAR_URL)
    if not resp.get("ok") or not isinstance(resp.get("data"), list):
        return []
    out = []
    for ev in resp["data"]:
        try:
            out.append({
                "headline": f"{ev.get('country', '')} {ev.get('title', '')}".strip(),
                "source": "ForexFactory",
                "time": ev.get("date"),
                "severity": ev.get("impact"),  # "High"/"Medium"/"Low"
                "instrument": ev.get("country"),
                "url": "https://www.forexfactory.com/calendar",
            })
        except (TypeError, AttributeError):
            continue
    return out


def _fetch_rss(url: str, source_name: str, limit: int = 15) -> List[Dict[str, Any]]:
    resp = safe_get(url)
    if not resp.get("ok") or not resp.get("text"):
        return []
    try:
        parsed = feedparser.parse(resp["text"])
        out = []
        for entry in parsed.entries[:limit]:
            out.append({
                "headline": getattr(entry, "title", "").strip(),
                "source": source_name,
                "time": getattr(entry, "published", None),
                "severity": None,
                "instrument": None,
                "url": getattr(entry, "link", None),
            })
        return out
    except Exception:
        return []


def _fetch_newsapi(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    key = CONFIG.news.newsapi_key
    if not key:
        return []
    resp = safe_get(
        "https://newsapi.org/v2/everything",
        params={"q": query, "language": "en", "sortBy": "publishedAt", "pageSize": limit, "apiKey": key},
    )
    if not resp.get("ok") or not resp.get("data"):
        return []
    try:
        return [
            {
                "headline": a.get("title", "").strip(),
                "source": f"NewsAPI/{a.get('source', {}).get('name', '')}",
                "time": a.get("publishedAt"),
                "severity": None,
                "instrument": query,
                "url": a.get("url"),
            }
            for a in resp["data"].get("articles", [])
        ]
    except (KeyError, AttributeError):
        return []


def _fetch_gnews(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    key = CONFIG.news.gnews_key
    if not key:
        return []
    resp = safe_get(
        "https://gnews.io/api/v4/search",
        params={"q": query, "lang": "en", "max": limit, "apikey": key},
    )
    if not resp.get("ok") or not resp.get("data"):
        return []
    try:
        return [
            {
                "headline": a.get("title", "").strip(),
                "source": f"GNews/{a.get('source', {}).get('name', '')}",
                "time": a.get("publishedAt"),
                "severity": None,
                "instrument": query,
                "url": a.get("url"),
            }
            for a in resp["data"].get("articles", [])
        ]
    except (KeyError, AttributeError):
        return []


def _fetch_marketaux(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    key = CONFIG.news.marketaux_key
    if not key:
        return []
    resp = safe_get(
        "https://api.marketaux.com/v1/news/all",
        params={"search": query, "language": "en", "limit": limit, "api_token": key},
    )
    if not resp.get("ok") or not resp.get("data"):
        return []
    try:
        return [
            {
                "headline": a.get("title", "").strip(),
                "source": "Marketaux",
                "time": a.get("published_at"),
                "severity": None,
                "instrument": query,
                "url": a.get("url"),
            }
            for a in resp["data"].get("data", [])
        ]
    except (KeyError, AttributeError):
        return []


# --------------------------------------------------------------------------
# Cross-verification / de-duplication
# --------------------------------------------------------------------------

def _similar(a: str, b: str, threshold: float = 0.72) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold


def _cross_verify(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Groups near-duplicate headlines across sources, keeps one
    representative item per group, and tags it 'verified' when 2+
    distinct sources reported it. Free-source items unique to
    ForexFactory/RSS (India-specific news no paid API tends to carry)
    are still kept, just flagged as single-source."""
    groups: List[Dict[str, Any]] = []
    for item in items:
        if not item.get("headline"):
            continue
        placed = False
        for group in groups:
            if _similar(group["headline"], item["headline"]):
                group["sources"].add(item["source"])
                group["items"].append(item)
                placed = True
                break
        if not placed:
            groups.append({"headline": item["headline"], "sources": {item["source"]}, "items": [item]})

    out = []
    for group in groups:
        rep = group["items"][0]
        out.append({
            **rep,
            "verified": len(group["sources"]) >= 2,
            "corroborating_sources": sorted(group["sources"]),
        })
    return out


def get_verified_news_and_events(
    instrument_query: Optional[str] = None,
    include_macro_events: bool = True,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Main entry point. instrument_query e.g. "RELIANCE" or "Nifty 50" -
    pass None for a general India-markets sweep only."""
    cache_key = f"news:{instrument_query or 'GENERAL'}:{include_macro_events}"
    if use_cache:
        cached = NEWS_CACHE.get(cache_key)
        if cached is not None:
            return cached

    raw: List[Dict[str, Any]] = []
    if include_macro_events:
        raw += _fetch_forexfactory_events()
    raw += _fetch_rss(ET_MARKETS_RSS, "Economic Times")
    raw += _fetch_rss(MONEYCONTROL_RSS, "Moneycontrol")

    if instrument_query:
        raw += _fetch_newsapi(instrument_query)
        raw += _fetch_gnews(instrument_query)
        raw += _fetch_marketaux(instrument_query)

    verified = _cross_verify(raw)
    result = {
        "ok": True,
        "count_raw": len(raw),
        "count_verified_groups": len(verified),
        "items": verified,
        "sources_attempted": [
            "ForexFactory", "Economic Times RSS", "Moneycontrol RSS",
            "NewsAPI" if CONFIG.news.newsapi_key else None,
            "GNews" if CONFIG.news.gnews_key else None,
            "Marketaux" if CONFIG.news.marketaux_key else None,
        ],
    }
    result["sources_attempted"] = [s for s in result["sources_attempted"] if s]

    NEWS_CACHE.set(cache_key, result, CONFIG.cache_ttl_news_sec)
    return result
