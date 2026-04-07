---
name: temperature
description: Fetches temperature forecasts based on historical averages for distant future dates. Use when user asks about temperature.
allowed-tools: Read Bash
---

# Temperature Skill

Extract temperature information for travel planning.

## When to Use
- User asks about temperature at a destination
- Recommending best times to visit
- Checking forecast for upcoming trip dates

## MCP Tool Available
Use the `fetch-weather` MCP server which exposes:
- `get_temperature` — temperature forecast for any city (based on past 3 years averages)
- `get_climate` — monthly climate averages (avg temp, rainfall, humidity) for seasonal planning

## Response Format
When returning weather information, include:
1. **Forecast** — temperature forecast (based on past 3 years averages)
2. **Best time to visit** — if the user hasn't committed to dates yet
