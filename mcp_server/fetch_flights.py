"""
MCP Server: Fetch Flights
Scrapes real flight data from Google Flights.

Usage (standalone MCP server):
    python -m mcp_server.fetch_flights

Usage (import handlers in app.py):
    from mcp_server.fetch_flights import search_flights, TOOL_HANDLERS

Dependencies:
    pip install primp protobuf selectolax
"""
from __future__ import annotations

import json
import traceback
from base64 import b64encode
from dataclasses import dataclass

from google.protobuf import descriptor_pool, runtime_version, symbol_database
from google.protobuf.internal import builder
from primp import Client
from selectolax.lexbor import LexborHTMLParser


# ══════════════════════════════════════════════════════════════════════════════
# Google Flights Extractor (formerly extract_flights.py)
# ══════════════════════════════════════════════════════════════════════════════

# -- Protobuf setup (inline descriptor, no .proto file needed) ----------------

runtime_version.ValidateProtobufRuntimeVersion(
    runtime_version.Domain.PUBLIC, 6, 31, 0, "", "flights.proto"
)

DESCRIPTOR = descriptor_pool.Default().AddSerializedFile(
    b'\n\rflights.proto"\x1a\n\x07\x41irport\x12\x0f\n\x07\x61irport\x18\x02 \x01(\t"\x90\x01\n\nFlightData\x12\x0c\n\x04\x64\x61te\x18\x02 \x01(\t\x12\x1e\n\x0c\x66rom_airport\x18\r \x01(\x0b\x32\x08.Airport\x12\x1c\n\nto_airport\x18\x0e \x01(\x0b\x32\x08.Airport\x12\x16\n\tmax_stops\x18\x05 \x01(\x05H\x00\x88\x01\x01\x12\x10\n\x08\x61irlines\x18\x06 \x03(\tB\x0c\n\n_max_stops"k\n\x04Info\x12\x19\n\x04\x64\x61ta\x18\x03 \x03(\x0b\x32\x0b.FlightData\x12\x13\n\x04seat\x18\t \x01(\x0e\x32\x05.Seat\x12\x1e\n\npassengers\x18\x08 \x03(\x0e\x32\n.Passenger\x12\x13\n\x04trip\x18\x13 \x01(\x0e\x32\x05.Trip*S\n\x04Seat\x12\x10\n\x0cUNKNOWN_SEAT\x10\x00\x12\x0b\n\x07\x45\x43ONOMY\x10\x01\x12\x13\n\x0fPREMIUM_ECONOMY\x10\x02\x12\x0c\n\x08\x42USINESS\x10\x03\x12\t\n\x05\x46IRST\x10\x04*E\n\x04Trip\x12\x10\n\x0cUNKNOWN_TRIP\x10\x00\x12\x0e\n\nROUND_TRIP\x10\x01\x12\x0b\n\x07ONE_WAY\x10\x02\x12\x0e\n\nMULTI_CITY\x10\x03*_\n\tPassenger\x12\x15\n\x11UNKNOWN_PASSENGER\x10\x00\x12\t\n\x05\x41\x44ULT\x10\x01\x12\t\n\x05\x43HILD\x10\x02\x12\x12\n\x0eINFANT_IN_SEAT\x10\x03\x12\x11\n\rINFANT_ON_LAP\x10\x04\x62\x06proto3'
)

_globals = globals()
builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, "flights_pb2", _globals)
# Injects: Airport, FlightData, Info, Seat, Trip, Passenger

# -- Data models --------------------------------------------------------------

@dataclass
class AirportInfo:
    name: str
    code: str

@dataclass
class SimpleDatetime:
    date: tuple  # (year, month, day)
    time: tuple  # (hour, minute)

@dataclass
class SingleFlight:
    from_airport: AirportInfo
    to_airport: AirportInfo
    departure: SimpleDatetime
    arrival: SimpleDatetime
    duration: int  # minutes
    plane_type: str

@dataclass
class CarbonEmission:
    typical_on_route: int  # grams
    emission: int          # grams

