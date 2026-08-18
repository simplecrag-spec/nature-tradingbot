import csv
import os
import sys
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STOCK_FILE = ROOT / "data" / "stocks.csv"
OPENFIGI_URL = "https://api.openfigi.com/v3/filter"

def fetch_global_equities(api_key: str) -> list[dict[str, str]]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    else:
        print("WARNING: No OpenFIGI API key found. Rate limits will be restricted.", file=sys.stderr)

    exchanges = ["US", "JP", "GB", "IN", "DE"]
    stocks = []

    for exch in exchanges:
        payload = {
            "exchCode": exch,
            "securityType2": "Common Stock"
        }
        
        response = requests.post(OPENFIGI_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            print(f"Failed to fetch {exch}: {response.text}", file=sys.stderr)
            continue
            
        data = response.json()
        
        for item in data.get("data", []):
            ticker = item.get("ticker")
            name = item.get("name")
            exchange = item.get("exchCode")
            
            if ticker and name and exchange:
                stocks.append({
                    "ticker": ticker,
                    "name": name,
                    "exchange": exchange
                })
                
    return stocks

def update_csv_cache(stocks: list[dict[str, str]]):
    STOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    with STOCK_FILE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "name", "exchange"])
        writer.writeheader()
        writer.writerows(stocks)
        
    print(f"Successfully updated cache with {len(stocks)} instruments.")

def main() -> int:
    api_key = os.getenv("OPENFIGI_API_KEY")
    try:
        print("Fetching global universe from OpenFIGI...")
        stocks = fetch_global_equities(api_key)
        
        if not stocks:
            raise RuntimeError("No stocks retrieved from API.")
            
        update_csv_cache(stocks)
        return 0
    except Exception as exc:
        print(f"UPDATER_ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
