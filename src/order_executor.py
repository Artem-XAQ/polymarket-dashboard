"""
Order executor — handles both paper and live order execution on Polymarket CLOB.
Paper mode simulates fills at market price. Live mode uses py-clob-client.
"""
from __future__ import annotations

import time
import logging
from typing import Optional
from dataclasses import dataclass
from src import database as db

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    fill_price: float = 0.0
    shares: float = 0.0
    amount_usd: float = 0.0
    error: Optional[str] = None


class PaperExecutor:
    """Simulates order execution at market price + slippage."""

    def __init__(self, slippage_bps: int = 50):
        """slippage_bps: simulated slippage in basis points (50 = 0.5%)"""
        self.slippage = slippage_bps / 10000

    def execute_buy(self, token_id: str, market_price: float,
                    amount_usd: float, market_id: str = "",
                    market_question: str = "", outcome: str = "") -> OrderResult:
        """Simulate a buy order."""
        if market_price <= 0 or market_price >= 1:
            return OrderResult(success=False, error=f"Invalid market price: {market_price}")

        # Apply slippage (buy at slightly worse price)
        fill_price = min(market_price * (1 + self.slippage), 0.99)
        shares = amount_usd / fill_price

        # Record in database
        trade_id = db.record_bot_trade(
            market_id=market_id,
            market_question=market_question,
            outcome=outcome,
            token_id=token_id,
            side="buy",
            price=fill_price,
            amount_usd=amount_usd,
            shares=shares,
            mode="paper",
            status="filled",
        )

        db.upsert_bot_position(
            market_id=market_id,
            market_question=market_question,
            outcome=outcome,
            token_id=token_id,
            shares=shares,
            avg_price=fill_price,
            cost_basis=amount_usd,
            mode="paper",
        )

        logger.info(f"PAPER BUY: {shares:.2f} shares of {outcome} @ {fill_price:.4f} (${amount_usd:.2f})")

        return OrderResult(
            success=True,
            order_id=f"paper_{trade_id}",
            fill_price=fill_price,
            shares=shares,
            amount_usd=amount_usd,
        )

    def execute_sell(self, token_id: str, market_price: float,
                     shares: float, market_id: str = "",
                     market_question: str = "", outcome: str = "") -> OrderResult:
        """Simulate a sell order."""
        if market_price <= 0 or market_price >= 1:
            return OrderResult(success=False, error=f"Invalid market price: {market_price}")

        # Apply slippage (sell at slightly worse price)
        fill_price = max(market_price * (1 - self.slippage), 0.01)
        amount_usd = shares * fill_price

        trade_id = db.record_bot_trade(
            market_id=market_id,
            market_question=market_question,
            outcome=outcome,
            token_id=token_id,
            side="sell",
            price=fill_price,
            amount_usd=amount_usd,
            shares=shares,
            mode="paper",
            status="filled",
        )

        db.upsert_bot_position(
            market_id=market_id,
            market_question=market_question,
            outcome=outcome,
            token_id=token_id,
            shares=-shares,
            avg_price=fill_price,
            cost_basis=-amount_usd,
            mode="paper",
        )

        logger.info(f"PAPER SELL: {shares:.2f} shares of {outcome} @ {fill_price:.4f} (${amount_usd:.2f})")

        return OrderResult(
            success=True,
            order_id=f"paper_{trade_id}",
            fill_price=fill_price,
            shares=shares,
            amount_usd=amount_usd,
        )


