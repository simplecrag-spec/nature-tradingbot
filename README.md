# 🌍 Nature Trading Bot

Research / paper-trading project.

## Architecture: two workflows, two cadences

- **`nature_bot.yml`** -- runs every 6 hours. Picks a city, scores live
  weather, and picks one stock from the *cached* universe. Never fetches
  the universe itself.
- **`update-universe.yml`** -- runs weekly (or on demand). Rebuilds
  `data/stocks.csv` from scratch and commits it back to the repo.

They're split because rebuilding a multi-thousand-ticker universe is
slower and more failure-prone than the live run, and because which
tickers *exist* barely changes hour to hour, so there's no reason to
redo that work every 6 hours.

## Core pipeline (live run, unchanged)

1. Global geographic universe is loaded locally through `geonamescache`.
2. Celestial + geographic mathematics selects one strongest city.
3. Live Open-Meteo conditions are fetched for that exact city.
4. Nature Score is calculated from weather only:
   - Temperature 25%
   - Humidity 15%
   - Pressure 15%
   - Cloud cover 15%
   - Wind 15%
   - Precipitation 5%
   - Rain 5%
   - Snow 5%
5. The stock universe is global and is NOT filtered by the selected city's country.
6. Each ticker receives a deterministic experimental Natural Number.
7. Stocks are compared silently and one winner is printed.
8. A paper-only position plan (allocation / holding period / entry-exit
   timing) is derived from the same scores -- see below.
9. No historical market data and no real-money trading are used.

## Position plan (new)

For a `BUY` signal, `main.py` now also prints a suggested paper
allocation (1-8% of a hypothetical portfolio, scaled by final score),
a target holding period (3-21 days, scaled by compatibility), and an
entry/exit timestamp pair. This is a deterministic extension of the
existing scoring -- same experimental status as everything else here,
not a risk-management method. If this bot is ever pointed at real
capital, replace this layer with actual position sizing and risk
management rather than tuning it further.

## Important scientific status

The Natural Number, compatibility mathematics, and the position-plan
sizing are all experimental constructs. None of them are established or
scientifically validated financial relationships.

## Stock universe: how it's built

`data/stocks.csv` is the local cache the live engine reads. `update_universe.py`
rebuilds it from free sources only:

1. **NASDAQ Trader symbol directory** (`nasdaqlisted.txt` + `otherlisted.txt`)
   -- no key required, ~10,000+ US-listed common stocks across
   NASDAQ/NYSE/NYSE American/Arca/Cboe/IEX. This is the reliable backbone.
2. **Wikipedia index-constituent tables** for FTSE 100, DAX, CAC 40,
   Nikkei 225, Hang Seng, S&P/TSX, S&P/ASX 200, and NIFTY 50 -- best
   effort. Each source is independent and non-fatal: if one page's table
   structure has changed, that source is skipped (logged to the Actions
   run) and every other source still lands in the cache.
3. **OpenFIGI** (optional, only if `OPENFIGI_API_KEY` is set) -- used
   *only* to validate the US ticker list in batches and drop anything it
   no longer recognizes. OpenFIGI maps identifiers you already have to
   metadata; it is not a bulk-discovery API, so it's deliberately not used
   to "find" the universe.

**Honest scope note:** this gets you a genuinely global universe in the
tens of thousands of tickers, not literally every share on every stock
exchange on Earth -- a complete, always-current global reference-data
feed like that is enterprise-priced everywhere (Bloomberg, Refinitiv,
etc.), not available for free. If you want deeper coverage of a specific
market later, add another source function to `update_universe.py` -- the
merge step dedupes by (ticker, exchange) automatically.

API keys must be stored in GitHub Actions Secrets and never hard-coded.

## GitHub Actions

- `nature_bot.yml`: `workflow_dispatch` + every 6 hours.
- `update-universe.yml`: `workflow_dispatch` + weekly (Sundays 04:10 UTC),
  needs `contents: write` to commit the refreshed `data/stocks.csv` back
  to the repo. If your default branch has protection rules that block
  bot pushes, you'll need a PAT with write access instead of the default
  `GITHUB_TOKEN`.

Run `update-universe.yml` manually first and check it lands a real
`data/stocks.csv`, then let `nature_bot.yml`'s schedule run on top of it.
