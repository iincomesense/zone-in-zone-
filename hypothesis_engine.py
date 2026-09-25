"""
hypothesis_engine.py - produces the "2-line Hindi hypothesis" the spec
asks to show against every scanned/validated zone.

Pipeline (as specified):
    1. Read global instruments + Nifty 50 index context
    2. Read OI confirmation for this zone
    3. Read FII/DII backdrop
    4. Read cross-verified news/events (ForexFactory + RSS + paid-free APIs)
    5. Ask a free LLM (Groq -> OpenRouter -> Gemini) for a MAX 2-line
       Hindi hypothesis grounded only in the above facts
    6. If every AI source is unavailable/quota-exhausted, fall back to a
       deterministic rule-based Hindi sentence built from the same facts
       - a trading dashboard must never go blank just because an LLM
         quota ran out.

Also implements the spec's index-conflict check: if this is a demand
zone, Nifty 50 should not itself be sitting in a supply zone nearby (and
vice-versa). That check needs an actual Nifty zone scan (zone_core.py
run on Nifty candles) which is a separate module - this file exposes the
hook (`nifty_zone_conflict`) and treats it as "unknown/not checked" when
the caller doesn't supply it, rather than guessing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import CONFIG
from providers._cache import HYPOTHESIS_CACHE
from providers import ai_provider, oi_provider

HYPOTHESIS_CACHE_TTL_SEC = 300  # 5 min - independent of candle timeframe;
# a zone's underlying facts (news/OI/FII-DII) don't change bar-by-bar, so
# this is deliberately decoupled from data_pipeline's candle-close cache.

SYSTEM_PROMPT_HI = (
    "आप एक भारतीय शेयर बाज़ार के सीनियर एल्गो-ट्रेडिंग विश्लेषक हैं। "
    "आपको नीचे दिए गए तथ्यों (facts) के आधार पर सिर्फ़ अधिकतम 2 पंक्तियों में, "
    "सिर्फ़ हिंदी में, एक संक्षिप्त ट्रेडिंग hypothesis देना है। "
    "कोई प्रस्तावना, कोई डिस्क्लेमर, कोई नंबरिंग मत दीजिए - सीधे hypothesis लिखिए। "
    "दिए गए तथ्यों से बाहर कोई नई जानकारी मत जोड़िए, अनुमान मत लगाइए।"
)


def _fmt_news_facts(news: Optional[Dict[str, Any]], max_items: int = 5) -> str:
    if not news or not news.get("ok") or not news.get("items"):
        return "कोई सत्यापित न्यूज़/इवेंट उपलब्ध नहीं।"
    lines = []
    for item in news["items"][:max_items]:
        tag = "सत्यापित" if item.get("verified") else "एकल-स्रोत"
        sev = f" [{item['severity']}]" if item.get("severity") else ""
        lines.append(f"- ({tag}{sev}, {item.get('source')}) {item.get('headline')}")
    return "\n".join(lines)


def _fmt_fii_dii_facts(fii_dii: Optional[Dict[str, Any]]) -> str:
    if not fii_dii or not fii_dii.get("ok") or not fii_dii.get("rows"):
        return "FII/DII डेटा उपलब्ध नहीं।"
    lines = []
    for row in fii_dii["rows"]:
        lines.append(
            f"- {row.get('date')} {row.get('category')}: नेट ₹{row.get('net_value_cr')} करोड़"
        )
    return "\n".join(lines)


def _fmt_oi_facts(oi_confirmation: Optional[Dict[str, Any]]) -> str:
    if not oi_confirmation or oi_confirmation.get("confirmed") is None:
        return "OI डेटा उपलब्ध नहीं / निर्णायक नहीं।"
    status = "पुष्टि करता है" if oi_confirmation["confirmed"] else "पुष्टि नहीं करता"
    return f"OI ज़ोन की {status}: {oi_confirmation.get('display', '')}"


def _fmt_global_facts(global_prices: Optional[Dict[str, Dict[str, Any]]]) -> str:
    if not global_prices:
        return "ग्लोबल इंस्ट्रूमेंट डेटा उपलब्ध नहीं।"
    lines = []
    for sym, p in global_prices.items():
        if p.get("ok"):
            lines.append(f"- {sym}: {p['price']} ({p['change_pct']:+.2f}%)")
    return "\n".join(lines) if lines else "ग्लोबल इंस्ट्रूमेंट डेटा उपलब्ध नहीं।"


def _rule_based_fallback(
    symbol: str,
    is_demand_zone: bool,
    oi_confirmation: Optional[Dict[str, Any]],
    fii_dii: Optional[Dict[str, Any]],
    news: Optional[Dict[str, Any]],
    nifty_zone_conflict: Optional[bool],
) -> str:
    """Deterministic, template-based Hindi sentence - used only when every
    AI source is unavailable. Grounded strictly in fetched facts, no
    invented numbers."""
    zone_word = "डिमांड" if is_demand_zone else "सप्लाई"
    parts: List[str] = [f"{symbol} में {zone_word} ज़ोन स्कैन हुआ।"]

    if oi_confirmation and oi_confirmation.get("confirmed") is True:
        parts.append("OI डेटा ज़ोन को सपोर्ट कर रहा है।")
    elif oi_confirmation and oi_confirmation.get("confirmed") is False:
        parts.append("OI डेटा ज़ोन के विपरीत दिख रहा है, सतर्क रहें।")

    if fii_dii and fii_dii.get("ok") and fii_dii.get("rows"):
        latest = fii_dii["rows"][0]
        try:
            net = float(latest.get("net_value_cr", 0))
            direction = "खरीदारी" if net > 0 else "बिकवाली"
            parts.append(f"{latest.get('category')} की हालिया {direction} भी इसी दिशा में है।")
        except (TypeError, ValueError):
            pass

    if news and news.get("items"):
        high_impact = [n for n in news["items"] if n.get("severity") == "High"]
        if high_impact:
            parts.append("आज बड़ा न्यूज़/इवेंट है, सावधानी बरतें।")

    if nifty_zone_conflict is True:
        parts.append("⚠️ Nifty 50 में विपरीत ज़ोन भी सक्रिय है, ट्रेड से पहले दोबारा जांचें।")

    return " ".join(parts[:2]) if len(parts) > 2 else " ".join(parts)


def get_zone_hypothesis(
    symbol: str,
    is_demand_zone: bool,
    market_context: Optional[Dict[str, Any]] = None,
    global_prices: Optional[Dict[str, Dict[str, Any]]] = None,
    nifty_zone_conflict: Optional[bool] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """market_context: output of data_pipeline.get_market_context(symbol).
    global_prices: output of price_provider.get_live_prices_bulk(GLOBAL_INSTRUMENTS).
    nifty_zone_conflict: True/False if the caller already ran a Nifty zone
        scan and knows whether an opposite zone sits nearby; None if not checked.

    Returns:
        {"ok": True, "hypothesis": "<=2 Hindi lines>", "source": "Groq"/"...
         "/Rule-based fallback", "nifty_zone_conflict": True/False/None}
    """
    cache_key = f"hyp:{symbol}:{is_demand_zone}"
    if use_cache:
        cached = HYPOTHESIS_CACHE.get(cache_key)
        if cached is not None:
            return cached

    market_context = market_context or {}
    news = market_context.get("news")
    fii_dii = market_context.get("fii_dii")
    price = market_context.get("price")

    oi_confirmation = None
    oi_snapshot = market_context.get("oi_snapshot")
    if oi_snapshot:
        oi_confirmation = oi_provider.check_oi_confirms_zone(oi_snapshot, is_demand_zone)

    facts_block = (
        f"इंस्ट्रूमेंट: {symbol}\n"
        f"ज़ोन प्रकार: {'डिमांड (Demand)' if is_demand_zone else 'सप्लाई (Supply)'}\n"
        f"मौजूदा प्राइस: {price.get('price') if price and price.get('ok') else 'उपलब्ध नहीं'} "
        f"({price.get('change_pct') if price and price.get('ok') else '-'}% )\n"
        f"OI पुष्टि: {_fmt_oi_facts(oi_confirmation)}\n"
        f"FII/DII (पिछले 3 दिन):\n{_fmt_fii_dii_facts(fii_dii)}\n"
        f"सत्यापित न्यूज़/इवेंट्स:\n{_fmt_news_facts(news)}\n"
        f"ग्लोबल इंस्ट्रूमेंट्स:\n{_fmt_global_facts(global_prices)}\n"
        f"Nifty 50 में विपरीत ज़ोन: "
        f"{'हां' if nifty_zone_conflict else ('नहीं' if nifty_zone_conflict is False else 'जांचा नहीं गया')}\n"
    )

    ai_result = ai_provider.generate_text(prompt=facts_block, system=SYSTEM_PROMPT_HI, max_tokens=120)

    if ai_result.get("ok") and ai_result.get("text"):
        # Hard-enforce the 2-line cap regardless of what the model returned.
        lines = [ln.strip() for ln in ai_result["text"].splitlines() if ln.strip()]
        hypothesis_text = " ".join(lines[:2]) if lines else ai_result["text"][:280]
        source = ai_result["source"]
    else:
        hypothesis_text = _rule_based_fallback(
            symbol, is_demand_zone, oi_confirmation, fii_dii, news, nifty_zone_conflict,
        )
        source = "Rule-based fallback"

    result = {
        "ok": True,
        "hypothesis": hypothesis_text,
        "source": source,
        "nifty_zone_conflict": nifty_zone_conflict,
        "oi_confirmation": oi_confirmation,
    }
    HYPOTHESIS_CACHE.set(cache_key, result, HYPOTHESIS_CACHE_TTL_SEC)
    return result
