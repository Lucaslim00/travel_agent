"""
MCP Server: Fetch Hotels
Scrapes real hotel data from Google Hotels.
No API key required.

Usage (standalone MCP server):
    python -m mcp_server.fetch_hotels

Usage (import handlers in app.py):
    from mcp_server.fetch_hotels import search_hotels, set_api_key, TOOL_HANDLERS
"""

from __future__ import annotations

import json
import re
import traceback

from primp import Client
from selectolax.lexbor import LexborHTMLParser


# ── API Key (kept for backward compatibility) ──────────────────────────────

def set_api_key(key: str) -> None:
    pass


# ── Tool Definitions (MCP schema) ───────────────────────────────────────────

TOOLS = [
    {
        "name": "search_hotels",
        "description": (
            "Search for real hotels using Google Hotels. "
            "Returns live prices, star ratings, guest ratings, deal labels, "
            "coordinates, and website links. No API key required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City to search in (e.g., 'Bangkok, Thailand', 'Tokyo, Japan')",
                },
                "check_in": {
                    "type": "string",
                    "description": "Check-in date (YYYY-MM-DD)",
                },
                "check_out": {
                    "type": "string",
                    "description": "Check-out date (YYYY-MM-DD)",
                },
                "adults": {
                    "type": "integer",
                    "description": "Number of adult guests (default: 2)",
                    "default": 2,
                },
                "budget": {
                    "type": "string",
                    "enum": ["budget", "mid-range", "luxury"],
                    "description": "Budget tier filter (default: mid-range)",
                    "default": "mid-range",
                },
                "currency": {
                    "type": "string",
                    "description": "Currency code for prices (default: USD)",
                    "default": "USD",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["city"],
        },
    },
]


# ── Budget tier → star range ───────────────────────────────────────────────

BUDGET_STARS = {
    "budget": (0, 2),
    "mid-range": (3, 3),
    "luxury": (4, 5),
}


# ── Google Hotels Scraper ──────────────────────────────────────────────────

GOOGLE_HOTELS_URL = "https://www.google.com/travel/hotels"


def _fetch_hotels_html(city: str, check_in: str, check_out: str, adults: int, currency: str) -> str:
    client = Client(
        impersonate="chrome_131",
        impersonate_os="macos",
        referer=True,
        cookie_store=True,
    )
    params = {
        "q": f"hotels in {city}",
        "hl": "en-US",
        "curr": currency,
    }
    if check_in:
        params["checkin"] = check_in
    if check_out:
        params["checkout"] = check_out
    if adults:
        params["adults"] = str(adults)
    return client.get(GOOGLE_HOTELS_URL, params=params).text


