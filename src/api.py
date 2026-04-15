"""
Polymarket API layer — Gamma (discovery), CLOB (prices/orderbook), Data (trades).
All public endpoints, no auth required for read operations.
"""
from __future__ import annotations

import requests
import time
from typing import Optional
import streamlit as st

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
DATA_BASE = "https://data-api.polymarket.com"

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def _get(url: str, params: Optional[dict] = None, timeout: int = 10) -> dict | list | None:
    """Safe GET with retry."""
    for attempt in range(3):
        try:
            resp = SESSION.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
            else:
                return None


# ── Gamma API (Market Discovery) ──────────────────────────────────────────────

@st.cache_data(ttl=300)
def get_active_events(limit: int = 50, offset: int = 0) -> list:
    """Fetch active events sorted by volume."""
    data = _get(f"{GAMMA_BASE}/events", params={
        "closed": "false",
        "limit": limit,
        "offset": offset,
        "order": "volume24hr",
        "ascending": "false",
    })
    return data or []


@st.cache_data(ttl=300)
def get_event(event_id: str) -> dict | None:
    """Fetch a single event by ID."""
    return _get(f"{GAMMA_BASE}/events/{event_id}")


@st.cache_data(ttl=300)
def get_markets(limit: int = 100, offset: int = 0) -> list:
    """Fetch active markets."""
    data = _get(f"{GAMMA_BASE}/markets", params={
        "closed": "false",
        "limit": limit,
        "offset": offset,
    })
    return data or []


@st.cache_data(ttl=60)
def search_markets(query: str, limit: int = 50) -> list:
    """Search markets by text query."""
    data = _get(f"{GAMMA_BASE}/markets", params={
        "closed": "false",
        "limit": limit,
        "tag": query,
    })
    if not data:
        # Fallback: fetch all and filter locally
        all_markets = get_markets(limit=200)
        q = query.lower()
        data = [m for m in all_markets if q in m.get("question", "").lower()
                or q in m.get("description", "").lower()]
    return data or []


@st.cache_data(ttl=120)
def get_markets_for_event(event_id: str) -> list:
    """Get all markets within an event."""
    data = _get(f"{GAMMA_BASE}/markets", params={
        "event_id": event_id,
        "closed": "false",
    })
    return data or []


# ── CLOB API (Prices, Orderbook, History) ─────────────────────────────────────

@st.cache_data(ttl=15)
def get_market_price(token_id: str) -> dict | None:
    """Get current price for a token."""
    data = _get(f"{CLOB_BASE}/price", params={"token_id": token_id})
    return data


@st.cache_data(ttl=15)
def get_midpoint(token_id: str) -> float | None:
    """Get midpoint price for a token."""
    data = _get(f"{CLOB_BASE}/midpoint", params={"token_id": token_id})
    if data and "mid" in data:
        try:
            return float(data["mid"])
        except (ValueError, TypeError):
            return None
    return None


@st.cache_data(ttl=30)
def get_order_book(token_id: str) -> dict | None:
    """Get order book (bids/asks) for a token."""
    data = _get(f"{CLOB_BASE}/book", params={"token_id": token_id})
    return data


@st.cache_data(ttl=60)
def get_price_history(token_id: str, interval: str = "1d", fidelity: int = 60) -> list:
    """
    Get price history timeseries.
    interval: max, 1w, 1d, 6h, 1h
    fidelity: seconds between data points
    """
    fidelity_map = {
        "1h": 60,
        "6h": 300,
        "1d": 600,
        "1w": 3600,
        "1m": 14400,
        "max": 86400,
    }
    f = fidelity_map.get(interval, fidelity)

    data = _get(f"{CLOB_BASE}/prices-history", params={
        "market": token_id,
        "interval": interval,
        "fidelity": f,
    })
    if data and "history" in data:
        return data["history"]
    return data if isinstance(data, list) else []


# ── Data API (Trades) ─────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def get_recent_trades(condition_id: str, limit: int = 50) -> list:
    """Get recent trades for a market condition."""
    # Try CLOB trades endpoint
    data = _get(f"{CLOB_BASE}/trades", params={
        "asset_id": condition_id,
        "limit": limit,
    })
    return data or []


