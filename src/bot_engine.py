"""
Bot engine — targets 5-minute BTC/ETH Up or Down binary markets on Polymarket.

Scans for active 5-min markets, estimates directional probability using
short-term momentum/mean-reversion/volatility signals, runs all 6 quant
formulas, checks risk limits, and executes trades (paper or live).
"""
from __future__ import annotations

import re
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from src import api
from src import database as db
from src.quant import run_scorecard, ev_gap, kelly_for_binary_market
from src.risk_manager import RiskManager, RiskLimits
from src.order_executor import get_executor, OrderResult
from src.utils import parse_market_price, parse_volume

logger = logging.getLogger(__name__)

# ── Market filtering patterns ────────────────────────────────────────────────

# Base pattern: matches any BTC/ETH Up or Down market
UPDOWN_PATTERN = re.compile(
    r"(Bitcoin|Ethereum|BTC|ETH)\s+Up\s+or\s+Down",
    re.IGNORECASE,
)

# Timeframe classification
# Time range pattern: "12:45AM-12:50AM" or "9:00AM-9:15AM"
TIME_RANGE_RE = re.compile(r"(\d+):(\d+)\s*([AP]M)\s*-\s*(\d+):(\d+)\s*([AP]M)", re.IGNORECASE)

# Daily pattern: "Up or Down on March 22?" (no specific time)
DAILY_PATTERN = re.compile(r"Up or Down on \w+ \d+\??", re.IGNORECASE)

# All supported timeframe buckets
TIMEFRAMES = ["5min", "15min", "1h", "4h", "daily"]
TIMEFRAME_LABELS = {
    "5min": "5 Min",
    "15min": "15 Min",
    "1h": "1 Hour",
    "4h": "4 Hours",
    "daily": "Daily",
}


def _parse_time_to_minutes(h: int, m: int, ampm: str) -> int:
    """Convert 12h time to minutes since midnight."""
    if ampm.upper() == "AM":
        hour = 0 if h == 12 else h
    else:
        hour = h if h == 12 else h + 12
    return hour * 60 + m


def classify_timeframe(question: str) -> str | None:
    """
    Classify a BTC/ETH Up or Down market into a timeframe bucket.
    Returns: "5min", "15min", "1h", "4h", "daily", or None if not a match.
    """
    if not UPDOWN_PATTERN.search(question):
        return None

    # Check daily first
    if DAILY_PATTERN.search(question):
        return "daily"

    # Check for time range
    match = TIME_RANGE_RE.search(question)
    if not match:
        # Single time like "March 22, 12AM ET" → 1 hour block
        if re.search(r"\d+[AP]M\s+ET$", question, re.IGNORECASE):
            return "1h"
        return None

    # Parse start and end times
    h1, m1, ap1 = int(match.group(1)), int(match.group(2)), match.group(3)
    h2, m2, ap2 = int(match.group(4)), int(match.group(5)), match.group(6)

    start_mins = _parse_time_to_minutes(h1, m1, ap1)
    end_mins = _parse_time_to_minutes(h2, m2, ap2)

    # Handle midnight crossing
    if end_mins < start_mins:
        end_mins += 24 * 60

    duration = end_mins - start_mins

    if duration <= 5:
        return "5min"
    elif duration <= 15:
        return "15min"
    elif duration <= 60:
        return "1h"
    elif duration <= 240:
        return "4h"
    else:
        return "daily"


def is_updown_market(question: str, timeframes: list[str] | None = None) -> bool:
    """
    Check if a market matches a crypto Up or Down pattern for the given timeframes.
    If timeframes is None, matches ALL timeframes.
    """
    tf = classify_timeframe(question)
    if tf is None:
        return False
    if timeframes is None:
        return True
    return tf in timeframes


# Backwards compat alias
def is_5min_updown_market(question: str) -> bool:
    return is_updown_market(question, ["5min"])


# ── Market expiry parsing ────────────────────────────────────────────────────