@dataclass
class FlightResult:
    type: str
    price: int
    airlines: list[str]
    flights: list[SingleFlight]
    carbon: CarbonEmission
    is_best: bool = False
    _outbound_count: int = 0  # how many legs are outbound (rest are return)

# -- Query builder ------------------------------------------------------------

SEAT_MAP = {
    "economy": Seat.ECONOMY,
    "premium-economy": Seat.PREMIUM_ECONOMY,
    "business": Seat.BUSINESS,
    "first": Seat.FIRST,
}

TRIP_MAP = {
    "round-trip": Trip.ROUND_TRIP,
    "one-way": Trip.ONE_WAY,
    "multi-city": Trip.MULTI_CITY,
}

def build_query(
    date: str,
    from_airport: str,
    to_airport: str,
    seat: str = "economy",
    trip: str = "one-way",
    adults: int = 1,
    language: str = "en-US",
    currency: str = "USD",
    max_stops: int | None = None,
    return_date: str | None = None,
) -> dict[str, str]:
    outbound = FlightData(
        date=date,
        from_airport=Airport(airport=from_airport),
        to_airport=Airport(airport=to_airport),
        max_stops=max_stops,
    )
    flight_data_list = [outbound]

    # For round-trip, add return leg to the query
    if trip == "round-trip" and return_date:
        return_leg = FlightData(
            date=return_date,
            from_airport=Airport(airport=to_airport),
            to_airport=Airport(airport=from_airport),
            max_stops=max_stops,
        )
        flight_data_list.append(return_leg)

    info = Info(
        data=flight_data_list,
        seat=SEAT_MAP[seat],
        trip=TRIP_MAP[trip],
        passengers=[Passenger.ADULT] * adults,
    )
    tfs = b64encode(info.SerializeToString()).decode("utf-8")
    return {"tfs": tfs, "hl": language, "curr": currency}

# -- Fetcher ------------------------------------------------------------------

GOOGLE_FLIGHTS_URL = "https://www.google.com/travel/flights"

def fetch_html(params: dict[str, str], proxy: str | None = None) -> str:
    client = Client(
        impersonate="chrome_131",
        impersonate_os="macos",
        referer=True,
        proxy=proxy,
        cookie_store=True,
    )
    return client.get(GOOGLE_FLIGHTS_URL, params=params).text

# -- Parser -------------------------------------------------------------------

def _norm_time(raw) -> tuple:
    if not raw:
        return (0, 0)
    return (raw[0], raw[1]) if len(raw) > 1 else (raw[0], 0)

def _norm_date(raw) -> tuple:
    if not raw:
        return (0, 0, 0)
    return tuple(raw) + (0,) * (3 - len(raw))

def _parse_leg(flight_data) -> list[SingleFlight]:
    """Parse a single leg (outbound or return) from flight data."""
    legs = []
    for sf in flight_data[2]:
        legs.append(SingleFlight(
            from_airport=AirportInfo(code=sf[3] or "", name=sf[4] or ""),
            to_airport=AirportInfo(code=sf[6] or "", name=sf[5] or ""),
            departure=SimpleDatetime(
                date=_norm_date(sf[20] if len(sf) > 20 else None),
                time=_norm_time(sf[8]),
            ),
            arrival=SimpleDatetime(
                date=_norm_date(sf[21] if len(sf) > 21 else None),
                time=_norm_time(sf[10]),
            ),
            duration=sf[11] or 0,
            plane_type=sf[17] or "",
        ))
    return legs


