"""Page 1: Overview — Top markets by volume, key metrics, trending."""

import streamlit as st
import pandas as pd
from src import api
from src.utils import format_usd, format_pct, parse_market_price, parse_volume

st.set_page_config(page_title="Overview", page_icon="📊", layout="wide")
st.title("📊 Market Overview")

# Auto-refresh
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = True

col_r1, col_r2 = st.columns([6, 1])
with col_r2:
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()

# Fetch data
with st.spinner("Loading markets..."):
    events = api.get_active_events(limit=50)

if not events:
    st.warning("No active events found. Polymarket API may be down.")
    st.stop()

# Extract metrics
all_markets = []
for event in events:
    markets = event.get("markets", [])
    for m in markets:
        m["event_title"] = event.get("title", "")
        all_markets.append(m)

# Key metrics
total_markets = len(all_markets)
total_volume = sum(parse_volume(m) for m in all_markets)

# Find top movers (most extreme prices near 50%)
competitive = [m for m in all_markets if 0.3 <= (parse_market_price(m) or 0) <= 0.7]
competitive.sort(key=lambda m: parse_volume(m), reverse=True)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Active Markets", f"{total_markets:,}")
col2.metric("Total Volume", format_usd(total_volume))
col3.metric("Active Events", f"{len(events):,}")
col4.metric("Competitive Markets", f"{len(competitive):,}")

st.divider()

# Trending markets table
st.subheader("🔥 Top Markets by Volume")

rows = []
for m in all_markets[:30]:
    price = parse_market_price(m)
    vol = parse_volume(m)
    rows.append({
        "Market": m.get("question", "")[:80],
        "Event": m.get("event_title", "")[:50],
        "Price": f"{price:.0%}" if price else "N/A",
        "Volume": format_usd(vol),
        "Volume_raw": vol,
    })

if rows:
    df = pd.DataFrame(rows).sort_values("Volume_raw", ascending=False).drop(columns=["Volume_raw"])
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No market data available.")

# Most competitive (close to 50/50)
st.subheader("⚔️ Most Competitive Markets (Close to 50/50)")
comp_rows = []
for m in competitive[:15]:
    price = parse_market_price(m) or 0
    comp_rows.append({
        "Market": m.get("question", "")[:80],
        "Yes Price": f"{price:.0%}",
        "No Price": f"{1 - price:.0%}",
        "Volume": format_usd(parse_volume(m)),
    })

if comp_rows:
    st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)

# Auto-refresh
if st.session_state.auto_refresh:
    import time
    time.sleep(30)
    st.rerun()
