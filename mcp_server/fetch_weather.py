"""
MCP Server: Fetch Weather
Uses Nominatim (OpenStreetMap) for free geocoding + Open-Meteo API for temperature data.
Fetches temperature forecasts based on historical averages for distant future dates.

Usage (standalone MCP server):
    python -m mcp_server.fetch_weather

Usage (import handlers in app.py):
    from mcp_server.fetch_weather import get_temperature, set_api_key, TOOL_HANDLERS

Requirements:
    - NO API keys needed! Uses completely free services:
      - Nominatim (OpenStreetMap) for geocoding
      - Open-Meteo for temperature data
"""

from __future__ import annotations

import json
import os
import traceback
import time
from datetime import date, datetime, timedelta

import requests


# ── Geocoding with Nominatim (free, no API key) ─────────────────────────────

def _geocode(city: str) -> tuple[float, float]:
    """Convert city name to (lat, lng) using Nominatim (OpenStreetMap) - FREE."""
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": city,
                "format": "json",
                "limit": 1,
            },
            headers={"User-Agent": "TinaTravel/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data:
            raise ValueError(f"Location not found: '{city}'")

        result = data[0]
        return float(result["lat"]), float(result["lon"])
    except Exception as e:
        raise ValueError(f"Geocoding failed for '{city}': {str(e)}")


# ── Tool Definitions (MCP schema) ───────────────────────────────────────────

TOOLS = [
    {
        "name": "get_temperature",
        "description": (
            "Get the historical average temperature (high/low in Celsius) "
            "for a city on a specific date or date range based on past 3 years of data. "
            "Uses Nominatim (OpenStreetMap) and Open-Meteo API - completely FREE, no API keys needed!"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name (e.g., 'Barcelona, Spain', 'Tokyo, Japan')",
                },
                "date": {
                    "type": "string",
                    "description": (
                        "Date to get temperature for in YYYY-MM-DD format. "
                        "If omitted, defaults to today."
                    ),
                },
                "end_date": {
                    "type": "string",
                    "description": (
                        "Optional end date (YYYY-MM-DD) for a multi-day range. "
                        "If omitted, only the single 'date' is returned."
                    ),
                },
            },
            "required": ["city"],
        },
    },
]


# ── Open-Meteo API ─────────────────────────────────────────────────────────


def _fetch_historical(lat: float, lng: float, start: date, end: date) -> dict:
    """Fetch daily historical temperature data from Open-Meteo Archive API."""
    resp = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": lat,
            "longitude": lng,
            "daily": "temperature_2m_max,temperature_2m_min",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": "auto",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_temperatures(data: dict) -> list[dict]:
    """Parse Open-Meteo daily response into a list of temperature entries."""
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])

    result = []
    for i, d in enumerate(dates):
        result.append({
            "date": d,
            "high_celsius": highs[i] if i < len(highs) else None,
            "low_celsius": lows[i] if i < len(lows) else None,
        })
    return result


def _calculate_climate_average(lat: float, lng: float, target_date: date) -> dict | None:
    """
    Calculate climate average for a specific date by querying historical data
    from the past 3 years on the same month/day.
    """
    today = date.today()
    all_temps = []

    # Query the same date from the past 3 years
    for year_offset in range(1, 4):
        query_date = target_date.replace(year=target_date.year - year_offset)
        # Only query if it's in the past
        if query_date >= today:
            continue

        try:
            hist_data = _fetch_historical(lat, lng, query_date, query_date)
            temps = _parse_temperatures(hist_data)
            if temps and temps[0].get("high_celsius") is not None:
                all_temps.append(temps[0])
        except Exception:
            # Skip years with errors
            continue

    if not all_temps:
        return None

    # Calculate averages
    avg_high = sum(t.get("high_celsius") for t in all_temps if t.get("high_celsius") is not None) / len(all_temps)
    avg_low = sum(t.get("low_celsius") for t in all_temps if t.get("low_celsius") is not None) / len(all_temps)

    return {
        "date": target_date.isoformat(),
        "high_celsius": round(avg_high, 1),
        "low_celsius": round(avg_low, 1),
        "is_climate_average": True,
        "based_on_years": len(all_temps),
    }


# ── Date helpers ───────────────────────────────────────────────────────────

def _parse_date(date_str: str) -> date:
    """Parse a YYYY-MM-DD string into a date object."""
    return datetime.strptime(date_str, "%Y-%m-%d").date()


# ── Tool Implementation ─────────────────────────────────────────────────────

def get_temperature(params: dict) -> str:
    """Get temperature forecast or historical average from Open-Meteo API."""
    city = params["city"]
    date_str = params.get("date")
    end_date_str = params.get("end_date")

    # Resolve dates
    today = date.today()

    if date_str:
        try:
            start_date = _parse_date(date_str)
        except ValueError:
            return json.dumps({
                "error": f"Invalid date format: '{date_str}'. Use YYYY-MM-DD.",
                "city": city,
            }, indent=2)
    else:
        start_date = today

    if end_date_str:
        try:
            end_date = _parse_date(end_date_str)
        except ValueError:
            return json.dumps({
                "error": f"Invalid end_date format: '{end_date_str}'. Use YYYY-MM-DD.",
                "city": city,
            }, indent=2)
    else:
        end_date = start_date

    if end_date < start_date:
        return json.dumps({
            "error": "end_date must be on or after date.",
            "city": city,
        }, indent=2)

    # Define forecast and historical windows
    historical_limit = today - timedelta(days=365 * 10)  # 10 years back

    try:
        # Geocode the city (using free Nominatim)
        lat, lng = _geocode(city)
        time.sleep(0.1)  # Rate limit for Nominatim (1 request/second recommended)

        temps = []
        data_source = None

        for check_date in [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]:
            if check_date >= historical_limit:
                climate = _calculate_climate_average(lat, lng, check_date)
                if climate:
                    temps.append(climate)
        data_source = "climate average (historical)"

        if not temps:
            return json.dumps({
                "error": (
                    f"No temperature data available for {start_date.isoformat()}"
                    + (f" to {end_date.isoformat()}" if end_date != start_date else "")
                    + ". Try dates closer to today or within 10 years in the past."
                ),
                "city": city,
            }, indent=2)

        result: dict = {
            "city": city,
            "coordinates": {"latitude": lat, "longitude": lng},
            "data_source": data_source,
        }

        if start_date == end_date:
            result["date"] = start_date.isoformat()
            t = temps[0]
            result["temperature"] = {
                "high_celsius": t.get("high_celsius"),
                "low_celsius": t.get("low_celsius"),
            }
            if t.get("is_climate_average"):
                result["note"] = f"Historical average based on {t.get('based_on_years')} years of data"
        else:
            result["date_range"] = {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            }
            result["temperatures"] = temps

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({
            "error": str(e),
            "detail": traceback.format_exc(),
            "city": city,
            "tip": "Check that the city name is valid and try again.",
        }, indent=2)


# ── Handler registry ────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "get_temperature": get_temperature,
}


# ── Standalone MCP server entry point ───────────────────────────────────────

if __name__ == "__main__":
    from mcp.server.fastmcp import FastMCP
    _mcp = FastMCP("weather-api")

    @_mcp.tool(name="get_temperature")
    def _get_temperature(city: str, date: str = "", end_date: str = "") -> str:
        """Fetches temperature forecasts based on historical averages for distant future dates."""
        params = {"city": city}
        if date:
            params["date"] = date
        if end_date:
            params["end_date"] = end_date
        return get_temperature(params)

    _mcp.run(transport="stdio")
