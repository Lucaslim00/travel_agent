import streamlit as st
import streamlit.components.v1 as components
import anthropic
import json
import logging
import re
import requests
import sys
import os
import time as _time
from collections import defaultdict
from datetime import datetime

# Add project root to path so we can import mcp_server as a package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════════════════════════════════════════════
# SECURITY: Prompt Injection Protection, Input Validation, Rate Limiting,
#           Guardrails Architecture, Output Filtering
# ══════════════════════════════════════════════════════════════════════════════

# ── Input Validation & Sanitization ──────────────────────────────────────────

# Max input length (characters)
MAX_INPUT_LENGTH = 2000

# Patterns that indicate prompt injection attempts
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(all\s+)?(previous|prior|above)",
    r"forget\s+(everything|all|your)\s+(instructions?|rules?|prompts?)",
    r"you\s+are\s+now\s+(a|an)\s+",
    r"new\s+instructions?\s*:",
    r"system\s*prompt\s*:",
    r"act\s+as\s+(a|an)\s+(?!travel)",
    r"pretend\s+(you\s+are|to\s+be)\s+",
    r"override\s+(your|the|all)\s+(instructions?|rules?|prompts?)",
    r"<\s*\/?script",
    r"<\s*\/?iframe",
    r"javascript\s*:",
    r"on(load|error|click)\s*=",
    r"\{\{\s*.*?\s*\}\}",
    r"__import__\s*\(",
    r"exec\s*\(",
    r"eval\s*\(",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def validate_input(user_input: str) -> tuple[bool, str]:
    """Validate and sanitize user input. Returns (is_valid, cleaned_input_or_error)."""
    # Length check
    if len(user_input) > MAX_INPUT_LENGTH:
        return False, f"Message too long — please keep it under {MAX_INPUT_LENGTH} characters."

    # Empty check
    stripped = user_input.strip()
    if not stripped:
        return False, "Please enter a message."

    # Prompt injection detection
    if _INJECTION_RE.search(stripped):
        return False, "I can only help with travel planning. Could you rephrase your request?"

    # HTML/script sanitization — strip dangerous tags but keep the text
    cleaned = re.sub(r"<\s*\/?\s*(script|iframe|object|embed|form|input|button|style)\b[^>]*>", "", stripped, flags=re.IGNORECASE)

    return True, cleaned


# ── Rate Limiting ────────────────────────────────────────────────────────────

# Simple per-session rate limiter
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 15  # max messages per window

if "rate_limit_log" not in st.session_state:
    st.session_state.rate_limit_log = []


def check_rate_limit() -> tuple[bool, str]:
    """Check if user has exceeded rate limit. Returns (allowed, message)."""
    now = _time.time()
    # Prune old entries
    st.session_state.rate_limit_log = [
        t for t in st.session_state.rate_limit_log if now - t < _RATE_LIMIT_WINDOW
    ]
    if len(st.session_state.rate_limit_log) >= _RATE_LIMIT_MAX:
        return False, f"Too many messages — please wait a moment before sending another."
    st.session_state.rate_limit_log.append(now)
    return True, ""


# ── Output Filtering ─────────────────────────────────────────────────────────

# Patterns to redact from assistant output
_OUTPUT_REDACT_PATTERNS = [
    (r"(sk-ant-api\d{2}-[A-Za-z0-9_-]{20,})", "[REDACTED_API_KEY]"),
    (r"(sk-[A-Za-z0-9]{20,})", "[REDACTED_KEY]"),
    (r"(AKIA[0-9A-Z]{16})", "[REDACTED_AWS_KEY]"),
    (r"(password\s*[:=]\s*[\"']?\S{6,}[\"']?)", "[REDACTED_CREDENTIAL]"),
    (r"(secret\s*[:=]\s*[\"']?\S{6,}[\"']?)", "[REDACTED_SECRET]"),
]


def filter_output(text: str) -> str:
    """Redact sensitive data from assistant output."""
    for pattern, replacement in _OUTPUT_REDACT_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


# ── Tool Input Guardrails ────────────────────────────────────────────────────

