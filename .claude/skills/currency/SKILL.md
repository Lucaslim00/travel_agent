---
name: currency
description: Convert currencies using REAL-TIME exchange rates. 150+ currencies supported. No API key needed.
allowed-tools: Read Bash
---

# Currency Skill

Convert between **150+ currencies** using real-time exchange rates from open.er-api.com.

## When to Use
- User asks about currency exchange or conversion
- Showing prices in the user's local currency
- Comparing costs across different currencies
- Planning a trip budget in a foreign currency

## MCP Tools Available
Use the `fetch-currency` MCP server which exposes:
- `convert_currency` — convert an amount between currencies (params: amount, from_currency, to_currency)
- `get_exchange_rate` — get current rate between two currencies without converting a specific amount (params: from_currency, to_currency)

## Response Format
When returning currency information, include:
1. **Converted amount** — clearly formatted with currency symbols
2. **Exchange rate** — both directions (e.g., 1 USD = 154.50 JPY, 1 JPY = 0.0065 USD)
3. **Context** — whether the rate is favorable, tips on where to exchange

## Common Currency Codes
- USD, EUR, GBP, JPY, AUD, CAD, CHF, CNY
- SGD, MYR, THB, KRW, INR, IDR, PHP, VND
- BRL, MXN, ARS, COP, PEN
- AED, SAR, QAR, KWD
- ZAR, EGP, NGN, KES

## Notes
- Rates are updated daily and cached for 1 hour
- No API key needed — uses free open.er-api.com
- Supports all ISO 4217 currency codes
