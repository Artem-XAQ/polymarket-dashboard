"""
Risk manager — position limits, daily loss tracking, kill switch.
The bot checks this before every trade. If any limit is breached, the trade is rejected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from src import database as db


@dataclass
class RiskLimits:
    max_position_size_usd: float = 50.0
    max_total_exposure_usd: float = 200.0
    max_daily_loss_usd: float = 50.0
    max_open_positions: int = 10
    min_ev_threshold: float = 0.03
    min_kelly_fraction: float = 0.01
    max_kelly_fraction: float = 0.25


class RiskManager:
    def __init__(self, limits: RiskLimits, mode: str = "paper"):
        self.limits = limits
        self.mode = mode
        self._kill_switch = False

    @property
    def is_killed(self) -> bool:
        # Check persistent state too
        state = db.get_bot_state("kill_switch")
        if state == "true":
            self._kill_switch = True
        return self._kill_switch

    def activate_kill_switch(self, reason: str = "Manual"):
        self._kill_switch = True
        db.set_bot_state("kill_switch", "true")
        db.log_bot_event("CRITICAL", f"KILL SWITCH ACTIVATED: {reason}")

    def deactivate_kill_switch(self):
        self._kill_switch = False
        db.set_bot_state("kill_switch", "false")
        db.log_bot_event("INFO", "Kill switch deactivated")

    def check_trade(self, amount_usd: float, ev_gap: float, kelly_fraction: float,
                    market_id: str = None) -> tuple[bool, str]:
        """
        Check if a proposed trade passes all risk filters.
        Returns: (allowed: bool, reason: str)
        """
        if self.is_killed:
            return False, "Kill switch is active"

        # Check position size
        if amount_usd > self.limits.max_position_size_usd:
            return False, f"Position size ${amount_usd:.2f} exceeds max ${self.limits.max_position_size_usd:.2f}"

        # Check EV threshold
        if ev_gap < self.limits.min_ev_threshold:
            return False, f"EV gap {ev_gap:.3f} below min threshold {self.limits.min_ev_threshold:.3f}"

        # Check Kelly
        if kelly_fraction < self.limits.min_kelly_fraction:
            return False, f"Kelly fraction {kelly_fraction:.4f} below min {self.limits.min_kelly_fraction:.4f}"

        # Check total exposure
        positions = db.get_bot_positions(mode=self.mode, status="open")
        total_exposure = sum(p["cost_basis"] for p in positions)
        if total_exposure + amount_usd > self.limits.max_total_exposure_usd:
            return False, f"Total exposure ${total_exposure + amount_usd:.2f} exceeds max ${self.limits.max_total_exposure_usd:.2f}"

        # Check open position count
        if len(positions) >= self.limits.max_open_positions:
            return False, f"Already at max {self.limits.max_open_positions} open positions"

        # Check if already in this market
        if market_id:
            existing = [p for p in positions if p["market_id"] == market_id]
            if existing:
                return False, f"Already have an open position in this market"

        # Check daily loss
        daily_pnl = db.get_bot_daily_pnl()
        if daily_pnl < -self.limits.max_daily_loss_usd:
            self.activate_kill_switch(f"Daily loss limit hit: ${daily_pnl:.2f}")
            return False, f"Daily loss ${daily_pnl:.2f} exceeds max ${self.limits.max_daily_loss_usd:.2f} — KILL SWITCH ACTIVATED"

        return True, "All checks passed"

    def cap_kelly(self, kelly_fraction: float) -> float:
        """Cap Kelly fraction to max allowed."""
        return min(kelly_fraction, self.limits.max_kelly_fraction)

    def get_status(self) -> dict:
        """Get current risk status for dashboard display."""
        positions = db.get_bot_positions(mode=self.mode, status="open")
        total_exposure = sum(p["cost_basis"] for p in positions)
        daily_pnl = db.get_bot_daily_pnl()

        return {
            "kill_switch": self.is_killed,
            "open_positions": len(positions),
            "max_positions": self.limits.max_open_positions,
            "total_exposure": total_exposure,
            "max_exposure": self.limits.max_total_exposure_usd,
            "daily_pnl": daily_pnl,
            "max_daily_loss": self.limits.max_daily_loss_usd,
            "exposure_pct": (total_exposure / self.limits.max_total_exposure_usd * 100) if self.limits.max_total_exposure_usd > 0 else 0,
            "loss_pct": (abs(daily_pnl) / self.limits.max_daily_loss_usd * 100) if daily_pnl < 0 and self.limits.max_daily_loss_usd > 0 else 0,
        }