# Allowed tools and their required/optional params for validation
_TOOL_PARAM_RULES = {
    "search_flights": {"required": ["origin", "destination", "departure_date"]},
    "search_hotels": {"required": ["city"]},
    "get_temperature": {"required": ["city"]},
    "convert_currency": {"required": ["amount", "from_currency", "to_currency"]},
    "get_exchange_rate": {"required": ["from_currency", "to_currency"]},
    "show_map": {"required": ["city"]},
    "get_distance": {"required": ["origin", "destination"]},
    "find_nearby": {"required": ["city", "category"]},
    "budget_add_item": {"required": ["category", "item", "amount"]},
    "budget_remove_item": {"required": ["item"]},
    "budget_get_summary": {"required": []},
    "budget_clear": {"required": []},
    "budget_set_currency": {"required": ["currency"]},
}


def validate_tool_call(name: str, params: dict) -> tuple[bool, str]:
    """Validate tool name and parameters before execution."""
    # Only allow known tools
    if name not in _TOOL_PARAM_RULES:
        logger.warning("BLOCKED unknown tool call: %s", name)
        return False, f"Unknown tool: {name}"

    # Check required params exist
    rules = _TOOL_PARAM_RULES[name]
    for param in rules["required"]:
        if param not in params or params[param] is None:
            logger.warning("BLOCKED tool %s — missing required param: %s", name, param)
            return False, f"Missing required parameter: {param}"

    # Sanitize string params (strip HTML/script injection in tool inputs)
    for key, value in params.items():
        if isinstance(value, str):
            params[key] = re.sub(r"<\s*\/?\s*(script|iframe|object|embed)\b[^>]*>", "", value, flags=re.IGNORECASE)

    return True, ""

# ── Logging Setup ──────────────────────────────────────────────────────────

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "mcp_tools.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("tina.mcp")

from mcp_server.fetch_weather import get_temperature
from mcp_server.fetch_flights import search_flights
from mcp_server.display_map import show_map, get_distance, find_nearby
from mcp_server.fetch_hotels import search_hotels
from mcp_server.fetch_currency import convert_currency, get_exchange_rate
from mcp_server.track_budget import add_item, remove_item, get_summary, clear_items

st.set_page_config(page_title="Tina - Travel Assistant Agent", page_icon="✈️", layout="wide")

# ── Chat Window CSS ────────────────────────────────────────────────────────
# Makes the chat area a fixed-height scrollable container like Claude/ChatGPT

st.markdown("""
<style>
/* ── Layout: push content below the fixed toolbar ─────────────────── */
.stMainBlockContainer { padding-top: 3.5rem !important; }

/* ── Chat input border cleanup ─────────────────────────────────────── */
[data-testid="stChatInput"] { border: none !important; box-shadow: none !important; }
[data-testid="stChatInput"] > div { border-color: #e2e8f0 !important; box-shadow: none !important; border-radius: 12px !important; }
[data-testid="stVerticalBlock"] > div:has(> [data-testid="stChatInput"]) { margin-top: -1rem !important; }

/* ── Chat message bubbles ──────────────────────────────────────────── */
[data-testid="stChatMessage"] { border-radius: 10px !important; padding: 0.85rem 1.1rem !important; margin-bottom: 0.4rem !important; }
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) { background-color: #f0f4ff !important; border-left: 3px solid #4361ee !important; }
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) { background-color: #fafafa !important; border-left: 3px solid #e2e8f0 !important; }

/* ── Itinerary day headings ─────────────────────────────────────────── */
[data-testid="stChatMessage"] h3 { color: #6d28d9 !important; border-bottom: 2px solid #ede9fe !important; padding-bottom: 0.25rem !important; margin-top: 1rem !important; }

/* ── Budget right column ────────────────────────────────────────────── */
[data-testid="stColumn"]:last-of-type { background-color: #f8fafc !important; border-left: 1px solid #e2e8f0 !important; }

/* ═══ WORKFLOW COLOUR BANNERS ═══════════════════════════════════════════
   Each section of the trip plan gets its own colour strip.
   Text is always white — ensured with the * selector below.         */
.pretavel-banner  { background: linear-gradient(90deg,#0d9488,#14b8a6); }
.flights-banner   { background: linear-gradient(90deg,#1d4ed8,#3b82f6); }
.hotels-banner    { background: linear-gradient(90deg,#b45309,#f59e0b); }
.itinerary-banner { background: linear-gradient(90deg,#6d28d9,#8b5cf6); }
.budget-banner    { background: linear-gradient(90deg,#047857,#10b981); }
.tips-banner      { background: linear-gradient(90deg,#be123c,#f43f5e); }
.map-banner       { background: linear-gradient(90deg,#0e7490,#06b6d4); }

.pretavel-banner, .flights-banner, .hotels-banner,
.itinerary-banner, .budget-banner, .tips-banner, .map-banner {
    padding: 0.6rem 1rem; border-radius: 8px;
    font-size: 1.05rem; font-weight: 700;
    margin: 0.9rem 0 0.5rem; letter-spacing: 0.01em;
}
.pretavel-banner *, .flights-banner *, .hotels-banner *,
.itinerary-banner *, .budget-banner *, .tips-banner *, .map-banner *,
.pretavel-banner, .flights-banner, .hotels-banner,
.itinerary-banner, .budget-banner, .tips-banner, .map-banner {
    color: #ffffff !important;
}
</style>
""", unsafe_allow_html=True)

