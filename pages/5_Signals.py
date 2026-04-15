"""Page 5: Signals — Price alerts and notifications."""

import streamlit as st
import pandas as pd
from src import api, database as db
from src.utils import parse_market_price

st.set_page_config(page_title="Signals", page_icon="🔔", layout="wide")
st.title("🔔 Signal Alerts")

# Create new signal
st.subheader("Create Alert")

markets = api.get_all_active_markets(max_pages=1)
if markets:
    market_options = {m.get("question", "Unknown")[:80]: m for m in markets if m.get("question")}
    selected = st.selectbox("Market", list(market_options.keys()), key="signal_market")
    market = market_options[selected]
    tokens = api.get_token_ids_for_market(market)

    if tokens:
        col1, col2, col3 = st.columns(3)
        with col1:
            outcome = st.radio("Outcome", list(tokens.keys()), horizontal=True, key="sig_outcome")
        with col2:
            condition = st.selectbox("Condition", ["above", "below", "crosses"])
        with col3:
            threshold = st.slider("Threshold", 0.01, 0.99, 0.50, 0.01, format="%.0f%%")

        price = parse_market_price(market)
        if price:
            st.caption(f"Current price: {price:.0%}")

        if st.button("Create Alert", type="primary"):
            db.add_signal(
                market.get("conditionId", ""),
                selected,
                tokens[outcome],
                outcome,
                condition,
                threshold,
            )
            st.success(f"Alert created: {outcome} {condition} {threshold:.0%}")
            st.rerun()

st.divider()

# Active signals
st.subheader("Active Alerts")
signals = db.get_active_signals()

if signals:
    for sig in signals:
        # Check current price
        current_price = None
        if sig.get("token_id"):
            current_price = api.get_midpoint(sig["token_id"])

        triggered = False
        if current_price is not None:
            if sig["condition"] == "above" and current_price > sig["threshold"]:
                triggered = True
            elif sig["condition"] == "below" and current_price < sig["threshold"]:
                triggered = True

        status_icon = "🔴" if triggered else "⚪"
        price_str = f"{current_price:.0%}" if current_price else "N/A"

        col1, col2 = st.columns([5, 1])
        with col1:
            st.markdown(
                f"{status_icon} **{sig['market_question'][:60]}** — "
                f"{sig.get('outcome', '')} {sig['condition']} {sig['threshold']:.0%} "
                f"(current: {price_str})"
            )
        with col2:
            if triggered:
                st.warning("TRIGGERED!")
                db.trigger_signal(sig["id"])

        if triggered:
            st.toast(f"Alert triggered: {sig['market_question'][:40]}")
else:
    st.info("No active alerts. Create one above!")