class LiveExecutor:
    """
    Real order execution via Polymarket CLOB API.
    Requires py-clob-client with valid credentials.
    """

    def __init__(self, private_key: str, api_key: str, api_secret: str,
                 api_passphrase: str, chain_id: int = 137):
        self.credentials = {
            "private_key": private_key,
            "api_key": api_key,
            "api_secret": api_secret,
            "api_passphrase": api_passphrase,
        }
        self.chain_id = chain_id
        self._client = None

    def _get_client(self):
        """Lazy-init the CLOB client. Requires py-clob-client (pip install py-clob-client)."""
        if self._client is None:
            try:
                from py_clob_client.client import ClobClient  # type: ignore
                from py_clob_client.clob_types import ApiCreds  # type: ignore

                host = "https://clob.polymarket.com"
                creds = ApiCreds(
                    api_key=self.credentials["api_key"],
                    api_secret=self.credentials["api_secret"],
                    api_passphrase=self.credentials["api_passphrase"],
                )
                self._client = ClobClient(
                    host,
                    key=self.credentials["private_key"],
                    chain_id=self.chain_id,
                    creds=creds,
                )
            except ImportError:
                raise RuntimeError("py-clob-client not installed. Run: pip install py-clob-client")
            except Exception as e:
                raise RuntimeError(f"Failed to initialize CLOB client: {e}")
        return self._client

    def execute_buy(self, token_id: str, market_price: float,
                    amount_usd: float, market_id: str = "",
                    market_question: str = "", outcome: str = "") -> OrderResult:
        """Execute a real buy order on Polymarket."""
        try:
            client = self._get_client()
            from py_clob_client.order_builder.constants import BUY

            # Create a market buy order
            order_args = {
                "token_id": token_id,
                "price": round(market_price, 2),
                "size": round(amount_usd / market_price, 2),
                "side": BUY,
            }

            signed_order = client.create_order(order_args)
            resp = client.post_order(signed_order)

            if resp and resp.get("success"):
                order_id = resp.get("orderID", "unknown")
                shares = amount_usd / market_price

                db.record_bot_trade(
                    market_id=market_id,
                    market_question=market_question,
                    outcome=outcome,
                    token_id=token_id,
                    side="buy",
                    price=market_price,
                    amount_usd=amount_usd,
                    shares=shares,
                    mode="live",
                    order_id=order_id,
                    status="pending",
                )

                db.upsert_bot_position(
                    market_id=market_id,
                    market_question=market_question,
                    outcome=outcome,
                    token_id=token_id,
                    shares=shares,
                    avg_price=market_price,
                    cost_basis=amount_usd,
                    mode="live",
                )

                logger.info(f"LIVE BUY: {shares:.2f} shares of {outcome} @ {market_price:.4f} order={order_id}")

                return OrderResult(
                    success=True,
                    order_id=order_id,
                    fill_price=market_price,
                    shares=shares,
                    amount_usd=amount_usd,
                )
            else:
                error = resp.get("errorMsg", "Unknown error") if resp else "No response"
                db.record_bot_trade(
                    market_id=market_id, market_question=market_question,
                    outcome=outcome, token_id=token_id, side="buy",
                    price=market_price, amount_usd=amount_usd, shares=0,
                    mode="live", status="failed",
                )
                return OrderResult(success=False, error=error)

        except Exception as e:
            logger.error(f"LIVE BUY FAILED: {e}")
            db.record_bot_trade(
                market_id=market_id, market_question=market_question,
                outcome=outcome, token_id=token_id, side="buy",
                price=market_price, amount_usd=amount_usd, shares=0,
                mode="live", status="failed",
            )
            db.log_bot_event("ERROR", f"Live buy failed: {e}")
            return OrderResult(success=False, error=str(e))

    def execute_sell(self, token_id: str, market_price: float,
                     shares: float, market_id: str = "",
                     market_question: str = "", outcome: str = "") -> OrderResult:
        """Execute a real sell order on Polymarket."""
        try:
            client = self._get_client()
            from py_clob_client.order_builder.constants import SELL

            order_args = {
                "token_id": token_id,
                "price": round(market_price, 2),
                "size": round(shares, 2),
                "side": SELL,
            }

            signed_order = client.create_order(order_args)
            resp = client.post_order(signed_order)

            if resp and resp.get("success"):
                order_id = resp.get("orderID", "unknown")
                amount_usd = shares * market_price

                db.record_bot_trade(
                    market_id=market_id, market_question=market_question,
                    outcome=outcome, token_id=token_id, side="sell",
                    price=market_price, amount_usd=amount_usd, shares=shares,
                    mode="live", order_id=order_id, status="pending",
                )

                db.upsert_bot_position(
                    market_id=market_id, market_question=market_question,
                    outcome=outcome, token_id=token_id,
                    shares=-shares, avg_price=market_price,
                    cost_basis=-amount_usd, mode="live",
                )

                logger.info(f"LIVE SELL: {shares:.2f} shares of {outcome} @ {market_price:.4f} order={order_id}")

                return OrderResult(
                    success=True, order_id=order_id,
                    fill_price=market_price, shares=shares, amount_usd=amount_usd,
                )
            else:
                error = resp.get("errorMsg", "Unknown error") if resp else "No response"
                return OrderResult(success=False, error=error)

        except Exception as e:
            logger.error(f"LIVE SELL FAILED: {e}")
            db.log_bot_event("ERROR", f"Live sell failed: {e}")
            return OrderResult(success=False, error=str(e))


def get_executor(mode: str, config: dict = None):
    """Factory to get the right executor based on mode."""
    if mode == "paper":
        return PaperExecutor()
    elif mode == "live":
        if not config:
            raise ValueError("Live mode requires config with API credentials")
        pm = config.get("polymarket", {})
        return LiveExecutor(
            private_key=pm["private_key"],
            api_key=pm["api_key"],
            api_secret=pm["api_secret"],
            api_passphrase=pm["api_passphrase"],
            chain_id=pm.get("chain_id", 137),
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")
