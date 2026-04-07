# Tina — AI Travel Agent

An AI-powered travel planning assistant built with Streamlit and Claude. Tina automates the entire trip planning workflow — from flights and hotels to a day-by-day itinerary — with live budget tracking and real data from Google Flights, Google Hotels, and more.

## Features

- **Real flight search** — Live prices from Google Flights via web scraping
- **Real hotel search** — Ratings, reviews, and prices from Google Hotels
- **Weather forecasts** — Historical temperature averages for packing guidance
- **Currency conversion** — Real-time exchange rates for 150+ currencies
- **Interactive maps** — Google Maps with pinned attractions, hotels, and restaurants
- **Live budget panel** — Running cost breakdown updated as the plan builds
- **Auto-location detection** — Detects your city, currency, and timezone from IP
- **Security hardened** — Input validation, prompt injection detection, output filtering, rate limiting

## How It Works

When you ask Tina to plan a trip, she completes a full 4-part workflow in a single turn:

1. **Pre-Travel Info** — Exchange rates, visa requirements, packing list, local customs, useful apps
2. **Flights** — Searches outbound and return flights, displays top options with prices
3. **Hotels** — Finds top-rated hotels (4.0+ stars, 1,000+ reviews), selects the best
4. **Itinerary** — Generates a day-by-day plan with morning/afternoon/evening activities, meals, transport costs, and a trip map

All costs are tracked in a live budget panel on the right side of the screen.

## Getting Started

### Prerequisites

- Python 3.9+
- An [Anthropic API key](https://console.anthropic.com/)
- (Optional) A Google Maps API key for map features — set as `GOOGLE_API_KEY` environment variable

### Installation

```bash
git clone https://github.com/your-username/travel-agent.git
cd travel-agent
pip install -r requirements.txt
```

### Running

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser and enter your Anthropic API key in the sidebar.

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` | Optional | Enables interactive maps, distance calculations, and nearby place search |

## Project Structure

```
travel_agent/
├── app.py                  # Main Streamlit app (chat UI, budget panel, tool routing, security)
├── requirements.txt        # Python dependencies
├── mcp_server/             # Backend tool modules
│   ├── fetch_flights.py    # Google Flights scraper
│   ├── fetch_hotels.py     # Google Hotels scraper
│   ├── fetch_weather.py    # Historical temperature API
│   ├── fetch_currency.py   # Real-time currency conversion
│   ├── display_map.py      # Google Maps + Places integration
│   ├── track_budget.py     # Budget tracking logic
│   └── mcp_protocol.py     # Shared JSON-RPC helpers
├── .claude/
│   ├── agents/
│   │   └── travel-agent.md # Tina's persona and workflow definition
│   └── skills/             # Skill definitions for Claude Code
└── .streamlit/
    └── config.toml         # Streamlit theme configuration
```

## Tech Stack

- **Frontend** — [Streamlit](https://streamlit.io/)
- **AI Model** — Claude Haiku 4.5 via [Anthropic SDK](https://docs.anthropic.com/)
- **Flight Data** — Google Flights (web scraping with protobuf parsing)
- **Hotel Data** — Google Hotels (web scraping)
- **Weather** — [Open-Meteo](https://open-meteo.com/) historical averages
- **Currency** — [ExchangeRate API](https://open.er-api.com/) (free, no key)
- **Maps** — Google Maps JavaScript API + Places API

## License

[MIT](LICENSE)
