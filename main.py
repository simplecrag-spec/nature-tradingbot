from __future__ import annotations

import csv
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geonamescache
import requests

ROOT = Path(__file__).resolve().parent
STOCK_FILE = ROOT / "data" / "stocks.csv"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

WEIGHTS = {
    "temperature": 0.25,
    "humidity": 0.15,
    "pressure": 0.15,
    "cloud_cover": 0.15,
    "wind": 0.15,
    "precipitation": 0.05,
    "rain": 0.05,
    "snow": 0.05,
}


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def gaussian_score(value: float, ideal: float, spread: float) -> float:
    return clamp(100.0 * math.exp(-((value - ideal) ** 2) / (2.0 * spread ** 2)))


def inverse_score(value: float, scale: float) -> float:
    return clamp(100.0 * math.exp(-max(0.0, value) / scale))


def natural_number(ticker: str) -> float:
    """Experimental deterministic ticker mathematics; not a validated financial model."""
    clean = "".join(ch for ch in ticker.upper() if ch.isalnum())
    if not clean:
        return 0.0
    weighted = 0
    digit_sum = 0
    for i, ch in enumerate(clean, start=1):
        value = ord(ch) - 64 if ch.isalpha() else int(ch)
        if ch.isdigit():
            digit_sum += value
        weighted += value * i
    return round((weighted * 0.618033988749895 +
                  digit_sum * 1.4142135623730951) % 100.0, 4)


# ---------------- CITY SELECTION ----------------
# Celestial mathematics is used ONLY here.
# It is intentionally never passed into nature_score().


def solar_declination(day_of_year: int) -> float:
    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1)
    return (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )


def celestial_city_score(latitude: float, longitude: float, when: datetime) -> float:
    day = when.timetuple().tm_yday
    decl = solar_declination(day)

    utc_hours = when.hour + when.minute / 60.0 + when.second / 3600.0
    solar_hours = (utc_hours + longitude / 15.0) % 24.0
    hour_angle = math.radians(15.0 * (solar_hours - 12.0))

    lat = math.radians(latitude)
    altitude = math.asin(
        math.sin(lat) * math.sin(decl)
        + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    )
    altitude_deg = math.degrees(altitude)

    solar_component = gaussian_score(altitude_deg, 45.0, 32.0)
    latitude_component = gaussian_score(abs(latitude), 25.0, 35.0)
    longitude_component = 100.0 - abs(math.sin(math.radians(longitude))) * 12.0

    return 0.65 * solar_component + 0.25 * latitude_component + 0.10 * longitude_component


def discover_world_locations() -> list[dict[str, Any]]:
    """
    Stable GeoNames-derived local dataset supplied by geonamescache.
    No world-country discovery API is called at runtime.
    """
    gc = geonamescache.GeonamesCache(min_city_population=5000)
    countries = gc.get_countries()
    cities = gc.get_cities()

    locations = []
    for city in cities.values():
        try:
            country_code = city.get("countrycode")
            country = countries.get(country_code, {})
            name = city.get("name")
            lat = float(city.get("latitude"))
            lon = float(city.get("longitude"))
            population = int(city.get("population") or 0)

            if (
                not name
                or not country_code
                or not (-90 <= lat <= 90)
                or not (-180 <= lon <= 180)
            ):
                continue

            locations.append({
                "name": name,
                "country": country.get("name", country_code),
                "country_code": country_code,
                "latitude": lat,
                "longitude": lon,
                "population": population,
            })
        except (TypeError, ValueError):
            continue

    if not locations:
        raise RuntimeError("Bundled geographic dataset produced zero valid cities")

    return locations


def select_city(locations: list[dict[str, Any]], when: datetime) -> dict[str, Any]:
    scored = []

    for city in locations:
        score = celestial_city_score(
            city["latitude"], city["longitude"], when
        )

        # Small geographic tie-breaker only.
        population_bonus = min(
            5.0, math.log10(max(1, city["population"])) / 2.0
        )

        scored.append((score + population_bonus, city))

    scored.sort(
        key=lambda x: (-x[0], x[1]["country_code"], x[1]["name"])
    )
    return scored[0][1]


# ---------------- LIVE WEATHER ----------------


