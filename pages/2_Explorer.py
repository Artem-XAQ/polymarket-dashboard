"""Page 2: Market Explorer — Search, browse, filter all active markets."""

import streamlit as st
import pandas as pd
from src import api
from src.utils import format_usd, parse_market_price, parse_volume

st.set_page_config(page_title="Explorer", page_icon="🔍", layout="wide")
st.title("🔍 Market Explorer")

# Search
col1, col2, col3 = st.columns([3, 1, 1])
with col1:
    query = st.text_input("Search markets", placeholder="e.g., Bitcoin, Trump, Fed rate...")
with col2:
    sort_by = st.selectbox("Sort by", ["Volume", "Price (High)", "Price (Low)"])
with col3:
    per_page = st.selectbox("Per page", [25, 50, 100], index=0)

# Pagination
if "explorer_page" not in st.session_state:
    st.session_state.explorer_page = 0

# Fetch
with st.spinner("Searching..."):
    if query:
        markets = api.search_markets(query, limit=200)
    else:
        markets = api.get_all_active_markets(max_pages=3)

if not markets:
    st.info("No markets found. Try a different search term.")
    st.stop()

# Build table data
rows = []
for m in markets:
    price = parse_market_price(m)
    vol = parse_volume(m)
    rows.append({
        "question": m.get("question", "")[:100],
        "price": price,
        "volume": vol,
        "condition_id": m.get("conditionId", ""),
        "market_id": m.get("id", ""),
        "end_date": m.get("endDateIso", "")[:10],
    })

df = pd.DataFrame(rows)

# Sort
if sort_by == "Volume":
    df = df.sort_values("volume", ascending=False)
elif sort_by == "Price (High)":
    df = df.sort_values("price", ascending=False)
elif sort_by == "Price (Low)":
    df = df.sort_values("price", ascending=True)

# Paginate
total = len(df)
start = st.session_state.explorer_page * per_page
end = start + per_page
page_df = df.iloc[start:end]

st.caption(f"Showing {start + 1}-{min(end, total)} of {total} markets")

# Display
display_df = page_df.copy()
display_df["Price"] = display_df["price"].apply(lambda x: f"{x:.0%}" if x else "N/A")
display_df["Volume"] = display_df["volume"].apply(format_usd)
display_df = display_df.rename(columns={"question": "Market", "end_date": "End Date"})
display_df = display_df[["Market", "Price", "Volume", "End Date"]]

st.dataframe(display_df, use_container_width=True, hide_index=True)

# Pagination controls
col_prev, col_info, col_next = st.columns([1, 4, 1])
max_page = (total - 1) // per_page
with col_prev:
    if st.button("← Prev") and st.session_state.explorer_page > 0:
        st.session_state.explorer_page -= 1
        st.rerun()
with col_next:
    if st.button("Next →") and st.session_state.explorer_page < max_page:
        st.session_state.explorer_page += 1
        st.rerun()
with col_info:
    st.caption(f"Page {st.session_state.explorer_page + 1} of {max_page + 1}")
