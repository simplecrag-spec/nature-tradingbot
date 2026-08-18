from __future__ import annotations

"""
Universe updater -- builds data/stocks.csv from free public sources.

Run this periodically (see .github/workflows/update-universe.yml), NOT on
every live run. main.py never fetches the universe itself; it only ever
reads the cached CSV this script writes. That split is intentional (see
README.md) -- it keeps the every-6h live run fast and keeps universe
discovery, which is slower and more failure-prone, on its own schedule.

Sources, each independent and best-effort. If one source fails or a page's
structure has changed, we log a warning to stderr and keep whatever the
OTHER sources returned rather than failing the whole run:

  1. NASDAQ Trader symbol directory (nasdaqlisted.txt + otherlisted.txt).
     No key required. Covers essentially all US-listed common stock across
     NASDAQ / NYSE / NYSE American / NYSE Arca / Cboe / IEX. This is the
     reliable backbone of the universe -- if every other source fails, this
     one alone still gets you several thousand tickers.

  2. Wikipedia index-constituent tables for a handful of major non-US
     markets. No key required, but Wikipedia table markup can drift, so
     each page is parsed by *looking for* a ticker-like + company-like
     column pair rather than assuming an exact header/position, and any
     page that doesn't match that shape is skipped rather than crashing
     the run.

  3. OpenFIGI mapping (optional, only if OPENFIGI_API_KEY is set as a repo
     secret). OpenFIGI maps identifiers you already have to metadata -- it
     is NOT a bulk discovery/listing API, so it is deliberately NOT used to
     "find" the universe (that was very likely why the old script failed
     repeatedly). Here it's used only to VALIDATE the US ticker list in
     batches of 100 and drop anything OpenFIGI no longer recognizes (e.g.
     delisted symbols). If this step fails or the key is absent, we skip it
     and keep the unvalidated list -- it never blocks the run.

Nothing here is a validated stock-picking signal. It just builds the
candidate list that main.py's (equally experimental) scoring runs over.
"""

import csv
import io
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
STOCK_FILE = ROOT / "data" / "stocks.csv"

REQUEST_HEADERS = {
    # A descriptive UA is good practice for both NASDAQ Trader and Wikipedia.
    "User-Agent": "nature-trading-bot-universe-updater/1.0 (personal research project)"
}

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

OTHER_EXCHANGE_CODES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEXG",
}

# (display name, Wikipedia URL, country, exchange label)
WIKIPEDIA_INDEX_SOURCES: list[tuple[str, str, str, str]] = [
    ("FTSE 100", "https://en.wikipedia.org/wiki/FTSE_100_Index", "United Kingdom", "LSE"),
    ("DAX", "https://en.wikipedia.org/wiki/DAX", "Germany", "XETRA"),
    ("CAC 40", "https://en.wikipedia.org/wiki/CAC_40", "France", "Euronext Paris"),
    ("Nikkei 225", "https://en.wikipedia.org/wiki/Nikkei_225", "Japan", "TSE"),
    ("Hang Seng Index", "https://en.wikipedia.org/wiki/Hang_Seng_Index", "Hong Kong", "HKEX"),
    ("S&P/TSX Composite Index", "https://en.wikipedia.org/wiki/S%26P/TSX_Composite_Index", "Canada", "TSX"),
    ("S&P/ASX 200", "https://en.wikipedia.org/wiki/S%26P/ASX_200", "Australia", "ASX"),
    ("NIFTY 50", "https://en.wikipedia.org/wiki/NIFTY_50", "India", "NSE"),
]

TICKER_HEADER_HINTS = ("ticker", "symbol", "code", "epic")
NAME_HEADER_HINTS = ("company", "name", "constituent")

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_BATCH_SIZE = 100  # max mapping jobs per request when using an API key


# ---------------- US: NASDAQ Trader symbol directory ----------------


def _download_text(url: str) -> str:
    response = requests.get(url, timeout=30, headers=REQUEST_HEADERS)
    response.raise_for_status()
    return response.text


def _strip_footer(lines: list[str]) -> list[str]:
    # Both files end with a "File Creation Time: ..." line, not a data row.
    if lines and lines[-1].lower().startswith("file creation time"):
        return lines[:-1]
    return lines


def _parse_nasdaq_listed(text: str) -> list[dict[str, str]]:
    lines = _strip_footer([ln for ln in text.splitlines() if ln.strip()])
    rows = []
    for record in csv.DictReader(lines, delimiter="|"):
        if record.get("Test Issue") == "Y" or record.get("ETF") == "Y":
            continue
        symbol = (record.get("Symbol") or "").strip()
        name = (record.get("Security Name") or "").strip()
        if not symbol or not name:
            continue
        rows.append({
            "ticker": symbol,
            "name": name,
            "country": "USA",
            "exchange": "NASDAQ",
        })
    return rows


def _parse_other_listed(text: str) -> list[dict[str, str]]:
    lines = _strip_footer([ln for ln in text.splitlines() if ln.strip()])
    rows = []
    for record in csv.DictReader(lines, delimiter="|"):
        if record.get("Test Issue") == "Y" or record.get("ETF") == "Y":
            continue
        symbol = (record.get("ACT Symbol") or "").strip()
        name = (record.get("Security Name") or "").strip()
        exch_code = (record.get("Exchange") or "").strip()
        if not symbol or not name:
            continue
        rows.append({
            "ticker": symbol.replace(".", "-"),
            "name": name,
            "country": "USA",
            "exchange": OTHER_EXCHANGE_CODES.get(exch_code, exch_code or "US-OTHER"),
        })
    return rows


