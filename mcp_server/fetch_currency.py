"""
MCP Server: Fetch currency
Uses the free Open ExchangeRate API (open.er-api.com) for real-time currency conversion.

Usage (standalone MCP server):
    python -m mcp_server.fetch_currency

Usage (import handlers in app.py):
    from mcp_server.fetch_currency import convert_currency, TOOL_HANDLERS

Requires:
    - No API key needed (free tier of open.er-api.com)
    - Supports 150+ currencies with daily-updated rates
"""

from __future__ import annotations

import json
import os
import time
import traceback
import requests


# ── Rate Cache ─────────────────────────────────────────────────────────────
# Cache exchange rates per base currency to avoid excessive API calls.
# Rates are refreshed at most once per hour.

_rate_cache: dict[str, dict] = {}   # {base_currency: {"rates": {...}, "fetched_at": timestamp}}
_CACHE_TTL = 3600  # 1 hour


def _get_rates(base: str) -> dict[str, float]:
    """Fetch exchange rates for a base currency, using cache when possible."""
    base = base.upper()
    now = time.time()

    cached = _rate_cache.get(base)
    if cached and (now - cached["fetched_at"]) < _CACHE_TTL:
        return cached["rates"]

    resp = requests.get(
        f"https://open.er-api.com/v6/latest/{base}",
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("result") != "success":
        raise ValueError(f"ExchangeRate API error: {data.get('error-type', 'unknown')}")

    rates = data.get("rates", {})
    _rate_cache[base] = {"rates": rates, "fetched_at": now}
    return rates


# ── Tool Definitions (MCP schema) ───────────────────────────────────────────

TOOLS = [
    {
        "name": "convert_currency",
        "description": (
            "Convert an amount from one currency to another using real-time exchange rates. "
            "Supports 150+ currencies (USD, EUR, GBP, JPY, AUD, SGD, MYR, THB, KRW, INR, CNY, etc.). "
            "Rates are updated daily from open.er-api.com."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "Amount to convert",
                },
                "from_currency": {
                    "type": "string",
                    "description": "Source currency code (e.g., 'USD', 'EUR', 'JPY')",
                },
                "to_currency": {
                    "type": "string",
                    "description": "Target currency code (e.g., 'EUR', 'MYR', 'THB')",
                },
            },
            "required": ["amount", "from_currency", "to_currency"],
        },
    },
    {
        "name": "get_exchange_rate",
        "description": (
            "Get the current exchange rate between two currencies. "
            "Use this to show the user what the rate is without converting a specific amount."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_currency": {
                    "type": "string",
                    "description": "Base currency code (e.g., 'USD')",
                },
                "to_currency": {
                    "type": "string",
                    "description": "Target currency code (e.g., 'JPY')",
                },
            },
            "required": ["from_currency", "to_currency"],
        },
    },
]


# ── Tool Implementations ────────────────────────────────────────────────────

def convert_currency(params: dict) -> str:
    """Convert an amount between currencies using real-time rates."""
    amount = params["amount"]
    from_c = params["from_currency"].upper()
    to_c = params["to_currency"].upper()

    try:
        rates = _get_rates(from_c)

        if to_c not in rates:
            return json.dumps({
                "error": f"Unsupported target currency: {to_c}",
                "supported_sample": sorted(list(rates.keys()))[:30],
            }, indent=2)

        rate = rates[to_c]
        converted = round(amount * rate, 2)

        result = {
            "from": {"amount": amount, "currency": from_c},
            "to": {"amount": converted, "currency": to_c},
            "rate": round(rate, 6),
            "rate_display": f"1 {from_c} = {round(rate, 4)} {to_c}",
            "inverse_rate": f"1 {to_c} = {round(1 / rate, 4)} {from_c}" if rate > 0 else None,
            "summary": f"{amount:,.2f} {from_c} = {converted:,.2f} {to_c}",
        }
        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({
            "error": str(e),
            "detail": traceback.format_exc(),
            "from_currency": from_c,
            "to_currency": to_c,
        }, indent=2)


def get_exchange_rate(params: dict) -> str:
    """Get the exchange rate between two currencies."""
    from_c = params["from_currency"].upper()
    to_c = params["to_currency"].upper()

    try:
        rates = _get_rates(from_c)

        if to_c not in rates:
            return json.dumps({
                "error": f"Unsupported currency: {to_c}",
            }, indent=2)

        rate = rates[to_c]

        result = {
            "from_currency": from_c,
            "to_currency": to_c,
            "rate": round(rate, 6),
            "rate_display": f"1 {from_c} = {round(rate, 4)} {to_c}",
            "inverse_rate": round(1 / rate, 6) if rate > 0 else None,
            "inverse_display": f"1 {to_c} = {round(1 / rate, 4)} {from_c}" if rate > 0 else None,
        }
        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({
            "error": str(e),
            "detail": traceback.format_exc(),
            "from_currency": from_c,
            "to_currency": to_c,
        }, indent=2)


# ── Handler registry ────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "convert_currency": convert_currency,
    "get_exchange_rate": get_exchange_rate,
}


# ── Standalone MCP server entry point ───────────────────────────────────────

if __name__ == "__main__":
    from mcp_server.mcp_protocol import run_server
    run_server("currency-api", "1.0.0", TOOLS, TOOL_HANDLERS)