def _parse_hotels(html: str, budget: str, limit: int) -> list[dict]:
    parser = LexborHTMLParser(html)

    # --- Build metadata lookup from payload (coords + website) ---
    meta_lookup: dict[str, dict] = {}
    script = parser.css_first(r"script.ds\:0")
    if script:
        try:
            js = script.text()
            raw = js.split("data:", 1)[1]
            raw = re.split(r",\s*sideChannel\b", raw)[0]
            payload = json.loads(raw)
            hotel_section = payload[0][0][0][1]
            key = "397419284"
            for entry in hotel_section:
                if not isinstance(entry, list) or len(entry) < 2:
                    continue
                if not isinstance(entry[1], dict) or key not in entry[1]:
                    continue
                h = entry[1][key][0]
                name = h[1] if len(h) > 1 else None
                if not name:
                    continue
                coords = None
                website = None
                try:
                    coords = h[2][0]
                except (IndexError, TypeError):
                    pass
                try:
                    website = h[2][29][2]
                except (IndexError, TypeError):
                    pass
                meta_lookup[name] = {"coords": coords, "website": website}
        except Exception:
            pass

    # --- Parse hotel cards ---
    min_stars, max_stars = BUDGET_STARS.get(budget, (0, 5))
    cards = parser.css(".uaTTDe")
    results = []

    for card in cards:
        try:
            full_text = card.text()
            segments = card.text(separator="|").split("|")
            name = segments[0].strip() if segments else ""
            if not name:
                continue

            # Price per night — match number immediately before "total"
            price_match = re.search(r"([\d,]+)\s*total", full_text, re.IGNORECASE)
            price = int(price_match.group(1).replace(",", "")) if price_match else None

            # Currency symbol/code shown in the card
            currency_match = re.search(r"(S\$|RM|[A-Z]{3}|[\$€£¥₩฿₹])\s*(?:[\d,]+)\s*total", full_text, re.IGNORECASE)
            displayed_currency = currency_match.group(1) if currency_match else None

            # Guest rating
            rating_match = re.search(r"(\d\.\d)\s*\(", full_text)
            rating = float(rating_match.group(1)) if rating_match else None

            # Review count
            review_match = re.search(r"\(([\d.]+[KM]?)\)", full_text)
            reviews = review_match.group(1) if review_match else None

            # Star rating
            stars_match = re.search(r"(\d)-star hotel", full_text)
            stars = int(stars_match.group(1)) if stars_match else None

            # Filter by budget tier
            if stars is not None and not (min_stars <= stars <= max_stars):
                continue

            # Price tier label
            if stars is not None:
                if stars <= 2:
                    price_tier = "Budget"
                elif stars == 3:
                    price_tier = "Mid-range"
                else:
                    price_tier = "Luxury"
            else:
                price_tier = "Unknown"

            # Deal label
            deal = None
            if "GREAT DEAL" in full_text:
                deal = "Great Deal"
            elif "DEAL" in full_text:
                deal = "Deal"

            meta = meta_lookup.get(name, {})
            coords = meta.get("coords")
            website = meta.get("website")

            hotel = {
                "name": name,
                "stars": stars,
                "price_per_night": price,
                "currency": displayed_currency,
                "rating": rating,
                "reviews": reviews,
                "price_tier": price_tier,
                "deal": deal,
                "website": website,
            }

            if coords and len(coords) >= 2:
                hotel["lat"] = coords[0]
                hotel["lng"] = coords[1]
                hotel["google_maps_link"] = (
                    f"https://www.google.com/maps/search/?api=1&query={coords[0]},{coords[1]}"
                )

            results.append(hotel)
            if len(results) >= limit:
                break

        except Exception:
            continue

    return results


# ── Tool Implementation ────────────────────────────────────────────────────

def search_hotels(params: dict) -> str:
    """Search for real hotels using Google Hotels scraper."""
    city = params["city"]
    check_in = params.get("check_in", "")
    check_out = params.get("check_out", "")
    adults = params.get("adults", "")
    budget = params.get("budget", "")
    currency = params.get("currency", "MYR")
    limit = params.get("limit", 5)

    try:
        html = _fetch_hotels_html(city, check_in, check_out, adults, currency)
        hotels = _parse_hotels(html, budget, limit)

        # If budget filter returns nothing, retry without star filter
        if not hotels:
            hotels = _parse_hotels(html, "all", limit)

        nights = None
        if check_in and check_out:
            try:
                from datetime import datetime
                d_in = datetime.strptime(check_in, "%Y-%m-%d")
                d_out = datetime.strptime(check_out, "%Y-%m-%d")
                nights = (d_out - d_in).days
                for h in hotels:
                    if h.get("price_per_night") and nights:
                        h["estimated_total"] = h["price_per_night"] * nights
            except ValueError:
                pass

        result = {
            "city": city,
            "budget_tier": budget,
            "currency": currency,
            "check_in": check_in or "Not specified",
            "check_out": check_out or "Not specified",
            "nights": nights,
            "adults": adults,
            "hotels": hotels,
            "total_found": len(hotels),
            "data_source": "Google Hotels",
            "note": "Prices shown are per night. Estimated total = price_per_night × nights. Actual total may vary due to variable nightly rates, taxes, and fees.",
        }
        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({
            "error": str(e),
            "detail": traceback.format_exc(),
            "city": city,
        }, indent=2)


# ── Handler registry ────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "search_hotels": search_hotels,
}


# ── Standalone MCP server entry point ───────────────────────────────────────

if __name__ == "__main__":
    from mcp_server.mcp_protocol import run_server
    run_server("hotels-api", "2.0.0", TOOLS, TOOL_HANDLERS)