# ── User Location Tracking ──────────────────────────────────────────────────

def get_location_from_ip() -> dict | None:
    """Get approximate user location from IP address using ip-api.com (free, no key)."""
    try:
        resp = requests.get("http://ip-api.com/json/?fields=status,city,regionName,country,countryCode,lat,lon,timezone,currency", timeout=5)
        data = resp.json()
        if data.get("status") == "success":
            return {
                "city": data.get("city", ""),
                "region": data.get("regionName", ""),
                "country": data.get("country", ""),
                "country_code": data.get("countryCode", ""),
                "latitude": data.get("lat"),
                "longitude": data.get("lon"),
                "timezone": data.get("timezone", ""),
                "currency": data.get("currency", ""),
            }
    except Exception:
        pass
    return None


if "user_location" not in st.session_state:
    st.session_state.user_location = get_location_from_ip()

# ── Session State Init ──────────────────────────────────────────────────────

if "budget_items" not in st.session_state:
    st.session_state.budget_items = []
if "budget_currency" not in st.session_state:
    st.session_state.budget_currency = "USD"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_maps" not in st.session_state:
    st.session_state.pending_maps = []  # list of HTML strings to render after chat

# ── Sidebar ─────────────────────────────────────────────────────────────────
api_key = st.sidebar.text_input("Anthropic API Key", type="password")
st.sidebar.divider()

# Location display and override
st.sidebar.markdown("### 📍 Your Location")
detected = st.session_state.user_location
if detected:
    default_location = f"{detected['city']}, {detected['region']}, {detected['country']}"
else:
    default_location = ""

user_location_input = st.sidebar.text_input(
    "Location (auto-detected or enter manually)",
    value=default_location,
    help="Used to personalise flight origins, currency, and recommendations.",
)

if user_location_input and user_location_input != default_location:
    st.session_state.user_location = {
        "city": user_location_input.split(",")[0].strip() if "," in user_location_input else user_location_input.strip(),
        "region": "",
        "country": user_location_input.split(",")[-1].strip() if "," in user_location_input else "",
        "country_code": "",
        "latitude": None,
        "longitude": None,
        "timezone": "",
        "currency": "",
    }

st.sidebar.divider()
st.sidebar.markdown("### Tina can help with:")
st.sidebar.markdown(
    "- Real flight searches (Google Flights)\n"
    "- Hotel recommendations\n"
    "- Real temperature forecasts (Based on past data)\n"
    "- Interactive 2D maps (Google Maps)\n"
    "- Currency conversion\n"
    "- Live budget tracking"
)

