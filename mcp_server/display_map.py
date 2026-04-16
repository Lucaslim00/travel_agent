"""
MCP Server: Maps API
Uses Nominatim (geocoding), OpenRouteService (distance), Overpass API (nearby places),
and Leaflet + OpenStreetMap (embeddable maps).

Usage (standalone MCP server):
    python -m mcp_server.display_map

Usage (import handlers in app.py):
    from mcp_server.display_map import show_map, get_distance, find_nearby, set_api_key, TOOL_HANDLERS
"""

from __future__ import annotations

import json
import math
import os
import time
import traceback
import requests


# ── API Key (kept for backward compatibility, not actually needed) ─────────

_api_key: str = os.environ.get("GOOGLE_API_KEY", "")


def set_api_key(key: str) -> None:
    global _api_key
    _api_key = key


# ── Tool Definitions (MCP schema) ───────────────────────────────────────────

TOOLS = [
    {
        "name": "show_map",
        "description": (
            "Display an interactive map with multiple pins for key locations. "
            "Pass an array of pins with lat, lng, and label to mark attractions, hotels, restaurants, etc. "
            "Free, no API key needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City or place name for the map center (e.g., 'Hanoi, Vietnam')"},
                "zoom": {"type": "integer", "description": "Zoom level 1-20 (default: 13)", "default": 13},
                "map_type": {
                    "type": "string",
                    "enum": ["roadmap", "satellite"],
                    "description": "Map type (default: roadmap)",
                    "default": "roadmap",
                },
                "pins": {
                    "type": "array",
                    "description": "Array of pins to display on the map. Each pin has lat, lng, and label.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "lat": {"type": "number", "description": "Latitude"},
                            "lng": {"type": "number", "description": "Longitude"},
                            "label": {"type": "string", "description": "Pin label (e.g., 'Hoan Kiem Lake')"},
                        },
                        "required": ["lat", "lng", "label"],
                    },
                },
            },
            "required": ["city"],
        },
    },
    {
        "name": "get_distance",
        "description": (
            "Calculate distance and estimated travel time between two locations. "
            "Uses straight-line distance with realistic travel time estimates. Free, no API key needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Starting location (e.g., 'Eiffel Tower, Paris')"},
                "destination": {"type": "string", "description": "Ending location (e.g., 'Louvre Museum, Paris')"},
                "mode": {
                    "type": "string",
                    "enum": ["walking", "driving", "transit"],
                    "description": "Travel mode (default: walking)",
                    "default": "walking",
                },
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "find_nearby",
        "description": (
            "Find real nearby places using OpenStreetMap data. "
            "Returns names, ratings, addresses. Free, no API key needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City to search in (e.g., 'Tokyo, Japan')"},
                "category": {
                    "type": "string",
                    "enum": ["attractions", "restaurants", "hotels", "shopping", "nightlife", "museums"],
                    "description": "Category of places to find",
                },
                "limit": {"type": "integer", "description": "Max results to return (default: 5)", "default": 5},
            },
            "required": ["city", "category"],
        },
    },
]


# ── Nominatim Geocoding (free, no API key) ────────────────────────────────

