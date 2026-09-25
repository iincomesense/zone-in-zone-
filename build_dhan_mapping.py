"""
build_dhan_mapping.py - one-time (or periodic) script that downloads
Dhan's public scrip-master CSV and builds data/dhan_security_ids.json,
mapping every NSE_INSTRUMENTS symbol (from instruments.py) to its Dhan
security_id. scheduler._fetch_candles() and any Dhan quote/order call
reads that JSON at runtime rather than hitting Dhan's CSV every time.

Run manually or on a daily cron/Render job:
    python build_dhan_mapping.py

Dhan publishes this CSV at a stable, undocumented-but-widely-used URL;
if it 404s, check Dhan's latest API docs for the current scrip-master
link and update DHAN_SCRIP_MASTER_URL below.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Dict

import requests

import instruments

DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
OUTPUT_PATH = Path(__file__).parent / "data" / "dhan_security_ids.json"

# Column names as published by Dhan's scrip master. Verify these against
# the actual CSV header the first time you run this - Dhan has changed
# column names before without notice.
COL_SYMBOL = "SEM_TRADING_SYMBOL"
COL_SECURITY_ID = "SEM_SMST_SECURITY_ID"
COL_EXCH = "SEM_EXM_EXCH_ID"
COL_SEGMENT = "SEM_SEGMENT"
COL_INSTRUMENT = "SEM_EXCH_INSTRUMENT_TYPE"


def download_scrip_master(timeout: float = 30.0) -> str:
    resp = requests.get(DHAN_SCRIP_MASTER_URL, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def build_mapping(csv_text: str) -> Dict[str, Dict[str, str]]:
    """Returns {trading_symbol: {"security_id": ..., "exchange_segment": "NSE_EQ"}}
    restricted to NSE cash-equity rows, matched against instruments.NSE_INSTRUMENTS."""
    wanted_symbols = set(instruments.NSE_INSTRUMENTS.keys())
    reader = csv.DictReader(io.StringIO(csv_text))

    mapping: Dict[str, Dict[str, str]] = {}
    unmatched = set(wanted_symbols)

    for row in reader:
        try:
            exch = row.get(COL_EXCH, "").strip().upper()
            segment = row.get(COL_SEGMENT, "").strip().upper()
            symbol = row.get(COL_SYMBOL, "").strip().upper()
            sec_id = row.get(COL_SECURITY_ID, "").strip()
        except AttributeError:
            continue

        if exch != "NSE" or "EQ" not in segment:
            continue
        if symbol not in wanted_symbols:
            continue
        if not sec_id:
            continue

        mapping[symbol] = {"security_id": sec_id, "exchange_segment": "NSE_EQ"}
        unmatched.discard(symbol)

    if unmatched:
        print(f"[warn] {len(unmatched)} symbols from instruments.py not found in "
              f"scrip master (name mismatch is common - check manually): "
              f"{sorted(unmatched)[:20]}{'...' if len(unmatched) > 20 else ''}", file=sys.stderr)

    return mapping


def main() -> None:
    print(f"Downloading Dhan scrip master from {DHAN_SCRIP_MASTER_URL} ...")
    csv_text = download_scrip_master()
    mapping = build_mapping(csv_text)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(mapping)} symbol mappings to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