if not api_key:
    st.info("Please enter your Anthropic API key in the sidebar to get started.")
    st.stop()

client = anthropic.Anthropic(api_key=api_key)

# ── Layout: Chat (left) | Budget Panel (right) ─────────────────────────────

chat_col, budget_col = st.columns([3, 1], gap="large")

# ── Budget Panel (right column) ─────────────────────────────────────────────

budget_currency = st.session_state.user_location.get("currency", "USD") if st.session_state.user_location else "USD"
st.session_state.budget_currency = budget_currency

with budget_col:
    st.markdown(f"""
<div style="background:linear-gradient(90deg,#047857,#10b981);
     color:#fff;padding:0.75rem 1rem;border-radius:8px;
     font-size:1.05rem;font-weight:700;margin-bottom:0.6rem;
     display:flex;justify-content:space-between;align-items:center;">
  <span>💰 Estimated Trip Budget</span>
  <span style="font-size:0.8rem;opacity:0.85;font-weight:500;">{budget_currency}</span>
</div>
""", unsafe_allow_html=True)
    budget_placeholder = st.empty()

    if st.session_state.budget_items and st.button("Clear all", type="secondary"):
        st.session_state.budget_items = []
        st.rerun()


def _render_budget():
    """Re-render the budget panel into the placeholder."""

    # Colour + icon per category
    _CAT_STYLE = {
        "Flights":       ("✈️", "#1d4ed8", "#eff6ff"),
        "Accommodation": ("🏨", "#b45309", "#fffbeb"),
        "Itinerary":     ("📅", "#6d28d9", "#f5f3ff"),
        "Food":          ("🍽️", "#be123c", "#fff1f2"),
        "Transport":     ("🚗", "#0e7490", "#ecfeff"),
        "Activities":    ("🎟️", "#047857", "#ecfdf5"),
        "Other":         ("📦", "#475569", "#f8fafc"),
    }

    with budget_placeholder.container():
        items = st.session_state.budget_items

        if items:
            # Group by category
            categories: dict[str, list] = {}
            for item in items:
                cat = item["category"]
                categories.setdefault(cat, []).append(item)

            grand_total = sum(i["amount"] for i in items)

            for cat, cat_items in categories.items():
                cat_total = sum(i["amount"] for i in cat_items)
                icon, accent, bg = _CAT_STYLE.get(cat, ("📦", "#475569", "#f8fafc"))
                pct = (cat_total / grand_total * 100) if grand_total else 0

                # Category header card
                st.markdown(f"""
<div style="background:{bg};border-left:3px solid {accent};
     border-radius:6px;padding:0.45rem 0.7rem;margin:0.4rem 0 0.1rem;">
  <span style="font-size:0.8rem;font-weight:700;color:{accent};">
    {icon} {cat.upper()}
  </span>
  <span style="float:right;font-size:0.85rem;font-weight:700;color:#1e293b;">
    {budget_currency} {cat_total:,.2f}
  </span>
</div>
""", unsafe_allow_html=True)

                # Progress bar
                st.markdown(f"""
<div style="background:#e2e8f0;border-radius:99px;height:4px;margin:0 0 0.3rem;">
  <div style="background:{accent};width:{pct:.1f}%;height:4px;border-radius:99px;"></div>
</div>
""", unsafe_allow_html=True)

                # Line items
                for it in cat_items:
                    st.markdown(f"""
<div style="display:flex;justify-content:space-between;
     padding:0.15rem 0.7rem;font-size:0.78rem;color:#64748b;">
  <span>• {it['item']}</span>
  <span>{budget_currency} {it['amount']:,.2f}</span>
</div>
""", unsafe_allow_html=True)

            # Grand total footer
            st.markdown(f"""
<div style="background:linear-gradient(90deg,#047857,#10b981);
     border-radius:8px;padding:0.7rem 1rem;margin-top:0.8rem;
     display:flex;justify-content:space-between;align-items:center;">
  <span style="color:#ffffff;font-size:0.9rem;font-weight:600;">Grand Total</span>
  <span style="color:#ffffff;font-size:1.1rem;font-weight:800;">
    {budget_currency} {grand_total:,.2f}
  </span>
</div>
""", unsafe_allow_html=True)

        else:
            st.markdown("""
<div style="text-align:center;padding:1.5rem 0.5rem;color:#94a3b8;">
  <div style="font-size:2rem;">🧳</div>
  <div style="font-size:0.82rem;margin-top:0.3rem;">
    Costs will appear here as Tina plans your trip.
  </div>
</div>
""", unsafe_allow_html=True)
            st.markdown(f"""
<div style="background:#f1f5f9;border-radius:8px;padding:0.7rem 1rem;
     display:flex;justify-content:space-between;">
  <span style="color:#64748b;font-size:0.9rem;font-weight:600;">Grand Total</span>
  <span style="color:#94a3b8;font-size:1rem;font-weight:700;">{budget_currency} 0.00</span>
</div>
""", unsafe_allow_html=True)


