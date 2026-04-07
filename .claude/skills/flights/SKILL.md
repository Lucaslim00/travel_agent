---
name: flights
description: Search REAL flights via Google Flights scraper. Live prices, airlines, stops, durations, carbon emissions. Requires IATA airport codes.
allowed-tools: Read Bash
---

# Flights Skill

Search and compare **real flight data** scraped from Google Flights.

## When to Use
- User asks about flights or airfare
- Planning a trip and need route options
- Comparing prices across airlines
- Finding direct vs layover options

## IMPORTANT: Airport Codes
This tool requires **IATA airport codes**, NOT city names. Common codes:
- New York: JFK / EWR / LGA
- Los Angeles: LAX
- London: LHR / LGW
- Paris: CDG / ORY
- Tokyo: NRT / HND
- Singapore: SIN
- Kuala Lumpur: KUL
- Bangkok: BKK
- Seoul: ICN
- Dubai: DXB
- Sydney: SYD
- Barcelona: BCN
- Rome: FCO

Always convert city names to the correct IATA code before calling `search_flights`.

## MCP Tool Available
Use the `fetch-flights` MCP server which exposes:
- `search_flights` — real Google Flights data with params: origin, destination, departure_date, return_date, passengers, seat_class (economy/premium-economy/business/first), max_stops, currency

## How the API Works
- For **round-trip**: provide both `departure_date` and `return_date`. The API runs **two separate one-way searches** (outbound + return) and returns both in a single response.
- For **one-way**: provide only `departure_date`. The API returns outbound flights only.

## Response Structure
The response contains two flight lists:

```json
{
  "route": "KUL ↔ NRT",
  "trip_type": "round-trip",
  "outbound_flights": [
    {
      "airlines": ["Batik Air"],
      "price_per_person": 1019,
      "total_price": 1019,
      "outbound": {
        "stops": 0,
        "total_duration": "7h 15m",
        "legs": [
          {
            "from": {"code": "KUL", "name": "..."},
            "to": {"code": "NRT", "name": "..."},
            "departure": "2026-08-10 07:15",
            "arrival": "2026-08-10 15:30",
            "duration": "7h 15m"
          }
        ]
      }
    }
  ],
  "return_flights": [
    {
      "airlines": ["Batik Air"],
      "price_per_person": 884,
      "total_price": 884,
      "outbound": {
        "stops": 0,
        "total_duration": "7h 30m",
        "legs": [
          {
            "from": {"code": "NRT", "name": "..."},
            "to": {"code": "KUL", "name": "..."},
            "departure": "2026-08-18 10:00",
            "arrival": "2026-08-18 16:30",
            "duration": "7h 30m"
          }
        ]
      }
    }
  ],
  "highlights": {
    "cheapest_outbound": "MYR 659 (Vietjet)",
    "cheapest_return": "MYR 718 (Vietjet)",
    "cheapest_round_trip": "MYR 1377"
  }
}
```

## Key Fields to Extract
- **`outbound_flights`** — list of outbound flight options (origin → destination)
- **`return_flights`** — list of return flight options (destination → origin). Empty for one-way.
- **`outbound.legs`** — individual flight segments (layovers = multiple legs)
- **`legs[].departure` / `legs[].arrival`** — use these to determine arrival time for itinerary planning (e.g., if arriving at 15:30, only plan afternoon/evening on Day 1)
- **`price_per_person`** — price per direction. For round-trip total, add cheapest outbound + cheapest return.
- **`highlights.cheapest_round_trip`** — pre-calculated cheapest combined price

## Response Format
When presenting flight information to the user:
1. **Pair outbound + return** as combined round-trip options
2. **Show combined total price** (outbound + return per person)
3. **Include arrival time** — this determines how the first/last day itinerary is planned
4. **Add one budget item** for the round-trip total via `budget_add_item` (category: "Flights")