# ── Bulk fetch helpers ────────────────────────────────────────────────────────

def get_market_by_id(condition_id: str) -> dict | None:
    """Fetch a single market by conditionId to check resolution status."""
    data = _get(f"{GAMMA_BASE}/markets/{condition_id}")
    return data


def check_market_resolved(condition_id: str) -> dict | None:
    """
    Check if a market has resolved. Returns:
    {"resolved": True, "winning_outcome": "Up"/"Down", "outcome_prices": {...}}
    or {"resolved": False} or None on error.
    """
    market = get_market_by_id(condition_id)
    if not market:
        # Market not found — might have been deleted/resolved
        return {"resolved": True, "winning_outcome": "unknown"}

    closed = market.get("closed", False)
    if not closed:
        # Also check if price is at 0 or 1 (effectively resolved)
        tokens = market.get("tokens", [])
        for t in tokens:
            price = t.get("price")
            if price is not None:
                try:
                    p = float(price)
                    if p >= 0.95:
                        return {
                            "resolved": True,
                            "winning_outcome": t.get("outcome", "unknown"),
                        }
                except (ValueError, TypeError):
                    pass
        return {"resolved": False}

    # Market is closed — determine winner
    winning = market.get("resolutionSource", "")
    tokens = market.get("tokens", [])
    winner = None
    for t in tokens:
        price = t.get("price")
        if price is not None:
            try:
                p = float(price)
                if p >= 0.95:
                    winner = t.get("outcome", "unknown")
            except (ValueError, TypeError):
                pass

    # If we can't determine from prices, check outcomes list
    if not winner:
        outcomes = market.get("outcomes", [])
        if isinstance(outcomes, str):
            import json
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []
        # Check clobTokenIds price from CLOB
        clob_ids = market.get("clobTokenIds", [])
        if isinstance(clob_ids, str):
            import json
            try:
                clob_ids = json.loads(clob_ids)
            except Exception:
                clob_ids = []
        for i, tid in enumerate(clob_ids):
            mid = get_midpoint(tid)
            if mid and mid >= 0.95 and i < len(outcomes):
                winner = outcomes[i]
                break

    return {
        "resolved": True,
        "winning_outcome": winner or "unknown",
    }


def get_all_active_markets(max_pages: int = 5) -> list:
    """Fetch all active markets with pagination."""
    all_markets = []
    for page in range(max_pages):
        batch = get_markets(limit=100, offset=page * 100)
        if not batch:
            break
        all_markets.extend(batch)
        if len(batch) < 100:
            break
    return all_markets


def get_markets_by_keywords(keywords: list[str], max_markets: int = 500) -> list:
    """Fetch markets matching any of the given keywords."""
    all_markets = get_all_active_markets()
    matched = []
    for m in all_markets:
        text = f"{m.get('question', '')} {m.get('description', '')}".lower()
        if any(kw.lower() in text for kw in keywords):
            matched.append(m)
    return matched[:max_markets]


# ── External Spot Price (for directional prediction) ─────────────────────────

def get_spot_candles(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 60) -> list:
    """
    Fetch recent candles from Binance public API. No auth needed.
    Returns list of {open, high, low, close, volume, timestamp}.
    Symbols: BTCUSDT, ETHUSDT
    Intervals: 1m, 3m, 5m, 15m, 1h
    """
    url = "https://api.binance.com/api/v3/klines"
    data = _get(url, params={"symbol": symbol, "interval": interval, "limit": limit})
    if not data:
        return []
    candles = []
    for c in data:
        candles.append({
            "timestamp": c[0],
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
        })
    return candles


def get_spot_price(symbol: str = "BTCUSDT") -> float | None:
    """Get current spot price from Binance."""
    data = _get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol})
    if data and "price" in data:
        return float(data["price"])
    return None