# Initial render
_render_budget()

# ── Load Agent Prompt from travel-agent.md ─────────────────────────────────

_AGENT_MD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".claude", "agents", "travel-agent.md")


def _load_agent_prompt() -> str:
    """Read travel-agent.md and strip the YAML frontmatter, returning the body."""
    with open(_AGENT_MD_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # Strip YAML frontmatter (between --- delimiters)
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].strip()
    return content


def _build_system_prompt() -> str:
    """Build the full system prompt: agent.md body + dynamic runtime context."""
    base_prompt = _load_agent_prompt()

    # ── Dynamic: user location context ─────────────────────────────────
    loc = st.session_state.user_location
    location_context = ""
    if loc and loc.get("city"):
        location_parts = [loc["city"]]
        if loc.get("region"):
            location_parts.append(loc["region"])
        if loc.get("country"):
            location_parts.append(loc["country"])
            location_str = ", ".join(location_parts)
            location_context = f"\n\n## Runtime Context\n\nThe user is currently located in **{location_str}**."
        if loc.get("currency"):
            location_context += f" Their local currency is {loc['currency']}."
        if loc.get("timezone"):
            location_context += f" Their timezone is {loc['timezone']}."
        location_context += (
            " Use this as the default origin for flights and default currency "
            "for price estimates unless the user specifies otherwise."
        )

    # ── Dynamic: budget state ──────────────────────────────────────────
    budget_summary = ""
    if st.session_state.budget_items:
        total = sum(i["amount"] for i in st.session_state.budget_items)
        budget_summary = (
            f"\n\nCurrent trip budget ({st.session_state.budget_currency}): "
            f"{len(st.session_state.budget_items)} items totalling "
            f"{st.session_state.budget_currency} {total:,.2f}. "
            "The budget panel is visible to the user on the right side of the screen."
        )
    else:
        budget_summary = (
            "\n\nThe budget panel is empty. Use the budget_add_item tool to add costs "
            "as you recommend flights, hotels, activities, etc."
        )

    workflow_reminder = (
        "\n\n## CRITICAL EXECUTION RULE\n"
        "When the user asks to plan a trip, you MUST complete ALL 4 parts of the workflow "
        "in a single conversation turn — Part 1 → Part 2 → Part 3 → Part 4 — without stopping, "
        "without asking permission, and without waiting for any user input between parts. "
        "Each part flows directly into the next. The workflow is only complete when all 4 parts "
        "plus the map, budget summary, and tips have been output."
    )

    return base_prompt + location_context + budget_summary + workflow_reminder

# ── Tool Definitions (Anthropic API format) ─────────────────────────────────