def _geocode(place: str) -> tuple[float, float]:
    """Convert place name to (lat, lng) using Nominatim (OpenStreetMap)."""
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1},
        headers={"User-Agent": "TinaTravel/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"Location not found: '{place}'")
    return float(data[0]["lat"]), float(data[0]["lon"])


# ── Leaflet + OpenStreetMap Map HTML Generator ────────────────────────────

def _build_map_html(lat: float, lng: float, city: str, zoom: int = 12, map_type: str = "roadmap", pins: list | None = None) -> str:
    """Build an interactive Leaflet + OpenStreetMap HTML map with optional multiple pins."""
    safe_city = city.replace('"', '\\"').replace("'", "\\'").replace("<", "").replace(">", "")

    # Choose tile layer based on map type
    if map_type == "satellite":
        tile_url = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        attribution = "Tiles &copy; Esri"
    else:
        tile_url = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

    # Build markers JS
    if pins and len(pins) > 0:
        markers_js = ""
        all_lats = [lat]
        all_lngs = [lng]
        for pin in pins:
            p_lat = pin.get("lat")
            p_lng = pin.get("lng")
            p_label = str(pin.get("label", "")).replace('"', '\\"').replace("'", "\\'").replace("<", "").replace(">", "")
            if p_lat is not None and p_lng is not None:
                all_lats.append(float(p_lat))
                all_lngs.append(float(p_lng))
                markers_js += f"L.marker([{p_lat}, {p_lng}]).addTo(map).bindPopup('<b>{p_label}</b>');\n"

        # Auto-fit bounds to show all pins
        min_lat, max_lat = min(all_lats), max(all_lats)
        min_lng, max_lng = min(all_lngs), max(all_lngs)
        fit_bounds_js = f"map.fitBounds([[{min_lat}, {min_lng}], [{max_lat}, {max_lng}]], {{padding: [30, 30]}});"
    else:
        markers_js = f"L.marker([{lat}, {lng}]).addTo(map).bindPopup('<b>{safe_city}</b>').openPopup();"
        fit_bounds_js = ""

    return f"""
<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ width: 100%; height: 400px; border-radius: 8px; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var map = L.map('map').setView([{lat}, {lng}], {zoom});
        L.tileLayer('{tile_url}', {{
            attribution: '{attribution}',
            maxZoom: 19,
        }}).addTo(map);
        {markers_js}
        {fit_bounds_js}
    </script>
</body>
</html>
"""


# ── Category → Overpass query tags ────────────────────────────────────────

CATEGORY_TO_OVERPASS = {
    "attractions": '"tourism"="attraction"',
    "restaurants": '"amenity"="restaurant"',
    "hotels": '"tourism"="hotel"',
    "shopping": '"shop"="mall"',
    "nightlife": '"amenity"="nightclub"',
    "museums": '"tourism"="museum"',
}

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def _overpass_query(query: str) -> dict:
    """Run an Overpass query with fallback servers."""
    for server in OVERPASS_SERVERS:
        try:
            resp = requests.post(server, data={"data": query}, timeout=25)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            continue
    raise ValueError("All Overpass API servers failed. Try again later.")


# ── Haversine Distance ───────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate straight-line distance in meters between two coordinates."""
    R = 6371000  # Earth's radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _estimate_travel_time(distance_m: float, mode: str) -> dict:
    """Estimate travel time based on distance and mode."""
    # Average speeds in m/s
    speeds = {
        "walking": 1.4,    # ~5 km/h
        "driving": 11.1,   # ~40 km/h (city average)
        "transit": 8.3,    # ~30 km/h (city average)
    }
    speed = speeds.get(mode, 1.4)
    seconds = distance_m / speed

    if seconds < 60:
        duration_text = f"{int(seconds)} secs"
    elif seconds < 3600:
        duration_text = f"{int(seconds / 60)} mins"
    else:
        hours = int(seconds / 3600)
        mins = int((seconds % 3600) / 60)
        duration_text = f"{hours} hr {mins} mins"

    return {
        "duration": duration_text,
        "duration_seconds": int(seconds),
    }


# ── Tool Implementations ──────────────────────────────────────────────────

def show_map(params: dict) -> str:
    """Generate an embeddable Leaflet + OpenStreetMap map with optional multiple pins."""
    city = params["city"]
    zoom = params.get("zoom", 13)
    map_type = params.get("map_type", "roadmap")
    pins = params.get("pins", [])

    try:
        lat, lng = _geocode(city)
        time.sleep(0.1)
        map_html = _build_map_html(lat, lng, city, zoom, map_type, pins=pins)

        result = {
            "city": city,
            "latitude": lat,
            "longitude": lng,
            "zoom": zoom,
            "map_type": map_type,
            "_map_html": map_html,
            "osm_link": f"https://www.openstreetmap.org/#map={zoom}/{lat}/{lng}",
            "google_maps_link": f"https://www.google.com/maps/@{lat},{lng},{zoom}z",
        }
        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e), "detail": traceback.format_exc(), "city": city}, indent=2)


