# 🌍 Nature Trading Bot

Research / paper-trading project.

## Core pipeline

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
8. No historical market data and no real-money trading are used.

## Important scientific status

The Natural Number and compatibility mathematics are experimental constructs. They are not established or scientifically validated financial relationships.

## Stock universe architecture

`data/stocks.csv` is the local cache used by the engine.

The intended next layer is a periodic OpenFIGI-based universe updater. That updater should refresh the cache rather than making the live Nature run rediscover thousands of instruments every time.

API keys must be stored in GitHub Actions Secrets and never hard-coded.

## GitHub Actions

The workflow supports:

- manual `workflow_dispatch`
- scheduled execution every 6 hours

The schedule should only be enabled as the final step after a successful manual run.
