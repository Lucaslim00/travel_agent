---
name: maps
description: Display maps, calculate distances, and find nearby attractions for travel destinations. Use when user asks about maps, distances, directions, or nearby places.
allowed-tools: Read Bash
---

# Maps Skill

Display maps and location-based information for travel planning.

## When to Use
- User wants to see a map of their destination
- Calculating distances between locations
- Finding nearby attractions, restaurants, or landmarks
- Planning daily walking/driving routes

## MCP Tool Available
Use the `display-map` MCP server which exposes:
- `get_map_url` — generate a static map URL for a location (city or lat/lon)
- `get_distance` — calculate distance and travel time between two points
- `find_nearby` — find nearby attractions, restaurants, or landmarks by category

## Response Format
When returning map/location information, include:
1. **Map link** — a viewable map URL for the destination
2. **Key distances** — airport to hotel, hotel to attractions
3. **Nearby highlights** — top-rated places within walking distance
4. **Transport recommendation** — walk, taxi, metro based on distance