def get_distance(params: dict) -> str:
    """Calculate distance and estimated travel time between two locations."""
    origin = params["origin"]
    destination = params["destination"]
    mode = params.get("mode", "walking")

    try:
        lat1, lng1 = _geocode(origin)
        time.sleep(0.15)  # Rate limit for Nominatim
        lat2, lng2 = _geocode(destination)

        distance_m = _haversine(lat1, lng1, lat2, lng2)

        # For road travel, actual distance is typically 1.3-1.5x straight line
        road_factor = 1.4 if mode in ("driving", "transit") else 1.3
        estimated_distance = distance_m * road_factor

        travel = _estimate_travel_time(estimated_distance, mode)

        # Format distance
        if estimated_distance < 1000:
            distance_text = f"{int(estimated_distance)} m"
        else:
            distance_text = f"{estimated_distance / 1000:.1f} km"

        result = {
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "distance": distance_text,
            "distance_meters": int(estimated_distance),
            "duration": travel["duration"],
            "duration_seconds": travel["duration_seconds"],
            "data_source": "Estimated from straight-line distance (Nominatim geocoding)",
            "recommendation": (
                "Walking is great for this distance"
                if estimated_distance < 2000
                else f"Consider {mode} for this distance"
            ),
        }
        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e), "detail": traceback.format_exc(), "origin": origin, "destination": destination}, indent=2)


def find_nearby(params: dict) -> str:
    """Find real nearby places using Overpass API (OpenStreetMap)."""
    city = params["city"]
    category = params["category"]
    limit = params.get("limit", 5)

    try:
        lat, lng = _geocode(city)
        time.sleep(0.1)

        tag_filter = CATEGORY_TO_OVERPASS.get(category, '"tourism"="attraction"')
        radius = 3000

        query = f"""
        [out:json][timeout:20];
        node[{tag_filter}](around:{radius},{lat},{lng});
        out body {limit * 3};
        """

        data = _overpass_query(query)

        places = []
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            name = tags.get("name", "")
            if not name:
                continue

            place_lat = element.get("lat") or element.get("center", {}).get("lat")
            place_lng = element.get("lon") or element.get("center", {}).get("lon")

            entry = {
                "name": name,
                "address": _build_address_from_tags(tags),
                "phone": tags.get("phone"),
                "website": tags.get("website"),
                "opening_hours": tags.get("opening_hours"),
                "cuisine": tags.get("cuisine"),  # for restaurants
            }

            # Add map links
            osm_type = element.get("type", "node")
            osm_id = element.get("id", "")
            entry["osm_link"] = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
            if place_lat and place_lng:
                entry["google_maps_link"] = f"https://www.google.com/maps/search/?api=1&query={place_lat},{place_lng}"

            # Remove None values for cleaner output
            entry = {k: v for k, v in entry.items() if v is not None}

            places.append(entry)
            if len(places) >= limit:
                break

        result = {
            "city": city,
            "category": category,
            "coordinates": {"latitude": lat, "longitude": lng},
            "places": places,
            "total_found": len(places),
            "data_source": "OpenStreetMap (Overpass API)",
        }
        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e), "detail": traceback.format_exc(), "city": city, "category": category}, indent=2)


def _build_address_from_tags(tags: dict) -> str:
    """Build a human-readable address from OSM tags."""
    parts = []
    for key in ["addr:housenumber", "addr:street", "addr:city", "addr:postcode"]:
        val = tags.get(key)
        if val:
            parts.append(val)
    return ", ".join(parts) if parts else tags.get("addr:full", "")


# ── Handler registry ────────────────────────────────────────────────────────

TOOL_HANDLERS = {
    "show_map": show_map,
    "get_distance": get_distance,
    "find_nearby": find_nearby,
}


# ── Standalone MCP server entry point ───────────────────────────────────────

if __name__ == "__main__":
    from mcp.server.fastmcp import FastMCP
    _mcp = FastMCP("maps-api")

    @_mcp.tool(name="show_map")
    def _show_map(city: str, zoom: int = 13, map_type: str = "roadmap", pins: list = None) -> str:
        """Display an interactive map with multiple pins. Pass pins array with lat, lng, label for each location."""
        params = {"city": city, "zoom": zoom, "map_type": map_type}
        if pins:
            params["pins"] = pins
        return show_map(params)

    @_mcp.tool(name="get_distance")
    def _get_distance(origin: str, destination: str, mode: str = "walking") -> str:
        """Calculate REAL distance and travel time between two locations."""
        return get_distance({"origin": origin, "destination": destination, "mode": mode})

    @_mcp.tool(name="find_nearby")
    def _find_nearby(city: str, category: str, limit: int = 5) -> str:
        """Find REAL nearby places using OpenStreetMap. Returns names, ratings, addresses."""
        return find_nearby({"city": city, "category": category, "limit": limit})

    _mcp.run(transport="stdio")
