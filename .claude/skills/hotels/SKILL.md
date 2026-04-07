---
name: hotels
description: Search REAL hotels via Google Places API. Live ratings, price levels, addresses, photos, and Google Maps links.
allowed-tools: Read Bash
---

# Hotels Skill

Search and compare **real hotel data** from Google Places API.

## When to Use
- User asks about accommodation, hotels, or where to stay
- Planning a trip and need lodging options
- Comparing hotels across budget tiers (budget, mid-range, luxury)

## MCP Tool Available
Use the `fetch-hotels` MCP server which exposes:
- `search_hotels` — real Google Places data with params: city, check_in, check_out, budget (budget/mid-range/luxury), limit

## Response Format
When returning hotel information, include:
1. **Top picks** — best rated, best value for the budget tier
2. **Rating + price tier** — star rating, user review count, price level ($-$$$$)
3. **Address** — full formatted address
4. **Photo** — include photo URL if available
5. **Google Maps link** — direct link to the hotel on Google Maps
6. **Budget tip** — always add the recommended hotel to the budget panel via `budget_add_item`

## Notes
- Google Places API returns price_level (0-4) not exact nightly rates
- Price levels: 0=Free, 1=Budget ($), 2=Mid-range ($$), 3=Upscale ($$$), 4=Luxury ($$$$)
- For exact pricing, suggest the user check booking platforms (Booking.com, Agoda, etc.)
- Requires Google API key with Places API enabled