TOOLS = [
    {
        "name": "search_flights",
        "description": "Search for REAL flights using Google Flights. Returns live prices, airlines, durations, stops, carbon emissions. REQUIRES IATA airport codes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Departure IATA airport code (e.g., 'JFK', 'LAX', 'SIN', 'KUL')"},
                "destination": {"type": "string", "description": "Arrival IATA airport code (e.g., 'NRT', 'CDG', 'LHR')"},
                "departure_date": {"type": "string", "description": "Departure date (YYYY-MM-DD)"},
                "return_date": {"type": "string", "description": "Return date (YYYY-MM-DD, omit for one-way)"},
                "passengers": {"type": "integer", "description": "Number of adult passengers (default: 1)", "default": 1},
                "seat_class": {"type": "string", "enum": ["economy", "premium-economy", "business", "first"], "default": "economy"},
                "max_stops": {"type": "integer", "description": "Maximum stops allowed. Omit for any."},
                "currency": {"type": "string", "description": "Currency for prices (default: USD)", "default": "USD"},
            },
            "required": ["origin", "destination", "departure_date"],
        },
    },
    {
        "name": "search_hotels",
        "description": "Search for REAL hotels using Google Hotels. Returns live per-night prices, star ratings, guest ratings, review counts, deal labels, and website links. Price is per night — estimated_total is calculated as price_per_night × nights.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City to search (e.g., 'Paris, France')"},
                "check_in": {"type": "string", "description": "Check-in date (YYYY-MM-DD)"},
                "check_out": {"type": "string", "description": "Check-out date (YYYY-MM-DD)"},
                "budget": {"type": "string", "enum": ["budget", "mid-range", "luxury"], "default": "mid-range"},
                "currency": {"type": "string", "description": "Currency code for prices (default: USD)", "default": "USD"},
                "limit": {"type": "integer", "description": "Max results (default: 5)", "default": 5},
            },
            "required": ["city"],
        },
    },
    {
        "name": "get_temperature",
        "description": "Getches temperature forecasts based on historical averages for distant future dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name (e.g., 'Barcelona, Spain')"},
                "date": {"type": "string", "description": "Date to get temperature for (YYYY-MM-DD). Defaults to today if omitted."},
                "end_date": {"type": "string", "description": "Optional end date (YYYY-MM-DD) for a multi-day range. If omitted, returns only the single date."},
            },
            "required": ["city"],
        },
    },
    {
        "name": "convert_currency",
        "description": "Convert an amount between currencies using REAL-TIME exchange rates from open.er-api.com. Supports 150+ currencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Amount to convert"},
                "from_currency": {"type": "string", "description": "Source currency code (e.g., 'USD')"},
                "to_currency": {"type": "string", "description": "Target currency code (e.g., 'EUR')"},
            },
            "required": ["amount", "from_currency", "to_currency"],
        },
    },
    {
        "name": "get_exchange_rate",
        "description": "Get the current exchange rate between two currencies without converting a specific amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_currency": {"type": "string", "description": "Base currency code (e.g., 'USD')"},
                "to_currency": {"type": "string", "description": "Target currency code (e.g., 'JPY')"},
            },
            "required": ["from_currency", "to_currency"],
        },
    },
    {
        "name": "show_map",
        "description": "Display an interactive map with multiple pins. Pass pins array with lat, lng, label for each location (attractions, hotels, restaurants).",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City for map center (e.g., 'Hanoi, Vietnam')"},
                "zoom": {"type": "integer", "description": "Zoom level 1-20 (default: 13)", "default": 13},
                "map_type": {"type": "string", "enum": ["roadmap", "satellite"], "default": "roadmap"},
                "pins": {
                    "type": "array",
                    "description": "Locations to pin on the map",
                    "items": {
                        "type": "object",
                        "properties": {
                            "lat": {"type": "number", "description": "Latitude"},
                            "lng": {"type": "number", "description": "Longitude"},
                            "label": {"type": "string", "description": "Pin label"},
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
        "description": "Calculate REAL distance and travel time between two locations using Google Distance Matrix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Starting location"},
                "destination": {"type": "string", "description": "Ending location"},
                "mode": {"type": "string", "enum": ["walking", "driving", "transit"], "default": "walking"},
            },
            "required": ["origin", "destination"],
        },
    },
    {
        "name": "find_nearby",
        "description": "Find REAL nearby places using Google Places API. Returns names, ratings, addresses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City to search in"},
                "category": {"type": "string", "enum": ["attractions", "restaurants", "hotels", "shopping", "nightlife", "museums"]},
                "limit": {"type": "integer", "description": "Max results (default: 5)", "default": 5},
            },
            "required": ["city", "category"],
        },
    },
    {
        "name": "budget_add_item",
        "description": "Add a cost item to the trip budget panel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Budget category (e.g., 'Flights', 'Accommodation', 'Food', 'Activities', 'Transport')"},
                "item": {"type": "string", "description": "Description of the item"},
                "amount": {"type": "number", "description": "Cost amount in the user's selected currency"},
            },
            "required": ["category", "item", "amount"],
        },
    },
    {
        "name": "budget_remove_item",
        "description": "Remove an item from the budget by name (partial match).",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Name of the item to remove"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "budget_get_summary",
        "description": "Get the current budget breakdown.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "budget_clear",
        "description": "Clear all items from the budget.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


# ── Tool Router ─────────────────────────────────────────────────────────────


def execute_tool(name: str, tool_input: dict) -> str:
    """Route tool calls to the correct handler, with logging."""

    logger.info("=" * 70)
    logger.info("TOOL CALL: %s", name)
    logger.info("INPUT: %s", json.dumps(tool_input, indent=2))

    start_time = datetime.now()
    result = _route_tool(name, tool_input)
    elapsed = (datetime.now() - start_time).total_seconds()

    # Log output (truncate if very long)
    output_preview = result[:2000] if len(result) > 2000 else result
    logger.info("OUTPUT (%s, %.2fs): %s", name, elapsed, output_preview)
    if len(result) > 2000:
        logger.info("  ... (truncated, full output is %d chars)", len(result))
    logger.info("=" * 70)

    return result


def _route_tool(name: str, tool_input: dict) -> str:
    """Route tool calls to the correct handler."""

    # ── Imported from MCP modules ───────────────────────────────────────
    if name == "search_flights":
        return search_flights(tool_input)

    elif name == "get_temperature":
        return get_temperature(tool_input)

    elif name == "show_map":
        result_str = show_map(tool_input)
        # Extract map HTML for inline rendering
        try:
            result_data = json.loads(result_str)
            if "_map_html" in result_data:
                st.session_state.pending_maps.append({
                    "city": result_data.get("city", ""),
                    "html": result_data["_map_html"],
                })
        except (json.JSONDecodeError, KeyError):
            pass
        return result_str

    elif name == "get_distance":
        return get_distance(tool_input)

    elif name == "find_nearby":
        return find_nearby(tool_input)

    # ── Imported from MCP modules (hotels + currency) ────────────────
    elif name == "search_hotels":
        return search_hotels(tool_input)

    elif name == "convert_currency":
        return convert_currency(tool_input)

    elif name == "get_exchange_rate":
        return get_exchange_rate(tool_input)

    # ── Budget tools ────────────────────────────────────────────────────
    elif name == "budget_add_item":
        st.session_state.budget_items, result = add_item(
            st.session_state.budget_items, st.session_state.budget_currency, tool_input
        )
        return result

    elif name == "budget_remove_item":
        st.session_state.budget_items, result = remove_item(
            st.session_state.budget_items, st.session_state.budget_currency, tool_input
        )
        return result

    elif name == "budget_get_summary":
        return get_summary(st.session_state.budget_items, st.session_state.budget_currency)

    elif name == "budget_clear":
        result = clear_items(st.session_state.budget_items, st.session_state.budget_currency)
        st.session_state.budget_items = []
        return result

    return json.dumps({"error": f"Unknown tool: {name}"})


# ── Chat (left column) ─────────────────────────────────────────────────────

with chat_col:
    st.markdown("""<h3 style="margin:0 0 0.5rem 0; color:#1a1a2e;">
        ✈️ Tina — Your Travel Agent
    </h3>""", unsafe_allow_html=True)

    # ── Scrollable chat window ─────────────────────────────────────────
    chat_container = st.container(height=595)

    with chat_container:
        # Display chat history (including saved maps)
        for message in st.session_state.messages:
            with st.chat_message(message["role"], avatar="👩🏻‍💼" if message["role"] == "assistant" else None):
                st.markdown(message["content"], unsafe_allow_html=True)
                # Render any maps saved with this message
                for map_data in message.get("maps", []):
                    st.caption(f"📍 Map: {map_data['city']}")
                    components.html(map_data["html"], height=420)

    # ── Chat Input (pinned below the container) ────────────────────────
    if prompt := st.chat_input("Where would you like to travel?"):
        # ── Security: Input validation & rate limiting ─────────────────
        is_valid, validated = validate_input(prompt)
        if not is_valid:
            with chat_container:
                with st.chat_message("assistant", avatar="👩🏻‍💼"):
                    st.warning(validated)
            st.stop()

        rate_ok, rate_msg = check_rate_limit()
        if not rate_ok:
            with chat_container:
                with st.chat_message("assistant", avatar="👩🏻‍💼"):
                    st.warning(rate_msg)
            st.stop()

        prompt = validated  # use sanitized input
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.pending_maps = []  # reset pending maps for this turn

        with chat_container:
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant", avatar="👩🏻‍💼"):
                api_messages: list[dict] = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state.messages
                ]

                # Friendly status messages (no tool names exposed)
                _TOOL_STATUS = {
                    "search_flights": "✈️ Searching for flights...",
                    "search_hotels": "🏨 Finding hotels...",
                    "get_temperature": "🌡️ Checking the weather...",
                    "convert_currency": "💱 Converting currency...",
                    "get_exchange_rate": "💱 Checking exchange rates...",
                    "show_map": "🗺️ Loading map...",
                    "get_distance": "📏 Calculating distance...",
                    "find_nearby": "📍 Finding nearby places...",
                    "budget_add_item": "💰 Updating budget...",
                    "budget_remove_item": "💰 Updating budget...",
                    "budget_get_summary": "💰 Reviewing budget...",
                    "budget_clear": "💰 Clearing budget...",
                }

                all_text_parts: list[str] = []
                max_iterations = 50

                for _ in range(max_iterations):
                    with st.spinner("Tina is thinking..."):
                        response = client.messages.create(
                            model="claude-haiku-4-5",
                            max_tokens=8192,
                            system=_build_system_prompt(),
                            tools=TOOLS,
                            messages=api_messages,
                        )

                    # Show any text from this iteration immediately
                    for block in response.content:
                        if hasattr(block, "text") and block.text.strip():
                            filtered_text = filter_output(block.text)
                            st.markdown(filtered_text, unsafe_allow_html=True)
                            all_text_parts.append(filtered_text)

                    if response.stop_reason == "end_turn":
                        break

                    assistant_content = response.content
                    api_messages.append({"role": "assistant", "content": assistant_content})

                    tool_results = []
                    for block in assistant_content:
                        if block.type == "tool_use":
                            # ── Security: Validate tool call before execution ──
                            tool_ok, tool_err = validate_tool_call(block.name, block.input)
                            if not tool_ok:
                                logger.warning("BLOCKED tool call: %s — %s", block.name, tool_err)
                                result = json.dumps({"error": tool_err})
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                })
                                continue

                            status_msg = _TOOL_STATUS.get(block.name, "🔍 Looking things up...")
                            with st.spinner(status_msg):
                                result = execute_tool(block.name, block.input)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            })
                            # Live-update budget panel after budget tool calls
                            if block.name.startswith("budget_"):
                                _render_budget()

                    api_messages.append({"role": "user", "content": tool_results})

                # Combine all text for saving to session
                assistant_text = "\n\n".join(all_text_parts)

                # Render any maps that were generated during this turn
                for map_data in st.session_state.pending_maps:
                    st.caption(f"📍 Map: {map_data['city']}")
                    components.html(map_data["html"], height=420)

        # Save message with associated maps
        st.session_state.messages.append({
            "role": "assistant",
            "content": assistant_text,
            "maps": list(st.session_state.pending_maps),
        })
        st.session_state.pending_maps = []

        # Rerun to update the budget panel
        st.rerun()
