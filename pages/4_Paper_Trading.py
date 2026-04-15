"""Page 4: Paper Trading — Virtual wallet, simulated trades, P&L tracking."""

import streamlit as st
import pandas as pd
from src import api, database as db
from src.utils import format_usd, parse_market_price

st.set_page_config(page_title="Paper Trading", page_icon="💰", layout="wide")
st.title("💰 Paper Trading")

# Wallet balance
balance = db.get_paper_balance()
positions = db.get_paper_positions("open")
total_value = balance + sum(p["cost_basis"] for p in positions)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Cash Balance", format_usd(balance))
col2.metric("Open Positions", str(len(positions)))
col3.metric("Portfolio Value", format_usd(total_value))
col4.metric("P&L", format_usd(total_value - 1000), delta=f"{((total_value / 1000) - 1) * 100:.1f}%")

st.divider()

# Place trade
st.subheader("Place Trade")

markets = api.get_all_active_markets(max_pages=1)
if markets:
    market_options = {m.get("question", "Unknown")[:80]: m for m in markets if m.get("question")}
    selected = st.selectbox("Market", list(market_options.keys()), key="paper_market")
    market = market_options[selected]

    tokens = api.get_token_ids_for_market(market)
    if tokens:
        col_t1, col_t2, col_t3 = st.columns(3)
        with col_t1:
            outcome = st.radio("Outcome", list(tokens.keys()), horizontal=True)
        with col_t2:
            side = st.radio("Side", ["Buy", "Sell"], horizontal=True)
        with col_t3:
            amount = st.number_input("Amount ($)", min_value=1.0, max_value=float(balance),
                                     value=min(50.0, balance), step=10.0)

        price = parse_market_price(market)
        if price:
            st.caption(f"Current price: {price:.0%} | Shares: ~{amount / price:.1f}")

            if st.button("Execute Trade", type="primary"):
                token_id = tokens[outcome]
                shares = amount / price

                if side == "Buy":
                    if amount > balance:
                        st.error("Insufficient balance!")
                    else:
                        db.record_paper_trade(
                            market.get("conditionId", ""), selected, outcome,
                            token_id, "buy", price, amount, shares
                        )
                        db.upsert_paper_position(
                            market.get("conditionId", ""), selected, outcome,
                            token_id, shares, price, amount
                        )
                        db.update_paper_balance(balance - amount)
                        st.success(f"Bought {shares:.2f} shares of {outcome} at {price:.0%}")
                        st.rerun()
                else:
                    # Find existing position to sell
                    existing = [p for p in positions
                                if p["market_id"] == market.get("conditionId", "")
                                and p["outcome"] == outcome]
                    if not existing:
                        st.error("No position to sell!")
                    else:
                        pos = existing[0]
                        sell_shares = min(shares, pos["shares"])
                        sell_amount = sell_shares * price
                        db.record_paper_trade(
                            market.get("conditionId", ""), selected, outcome,
                            token_id, "sell", price, sell_amount, sell_shares
                        )
                        db.upsert_paper_position(
                            market.get("conditionId", ""), selected, outcome,
                            token_id, -sell_shares, price, -sell_amount
                        )
                        db.update_paper_balance(balance + sell_amount)
                        st.success(f"Sold {sell_shares:.2f} shares of {outcome} at {price:.0%}")
                        st.rerun()

st.divider()

# Open positions
st.subheader("Open Positions")
if positions:
    pos_rows = []
    for p in positions:
        current_price = parse_market_price({"outcomePrices": f'[{p["avg_price"]}]'}) or p["avg_price"]
        # Try to get live price
        if p.get("token_id"):
            live = api.get_midpoint(p["token_id"])
            if live:
                current_price = live

        pnl = (current_price - p["avg_price"]) * p["shares"]
        pos_rows.append({
            "Market": p["market_question"][:60] if p.get("market_question") else p["market_id"][:20],
            "Outcome": p["outcome"],
            "Shares": f"{p['shares']:.2f}",
            "Avg Price": f"{p['avg_price']:.0%}",
            "Current": f"{current_price:.0%}",
            "Cost": format_usd(p["cost_basis"]),
            "P&L": format_usd(pnl),
        })
    st.dataframe(pd.DataFrame(pos_rows), use_container_width=True, hide_index=True)
else:
    st.info("No open positions. Place a trade above!")

# Trade history
st.subheader("Trade History")
trades = db.get_paper_trades(limit=30)
if trades:
    trade_rows = []
    for t in trades:
        trade_rows.append({
            "Time": t["timestamp"][:16],
            "Market": (t.get("market_question") or "")[:50],
            "Side": t["side"].upper(),
            "Outcome": t["outcome"],
            "Price": f"{t['price']:.0%}",
            "Amount": format_usd(t["amount"]),
            "Shares": f"{t['shares']:.2f}",
        })
    st.dataframe(pd.DataFrame(trade_rows), use_container_width=True, hide_index=True)

# Reset wallet
st.divider()
with st.expander("Reset Wallet"):
    new_balance = st.number_input("Starting balance", value=1000.0, step=100.0)
    if st.button("Reset", type="secondary"):
        db.reset_paper_wallet(new_balance)
        st.success(f"Wallet reset to {format_usd(new_balance)}")
        st.rerun()