def fetch_live_weather(city: dict[str, Any]) -> dict[str, float]:
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "current": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "pressure_msl",
            "cloud_cover",
            "wind_speed_10m",
            "precipitation",
            "rain",
            "snowfall",
        ]),
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "timezone": "auto",
    }

    response = requests.get(
        OPEN_METEO_URL, params=params, timeout=30
    )
    response.raise_for_status()

    current = response.json().get("current") or {}

    required = [
        "temperature_2m",
        "relative_humidity_2m",
        "pressure_msl",
        "cloud_cover",
        "wind_speed_10m",
        "precipitation",
        "rain",
        "snowfall",
    ]

    missing = [key for key in required if current.get(key) is None]
    if missing:
        raise RuntimeError(
            "Open-Meteo response missing live fields: "
            + ", ".join(missing)
        )

    return {
        "temperature": float(current["temperature_2m"]),
        "humidity": float(current["relative_humidity_2m"]),
        "pressure": float(current["pressure_msl"]),
        "cloud_cover": float(current["cloud_cover"]),
        "wind": float(current["wind_speed_10m"]),
        "precipitation": float(current["precipitation"]),
        "rain": float(current["rain"]),
        "snow": float(current["snowfall"]),
    }


def nature_score(weather: dict[str, float]) -> float:
    """
    100% WEATHER.
    Celestial/city-selection values are deliberately excluded.
    """
    component_scores = {
        "temperature": gaussian_score(
            weather["temperature"], 20.0, 12.0
        ),
        "humidity": gaussian_score(
            weather["humidity"], 50.0, 25.0
        ),
        "pressure": gaussian_score(
            weather["pressure"], 1013.25, 18.0
        ),
        "cloud_cover": gaussian_score(
            weather["cloud_cover"], 35.0, 30.0
        ),
        "wind": inverse_score(
            weather["wind"], 35.0
        ),
        "precipitation": inverse_score(
            weather["precipitation"], 8.0
        ),
        "rain": inverse_score(
            weather["rain"], 5.0
        ),
        "snow": inverse_score(
            weather["snow"], 2.0
        ),
    }

    return round(
        sum(component_scores[k] * WEIGHTS[k] for k in WEIGHTS),
        2,
    )


# ---------------- GLOBAL STOCK UNIVERSE ----------------


def load_stock_universe() -> list[dict[str, str]]:
    if not STOCK_FILE.exists():
        raise RuntimeError(
            f"Stock universe cache not found: {STOCK_FILE}"
        )

    with STOCK_FILE.open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))

    rows = [
        row
        for row in rows
        if row.get("ticker")
        and row.get("name")
        and row.get("exchange")
    ]

    if not rows:
        raise RuntimeError("Stock universe cache contains no valid stocks")

    return rows


def choose_stock(
    stocks: list[dict[str, str]], score: float
) -> dict[str, Any]:
    candidates = []

    for stock in stocks:
        nn = natural_number(stock["ticker"])

        # Stock-country information is intentionally NOT used.
        compatibility = round(
            100.0 - abs(nn - score), 2
        )

        final = round(
            0.5 * score + 0.5 * compatibility, 2
        )

        candidates.append(
            (final, compatibility, nn, stock)
        )

    candidates.sort(
        key=lambda x: (
            -x[0],
            -x[1],
            x[3]["ticker"],
            x[3]["exchange"],
        )
    )

    final, compatibility, nn, stock = candidates[0]

    signal = (
        "BUY"
        if final >= 80
        else "WATCH"
        if final >= 65
        else "SELL"
    )

    return {
        "stock": stock,
        "natural_number": nn,
        "compatibility": compatibility,
        "final_score": final,
        "signal": signal,
    }


def run_engine() -> dict[str, Any]:
    now = datetime.now(timezone.utc)

    # 1. City first.
    locations = discover_world_locations()
    city = select_city(locations, now)

    # 2. Then live weather for that exact city.
    weather = fetch_live_weather(city)
    score = nature_score(weather)

    # 3. Then global stock universe. City country is NOT a filter.
    stocks = load_stock_universe()
    selection = choose_stock(stocks, score)

    return {
        "city": city,
        "nature_score": score,
        **selection,
    }


def main() -> int:
    try:
        result = run_engine()

        city = result["city"]
        stock = result["stock"]

        print("🌍 NATURE TRADING BOT")
        print()
        print(
            f"Selected City: "
            f"{city['name']}, {city['country']}"
        )
        print(
            f"Live Nature Score: "
            f"{result['nature_score']:.2f}"
        )
        print()
        print(
            f"Selected Stock: "
            f"{stock['ticker']} ({stock['exchange']})"
        )
        print(
            f"Natural Number: "
            f"{result['natural_number']:.4f}"
        )
        print(
            f"Compatibility: "
            f"{result['compatibility']:.2f}"
        )
        print(
            f"Final Score: "
            f"{result['final_score']:.2f}"
        )

        icon = (
            "🟢"
            if result["signal"] == "BUY"
            else "🟡"
            if result["signal"] == "WATCH"
            else "🔴"
        )
        print(
            f"Signal: {icon} {result['signal']}"
        )

        return 0

    except Exception as exc:
        print(f"ENGINE_ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
