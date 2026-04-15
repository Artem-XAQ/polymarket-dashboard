"""
SQLite database layer for paper trading, bot trades, signals, and price snapshots.
"""
from __future__ import annotations

import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "dashboard.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    # Paper trading
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_wallet (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL NOT NULL DEFAULT 1000.0,
            initial_balance REAL NOT NULL DEFAULT 1000.0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    c.execute("INSERT OR IGNORE INTO paper_wallet (id, balance, initial_balance) VALUES (1, 1000.0, 1000.0)")

    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_question TEXT,
            outcome TEXT NOT NULL,
            token_id TEXT,
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            price REAL NOT NULL,
            amount REAL NOT NULL,
            shares REAL NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'expired'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_question TEXT,
            outcome TEXT NOT NULL,
            token_id TEXT,
            shares REAL NOT NULL,
            avg_price REAL NOT NULL,
            cost_basis REAL NOT NULL,
            opened_at TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed'))
        )
    """)

    # Signals / alerts
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_question TEXT,
            token_id TEXT,
            outcome TEXT,
            condition TEXT NOT NULL CHECK (condition IN ('above', 'below', 'crosses')),
            threshold REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            triggered_at TEXT,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'triggered', 'expired'))
        )
    """)

    # Bot trades (separate from paper)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_question TEXT,
            outcome TEXT NOT NULL,
            token_id TEXT,
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            price REAL NOT NULL,
            amount_usd REAL NOT NULL,
            shares REAL NOT NULL,
            mode TEXT NOT NULL DEFAULT 'paper' CHECK (mode IN ('paper', 'live')),
            strategy TEXT,
            ev_gap REAL,
            kelly_fraction REAL,
            model_probability REAL,
            market_probability REAL,
            order_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'filled', 'partial', 'cancelled', 'failed')),
            error_message TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_question TEXT,
            outcome TEXT NOT NULL,
            token_id TEXT,
            shares REAL NOT NULL,
            avg_price REAL NOT NULL,
            cost_basis REAL NOT NULL,
            current_price REAL,
            unrealized_pnl REAL,
            mode TEXT NOT NULL DEFAULT 'paper',
            opened_at TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at TEXT,
            realized_pnl REAL,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Scan history for live scanner
    c.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id TEXT NOT NULL,
            market_question TEXT,
            signal TEXT,
            model_prob REAL,
            market_prob REAL,
            ev_gap REAL,
            kelly_size REAL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Bot event log
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL DEFAULT 'INFO',
            message TEXT NOT NULL,
            details TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()


# ── Paper Trading ─────────────────────────────────────────────────────────────

def get_paper_balance() -> float:
    conn = get_connection()
    row = conn.execute("SELECT balance FROM paper_wallet WHERE id = 1").fetchone()
    conn.close()
    return row["balance"] if row else 1000.0


def update_paper_balance(new_balance: float):
    conn = get_connection()
    conn.execute("UPDATE paper_wallet SET balance = ? WHERE id = 1", (new_balance,))
    conn.commit()
    conn.close()


def reset_paper_wallet(initial_balance: float = 1000.0):
    conn = get_connection()
    conn.execute("UPDATE paper_wallet SET balance = ?, initial_balance = ? WHERE id = 1",
                 (initial_balance, initial_balance))
    conn.execute("UPDATE paper_positions SET status = 'closed', closed_at = datetime('now') WHERE status = 'open'")
    conn.commit()
    conn.close()


