"""
MCP Server: Budget API
Provides budget tracking tools — add, remove, list, and clear trip cost items.

Usage (standalone MCP server):
    python -m mcp_server.track_budget

Usage (import handlers in app.py):
    Budget tools need st.session_state, so app.py uses its own thin wrappers
    that delegate to the pure-logic helpers below.
"""

from __future__ import annotations

import json


# ── Tool Definitions (MCP schema) ───────────────────────────────────────────

TOOLS = [
    {
        "name": "budget_add_item",
        "description": "Add a cost item to the trip budget. Use whenever recommending a flight, hotel, activity, meal, or any expense.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Budget category (e.g., 'Flights', 'Accommodation', 'Food', 'Activities', 'Transport', 'Insurance')"},
                "item": {"type": "string", "description": "Description of the item (e.g., 'ANA Flight NH 109 - Round trip')"},
                "amount": {"type": "number", "description": "Cost amount in the trip currency"},
            },
            "required": ["category", "item", "amount"],
        },
    },
    {
        "name": "budget_remove_item",
        "description": "Remove an item from the budget by name (partial match). Use when the user changes their mind.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Name of the item to remove (partial match supported)"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "budget_get_summary",
        "description": "Get the current budget breakdown with all items grouped by category and the grand total.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "budget_clear",
        "description": "Clear all items from the budget. Use when the user wants to start planning from scratch.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "budget_set_currency",
        "description": "Set the currency for the budget.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "currency": {"type": "string", "description": "Currency code (e.g., 'USD', 'EUR', 'JPY')"},
            },
            "required": ["currency"],
        },
    },
]


# ── Pure-logic helpers (no global state — accept items list + currency) ─────

def add_item(items: list[dict], currency: str, params: dict) -> tuple[list[dict], str]:
    """Add an item to the list. Returns (updated_items, json_result)."""
    new_item = {"category": params["category"], "item": params["item"], "amount": params["amount"]}
    items.append(new_item)
    total = sum(i["amount"] for i in items)
    result = json.dumps({
        "status": "added",
        "item": new_item["item"],
        "category": new_item["category"],
        "amount": new_item["amount"],
        "budget_total": round(total, 2),
        "currency": currency,
        "item_count": len(items),
    }, indent=2)
    return items, result


def remove_item(items: list[dict], currency: str, params: dict) -> tuple[list[dict], str]:
    """Remove items matching partial name. Returns (updated_items, json_result)."""
    search = params["item"].lower()
    removed = [i for i in items if search in i["item"].lower()]
    remaining = [i for i in items if search not in i["item"].lower()]
    total = sum(i["amount"] for i in remaining)
    result = json.dumps({
        "status": "removed" if removed else "not_found",
        "removed_items": removed,
        "removed_count": len(removed),
        "budget_total": round(total, 2),
        "currency": currency,
    }, indent=2)
    return remaining, result


def get_summary(items: list[dict], currency: str) -> str:
    """Return a JSON summary grouped by category."""
    categories: dict[str, list] = {}
    for item in items:
        categories.setdefault(item["category"], []).append(item)
    summary = {cat: {"items": ci, "subtotal": round(sum(i["amount"] for i in ci), 2)} for cat, ci in categories.items()}
    total = sum(i["amount"] for i in items)
    return json.dumps({"currency": currency, "categories": summary, "grand_total": round(total, 2), "item_count": len(items)}, indent=2)


def clear_items(items: list[dict], currency: str) -> str:
    """Return a JSON confirmation of clearing."""
    return json.dumps({"status": "cleared", "removed_count": len(items), "budget_total": 0, "currency": currency}, indent=2)


# ── Standalone MCP server (uses in-memory global state) ─────────────────────

_budget_items: list[dict] = []
_budget_currency = "USD"


def _mcp_add(params: dict) -> str:
    global _budget_items
    _budget_items, result = add_item(_budget_items, _budget_currency, params)
    return result

def _mcp_remove(params: dict) -> str:
    global _budget_items
    _budget_items, result = remove_item(_budget_items, _budget_currency, params)
    return result

def _mcp_summary(params: dict) -> str:
    return get_summary(_budget_items, _budget_currency)

def _mcp_clear(params: dict) -> str:
    global _budget_items
    result = clear_items(_budget_items, _budget_currency)
    _budget_items = []
    return result

def _mcp_set_currency(params: dict) -> str:
    global _budget_currency
    _budget_currency = params["currency"].upper()
    return json.dumps({"status": "currency_set", "currency": _budget_currency}, indent=2)


TOOL_HANDLERS = {
    "budget_add_item": _mcp_add,
    "budget_remove_item": _mcp_remove,
    "budget_get_summary": _mcp_summary,
    "budget_clear": _mcp_clear,
    "budget_set_currency": _mcp_set_currency,
}


if __name__ == "__main__":
    from mcp_server.mcp_protocol import run_server
    run_server("budget-api", "1.0.0", TOOLS, TOOL_HANDLERS)