def parse_flights(html: str) -> list[FlightResult]:
    parser = LexborHTMLParser(html)
    script = parser.css_first(r"script.ds\:1")
    if script is None:
        raise ValueError("Could not find flight data script in HTML")

    js = script.text()
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    payload = json.loads(data)

    best_flights = payload[2][0] if payload[2][0] is not None else []
    other_flights = payload[3][0] if payload[3][0] is not None else []
    all_flights = best_flights + other_flights
    num_best = len(best_flights)
    results = []

    for idx, k in enumerate(all_flights):
        try:
            outbound = k[0]
            price = k[1][0][1]

            # Parse outbound leg
            outbound_legs = _parse_leg(outbound)

            # Parse return leg (k[1] contains return flight data for round-trips)
            return_legs = []
            if len(k) > 1 and isinstance(k[1], list) and len(k[1]) > 2 and isinstance(k[1][2], list):
                try:
                    return_legs = _parse_leg(k[1])
                except (IndexError, TypeError, KeyError):
                    pass

            all_legs = outbound_legs + return_legs

            extras = outbound[22] if len(outbound) > 22 and outbound[22] else None
            results.append(FlightResult(
                type=outbound[0],
                price=price,
                airlines=outbound[1],
                flights=all_legs,
                carbon=CarbonEmission(
                    typical_on_route=extras[8] if extras and len(extras) > 8 else 0,
                    emission=extras[7] if extras and len(extras) > 7 else 0,
                ),
                is_best=idx < num_best,
                _outbound_count=len(outbound_legs),
            ))
        except (IndexError, TypeError):
            continue

    return results

# -- Core API -----------------------------------------------------------------

def get_flights(
    date: str,
    from_airport: str,
    to_airport: str,
    seat: str = "economy",
    trip: str = "one-way",
    adults: int = 1,
    language: str = "en-US",
    currency: str = "USD",
    max_stops: int | None = None,
    proxy: str | None = None,
    return_date: str | None = None,
) -> list[FlightResult]:
    params = build_query(
        date=date, from_airport=from_airport, to_airport=to_airport,
        seat=seat, trip=trip, adults=adults,
        language=language, currency=currency, max_stops=max_stops,
        return_date=return_date,
    )
    return parse_flights(fetch_html(params, proxy=proxy))


# ══════════════════════════════════════════════════════════════════════════════
# MCP Server Tool Layer
# ══════════════════════════════════════════════════════════════════════════════

# ── Tool Definitions (MCP schema) ───────────────────────────────────────────

TOOLS = [
    {
        "name": "search_flights",
        "description": (
            "Search for real flights between two cities using Google Flights. "
            "Returns live prices, airlines, durations, stops, carbon emissions, "
            "and departure/arrival times. Requires IATA airport codes (e.g., 'JFK', 'NRT')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": "Departure IATA airport code (e.g., 'JFK', 'LAX', 'SIN', 'KUL')",
                },
                "destination": {
                    "type": "string",
                    "description": "Arrival IATA airport code (e.g., 'NRT', 'CDG', 'LHR')",
                },
                "departure_date": {
                    "type": "string",
                    "description": "Departure date in YYYY-MM-DD format",
                },
                "return_date": {
                    "type": "string",
                    "description": "Return date in YYYY-MM-DD format (omit for one-way)",
                },
                "passengers": {
                    "type": "integer",
                    "description": "Number of adult passengers (default: 1)",
                    "default": 1,
                },
                "seat_class": {
                    "type": "string",
                    "enum": ["economy", "premium-economy", "business", "first"],
                    "description": "Cabin class (default: economy)",
                    "default": "economy",
                },
                "max_stops": {
                    "type": "integer",
                    "description": "Maximum number of stops. Omit for any number of stops.",
                },
                "currency": {
                    "type": "string",
                    "description": "Currency code for prices (default: USD)",
                    "default": "USD",
                },
            },
            "required": ["origin", "destination", "departure_date"],
        },
    },
]


# ── Formatting Helpers ──────────────────────────────────────────────────────

def _format_time(dt: SimpleDatetime) -> str:
    """Format a SimpleDatetime into a readable string."""
    try:
        y, m, d = dt.date if dt.date else (0, 0, 0)
        h, mi = dt.time if dt.time else (0, 0)
        y = y or 0
        m = m or 0
        d = d or 0
        h = h or 0
        mi = mi or 0
        if y and m and d:
            return f"{y:04d}-{m:02d}-{d:02d} {h:02d}:{mi:02d}"
        return f"{h:02d}:{mi:02d}"
    except (TypeError, ValueError):
        return "N/A"


