"""
Page 8: Bot Monitor — Start/stop the bot, view live trades, positions, P&L, risk status.
This is the command center for the automated trading bot.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone
from src import database as db
from src.bot_engine import TradingBot, load_config, TIMEFRAME_LABELS, TIMEFRAMES
from src.risk_manager import RiskManager, RiskLimits
from src.utils import format_usd, color_for_signal, signal_emoji

st.set_page_config(page_title="Bot Monitor", page_icon="🤖", layout="wide")
st.title("🤖 Bot Monitor — BTC/ETH Up or Down")

# ── Initialize bot in session state ───────────────────────────────────────────
if "bot" not in st.session_state:
    config = load_config()
    st.session_state.bot = TradingBot(config)
    st.session_state.bot_config = config

bot: TradingBot = st.session_state.bot
config = st.session_state.bot_config

# ── Bot Status Bar ────────────────────────────────────────────────────────────
is_running = db.get_bot_state("bot_running") == "true"
mode = db.get_bot_state("bot_mode") or config.get("bot", {}).get("mode", "paper")
scan_count = db.get_bot_state("scan_count") or "0"
trade_count = db.get_bot_state("trade_count") or "0"
last_scan = db.get_bot_state("last_scan_at") or "Never"
started_at = db.get_bot_state("bot_started_at") or "—"

# Status bar
status_color = "🟢" if is_running else "🔴"
mode_badge = "📝 PAPER" if mode == "paper" else "💰 LIVE"
tf_list = ", ".join(TIMEFRAME_LABELS.get(t, t) for t in bot.timeframes)

st.markdown(f"""
### {status_color} Bot Status: {"RUNNING" if is_running else "STOPPED"} | {mode_badge}
**Timeframes:** {tf_list} | **Assets:** {', '.join(bot.assets)}
""")

open_positions = db.get_bot_positions(mode=mode, status="open")
open_count = len(open_positions)

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Scans", scan_count)
col2.metric("Trades", trade_count)
col3.metric("Open Trades", open_count)
col4.metric("Last Scan", last_scan[:19] if last_scan != "Never" else "Never")
col5.metric("Started", started_at[:19] if started_at != "—" else "—")
col6.metric("Mode", mode.upper())

st.divider()

# ── Controls ──────────────────────────────────────────────────────────────────
col_ctrl1, col_ctrl2, col_ctrl3, col_ctrl4, col_ctrl5 = st.columns(5)

with col_ctrl1:
    if not bot.is_running:
        if st.button("▶️ Start Bot", type="primary", use_container_width=True):
            bot.start()
            st.success("Bot started!")
            st.rerun()
    else:
        if st.button("⏹ Stop Bot", type="secondary", use_container_width=True):
            bot.stop()
            st.warning("Bot stopped.")
            st.rerun()

with col_ctrl2:
    kill_active = db.get_bot_state("kill_switch") == "true"
    if kill_active:
        if st.button("🔓 Deactivate Kill Switch", use_container_width=True):
            bot.risk.deactivate_kill_switch()
            st.success("Kill switch deactivated")
            st.rerun()
    else:
        if st.button("🛑 KILL SWITCH", type="secondary", use_container_width=True):
            bot.risk.activate_kill_switch("Manual activation from dashboard")
            st.error("Kill switch activated — all trading halted!")
            st.rerun()

with col_ctrl3:
    positions = db.get_bot_positions(mode=mode, status="open")
    if positions:
        if st.button("🚫 Close All Positions", type="secondary", use_container_width=True):
            closed = db.close_all_bot_positions(mode=mode)
            db.log_bot_event("WARNING", f"Closed all {closed} positions from dashboard")
            st.warning(f"Closed {closed} positions!")
            st.rerun()
    else:
        st.button("🚫 Close All Positions", use_container_width=True, disabled=True)

with col_ctrl4:
    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()

with col_ctrl5:
    st.caption(f"Scan interval: {config.get('bot', {}).get('scan_interval_seconds', 30)}s")

# ── Timeframe selector (for next restart) ────────────────────────────────────
with st.expander("⏱️ Timeframe Settings"):
    st.caption("Select which timeframes the bot should trade. Changes take effect on next bot start.")
    selected_tfs = st.multiselect(
        "Active Timeframes",
        options=TIMEFRAMES,
        default=bot.timeframes,
        format_func=lambda x: TIMEFRAME_LABELS.get(x, x),
    )
    if st.button("Apply Timeframes"):
        bot.timeframes = selected_tfs
        config.setdefault("bot", {})["timeframes"] = selected_tfs
        db.log_bot_event("INFO", f"Timeframes updated: {selected_tfs}")
        st.success(f"Timeframes set to: {', '.join(TIMEFRAME_LABELS.get(t, t) for t in selected_tfs)}")

st.divider()

# ── Risk Dashboard ────────────────────────────────────────────────────────────
st.subheader("⚠️ Risk Status")

risk_status = bot.risk.get_status()

col_r1, col_r2, col_r3, col_r4 = st.columns(4)

with col_r1:
    st.metric("Kill Switch", "🔴 ACTIVE" if risk_status["kill_switch"] else "🟢 OFF")

with col_r2:
    st.metric("Open Positions",
              f"{risk_status['open_positions']}/{risk_status['max_positions']}")
    st.progress(min(risk_status['open_positions'] / max(risk_status['max_positions'], 1), 1.0))

with col_r3:
    st.metric("Total Exposure",
              f"{format_usd(risk_status['total_exposure'])} / {format_usd(risk_status['max_exposure'])}")
    st.progress(min(risk_status['exposure_pct'] / 100, 1.0))

with col_r4:
    pnl_color = "normal" if risk_status['daily_pnl'] >= 0 else "inverse"
    st.metric("Daily P&L", format_usd(risk_status['daily_pnl']),
              delta=f"Limit: {format_usd(-risk_status['max_daily_loss'])}")
    if risk_status['daily_pnl'] < 0:
        st.progress(min(risk_status['loss_pct'] / 100, 1.0))

st.divider()

# ── Open Positions (with individual close buttons) ───────────────────────────
st.subheader("📈 Open Positions")

positions = db.get_bot_positions(mode=mode, status="open")
if positions:
    for p in positions:
        pnl = 0
        if p.get("current_price") and p.get("avg_price"):
            pnl = (p["current_price"] - p["avg_price"]) * p["shares"]

        col_a, col_b, col_c, col_d, col_e, col_f = st.columns([3, 1, 1, 1, 1, 1])
        col_a.markdown(f"**{(p.get('market_question') or p['market_id'])[:55]}**")
        col_b.caption(f"🎯 {p['outcome']}")
        col_c.caption(f"📊 {p['shares']:.1f} shares")
        col_d.caption(f"💲 {p['avg_price']:.2%}")
        col_e.caption(f"💰 {format_usd(p['cost_basis'])}")
        with col_f:
            if st.button("❌ Close", key=f"close_{p['id']}", use_container_width=True):
                db.close_bot_position(p["id"], sell_price=p.get("current_price") or p["avg_price"])
                db.log_bot_event("INFO",
                                 f"Manually closed position: {(p.get('market_question') or '')[:50]}")
                st.rerun()
else:
    st.info("No open positions.")

st.divider()

# ── Trade History ─────────────────────────────────────────────────────────────
st.subheader("📋 Trade History")

trades = db.get_bot_trades(mode=mode, limit=50)
if trades:
    trade_rows = []
    for t in trades:
        trade_rows.append({
            "Time": t["timestamp"][:19],
            "Side": t["side"].upper(),
            "Market": (t.get("market_question") or "")[:50],
            "Outcome": t["outcome"],
            "Price": f"{t['price']:.2%}",
            "Amount": format_usd(t["amount_usd"]),
            "Shares": f"{t['shares']:.2f}",
            "EV Gap": f"{t.get('ev_gap', 0):.3f}" if t.get("ev_gap") else "—",
            "Kelly": f"{t.get('kelly_fraction', 0):.3f}" if t.get("kelly_fraction") else "—",
            "Status": t["status"],
        })

    st.dataframe(pd.DataFrame(trade_rows), use_container_width=True, hide_index=True)

    # P&L chart
    if len(trades) >= 2:
        amounts = [t["amount_usd"] if t["side"] == "sell" else -t["amount_usd"] for t in reversed(trades)]
        cumulative = []
        running = 0
        for a in amounts:
            running += a
            cumulative.append(running)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            y=cumulative, mode='lines+markers',
            line=dict(color='#00d4aa', width=2),
            fill='tozeroy', fillcolor='rgba(0,212,170,0.1)',
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        fig.update_layout(template="plotly_dark", height=300,
                          xaxis_title="Trade #", yaxis_title="Cumulative P&L ($)",
                          margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No trades yet. Start the bot to begin trading!")

st.divider()

# ── Bot Log ───────────────────────────────────────────────────────────────────
st.subheader("📝 Bot Log")

log_level = st.radio("Filter", ["All", "INFO", "TRADE", "WARNING", "ERROR", "CRITICAL"], horizontal=True)
logs = db.get_bot_logs(limit=100, level=log_level if log_level != "All" else None)

if logs:
    for log in logs[:30]:
        level = log["level"]
        icon = {"INFO": "ℹ️", "TRADE": "💹", "WARNING": "⚠️", "ERROR": "❌",
                "CRITICAL": "🚨", "DEBUG": "🔍"}.get(level, "📝")
        st.markdown(f"`{log['timestamp'][:19]}` {icon} **{level}** — {log['message']}")
        if log.get("details"):
            st.caption(f"    {log['details'][:200]}")
else:
    st.info("No log entries yet.")

# ── Configuration ─────────────────────────────────────────────────────────────
st.divider()
with st.expander("⚙️ Bot Configuration"):
    st.json(config)

    st.subheader("Adjust Risk Limits")
    col1, col2 = st.columns(2)
    with col1:
        new_max_pos = st.number_input("Max Position Size ($)", 1.0, 10000.0,
                                       float(bot.risk.limits.max_position_size_usd))
        new_max_exp = st.number_input("Max Total Exposure ($)", 10.0, 100000.0,
                                       float(bot.risk.limits.max_total_exposure_usd))
    with col2:
        new_max_loss = st.number_input("Max Daily Loss ($)", 1.0, 10000.0,
                                        float(bot.risk.limits.max_daily_loss_usd))
        new_max_open = st.number_input("Max Open Positions", 1, 50,
                                        int(bot.risk.limits.max_open_positions))

    if st.button("Update Limits"):
        bot.risk.limits.max_position_size_usd = new_max_pos
        bot.risk.limits.max_total_exposure_usd = new_max_exp
        bot.risk.limits.max_daily_loss_usd = new_max_loss
        bot.risk.limits.max_open_positions = new_max_open
        db.log_bot_event("INFO", "Risk limits updated from dashboard")
        st.success("Risk limits updated!")

# Auto-refresh every 10 seconds when bot is running
if is_running:
    import time
    time.sleep(10)
    st.rerun()
