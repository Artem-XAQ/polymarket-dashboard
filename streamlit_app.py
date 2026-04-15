"""
Polymarket Trading Dashboard — Main entrypoint.
Run with: streamlit run streamlit_app.py
"""

import streamlit as st

st.set_page_config(
    page_title="Polymarket Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize database on first load
from src import database as db
db.init_db()

st.title("📊 Polymarket Trading Dashboard")

st.markdown("""
Welcome to your Polymarket trading command center.

### Pages

| Page | Description |
|------|-------------|
| **📊 Overview** | Top markets by volume, trending, key metrics |
| **🔍 Explorer** | Search and browse all active markets |
| **📈 Market Detail** | Price charts, order book, recent trades |
| **💰 Paper Trading** | Virtual $1K wallet, simulated trades |
| **🔔 Signals** | Price alerts and notifications |
| **🧮 Quant Tools** | 6 hedge fund formulas + Trade Scorecard |
| **📡 Live Scanner** | Auto-scan 5-min BTC/ETH Up or Down markets |
| **🤖 Bot Monitor** | Start/stop automated bot, view trades & risk |

### Quick Start

1. **Browse markets** → Overview or Explorer
2. **Scan for trades** → Live Scanner (filters to 5-min BTC/ETH markets)
3. **Analyze a trade** → Quant Tools → Trade Scorecard
4. **Automate** → Bot Monitor (starts in paper mode)

### Bot Focus: 5-Minute Crypto Up/Down Markets

The bot targets **"Bitcoin Up or Down"** and **"Ethereum Up or Down"** 5-minute binary markets.
These resolve every 5 minutes — high frequency, clear outcomes.

- **Paper** (default): Simulated trades, no real money
- **Live**: Real execution via Polymarket CLOB API (requires `config.yaml` with credentials)

Use the sidebar to navigate between pages.
""")

# Show bot status in sidebar
st.sidebar.divider()
st.sidebar.subheader("Bot Status")
bot_running = db.get_bot_state("bot_running") == "true"
bot_mode = db.get_bot_state("bot_mode") or "paper"
if bot_running:
    st.sidebar.success(f"🟢 Bot RUNNING ({bot_mode})")
else:
    st.sidebar.info("🔴 Bot STOPPED")

kill = db.get_bot_state("kill_switch") == "true"
if kill:
    st.sidebar.error("🛑 KILL SWITCH ACTIVE")