# Pattern: "March 24, 12:55AM-1:00AM ET"  or  "March 25, 1AM ET"
DATE_TIME_RE = re.compile(
    r"(\w+)\s+(\d+),?\s+"           # Month Day
    r"(\d+):?(\d+)?\s*([AP]M)\s*"   # Start time
    r"(?:-\s*(\d+):?(\d+)?\s*([AP]M)\s*)?"  # Optional end time
    r"ET",
    re.IGNORECASE,
)

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_market_end_time(question: str) -> datetime | None:
    """
    Parse the market's end/resolution time from the question string.
    Returns a timezone-aware datetime in UTC, or None if unparseable.

    Examples:
      "Bitcoin Up or Down - March 24, 12:55AM-1:00AM ET" → 1:00AM ET on March 24
      "Bitcoin Up or Down - March 25, 1AM ET" → 1AM ET + 1hr on March 25
    """
    match = DATE_TIME_RE.search(question)
    if not match:
        return None

    month_str = match.group(1).lower()
    day = int(match.group(2))
    month = MONTH_MAP.get(month_str)
    if not month:
        return None

    # Use current year (or next year if month is earlier than now)
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    et = ZoneInfo("America/New_York")

    # If there's an end time, use it as the resolution time
    if match.group(6):
        end_h = int(match.group(6))
        end_m = int(match.group(7) or 0)
        end_ap = match.group(8)
        end_mins = _parse_time_to_minutes(end_h, end_m, end_ap)
    else:
        # Single time like "1AM ET" — assume 1-hour block, resolution = start + 1h
        start_h = int(match.group(3))
        start_m = int(match.group(4) or 0)
        start_ap = match.group(5)
        end_mins = _parse_time_to_minutes(start_h, start_m, start_ap) + 60

    end_hour = end_mins // 60
    end_minute = end_mins % 60

    # Handle day rollover (e.g., 24:00 = next day 00:00)
    extra_days = 0
    if end_hour >= 24:
        end_hour -= 24
        extra_days = 1

    try:
        end_et = datetime(year, month, day, end_hour, end_minute, tzinfo=et)
        end_et += timedelta(days=extra_days)
        return end_et.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def get_asset_from_question(question: str) -> str:
    """Extract BTC or ETH from market question."""
    q = question.lower()
    if "bitcoin" in q or "btc" in q:
        return "BTC"
    elif "ethereum" in q or "eth" in q:
        return "ETH"
    return "UNKNOWN"