def get_updown_crypto_markets(asset_keywords: list[str] | None = None,
                               limit: int = 100) -> list:
    """
    Fetch live crypto Up or Down markets via the Events endpoint with tag=Crypto.
    This is the correct way to find short-duration binary markets —
    the /markets endpoint doesn't reliably return them.

    Returns a flat list of market dicts (extracted from events).
    Optionally filters to specific assets (e.g., ["Bitcoin", "Ethereum"]).
    """
    data = _get(f"{GAMMA_BASE}/events", params={
        "closed": "false",
        "active": "true",
        "limit": limit,
        "tag": "Crypto",
        "order": "startDate",
        "ascending": "false",
    })
    if not data:
        return []

    markets = []
    kw_lower = [k.lower() for k in (asset_keywords or [])]

    for event in data:
        title = event.get("title", "")
        if "up or down" not in title.lower():
            continue

        # Filter by asset keywords if provided
        if kw_lower:
            title_lower = title.lower()
            if not any(kw in title_lower for kw in kw_lower):
                continue

        # Extract markets from event
        event_markets = event.get("markets", [])
        for m in event_markets:
            if m.get("closed"):
                continue
            markets.append(m)

    return markets


# ── Non-cached versions for bot thread ────────────────────────────────────────
# Streamlit's @st.cache_data does not work from background threads.
# The bot engine must use these direct versions instead.

def get_midpoint_live(token_id: str) -> float | None:
    """Get midpoint price — no Streamlit cache. Safe for bot thread."""
    data = _get(f"{CLOB_BASE}/midpoint", params={"token_id": token_id})
    if data and "mid" in data:
        try:
            return float(data["mid"])
        except (ValueError, TypeError):
            return None
    return None


def get_order_book_live(token_id: str) -> dict | None:
    """Get order book — no Streamlit cache. Safe for bot thread."""
    return _get(f"{CLOB_BASE}/book", params={"token_id": token_id})


def get_market_by_id_live(condition_id: str) -> dict | None:
    """Fetch a single market by conditionId — no Streamlit cache."""
    return _get(f"{GAMMA_BASE}/markets/{condition_id}")


def check_market_resolved_live(condition_id: str) -> dict | None:
    """
    Check if a market has resolved — no Streamlit cache. Safe for bot thread.
    Returns {"resolved": True, "winning_outcome": "Up"/"Down"} or {"resolved": False}.
    """
    market = get_market_by_id_live(condition_id)
    if not market:
        return {"resolved": True, "winning_outcome": "unknown"}

    closed = market.get("closed", False)
    if not closed:
        tokens = market.get("tokens", [])
        for t in tokens:
            price = t.get("price")
            if price is not None:
                try:
                    p = float(price)
                    if p >= 0.95:
                        return {
                            "resolved": True,
                            "winning_outcome": t.get("outcome", "unknown"),
                        }
                except (ValueError, TypeError):
                    pass
        return {"resolved": False}

    # Market is closed — determine winner
    tokens = market.get("tokens", [])
    winner = None
    for t in tokens:
        price = t.get("price")
        if price is not None:
            try:
                p = float(price)
                if p >= 0.95:
                    winner = t.get("outcome", "unknown")
            except (ValueError, TypeError):
                pass

    if not winner:
        outcomes = market.get("outcomes", [])
        if isinstance(outcomes, str):
            import json
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []
        clob_ids = market.get("clobTokenIds", [])
        if isinstance(clob_ids, str):
            import json
            try:
                clob_ids = json.loads(clob_ids)
            except Exception:
                clob_ids = []
        for i, tid in enumerate(clob_ids):
            mid = get_midpoint_live(tid)
            if mid and mid >= 0.95 and i < len(outcomes):
                winner = outcomes[i]
                break

    return {
        "resolved": True,
        "winning_outcome": winner or "unknown",
    }


def get_token_ids_for_market(market: dict) -> dict:
    """Extract token IDs from a market object."""
    tokens = {}
    clob_token_ids = market.get("clobTokenIds")
    outcomes = market.get("outcomes")
    if clob_token_ids and outcomes:
        if isinstance(clob_token_ids, str):
            # Sometimes it's a JSON string
            import json
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except json.JSONDecodeError:
                clob_token_ids = []
        if isinstance(outcomes, str):
            import json
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = []
        for i, outcome in enumerate(outcomes):
            if i < len(clob_token_ids):
                tokens[outcome] = clob_token_ids[i]
    return tokens