def _format_duration(minutes: int) -> str:
    """Convert minutes to 'Xh Ym' string."""
    if not minutes or minutes <= 0:
        return "N/A"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _legs_to_list(flights: list[SingleFlight]) -> list[dict]:
    """Convert a list of SingleFlight into JSON-serialisable dicts."""
    result = []
    for sf in flights:
        result.append({
            "from": {"code": sf.from_airport.code, "name": sf.from_airport.name},
            "to": {"code": sf.to_airport.code, "name": sf.to_airport.name},
            "departure": _format_time(sf.departure),
            "arrival": _format_time(sf.arrival),
            "duration": _format_duration(sf.duration),
            "duration_minutes": sf.duration,
            "plane": sf.plane_type,
        })
    return result


def _flight_result_to_dict(fr: FlightResult, passengers: int) -> dict:
    """Convert a FlightResult dataclass to a JSON-serialisable dict."""
    outbound_count = fr._outbound_count or len(fr.flights)
    outbound_flights = fr.flights[:outbound_count]
    return_flights = fr.flights[outbound_count:]

    outbound_duration = sum(sf.duration for sf in outbound_flights)
    return_duration = sum(sf.duration for sf in return_flights)
    total_duration = outbound_duration + return_duration

    result = {
        "airlines": fr.airlines,
        "price_per_person": fr.price,
        "total_price": fr.price * passengers,
        "cabin_type": fr.type or "Economy",
        "is_best": fr.is_best,
        "carbon_emission_grams": fr.carbon.emission,
        "carbon_typical_grams": fr.carbon.typical_on_route,
        "outbound": {
            "stops": max(len(outbound_flights) - 1, 0),
            "total_duration": _format_duration(outbound_duration),
            "total_duration_minutes": outbound_duration,
            "legs": _legs_to_list(outbound_flights),
        },
    }

    if return_flights:
        result["return"] = {
            "stops": max(len(return_flights) - 1, 0),
            "total_duration": _format_duration(return_duration),
            "total_duration_minutes": return_duration,
            "legs": _legs_to_list(return_flights),
        }

    # Keep top-level totals for sorting
    result["stops"] = result["outbound"]["stops"] + (result.get("return", {}).get("stops", 0))
    result["total_duration"] = _format_duration(total_duration)
    result["total_duration_minutes"] = total_duration

    return result


# ── Tool Implementation ─────────────────────────────────────────────────────

def _search_one_way(origin, destination, date, seat_class, passengers, currency, max_stops):
    """Search one-way flights and return formatted results."""
    results = get_flights(
        date=date,
        from_airport=origin,
        to_airport=destination,
        seat=seat_class,
        trip="one-way",
        adults=passengers,
        currency=currency,
        max_stops=max_stops,
    )
    if not results:
        return []
    flights = [_flight_result_to_dict(fr, passengers) for fr in results]
    flights.sort(key=lambda f: (not f["is_best"], f["price_per_person"]))
    return flights