class TradingBot:
    """
    5-Minute Binary Market Bot.
    Targets BTC/ETH "Up or Down" markets on Polymarket.
    Runs in a loop: scan -> filter 5-min markets -> analyze -> decide -> execute -> log.
    """

    def __init__(self, config: dict):
        self.config = config
        self.mode = config.get("bot", {}).get("mode", "paper")
        self.scan_interval = config.get("bot", {}).get("scan_interval_seconds", 15)
        self.assets = config.get("bot", {}).get("assets", ["Bitcoin", "Ethereum"])
        self.timeframes = config.get("bot", {}).get("timeframes", TIMEFRAMES)

        # Build search keywords from assets
        self.keywords = []
        for asset in self.assets:
            self.keywords.append(asset)
            if asset.lower() == "bitcoin":
                self.keywords.append("BTC")
            elif asset.lower() == "ethereum":
                self.keywords.append("ETH")

        # Risk manager
        risk_cfg = config.get("risk", {})
        limits = RiskLimits(
            max_position_size_usd=risk_cfg.get("max_position_size_usd", 25.0),
            max_total_exposure_usd=risk_cfg.get("max_total_exposure_usd", 150.0),
            max_daily_loss_usd=risk_cfg.get("max_daily_loss_usd", 50.0),
            max_open_positions=risk_cfg.get("max_open_positions", 5),
            min_ev_threshold=risk_cfg.get("min_ev_threshold", 0.03),
            min_kelly_fraction=risk_cfg.get("min_kelly_fraction", 0.01),
            max_kelly_fraction=risk_cfg.get("max_kelly_fraction", 0.20),
        )
        self.risk = RiskManager(limits, mode=self.mode)

        # Order executor
        self.executor = get_executor(self.mode, config)

        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._scan_count = 0
        self._trade_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        """Start the bot in a background thread."""
        if self._running:
            logger.warning("Bot is already running")
            return

        self._running = True
        db.set_bot_state("bot_running", "true")
        db.set_bot_state("bot_mode", self.mode)
        db.set_bot_state("bot_started_at", datetime.now(timezone.utc).isoformat())
        db.log_bot_event("INFO",
                         f"Bot started in {self.mode} mode — targeting 5-min BTC/ETH markets",
                         f"Scan interval: {self.scan_interval}s | Assets: {self.assets}")

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the bot."""
        self._running = False
        db.set_bot_state("bot_running", "false")
        db.log_bot_event("INFO", "Bot stopped")
        logger.info("Bot stopped")

    def _run_loop(self):
        """Main trading loop."""
        while self._running:
            try:
                if self.risk.is_killed:
                    db.log_bot_event("WARNING", "Bot paused — kill switch active")
                    time.sleep(self.scan_interval)
                    continue

                self._manage_positions()
                self._scan_and_trade()
                self._scan_count += 1
                db.set_bot_state("scan_count", str(self._scan_count))
                db.set_bot_state("last_scan_at", datetime.now(timezone.utc).isoformat())

            except Exception as e:
                logger.error(f"Error in bot loop: {e}")
                db.log_bot_event("ERROR", f"Bot loop error: {e}")

            time.sleep(self.scan_interval)

    def _manage_positions(self):
        """
        Active position management — the core arbitrage logic.

        For each open position:
        1. Check current price of the token we hold
        2. TAKE PROFIT if price moved in our favor (sell for profit)
        3. STOP LOSS if price moved against us (cut losses)
        4. RESOLVE if market has closed (binary $1 or $0 outcome)

        This is the key insight: don't wait for binary resolution.
        Trade the price movement as the underlying asset moves.
        """
        positions = db.get_bot_positions(mode=self.mode, status="open")
        if not positions:
            return

        # Exit thresholds (configurable)
        exits_cfg = self.config.get("exits", {})
        take_profit_pct = exits_cfg.get("take_profit_pct", 0.03)
        stop_loss_pct = exits_cfg.get("stop_loss_pct", 0.03)
        trailing_stop_pct = exits_cfg.get("trailing_stop_pct", 0.02)
        max_hold_cfg = exits_cfg.get("max_hold_minutes", {"5min": 4, "15min": 12})
        now_utc = datetime.now(timezone.utc)

        for pos in positions:
            try:
                token_id = pos.get("token_id")
                market_id = pos["market_id"]
                avg_price = pos["avg_price"]
                shares = pos["shares"]
                cost_basis = pos["cost_basis"]
                outcome_held = pos["outcome"].lower()

                if not token_id:
                    continue

                # ── Step 1: Get current live price ──
                # Use non-cached API calls (st.cache_data doesn't work in threads)
                current_price = api.get_midpoint_live(token_id)
                if current_price is None:
                    # Try from order book
                    book = api.get_order_book_live(token_id)
                    if book:
                        bids = book.get("bids", [])
                        if bids:
                            current_price = float(bids[0].get("price", 0))
                    if current_price is None:
                        continue

                # Update current price in DB
                self._update_position_price(pos["id"], current_price)

                # ── Step 2: TIME-BASED EXIT (HFT max hold time) ──
                opened_at_str = pos.get("opened_at")
                if opened_at_str:
                    try:
                        opened_dt = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                        if opened_dt.tzinfo is None:
                            opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                        hold_minutes = (now_utc - opened_dt).total_seconds() / 60
                        tf = classify_timeframe(pos.get("market_question", ""))
                        max_hold = max_hold_cfg.get(tf, 60) if tf else 60
                        if hold_minutes >= max_hold:
                            pnl = (current_price - avg_price) * shares
                            self._close_position(pos, sell_price=current_price,
                                                  reason="TIME_EXIT", pnl=pnl)
                            continue
                    except Exception:
                        pass

                # ── Step 4: Check if market is resolved ──
                if current_price >= 0.95:
                    # Our token went to ~$1 → WIN
                    pnl = (1.0 - avg_price) * shares
                    self._close_position(pos, sell_price=1.0, reason="RESOLVED_WIN",
                                          pnl=pnl)
                    continue
                elif current_price <= 0.05:
                    # Our token went to ~$0 → LOSS
                    pnl = -cost_basis
                    self._close_position(pos, sell_price=0.0, reason="RESOLVED_LOSS",
                                          pnl=pnl)
                    continue

                # ── Step 3: Calculate unrealized P&L ──
                price_change_pct = (current_price - avg_price) / avg_price
                unrealized_pnl = (current_price - avg_price) * shares

                # ── Step 4: TAKE PROFIT ──
                if price_change_pct >= take_profit_pct:
                    self._close_position(pos, sell_price=current_price,
                                          reason="TAKE_PROFIT", pnl=unrealized_pnl)
                    continue

                # ── Step 5: STOP LOSS ──
                if price_change_pct <= -stop_loss_pct:
                    self._close_position(pos, sell_price=current_price,
                                          reason="STOP_LOSS", pnl=unrealized_pnl)
                    continue

                # ── Step 6: TRAILING STOP (if in profit, protect gains) ──
                if price_change_pct > 0.02:  # Only activate if already in profit
                    # Check if price has pulled back from peak
                    high_price = pos.get("current_price") or current_price
                    if current_price > high_price:
                        # New high — just update
                        pass
                    elif high_price > avg_price:
                        pullback = (high_price - current_price) / high_price
                        if pullback >= trailing_stop_pct:
                            self._close_position(pos, sell_price=current_price,
                                                  reason="TRAILING_STOP", pnl=unrealized_pnl)
                            continue

                # ── Step 7: Also check for resolution via API (as fallback) ──
                result = api.check_market_resolved_live(market_id)
                if result and result.get("resolved"):
                    winning_outcome = (result.get("winning_outcome") or "").lower()
                    if winning_outcome and winning_outcome != "unknown":
                        we_won = (outcome_held == winning_outcome)
                        if we_won:
                            pnl = (1.0 - avg_price) * shares
                            self._close_position(pos, sell_price=1.0,
                                                  reason="RESOLVED_WIN", pnl=pnl)
                        else:
                            self._close_position(pos, sell_price=0.0,
                                                  reason="RESOLVED_LOSS", pnl=-cost_basis)

            except Exception as e:
                logger.debug(f"Error managing position {pos.get('id')}: {e}")
                continue

    def _update_position_price(self, position_id: int, current_price: float):
        """Update the current price of a position in the database."""
        conn = db.get_connection()
        conn.execute(
            "UPDATE bot_positions SET current_price = ?, unrealized_pnl = "
            "(? - avg_price) * shares WHERE id = ?",
            (current_price, current_price, position_id)
        )
        conn.commit()
        conn.close()

    def _close_position(self, pos: dict, sell_price: float, reason: str, pnl: float):
        """Close a position and log the result."""
        # Execute sell via executor
        result = self.executor.execute_sell(
            token_id=pos.get("token_id", ""),
            market_price=sell_price,
            shares=pos["shares"],
            market_id=pos["market_id"],
            market_question=pos.get("market_question", ""),
            outcome=pos["outcome"],
        )

        # Close position in DB
        db.close_bot_position(pos["id"], sell_price=sell_price)

        # Format log
        entry = pos["avg_price"]
        pnl_sign = "+" if pnl >= 0 else ""
        pnl_pct = ((sell_price - entry) / entry * 100) if entry > 0 else 0

        reason_emoji = {
            "TAKE_PROFIT": "💰",
            "STOP_LOSS": "🛑",
            "TRAILING_STOP": "📉",
            "TIME_EXIT": "⏱️",
            "RESOLVED_WIN": "✅",
            "RESOLVED_LOSS": "❌",
        }.get(reason, "📤")

        db.log_bot_event("TRADE",
                         f"📤 EXIT {reason_emoji} {reason}: {pnl_sign}${abs(pnl):.2f} ({pnl_pct:+.1f}%) | "
                         f"{pos['outcome']} @ {entry:.2%} → {sell_price:.2%}",
                         f"Why closed: {reason} | "
                         f"{pos.get('market_question', '')[:60]} | "
                         f"Shares: {pos['shares']:.1f}")

        self._trade_count += 1
        db.set_bot_state("trade_count", str(self._trade_count))

    def _scan_and_trade(self):
        """Single scan cycle: find markets, analyze, trade."""
        db.log_bot_event("INFO", f"Scanning for {'/'.join(self.assets)} Up or Down markets...")

        # Use the events endpoint which correctly returns short-duration markets
        all_markets = api.get_updown_crypto_markets(asset_keywords=self.assets)
        if not all_markets:
            db.log_bot_event("WARNING", "No Up or Down markets returned from API — check connection")
            return

        # Filter to configured timeframes, skip resolved markets,
        # and only keep markets that resolve soon (time-to-expiry filter).
        # Max lead times per timeframe — don't buy a 5-min market 2 hours early.
        # For short markets, only enter close to expiry.
        # For long markets (1h+), always allow entry — we scalp price movement,
        # not hold to resolution. The time-based exit handles the rest.
        max_lead_minutes = {
            "5min": 10,
            "15min": 30,
            "1h": 9999,
            "4h": 9999,
            "daily": 9999,
        }

        now_utc = datetime.now(timezone.utc)
        matched_markets = []
        for m in all_markets:
            question = m.get("question", "")
            tf = classify_timeframe(question)
            if tf and tf in self.timeframes:
                price = parse_market_price(m)
                if price and 0.03 < price < 0.97:
                    # Time-to-expiry gate: skip markets that resolve too far out
                    end_time = parse_market_end_time(question)
                    if end_time:
                        minutes_to_expiry = (end_time - now_utc).total_seconds() / 60
                        lead_limit = max_lead_minutes.get(tf, 30)
                        if minutes_to_expiry > lead_limit:
                            continue  # Too far out — skip
                        if minutes_to_expiry < 0.5:
                            continue  # Already expired / about to expire
                    m["_timeframe"] = tf
                    matched_markets.append(m)

        if not matched_markets:
            # Find the soonest market to give a useful status message
            soonest_mins = None
            for m in all_markets:
                q = m.get("question", "")
                end = parse_market_end_time(q)
                if end:
                    mins = (end - now_utc).total_seconds() / 60
                    if mins > 0 and (soonest_mins is None or mins < soonest_mins):
                        soonest_mins = mins
            if soonest_mins is not None:
                hours = int(soonest_mins // 60)
                mins = int(soonest_mins % 60)
                db.log_bot_event("INFO",
                                 f"No markets in window yet — nearest resolves in {hours}h {mins}m "
                                 f"(API: {len(all_markets)} Up or Down markets found)")
            else:
                db.log_bot_event("INFO",
                                 f"No active markets match timeframes {self.timeframes} "
                                 f"(API: {len(all_markets)} Up or Down markets found)")
            return

        tf_counts = {}
        for m in matched_markets:
            tf = m.get("_timeframe", "?")
            tf_counts[tf] = tf_counts.get(tf, 0) + 1

        db.log_bot_event("INFO",
                         f"Found {len(matched_markets)} tradeable markets: {tf_counts}")

        # Analyze each matched market
        opportunities = []
        skipped_pass = []
        for market in matched_markets:
            try:
                result = self._analyze_5min_market(market)
                if result:
                    if result["signal"] in ("STRONG BUY", "STRONG FADE", "BUY", "FADE", "CONDITIONAL"):
                        opportunities.append(result)
                    else:
                        skipped_pass.append(result)
            except Exception as e:
                logger.debug(f"Error analyzing market: {e}")
                continue

        # Sort by EV (best first)
        opportunities.sort(key=lambda x: abs(x.get("ev_gap", 0)), reverse=True)

        if opportunities:
            db.log_bot_event("INFO",
                             f"Found {len(opportunities)} trade opportunities",
                             str([f"{o['asset']} {o['signal']} EV:{o['ev_gap']:.3f}" for o in opportunities[:5]]))
        elif skipped_pass:
            db.log_bot_event("INFO",
                             f"Analyzed {len(skipped_pass)} markets — all PASS (no edge detected)",
                             str([f"{o['asset']} model={o['model_prob']:.2f} mkt={o['market_price']:.2f}"
                                  for o in skipped_pass[:3]]))

        # Execute top opportunities — with correlation limit.
        # Don't stack more than max_correlated same-asset, same-direction positions
        # since they're essentially the same bet across different time windows.
        max_correlated = self.config.get("risk", {}).get("max_correlated_positions", 3)
        open_positions = db.get_bot_positions(mode=self.mode, status="open")

        for opp in opportunities:
            if not self._running or self.risk.is_killed:
                break

            # Count how many open positions share the same asset + direction
            opp_asset = opp["asset"]
            opp_side = opp["side"]
            correlated = sum(
                1 for p in open_positions
                if get_asset_from_question(p.get("market_question", "")) == opp_asset
                and p.get("outcome", "").lower() in (
                    ("up", "yes") if opp_side == "YES" else ("down", "no")
                )
            )
            if correlated >= max_correlated:
                db.log_bot_event("INFO",
                                 f"Skipped: already {correlated} correlated {opp_asset} "
                                 f"{'UP' if opp_side == 'YES' else 'DOWN'} positions "
                                 f"(max {max_correlated})",
                                 f"{opp['market_question'][:60]}")
                continue

            self._try_execute(opp)
            # Refresh open positions after a successful execution
            open_positions = db.get_bot_positions(mode=self.mode, status="open")

    def _analyze_5min_market(self, market: dict) -> Optional[dict]:
        """
        Analyze a 5-minute up/down market.
        The key insight: these markets ask "Will BTC/ETH be UP or DOWN in 5 minutes?"
        We use ultra-short-term momentum, volatility regime, and orderbook imbalance.
        """
        market_id = market.get("conditionId") or market.get("id", "")
        question = market.get("question", "Unknown")
        asset = get_asset_from_question(question)
        tokens = api.get_token_ids_for_market(market)

        if not tokens:
            return None

        # Find the UP token (these markets are binary: Up vs Down)
        up_outcome = None
        up_token = None
        down_outcome = None
        down_token = None

        for outcome_name, token_id in tokens.items():
            lower = outcome_name.lower()
            if lower in ("up", "yes"):
                up_outcome = outcome_name
                up_token = token_id
            elif lower in ("down", "no"):
                down_outcome = outcome_name
                down_token = token_id

        if not up_token:
            # Take first as "up"
            up_outcome = list(tokens.keys())[0]
            up_token = list(tokens.values())[0]

        # Get current market price for UP outcome
        market_price = parse_market_price(market)
        if market_price is None:
            midpoint = api.get_midpoint_live(up_token)
            if midpoint:
                market_price = midpoint
            else:
                return None

        if market_price <= 0.03 or market_price >= 0.97:
            return None  # Already resolved or too one-sided

        # Estimate model probability for UP
        model_prob = self._estimate_5min_probability(up_token, market_price, asset)
        if model_prob is None:
            return None

        # Run full scorecard
        scorecard = run_scorecard(
            model_prob=model_prob,
            market_price=market_price,
            bankroll=self.risk.limits.max_total_exposure_usd,
            fee_rate=0.02,
            volatility=0.10,  # Higher vol for 5-min markets
        )

        result = {
            "market_id": market_id,
            "market_question": question,
            "asset": asset,
            "outcome": up_outcome if scorecard["side"] == "YES" else (down_outcome or f"NO ({up_outcome})"),
            "token_id": up_token if scorecard["side"] == "YES" else (down_token or up_token),
            "market_price": market_price,
            "model_prob": model_prob,
            "signal": scorecard["signal"],
            "score": scorecard["score"],
            "side": scorecard["side"],
            "ev_gap": scorecard["ev"]["net_ev"],
            "kelly_fraction": scorecard["kelly"]["kelly_fraction"],
            "position_size": scorecard["position_size_usd"],
        }

        # Log scan
        db.record_scan(
            market_id=market_id,
            market_question=question,
            signal=scorecard["signal"],
            model_prob=model_prob,
            market_prob=market_price,
            ev_gap=scorecard["ev"]["net_ev"],
            kelly_size=scorecard["position_size_usd"],
        )

        return result

    def _estimate_5min_probability(self, token_id: str, current_price: float,
                                    asset: str) -> Optional[float]:
        """
        Estimate probability of UP using the ACTUAL underlying spot price
        from Binance, NOT Polymarket's own (near-useless) market history.

        These markets ask: "Will BTC/ETH be up or down in 5 minutes?"
        So we look at BTC/ETH spot price action to predict direction.

        Signals:
        1. Short-term momentum (1m candles over last 15 mins)
        2. Medium-term momentum (5m candles over last 1 hour)
        3. RSI (overbought/oversold)
        4. Volume surge (unusual volume = trend continuation)
        5. Polymarket order book imbalance (crowd wisdom)
        """
        try:
            # Map asset to Binance symbol
            symbol = "BTCUSDT" if asset == "BTC" else "ETHUSDT"

            # ── Signal 1: Short-term momentum (last 15 x 1m candles) ──
            candles_1m = api.get_spot_candles(symbol, interval="1m", limit=15)
            momentum_1m = 0.0
            rsi = 50.0
            vol_surge = 0.0

            if candles_1m and len(candles_1m) >= 5:
                closes = [c["close"] for c in candles_1m]
                volumes = [c["volume"] for c in candles_1m]

                # Momentum: % change over last 5 candles
                momentum_1m = (closes[-1] - closes[-5]) / closes[-5]

                # RSI (14-period, but we have 15 candles so use what we have)
                gains, losses = [], []
                for i in range(1, len(closes)):
                    delta = closes[i] - closes[i-1]
                    if delta > 0:
                        gains.append(delta)
                        losses.append(0)
                    else:
                        gains.append(0)
                        losses.append(abs(delta))

                if gains:
                    avg_gain = sum(gains) / len(gains)
                    avg_loss = sum(losses) / len(losses)
                    if avg_loss > 0:
                        rs = avg_gain / avg_loss
                        rsi = 100 - (100 / (1 + rs))
                    else:
                        rsi = 100

                # Volume surge: current vs average
                if len(volumes) >= 3:
                    avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
                    if avg_vol > 0:
                        vol_surge = (volumes[-1] / avg_vol) - 1  # >0 = above avg

            # ── Signal 2: Medium-term momentum (last 12 x 5m candles = 1hr) ──
            candles_5m = api.get_spot_candles(symbol, interval="5m", limit=12)
            momentum_5m = 0.0

            if candles_5m and len(candles_5m) >= 3:
                closes_5m = [c["close"] for c in candles_5m]
                momentum_5m = (closes_5m[-1] - closes_5m[-3]) / closes_5m[-3]

            # ── Signal 3: Polymarket order book imbalance ──
            book_signal = 0.0
            try:
                book = api.get_order_book_live(token_id)
                if book:
                    bids = book.get("bids", [])
                    asks = book.get("asks", [])
                    bid_vol = sum(float(b.get("size", 0)) for b in bids[:10])
                    ask_vol = sum(float(a.get("size", 0)) for a in asks[:10])
                    total = bid_vol + ask_vol
                    if total > 0:
                        book_signal = (bid_vol - ask_vol) / total
            except Exception:
                pass

            # ── Combine signals into directional probability ──
            # Convert momentum to signal strength (-1 to +1 range)
            # For crypto, even 0.05% in 5 minutes is a signal
            mom_1m_signal = max(-1, min(1, momentum_1m / 0.001))  # ±0.1% = full signal
            mom_5m_signal = max(-1, min(1, momentum_5m / 0.003))  # ±0.3% = full signal

            # RSI signal: >60 = bullish momentum, <40 = bearish
            rsi_signal = (rsi - 50) / 50  # -1 to +1

            # Volume surge amplifies momentum direction
            vol_amplifier = 1.0 + min(vol_surge, 1.0) * 0.3  # Up to 30% boost

            # Weighted combination
            raw_signal = (
                mom_1m_signal * 0.35 +    # Short-term momentum (strongest)
                mom_5m_signal * 0.25 +    # Medium-term trend
                rsi_signal * 0.15 +       # RSI confirmation
                book_signal * 0.25        # Polymarket crowd wisdom
            ) * vol_amplifier

            # Convert signal to probability
            # Aggressive scaling: even small crypto moves (0.1%) should create
            # meaningful probability divergence since these are 5-min binary bets.
            # Scale: 0 signal = 50%, ±0.3 signal = ~60%/40%, ±0.7 signal = ~70%/30%
            model_prob = 0.50 + raw_signal * 0.30
            model_prob = max(0.10, min(0.90, model_prob))

            return model_prob

        except Exception as e:
            logger.debug(f"Error estimating probability: {e}")
            return None

    def _try_execute(self, opportunity: dict):
        """Try to execute a trade opportunity after risk checks.

        Position size is fixed (max_position_size_usd from config).
        Kelly fraction drives direction confidence and risk gate, not sizing —
        this ensures paper trades fire at the configured $250 amount.
        """
        # Fixed position size — don't scale by Kelly (would make trades too small)
        amount_usd = self.risk.limits.max_position_size_usd
        kelly = self.risk.cap_kelly(opportunity["kelly_fraction"])

        # Risk check
        allowed, reason = self.risk.check_trade(
            amount_usd=amount_usd,
            ev_gap=opportunity["ev_gap"],
            kelly_fraction=kelly,
            market_id=opportunity["market_id"],
        )

        if not allowed:
            db.log_bot_event("INFO", f"Trade blocked: {reason}",
                             f"{opportunity['asset']} {opportunity['signal']} | "
                             f"EV:{opportunity['ev_gap']:.3f} | "
                             f"{opportunity['market_question'][:60]}")
            return

        # Execute
        side = opportunity["side"]
        token_id = opportunity["token_id"]
        price = opportunity["market_price"]

        if side == "YES":
            # Buy UP token
            result = self.executor.execute_buy(
                token_id=token_id,
                market_price=price,
                amount_usd=amount_usd,
                market_id=opportunity["market_id"],
                market_question=opportunity["market_question"],
                outcome=opportunity["outcome"],
            )
        elif side == "NO":
            # Buy DOWN token (price is 1 - up_price)
            result = self.executor.execute_buy(
                token_id=token_id,
                market_price=1 - price,
                amount_usd=amount_usd,
                market_id=opportunity["market_id"],
                market_question=opportunity["market_question"],
                outcome=opportunity["outcome"],
            )
        else:
            return

        if result.success:
            self._trade_count += 1
            db.set_bot_state("trade_count", str(self._trade_count))
            direction = "UP" if side == "YES" else "DOWN"
            db.log_bot_event(
                "TRADE",
                f"📥 ENTER {opportunity['asset']} {direction} — "
                f"{result.shares:.2f} shares @ {result.fill_price:.4f} (${amount_usd:.2f}) | "
                f"Signal: {opportunity['signal']} ({opportunity['score']}/6)",
                f"Why: model={opportunity['model_prob']:.2f} vs market={opportunity['market_price']:.2f} "
                f"(edge={opportunity['model_prob'] - opportunity['market_price']:+.2f}) | "
                f"EV:{opportunity['ev_gap']:.3f} | Kelly:{kelly:.3f} | "
                f"TP@+4% SL@-2% | {opportunity['market_question'][:60]}",
            )
        else:
            db.log_bot_event("ERROR", f"Trade failed: {result.error}",
                             f"{opportunity['asset']} | {opportunity['market_question'][:60]}")


def load_config(config_path: str = "config.yaml") -> dict:
    """Load bot configuration from YAML file."""
    import yaml
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), config_path)
    if not os.path.exists(path):
        # Return defaults for paper mode — all BTC/ETH up/down timeframes
        return {
            "bot": {
                "mode": "paper",
                "scan_interval_seconds": 150,
                "assets": ["Bitcoin", "Ethereum"],
                "timeframes": ["5min", "15min", "1h", "4h", "daily"],
            },
            "risk": {
                "max_position_size_usd": 250.0,
                "max_total_exposure_usd": 1500.0,
                "max_daily_loss_usd": 300.0,
                "max_open_positions": 5,
                "min_ev_threshold": 0.005,
                "min_kelly_fraction": 0.001,
                "max_kelly_fraction": 0.20,
                "max_correlated_positions": 2,
            },
            "exits": {
                "take_profit_pct": 0.04,
                "stop_loss_pct": 0.02,
                "trailing_stop_pct": 0.02,
            },
        }

    with open(path, "r") as f:
        return yaml.safe_load(f)
