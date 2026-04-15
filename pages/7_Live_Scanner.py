"""Page 7: Live Scanner — Auto-scan for 5-minute BTC/ETH Up or Down markets."""

import streamlit as st
import pandas as pd
import time
from src import api, database as db
from src.quant import ev_gap, kelly_for_binary_market, run_scorecard
from src.bot_engine import is_updown_market, classify_timeframe, get_asset_from_question, TIMEFRAMES, TIMEFRAME_LABELS
from src.utils import format_usd, parse_market_price, parse_volume, signal_emoji, color_for_signal

st.set_page_config(page_title="Live Scanner", page_icon="📡", layout="wide")
st.title("📡 Live Scanner — BTC/ETH Up or Down")

# Settings
with st.sidebar:
    st.subheader("Scanner Settings")
    assets = st.multiselect(
        "Assets",
        ["Bitcoin", "Ethereum"],
        default=["Bitcoin", "Ethereum"]
    )
    selected_tfs = st.multiselect(
        "Timeframes",
        options=TIMEFRAMES,
        default=TIMEFRAMES,
        format_func=lambda x: TIMEFRAME_LABELS.get(x, x),
    )
    min_ev = st.slider("Min EV Gap", 0.01, 0.20, 0.03, 0.01, format="%.0f%%")
    refresh_sec = st.slider("Refresh (seconds)", 15, 120, 30, 15)
    fee_rate = st.slider("Fee Rate", 0.0, 0.05, 0.02, 0.005)
    show_all = st.checkbox("Show all markets (not just opportunities)", value=True)

# Build keywords
keywords = []
for asset in assets:
    keywords.append(asset)
    if asset == "Bitcoin":
        keywords.extend(["BTC", "bitcoin"])
    elif asset == "Ethereum":
        keywords.extend(["ETH", "ethereum"])

