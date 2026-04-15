"""Page 3: Market Detail — Price chart, order book, recent trades."""

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from src import api
from src.utils import format_usd, parse_market_price, parse_volume

st.set_page_config(page_title="Market Detail", page_icon="📈", layout="wide")
st.title("📈 Market Detail")

# Fetch all markets for selection
markets = api.get_all_active_markets(max_pages=2)
if not markets:
    st.warning("No active markets found.")
    st.stop()

# Market selector
market_options = {m.get("question", "Unknown")[:80]: m for m in markets if m.get("question")}
selected_name = st.selectbox("Select Market", list(market_options.keys()))
market = market_options[selected_name]

# Get token IDs
tokens = api.get_token_ids_for_market(market)
if not tokens:
    st.warning("No token data available for this market.")
    st.stop()

# Select outcome
outcome = st.radio("Outcome", list(tokens.keys()), horizontal=True)
token_id = tokens[outcome]

# Key stats
price = parse_market_price(market)
vol = parse_volume(market)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Current Price", f"{price:.0%}" if price else "N/A")
col2.metric("Volume", format_usd(vol))
col3.metric("Spread", "—")
col4.metric("Token ID", token_id[:12] + "...")

st.divider()

# Price history chart
st.subheader("Price History")
timeframe = st.radio("Timeframe", ["1h", "6h", "1d", "1w", "max"], horizontal=True, index=2)

with st.spinner("Loading price history..."):
    history = api.get_price_history(token_id, interval=timeframe)

if history:
    times = []
    prices = []
    for point in history:
        t = point.get("t") or point.get("timestamp")
        p = point.get("p") or point.get("price")
        if t and p:
            try:
                times.append(t)
                prices.append(float(p))
            except (ValueError, TypeError):
                continue

    if prices:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=list(range(len(prices))), y=prices,
            mode='lines', name=outcome,
            line=dict(color='#00d4aa', width=2),
            fill='tozeroy', fillcolor='rgba(0,212,170,0.1)',
        ))
        fig.update_layout(
            template="plotly_dark",
            height=400,
            yaxis_title="Price",
            yaxis_tickformat=".0%",
            xaxis_title="Time",
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No price data available for this timeframe.")
else:
    st.info("No price history available.")

# Order book
st.subheader("Order Book")
col_book1, col_book2 = st.columns(2)

with st.spinner("Loading order book..."):
    book = api.get_order_book(token_id)

if book:
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    with col_book1:
        st.markdown("**Bids (Buy Orders)**")
        if bids:
            bid_df = pd.DataFrame(bids[:15])
            st.dataframe(bid_df, use_container_width=True, hide_index=True)
        else:
            st.info("No bids")

    with col_book2:
        st.markdown("**Asks (Sell Orders)**")
        if asks:
            ask_df = pd.DataFrame(asks[:15])
            st.dataframe(ask_df, use_container_width=True, hide_index=True)
        else:
            st.info("No asks")

    # Depth chart
    if bids and asks:
        bid_prices = [float(b.get("price", 0)) for b in bids]
        bid_sizes = [float(b.get("size", 0)) for b in bids]
        ask_prices = [float(a.get("price", 0)) for a in asks]
        ask_sizes = [float(a.get("size", 0)) for a in asks]

        fig_depth = go.Figure()
        fig_depth.add_trace(go.Bar(x=bid_prices, y=bid_sizes, name="Bids", marker_color="#00d4aa"))
        fig_depth.add_trace(go.Bar(x=ask_prices, y=ask_sizes, name="Asks", marker_color="#ff6666"))
        fig_depth.update_layout(
            template="plotly_dark", height=300,
            barmode="overlay", xaxis_title="Price", yaxis_title="Size",
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig_depth, use_container_width=True)
else:
    st.info("Order book not available.")

# Recent trades
st.subheader("Recent Trades")
condition_id = market.get("conditionId", token_id)
trades = api.get_recent_trades(condition_id)

if trades:
    trade_rows = []
    for t in trades[:20]:
        trade_rows.append({
            "Price": t.get("price", ""),
            "Size": t.get("size", ""),
            "Side": t.get("side", ""),
            "Time": t.get("timestamp", "")[:19],
        })
    if trade_rows:
        st.dataframe(pd.DataFrame(trade_rows), use_container_width=True, hide_index=True)
else:
    st.info("No recent trades available.")