def record_paper_trade(market_id: str, market_question: str, outcome: str,
                       token_id: str, side: str, price: float, amount: float, shares: float):
    conn = get_connection()
    conn.execute("""
        INSERT INTO paper_trades (market_id, market_question, outcome, token_id, side, price, amount, shares)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (market_id, market_question, outcome, token_id, side, price, amount, shares))
    conn.commit()
    conn.close()


def get_paper_trades(limit: int = 50) -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM paper_trades ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_paper_position(market_id: str, market_question: str, outcome: str,
                          token_id: str, shares: float, avg_price: float, cost_basis: float):
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM paper_positions WHERE market_id = ? AND outcome = ? AND status = 'open'",
        (market_id, outcome)
    ).fetchone()

    if existing:
        new_shares = existing["shares"] + shares
        if new_shares <= 0.001:
            conn.execute("UPDATE paper_positions SET status = 'closed', closed_at = datetime('now') WHERE id = ?",
                         (existing["id"],))
        else:
            new_cost = existing["cost_basis"] + cost_basis
            new_avg = new_cost / new_shares if new_shares > 0 else 0
            conn.execute("""
                UPDATE paper_positions SET shares = ?, avg_price = ?, cost_basis = ? WHERE id = ?
            """, (new_shares, new_avg, new_cost, existing["id"]))
    else:
        if shares > 0:
            conn.execute("""
                INSERT INTO paper_positions (market_id, market_question, outcome, token_id, shares, avg_price, cost_basis)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (market_id, market_question, outcome, token_id, shares, avg_price, cost_basis))

    conn.commit()
    conn.close()


