"""
Formatting and calculation helpers.
"""
from __future__ import annotations

from datetime import datetime, timezone


def format_usd(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif abs(value) >= 1_000:
        return f"${value / 1_000:.1f}K"
    else:
        return f"${value:.2f}"


def format_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def format_prob(value: float) -> str:
    return f"{value * 100:.0f}%"


def format_timestamp(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return ts or ""


def time_ago(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = diff.total_seconds()
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m ago"
        elif seconds < 86400:
            return f"{int(seconds / 3600)}h ago"
        else:
            return f"{int(seconds / 86400)}d ago"
    except (ValueError, AttributeError):
        return ""


def parse_market_price(market: dict):
    """Extract a usable price from a market object."""
    for key in ["outcomePrices", "bestAsk", "lastTradePrice"]:
        val = market.get(key)
        if val:
            if isinstance(val, str):
                import json
                try:
                    prices = json.loads(val)
                    if isinstance(prices, list) and prices:
                        return float(prices[0])
                except (json.JSONDecodeError, ValueError):
                    try:
                        return float(val)
                    except ValueError:
                        continue
            elif isinstance(val, (int, float)):
                return float(val)
    return None


def parse_volume(market: dict) -> float:
    """Extract volume from market."""
    for key in ["volume", "volume24hr", "volumeNum"]:
        val = market.get(key)
        if val:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return 0.0


def color_for_signal(signal: str) -> str:
    """Return a color for a signal type."""
    signal_colors = {
        "STRONG BUY": "#00ff88",
        "BUY": "#00d4aa",
        "CONDITIONAL": "#ffaa00",
        "FADE": "#ff6666",
        "STRONG FADE": "#ff3333",
        "PASS": "#888888",
    }
    return signal_colors.get(signal, "#888888")


def signal_emoji(signal: str) -> str:
    emojis = {
        "STRONG BUY": "🟢",
        "BUY": "🟩",
        "CONDITIONAL": "🟡",
        "FADE": "🟠",
        "STRONG FADE": "🔴",
        "PASS": "⚪",
    }
    return emojis.get(signal, "⚪")