def fetch_us_nasdaq_trader() -> list[dict[str, str]]:
    rows = _parse_nasdaq_listed(_download_text(NASDAQ_LISTED_URL))
    rows += _parse_other_listed(_download_text(OTHER_LISTED_URL))
    return rows


# ---------------- International: Wikipedia index constituents ----------------


def fetch_wikipedia_index(name: str, url: str, country: str, exchange: str) -> list[dict[str, str]]:
    response = requests.get(url, timeout=30, headers=REQUEST_HEADERS)
    response.raise_for_status()
    tables = pd.read_html(io.StringIO(response.text))

    for table in tables:
        ticker_col = next(
            (c for c in table.columns if any(h in str(c).lower() for h in TICKER_HEADER_HINTS)),
            None,
        )
        name_col = next(
            (c for c in table.columns if any(h in str(c).lower() for h in NAME_HEADER_HINTS)),
            None,
        )
        if ticker_col is None or name_col is None:
            continue

        rows = []
        for _, record in table.iterrows():
            ticker = str(record[ticker_col]).strip()
            company = str(record[name_col]).strip()
            if not ticker or ticker.lower() == "nan" or not company or company.lower() == "nan":
                continue
            rows.append({
                "ticker": ticker,
                "name": company,
                "country": country,
                "exchange": exchange,
            })
        if rows:
            return rows

    raise RuntimeError(f"no ticker/company table found on the {name} Wikipedia page")


# ---------------- Merge ----------------


def merge_universe(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for group in groups:
        for row in group:
            key = (row["ticker"].upper(), row["exchange"])
            seen.setdefault(key, row)
    return sorted(seen.values(), key=lambda r: (r["exchange"], r["ticker"]))


# ---------------- Optional: OpenFIGI validation (US only) ----------------


def validate_us_tickers_with_openfigi(tickers: list[str], api_key: str) -> set[str]:
    """
    Best-effort only. Confirms which US tickers OpenFIGI currently
    recognizes (idType=TICKER, exchCode=US) and returns that subset.
    Any HTTP error propagates to the caller, which treats it as non-fatal.
    """
    confirmed: set[str] = set()
    headers = {"Content-Type": "application/json", "X-OPENFIGI-APIKEY": api_key}

    for i in range(0, len(tickers), OPENFIGI_BATCH_SIZE):
        batch = tickers[i:i + OPENFIGI_BATCH_SIZE]
        jobs = [{"idType": "TICKER", "idValue": t, "exchCode": "US"} for t in batch]

        response = requests.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=30)
        if response.status_code == 429:
            time.sleep(15)
            response = requests.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=30)
        response.raise_for_status()

        for ticker, result in zip(batch, response.json()):
            if isinstance(result, dict) and result.get("data"):
                confirmed.add(ticker)

        time.sleep(0.25)  # stay comfortably under the per-minute cap

    return confirmed


# ---------------- Orchestration ----------------


def build_universe() -> list[dict[str, str]]:
    groups: list[list[dict[str, str]]] = []

    try:
        us_rows = fetch_us_nasdaq_trader()
        print(f"NASDAQ Trader: {len(us_rows)} US rows", file=sys.stderr)
        groups.append(us_rows)
    except Exception as exc:
        print(f"NASDAQ Trader source failed (non-fatal): {exc}", file=sys.stderr)

    for name, url, country, exchange in WIKIPEDIA_INDEX_SOURCES:
        try:
            idx_rows = fetch_wikipedia_index(name, url, country, exchange)
            print(f"{name}: {len(idx_rows)} rows", file=sys.stderr)
            groups.append(idx_rows)
        except Exception as exc:
            print(f"{name} source failed (non-fatal): {exc}", file=sys.stderr)

    if not groups:
        raise RuntimeError("every universe source failed -- refusing to overwrite the cache with nothing")

    universe = merge_universe(*groups)

    api_key = os.environ.get("OPENFIGI_API_KEY", "").strip()
    if api_key:
        try:
            us_tickers = [r["ticker"] for r in universe if r["country"] == "USA"]
            confirmed = validate_us_tickers_with_openfigi(us_tickers, api_key)
            before = len(universe)
            universe = [r for r in universe if r["country"] != "USA" or r["ticker"] in confirmed]
            print(f"OpenFIGI validation: kept {len(universe)}/{before} rows", file=sys.stderr)
        except Exception as exc:
            print(f"OpenFIGI validation skipped (non-fatal): {exc}", file=sys.stderr)
    else:
        print("OPENFIGI_API_KEY not set -- skipping optional validation step", file=sys.stderr)

    return universe


def write_universe(rows: list[dict[str, str]]) -> None:
    STOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STOCK_FILE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "name", "country", "exchange"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    try:
        universe = build_universe()
        write_universe(universe)
        print(f"Wrote {len(universe)} rows to {STOCK_FILE}")
        return 0
    except Exception as exc:
        print(f"UNIVERSE_UPDATE_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