def get_paper_positions(status: str = "open") -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM paper_positions WHERE status = ? ORDER BY opened_at DESC",
                        (status,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Signals ───────────────────────────────────────────────────────────────────

def add_signal(market_id: str, market_question: str, token_id: str,
               outcome: str, condition: str, threshold: float):
    conn = get_connection()
    conn.execute("""
        INSERT INTO signals (market_id, market_question, token_id, outcome, condition, threshold)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (market_id, market_question, token_id, outcome, condition, threshold))
    conn.commit()
    conn.close()


def get_active_signals() -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM signals WHERE status = 'active' ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def trigger_signal(signal_id: int):
    conn = get_connection()
    conn.execute("UPDATE signals SET status = 'triggered', triggered_at = datetime('now') WHERE id = ?",
                 (signal_id,))
    conn.commit()
    conn.close()


# ── Bot Trades & State ────────────────────────────────────────────────────────

def record_bot_trade(market_id: str, market_question: str, outcome: str,
                     token_id: str, side: str, price: float, amount_usd: float,
                     shares: float, mode: str = "paper", strategy: str = None,
                     ev_gap: float = None, kelly_fraction: float = None,
                     model_probability: float = None, market_probability: float = None,
                     order_id: str = None, status: str = "filled") -> int:
    conn = get_connection()
    c = conn.execute("""
        INSERT INTO bot_trades (market_id, market_question, outcome, token_id, side, price,
                                amount_usd, shares, mode, strategy, ev_gap, kelly_fraction,
                                model_probability, market_probability, order_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (market_id, market_question, outcome, token_id, side, price, amount_usd,
          shares, mode, strategy, ev_gap, kelly_fraction, model_probability,
          market_probability, order_id, status))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def get_bot_trades(mode: Optional[str] = None, limit: int = 100) -> list:
    conn = get_connection()
    if mode:
        rows = conn.execute(
            "SELECT * FROM bot_trades WHERE mode = ? ORDER BY timestamp DESC LIMIT ?",
            (mode, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bot_trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_bot_position(market_id: str, market_question: str, outcome: str,
                        token_id: str, shares: float, avg_price: float,
                        cost_basis: float, mode: str = "paper"):
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM bot_positions WHERE market_id = ? AND outcome = ? AND mode = ? AND status = 'open'",
        (market_id, outcome, mode)
    ).fetchone()

    if existing:
        new_shares = existing["shares"] + shares
        if new_shares <= 0.001:
            realized = (avg_price - existing["avg_price"]) * abs(shares) if shares < 0 else 0
            conn.execute("""
                UPDATE bot_positions SET status = 'closed', closed_at = datetime('now'),
                realized_pnl = ? WHERE id = ?
            """, (realized, existing["id"]))
        else:
            new_cost = existing["cost_basis"] + cost_basis
            new_avg = new_cost / new_shares if new_shares > 0 else 0
            conn.execute("""
                UPDATE bot_positions SET shares = ?, avg_price = ?, cost_basis = ? WHERE id = ?
            """, (new_shares, new_avg, new_cost, existing["id"]))
    else:
        if shares > 0:
            conn.execute("""
                INSERT INTO bot_positions (market_id, market_question, outcome, token_id,
                                           shares, avg_price, cost_basis, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (market_id, market_question, outcome, token_id, shares, avg_price, cost_basis, mode))

    conn.commit()
    conn.close()


def get_bot_positions(mode: Optional[str] = None, status: str = "open") -> list:
    conn = get_connection()
    if mode:
        rows = conn.execute(
            "SELECT * FROM bot_positions WHERE mode = ? AND status = ? ORDER BY opened_at DESC",
            (mode, status)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bot_positions WHERE status = ? ORDER BY opened_at DESC",
            (status,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def close_bot_position(position_id: int, sell_price: float = None) -> dict | None:
    """Close a single bot position by ID. Returns the closed position or None."""
    conn = get_connection()
    pos = conn.execute("SELECT * FROM bot_positions WHERE id = ? AND status = 'open'",
                       (position_id,)).fetchone()
    if not pos:
        conn.close()
        return None

    pos_dict = dict(pos)
    realized_pnl = 0.0
    if sell_price and pos_dict.get("avg_price"):
        realized_pnl = (sell_price - pos_dict["avg_price"]) * pos_dict["shares"]

    conn.execute("""
        UPDATE bot_positions SET status = 'closed', closed_at = datetime('now'),
        realized_pnl = ? WHERE id = ?
    """, (realized_pnl, position_id))

    # Record a sell trade
    record_bot_trade(
        market_id=pos_dict["market_id"],
        market_question=pos_dict.get("market_question", ""),
        outcome=pos_dict["outcome"],
        token_id=pos_dict.get("token_id", ""),
        side="sell",
        price=sell_price or pos_dict["avg_price"],
        amount_usd=pos_dict["cost_basis"],
        shares=pos_dict["shares"],
        mode=pos_dict.get("mode", "paper"),
        status="filled",
    )

    conn.commit()
    conn.close()
    return pos_dict


def close_all_bot_positions(mode: str = None) -> int:
    """Close all open bot positions. Returns count of closed positions."""
    conn = get_connection()
    if mode:
        rows = conn.execute(
            "SELECT id FROM bot_positions WHERE status = 'open' AND mode = ?", (mode,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM bot_positions WHERE status = 'open'"
        ).fetchall()

    count = 0
    for row in rows:
        conn.execute("""
            UPDATE bot_positions SET status = 'closed', closed_at = datetime('now'),
            realized_pnl = 0 WHERE id = ?
        """, (row["id"],))
        count += 1

    conn.commit()
    conn.close()
    return count


def set_bot_state(key: str, value: str):
    conn = get_connection()
    conn.execute("""
        INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    """, (key, value))
    conn.commit()
    conn.close()


def get_bot_state(key: str) -> Optional[str]:
    conn = get_connection()
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def log_bot_event(level: str, message: str, details: str = None):
    conn = get_connection()
    conn.execute("INSERT INTO bot_log (level, message, details) VALUES (?, ?, ?)",
                 (level, message, details))
    conn.commit()
    conn.close()


def get_bot_logs(limit: int = 100, level: Optional[str] = None) -> list:
    conn = get_connection()
    if level:
        rows = conn.execute(
            "SELECT * FROM bot_log WHERE level = ? ORDER BY timestamp DESC LIMIT ?",
            (level, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bot_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def record_scan(market_id: str, market_question: str, signal: str,
                model_prob: float, market_prob: float, ev_gap: float, kelly_size: float):
    conn = get_connection()
    conn.execute("""
        INSERT INTO scan_history (market_id, market_question, signal, model_prob, market_prob, ev_gap, kelly_size)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (market_id, market_question, signal, model_prob, market_prob, ev_gap, kelly_size))
    conn.commit()
    conn.close()


def get_scan_history(limit: int = 200) -> list:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM scan_history ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot_daily_pnl() -> float:
    """Get today's realized P&L from bot trades."""
    conn = get_connection()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT COALESCE(SUM(realized_pnl), 0) as pnl FROM bot_positions
        WHERE closed_at LIKE ? AND status = 'closed'
    """, (f"{today}%",)).fetchone()
    conn.close()
    return rows["pnl"] if rows else 0.0


# Initialize on import
init_db()