def search_flights(params: dict) -> str:
    """Search Google Flights for real flight data."""
    origin = params["origin"].upper()
    destination = params["destination"].upper()
    departure_date = params["departure_date"]
    return_date = params.get("return_date")
    passengers = params.get("passengers", 1)
    seat_class = params.get("seat_class", "economy")
    max_stops = params.get("max_stops")
    currency = params.get("currency", "USD")

    trip_type = "round-trip" if return_date else "one-way"

    try:
        # Always search as one-way (Google Flights doesn't return return legs in the same payload)
        outbound = _search_one_way(origin, destination, departure_date, seat_class, passengers, currency, max_stops)

        if not outbound:
            return json.dumps({
                "route": f"{origin} ↔ {destination}" if return_date else f"{origin} → {destination}",
                "departure_date": departure_date,
                "return_date": return_date or "N/A",
                "passengers": passengers,
                "seat_class": seat_class,
                "outbound_flights": [],
                "return_flights": [],
                "message": "No outbound flights found. Try different dates or airports.",
            }, indent=2)

        if return_date:
            # Search return as a separate one-way (destination → origin)
            inbound = _search_one_way(destination, origin, return_date, seat_class, passengers, currency, max_stops)

            ob_cheapest = min(outbound, key=lambda f: f["price_per_person"])
            ob_fastest = min(outbound, key=lambda f: f["total_duration_minutes"])
            rt_cheapest = min(inbound, key=lambda f: f["price_per_person"]) if inbound else None
            rt_fastest = min(inbound, key=lambda f: f["total_duration_minutes"]) if inbound else None

            result = {
                "route": f"{origin} ↔ {destination}",
                "departure_date": departure_date,
                "return_date": return_date,
                "trip_type": "round-trip",
                "passengers": passengers,
                "seat_class": seat_class,
                "currency": currency,
                "outbound_flights": outbound[:5],
                "return_flights": inbound[:5] if inbound else [],
                "highlights": {
                    "cheapest_outbound": f"{currency} {ob_cheapest['price_per_person']} ({', '.join(ob_cheapest['airlines'])})",
                    "fastest_outbound": f"{currency} {ob_fastest['price_per_person']} ({ob_fastest['total_duration']})",
                    "cheapest_return": f"{currency} {rt_cheapest['price_per_person']} ({', '.join(rt_cheapest['airlines'])})" if rt_cheapest else None,
                    "fastest_return": f"{currency} {rt_fastest['price_per_person']} ({rt_fastest['total_duration']})" if rt_fastest else None,
                    "cheapest_round_trip": f"{currency} {ob_cheapest['price_per_person'] + rt_cheapest['price_per_person']}" if rt_cheapest else None,
                },
            }
        else:
            cheapest = min(outbound, key=lambda f: f["price_per_person"])
            fastest = min(outbound, key=lambda f: f["total_duration_minutes"])

            result = {
                "route": f"{origin} → {destination}",
                "departure_date": departure_date,
                "return_date": "N/A",
                "trip_type": "one-way",
                "passengers": passengers,
                "seat_class": seat_class,
                "currency": currency,
                "outbound_flights": outbound[:10],
                "return_flights": [],
                "highlights": {
                    "cheapest": f"{currency} {cheapest['price_per_person']} ({', '.join(cheapest['airlines'])})",
                    "fastest": f"{currency} {fastest['price_per_person']} ({fastest['total_duration']})",
                },
            }

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({
            "error": str(e),
            "detail": traceback.format_exc(),
            "route": f"{origin} ↔ {destination}" if return_date else f"{origin} → {destination}",
            "departure_date": departure_date,
            "tip": "Make sure you're using valid IATA airport codes (e.g., JFK, NRT, CDG, LHR, SIN).",
        }, indent=2)


# ── Handler registry (importable by app.py) ─────────────────────────────────

TOOL_HANDLERS = {
    "search_flights": search_flights,
}


# ── Standalone MCP server entry point ───────────────────────────────────────

if __name__ == "__main__":
    from mcp.server.fastmcp import FastMCP
    _mcp = FastMCP("flights-api")

    @_mcp.tool(name="search_flights")
    def _search_flights(origin: str, destination: str, departure_date: str, return_date: str = "", passengers: int = 1, seat_class: str = "economy", max_stops: int = -1, currency: str = "USD") -> str:
        """Search for REAL flights using Google Flights. Returns live prices, airlines, durations, stops, carbon emissions. REQUIRES IATA airport codes."""
        params = {"origin": origin, "destination": destination, "departure_date": departure_date, "passengers": passengers, "seat_class": seat_class, "currency": currency}
        if return_date:
            params["return_date"] = return_date
        if max_stops >= 0:
            params["max_stops"] = max_stops
        return search_flights(params)

    _mcp.run(transport="stdio")
