"""
instruments.py - GLOBAL and NSE INSTRUMENTS with Add Option.

Same data as originally supplied, with one change: get_live_price_with_change()
now goes through providers.price_provider's Dhan -> NSE -> Yahoo fallback
chain instead of calling yfinance directly, so it benefits from the same
caching and no-crash guarantees as the rest of the pipeline.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from providers import price_provider

# GLOBAL INSTRUMENTS - Add करने का विकल्प हो
GLOBAL_INSTRUMENTS: Dict[str, Dict[str, str]] = {
    "DXY": {"name": "US Dollar Index", "symbol": "DX-Y.NYB", "desc": "अमेरिकी डॉलर इंडेक्स", "yahoo": "DX-Y.NYB", "tradingview": "TVC:DXY"},
    "USDINR": {"name": "USD/INR", "symbol": "USDINR=X", "desc": "अमेरिकी डॉलर बनाम भारतीय रुपया", "yahoo": "USDINR=X", "tradingview": "FX_IDC:USDINR"},
    "TLT": {"name": "iShares 20+ Year Treasury Bond ETF", "symbol": "TLT", "desc": "यूएस बॉन्ड ईटीएफ", "yahoo": "TLT", "tradingview": "NASDAQ:TLT"},
    "US10Y": {"name": "US 10-Year Treasury Yield", "symbol": "^TNX", "desc": "अमेरिकी 10-वर्षीय बॉन्ड यील्ड", "yahoo": "^TNX", "tradingview": "TVC:US10Y"},
    "XAUUSD": {"name": "Gold / US Dollar", "symbol": "GC=F", "desc": "गोल्ड / अमेरिकी डॉलर", "yahoo": "GC=F", "tradingview": "TVC:GOLD"},
    "XAGUSD": {"name": "Silver / US Dollar", "symbol": "SI=F", "desc": "चांदी / यूएस डॉलर", "yahoo": "SI=F", "tradingview": "TVC:SILVER"},
    "SPOTCRUDE": {"name": "WTI Crude Oil Spot", "symbol": "CL=F", "desc": "कच्चा तेल / क्रूड ऑयल", "yahoo": "CL=F", "tradingview": "TVC:USOIL"},
    "US30": {"name": "Dow Jones Industrial Average", "symbol": "^DJI", "desc": "अमेरिकी डाउ जोन्स इंडेक्स", "yahoo": "^DJI", "tradingview": "TVC:DJI"},
    "US500": {"name": "S&P 500", "symbol": "^GSPC", "desc": "अमेरिकी एस एंड पी 500 इंडेक्स", "yahoo": "^GSPC", "tradingview": "TVC:SPX"},
    "000001": {"name": "SSE Composite Index", "symbol": "000001.SS", "desc": "शंघाई, चीन शेयर बाज़ार इंडेक्स", "yahoo": "000001.SS", "tradingview": "SSE:000001"},
    "XIN9": {"name": "FTSE China A50 Index", "symbol": "XIN9.FT", "desc": "चाइना A50 इंडेक्स", "yahoo": "XIN9.FT", "tradingview": "FTSE:XIN9"},
    "JP225": {"name": "Nikkei 225", "symbol": "^N225", "desc": "जापानी शेयर बाज़ार इंडेक्स", "yahoo": "^N225", "tradingview": "TVC:NI225"},
    "GIFTNIFTY": {"name": "GIFT NIFTY 50 INDEX FUTURES", "symbol": "^NSEI", "desc": "GIFT निफ्टी 50 फ्यूचर्स - गिफ्ट सिटी", "yahoo": "^NSEI", "tradingview": "NSE:NIFTY"},
    "NIFTY1!": {"name": "Nifty 50 Futures", "symbol": "^NSEI", "desc": "भारतीय निफ्टी 50 फ्यूचर्स", "yahoo": "^NSEI", "tradingview": "NSE:NIFTY"},
    "FTSE100": {"name": "FTSE 100", "symbol": "^FTSE", "desc": "यूके/लंदन शेयर बाज़ार इंडेक्स", "yahoo": "^FTSE", "tradingview": "TVC:UKX"},
    "DAX": {"name": "DAX Index", "symbol": "^GDAXI", "desc": "जर्मन शेयर बाज़ार इंडेक्स", "yahoo": "^GDAXI", "tradingview": "XETR:DAX"},
}

# NSE INSTRUMENTS - Add करने का विकल्प हो - Provided list (symbol -> instrument token)
NSE_INSTRUMENTS: Dict[str, int] = {
    "360ONE": 46340, "ABB": 157024, "ABCAPITAL": 110843, "ADANIENSOL": 172388, "ADANIENT": 397724,
    "ADANIGREEN": 214347, "ADANIPORTS": 393355, "ADANIPOWER": 399579, "ALKEM": 62054, "AMBER": 26359,
    "AMBUJACEM": 100635, "ANGELONE": 27392, "APLAPOLLO": 62473, "APOLLOHOSP": 124374, "ASHOKLEY": 99268,
    "ASIANPAINT": 242278, "ASTRAL": 40107, "ATHERENERG": 62822, "AUBANK": 79769, "AUROPHARMA": 95116,
    "AXISBANK": 396295, "BAJAJ-AUTO": 327539, "BAJAJFINSV": 315248, "BAJAJHLDNG": 124226, "BAJFINANCE": 659728,
    "BANDHANBNK": 26575, "BANKBARODA": 123596, "BANKINDIA": 66014, "BDL": 46004, "BEL": 296302,
    "BHARATFORG": 93514, "BHARTIARTL": 1147913, "BHEL": 150112, "BIOCON": 64708, "BLUESTARCO": 30510,
    "BOSCHLTD": 138101, "BPCL": 134886, "BRITANNIA": 122879, "BSE": 138482, "CAMS": 18593,
    "CANBK": 113837, "CDSL": 29147, "CGPOWER": 140373, "CHOLAFIN": 157053, "CIPLA": 111888,
    "COALINDIA": 255969, "COCHINSHIP": 39438, "COFORGE": 87391, "COLPAL": 49752, "CONCOR": 38690,
    "CROMPTON": 14965, "CUMMINSIND": 139570, "DABUR": 67570, "DELHIVERY": 34299, "DIVISLAB": 241576,
    "DIXON": 86582, "DLF": 168792, "DMART": 245897, "DRREDDY": 95877, "EICHERMOT": 209474,
    "ETERNAL": 297104, "FEDERALBNK": 84707, "FORCEMOT": 23058, "FORTIS": 68324, "GAIL": 114012,
    "GLENMARK": 68784, "GMRAIRPORT": 102897, "GODFRYPHLP": 31992, "GODREJCP": 89846, "GODREJPROP": 59703,
    "GRASIM": 225316, "GVT&D": 110261, "HAL": 324757, "HAVELLS": 72421, "HCLTECH": 349964,
    "HDFCAMC": 105414, "HDFCBANK": 1097417, "HDFCLIFE": 118681, "HEROMOTOCO": 106075, "HINDALCO": 224426,
    "HINDPETRO": 75857, "HINDUNILVR": 463668, "HINDZINC": 253942, "HYUNDAI": 179165, "ICICIBANK": 1021468,
    "ICICIGI": 75440, "ICICIPRULI": 71698, "IDEA": 162731, "IDFCFIRSTB": 74759, "IEX": 10580,
    "INDHOTEL": 104156, "INDIANB": 118492, "INDIGO": 192444, "INDUSINDBK": 78576, "INDUSTOWER": 99370,
    "INFY": 457664, "INOXWIND": 12858, "IOC": 194308, "IREDA": 31924, "IRFC": 108991,
    "ITC": 330912, "JINDALSTEL": 118537, "JIOFIN": 158145, "JSWENERGY": 98921, "JSWSTEEL": 323410,
    "JUBLFOOD": 31746, "KALYANKJIL": 61841, "KAYNES": 24187, "KEI": 46366, "KFINTECH": 16058,
    "KOTAKBANK": 422278, "KPITTECH": 15665, "LAURUSLABS": 99417, "LICHSGFIN": 30916, "LICI": 262709,
    "LODHA": 122069, "LT": 545407, "LTF": 78690, "LTM": 135008, "LUPIN": 96483, "M&M": 380642,
    "MAHABANK": 66109, "MANAPPURAM": 31909, "MANKIND": 96153, "MARICO": 105658, "MARUTI": 399103,
    "MAXHEALTH": 95852, "MAZDOCK": 99837, "MCX": 83364, "MFSL": 53156, "MOTHERSON": 169237,
    "MOTILALOFS": 62461, "MPHASIS": 46216, "MUTHOOTFIN": 116980, "NAM-INDIA": 74208, "NATIONALUM": 68708,
    "NAUKRI": 85488, "NBCC": 23382, "NESTLEIND": 271989, "NHPC": 76593, "NMDC": 74467,
    "NTPC": 322414, "NYKAA": 95264, "OBEROIRLTY": 68197, "OFSS": 105005, "OIL": 79427,
    "ONGC": 295196, "PAGEIND": 40489, "PATANJALI": 37260, "PAYTM": 106396, "PERSISTENT": 88161,
    "PETRONET": 43215, "PFC": 117352, "PGEL": 16110, "PHOENIXLTD": 69461, "PIDILITIND": 165863,
    "PIIND": 37396, "PNB": 134467, "PNBHOUSING": 30410, "POLICYBZR": 84831, "POLYCAB": 125027,
    "POWERGRID": 247396, "POWERINDIA": 141072, "PREMIERENE": 45235, "PRESTIGE": 68357, "RADICO": 60212,
    "RBLBANK": 64229, "RECLTD": 83921, "RELIANCE": 1788993, "RVNL": 44305, "SAGILITY": 21918,
    "SAIL": 81289, "SBICARD": 62642, "SBILIFE": 178095, "SBIN": 937923, "SHREECEM": 86071,
    "SHRIRAMFIN": 244946, "SIEMENS": 141629, "SOLARINDS": 194056, "SONACOMS": 49244, "SRF": 75495,
    "SUNPHARMA": 455634, "SUPREMEIND": 44161, "SUZLON": 62337, "SWIGGY": 72338, "TATACONSUM": 99959,
    "TATAELXSI": 22166, "TATAPOWER": 117588, "TATASTEEL": 235503, "TCS": 833607, "TECHM": 141469,
    "TIINDIA": 52226, "TITAN": 445431, "TMPV": 114722, "TORNTPHARM": 184745, "TRENT": 152131,
    "TVSMOTOR": 196416, "ULTRACEMCO": 335589, "UNIONBANK": 142916, "UNITDSPR": 104499, "UNOMINDA": 72166,
    "UPL": 49208, "VBL": 137869, "VEDL": 106250, "VMM": 49725, "VOLTAS": 38714, "WAAREEENER": 75796,
    "WIPRO": 174508, "YESBANK": 70623, "ZYDUSLIFE": 111720,
}


def add_global_instrument(key: str, name: str, yahoo_symbol: str, tradingview_symbol: str, desc: str = "") -> bool:
    """Add new global instrument - Option"""
    GLOBAL_INSTRUMENTS[key] = {
        "name": name, "symbol": yahoo_symbol, "desc": desc,
        "yahoo": yahoo_symbol, "tradingview": tradingview_symbol,
    }
    return True


def add_nse_instrument(symbol: str, token: int = 0) -> bool:
    """Add new NSE instrument - Option"""
    NSE_INSTRUMENTS[symbol] = token
    return True


def get_all_symbols() -> Dict[str, Any]:
    """Get all symbols list"""
    global_list = list(GLOBAL_INSTRUMENTS.keys())
    nse_list = list(NSE_INSTRUMENTS.keys())
    return {"global": global_list, "nse": nse_list, "total": len(global_list) + len(nse_list)}


def get_live_price_with_change(symbol: str, yahoo_symbol: Optional[str] = None) -> Dict[str, Any]:
    """Live price with change% - now via the multi-source fallback chain
    (Dhan -> NSE -> Yahoo) instead of Yahoo-only, so a single dead source
    can't blank out the dashboard header row."""
    meta = GLOBAL_INSTRUMENTS.get(symbol)
    resolved_yahoo = yahoo_symbol or (meta.get("yahoo") if meta else None)
    return price_provider.get_live_price(symbol, yahoo_symbol=resolved_yahoo)


def tradingview_link(symbol: str) -> Optional[str]:
    """Returns an inbuilt TradingView chart link for a global instrument,
    or a plausible NSE: prefixed one for an NSE equity - used by the
    dashboard's per-row chart link."""
    meta = GLOBAL_INSTRUMENTS.get(symbol)
    if meta and meta.get("tradingview"):
        return f"https://www.tradingview.com/chart/?symbol={meta['tradingview']}"
    if symbol in NSE_INSTRUMENTS:
        return f"https://www.tradingview.com/chart/?symbol=NSE:{symbol}"
    return None