# Scan
if st.button("🔄 Scan Now", type="primary") or True:
    with st.spinner(f"Scanning for {', '.join(assets)} Up or Down markets..."):
        all_markets = api.get_updown_crypto_markets(asset_keywords=assets)

    if not all_markets:
        st.warning("No Up or Down markets found. Check if Polymarket has active markets right now.")
        st.stop()

    # Filter to selected timeframes
    matched_markets = []
    for m in all_markets:
        question = m.get("question", "")
        tf = classify_timeframe(question)
        if tf and tf in selected_tfs:
            price = parse_market_price(m)
            if price is not None:
                m["_price"] = price
                m["_asset"] = get_asset_from_question(question)
                m["_timeframe"] = tf
                matched_markets.append(m)

    # Split into active (tradeable) and resolved
    active_markets = [m for m in matched_markets if 0.03 < m["_price"] < 0.97]
    resolved_markets = [m for m in matched_markets if m["_price"] <= 0.03 or m["_price"] >= 0.97]

    # Timeframe breakdown
    tf_counts = {}
    for m in active_markets:
        tf = m.get("_timeframe", "?")
        tf_counts[tf] = tf_counts.get(tf, 0) + 1

    # Stats
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Found", len(all_markets))
    col2.metric("Matched Markets", len(matched_markets))
    col3.metric("Active (Tradeable)", len(active_markets))
    col4.metric("Last Scan", time.strftime("%H:%M:%S"))

    # Timeframe breakdown badges
    if tf_counts:
        tf_str = " | ".join(f"**{TIMEFRAME_LABELS.get(k, k)}**: {v}" for k, v in sorted(tf_counts.items()))
        st.markdown(f"📊 {tf_str}")

    st.divider()

    # Analyze active markets
    opportunities = []
    all_analyzed = []

    for m in active_markets:
        price = m["_price"]
        asset = m["_asset"]
        tokens = api.get_token_ids_for_market(m)
        if not tokens:
            continue

        # Find UP token
        up_outcome = None
        up_token = None
        for outcome, token_id in tokens.items():
            if outcome.lower() in ("up", "yes"):
                up_outcome = outcome
                up_token = token_id
                break
        if not up_token:
            up_outcome = list(tokens.keys())[0]
            up_token = list(tokens.values())[0]

        # Get order book for analysis
        book_signal = 0.0
        try:
            book = api.get_order_book(up_token)
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

        # Simple model: momentum from price history + book imbalance
        model_prob = price  # Default
        history = api.get_price_history(up_token, interval="1h", fidelity=60)
        if history and len(history) >= 3:
            prices_hist = []
            for pt in history:
                p = pt.get("p") or pt.get("price")
                if p:
                    try:
                        prices_hist.append(float(p))
                    except (ValueError, TypeError):
                        continue
            if len(prices_hist) >= 3:
                momentum = (prices_hist[-1] - prices_hist[0]) / max(prices_hist[0], 0.01)
                avg = sum(prices_hist) / len(prices_hist)
                deviation = (price - avg) / max(avg, 0.01)
                adjustment = (momentum * 0.45) - (deviation * 0.25) + (book_signal * 0.30)
                model_prob = max(0.05, min(0.95, price + adjustment * 0.20))

        # Run EV + Kelly + Scorecard
        ev = ev_gap(model_prob, price, fee_rate)
        kelly = kelly_for_binary_market(model_prob, price)
        scorecard = run_scorecard(model_prob, price, 1000, fee_rate, volatility=0.10)

        entry = {
            "market_id": m.get("conditionId", ""),
            "question": m.get("question", "Unknown"),
            "asset": asset,
            "timeframe": m.get("_timeframe", "?"),
            "outcome": up_outcome,
            "token_id": up_token,
            "market_price": price,
            "model_prob": model_prob,
            "ev_raw": ev["raw_ev"],
            "ev_net": ev["net_ev"],
            "kelly": kelly["kelly_fraction"],
            "kelly_side": kelly["side"],
            "signal": scorecard["signal"],
            "score": scorecard["score"],
            "volume": parse_volume(m),
            "book_imbalance": book_signal,
        }

        all_analyzed.append(entry)

        if ev["is_opportunity"] and ev["net_ev"] >= min_ev:
            opportunities.append(entry)
            # Log scan
            db.record_scan(
                entry["market_id"], entry["question"], entry["signal"],
                entry["model_prob"], entry["market_price"], entry["ev_net"], entry["kelly"]
            )

    # Sort by EV
    opportunities.sort(key=lambda x: x["ev_net"], reverse=True)
    all_analyzed.sort(key=lambda x: x["ev_net"], reverse=True)

    # Display opportunities
    st.subheader(f"🎯 Opportunities ({len(opportunities)})")

    if opportunities:
        for opp in opportunities:
            signal_color = color_for_signal(opp["signal"])
            emoji = signal_emoji(opp["signal"])
            book_dir = "📈" if opp["book_imbalance"] > 0.1 else ("📉" if opp["book_imbalance"] < -0.1 else "➡️")
            tf_label = TIMEFRAME_LABELS.get(opp.get("timeframe", ""), opp.get("timeframe", ""))

            with st.expander(
                f"{emoji} **{opp['signal']}** | {opp['asset']} [{tf_label}] | "
                f"{opp['question'][:60]} | EV: {opp['ev_net']:.3f}",
                expanded=(opp["score"] >= 4),
            ):
                col_a, col_b, col_c, col_d, col_e = st.columns(5)
                col_a.metric("Market (Up)", f"{opp['market_price']:.0%}")
                col_b.metric("Model (Up)", f"{opp['model_prob']:.0%}")
                col_c.metric("Net EV", f"{opp['ev_net']:.4f}")
                col_d.metric("Kelly", f"{opp['kelly']:.3f} ({opp['kelly_side']})")
                col_e.metric("Book Pressure", f"{book_dir} {opp['book_imbalance']:.2f}")

                st.caption(f"Score: {opp['score']}/6 | Volume: {format_usd(opp['volume'])}")
    else:
        st.info("No EV opportunities found above threshold. Markets may be efficiently priced.")

    # Tabs: BTC / ETH / All Markets / Summary
    st.divider()
    tab_btc, tab_eth, tab_all, tab_table, tab_history = st.tabs([
        "₿ BTC Markets", "Ξ ETH Markets", "All Active", "Summary Table", "Scan History"
    ])

    def show_market_list(markets_list, label=""):
        if not markets_list:
            st.info(f"No active {label} markets found.")
            return
        for m in markets_list:
            emoji = signal_emoji(m["signal"])
            book_dir = "📈" if m["book_imbalance"] > 0.1 else ("📉" if m["book_imbalance"] < -0.1 else "➡️")
            tf_label = TIMEFRAME_LABELS.get(m.get("timeframe", ""), "")
            st.markdown(
                f"{emoji} **{m['signal']}** ({m['score']}/6) | "
                f"`{tf_label}` | {m['question'][:55]} | "
                f"Up: {m['market_price']:.0%} | Model: {m['model_prob']:.0%} | "
                f"EV: {m['ev_net']:.4f} | {book_dir}"
            )

    display_list = all_analyzed if show_all else opportunities

    with tab_btc:
        btc = [o for o in display_list if o["asset"] == "BTC"]
        show_market_list(btc, "BTC")

    with tab_eth:
        eth = [o for o in display_list if o["asset"] == "ETH"]
        show_market_list(eth, "ETH")

    with tab_all:
        show_market_list(display_list)

    with tab_table:
        if display_list:
            df = pd.DataFrame([{
                "Signal": f"{signal_emoji(o['signal'])} {o['signal']}",
                "TF": TIMEFRAME_LABELS.get(o.get("timeframe", ""), "?"),
                "Asset": o["asset"],
                "Market": o["question"][:50],
                "Up Price": f"{o['market_price']:.0%}",
                "Model": f"{o['model_prob']:.0%}",
                "EV": f"{o['ev_net']:.4f}",
                "Kelly": f"{o['kelly']:.3f}",
                "Side": o["kelly_side"],
                "Score": f"{o['score']}/6",
                "Book": f"{o['book_imbalance']:+.2f}",
            } for o in display_list])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No markets to display.")

    with tab_history:
        scan_hist = db.get_scan_history(limit=50)
        if scan_hist:
            hist_df = pd.DataFrame([{
                "Time": h["timestamp"][:16],
                "Signal": h["signal"],
                "Market": (h.get("market_question") or "")[:50],
                "Model": f"{h['model_prob']:.0%}" if h.get("model_prob") else "",
                "Market Price": f"{h['market_prob']:.0%}" if h.get("market_prob") else "",
                "EV": f"{h['ev_gap']:.4f}" if h.get("ev_gap") else "",
            } for h in scan_hist])
            st.dataframe(hist_df, use_container_width=True, hide_index=True)
        else:
            st.info("No scan history yet.")

    # Resolved markets (recent)
    if resolved_markets:
        st.divider()
        with st.expander(f"📋 Recently Resolved ({len(resolved_markets)})"):
            for m in resolved_markets[:10]:
                price = m["_price"]
                result_str = "✅ UP" if price >= 0.97 else "🔴 DOWN"
                st.markdown(f"{result_str} — {m.get('question', '')[:70]}")

# Auto-refresh
time.sleep(refresh_sec)
st.rerun()
