"""Page 6: Quant Tools — All 6 hedge fund formulas with Trade Scorecard."""

import streamlit as st
import plotly.graph_objects as go
import numpy as np
from src.quant import (
    run_scorecard, lmsr_price_curve, lmsr_price_impact,
    kelly_for_binary_market, kelly_growth_simulation,
    ev_gap, kl_arb_signal, kl_heatmap, symmetric_kl,
    bayesian_chain, stoikov_reservation_price, stoikov_curve,
)
from src.utils import color_for_signal

st.set_page_config(page_title="Quant Tools", page_icon="🧮", layout="wide")
st.title("🧮 Quant Tools — 6 Hedge Fund Formulas")

tab_score, tab_lmsr, tab_kelly, tab_ev, tab_kl, tab_bayes, tab_stoikov = st.tabs([
    "📊 Trade Scorecard", "F1: LMSR", "F2: Kelly", "F3: EV Gap",
    "F4: KL-Divergence", "F5: Bayesian", "F6: Stoikov"
])

# ── Trade Scorecard ───────────────────────────────────────────────────────────
with tab_score:
    st.subheader("Trade Scorecard — All 6 Formulas")

    col1, col2 = st.columns(2)
    with col1:
        model_prob = st.slider("Your Model Probability", 0.05, 0.95, 0.55, 0.01,
                               format="%.0f%%", key="sc_model")
        market_price = st.slider("Market Price", 0.05, 0.95, 0.45, 0.01,
                                 format="%.0f%%", key="sc_market")
    with col2:
        bankroll = st.number_input("Bankroll ($)", 100, 100000, 1000, key="sc_bank")
        fee_rate = st.slider("Fee Rate", 0.0, 0.05, 0.02, 0.005, format="%.1f%%", key="sc_fee")

    if st.button("Run Scorecard", type="primary"):
        result = run_scorecard(model_prob, market_price, bankroll, fee_rate)

        # Signal display
        signal_color = color_for_signal(result["signal"])
        st.markdown(f"""
        <div style="padding: 20px; border-radius: 10px; border: 2px solid {signal_color};
                    text-align: center; margin: 10px 0;">
            <h1 style="color: {signal_color}; margin: 0;">{result['signal']}</h1>
            <p>Score: {result['score']}/{result['max_score']} | Side: {result['side']} |
            Position: ${result['position_size_usd']:.2f}</p>
        </div>
        """, unsafe_allow_html=True)

        for reason in result["reasons"]:
            st.markdown(f"- {reason}")

        # Detailed results
        with st.expander("Detailed Formula Results"):
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.metric("EV Gap (net)", f"{result['ev']['net_ev']:.4f}")
                st.metric("Kelly Fraction", f"{result['kelly']['kelly_fraction']:.3f}")
            with col_b:
                st.metric("Kelly Side", result['kelly']['side'])
                st.metric("Bayesian Posterior", f"{result['bayesian_posterior']:.3f}")
            with col_c:
                st.metric("Stoikov Reservation", f"{result['stoikov']['reservation_price']:.4f}")
                st.metric("LMSR Impact", f"{result['lmsr']['impact']:.4f}")

# ── F1: LMSR ─────────────────────────────────────────────────────────────────
with tab_lmsr:
    st.subheader("F1: LMSR Pricing — Price Impact Calculator")

    col1, col2 = st.columns(2)
    with col1:
        b = st.slider("Liquidity Parameter (b)", 1.0, 50.0, 10.0, 0.5)
    with col2:
        trade_size = st.slider("Trade Size (shares)", 1, 500, 100)

    impact = lmsr_price_impact(0, trade_size, b)
    st.metric("Price Impact", f"{impact['impact']:.4f}")
    st.metric("Cost", f"${impact['cost']:.2f}")

    qs, prices = lmsr_price_curve(b)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=qs, y=prices, mode='lines', line=dict(color='#00d4aa')))
    fig.update_layout(template="plotly_dark", height=350, xaxis_title="Quantity", yaxis_title="Price")
    st.plotly_chart(fig, use_container_width=True)

# ── F2: Kelly ─────────────────────────────────────────────────────────────────
with tab_kelly:
    st.subheader("F2: Kelly Criterion — Optimal Bet Sizing")

    col1, col2 = st.columns(2)
    with col1:
        k_model = st.slider("Model Probability", 0.05, 0.95, 0.60, 0.01, key="k_model")
        k_market = st.slider("Market Price", 0.05, 0.95, 0.45, 0.01, key="k_market")
    with col2:
        k_bankroll = st.number_input("Bankroll", 100, 100000, 1000, key="k_bank")

    result = kelly_for_binary_market(k_model, k_market)
    st.metric("Kelly Fraction", f"{result['kelly_fraction']:.3f}")
    st.metric("Bet Size", f"${result['kelly_fraction'] * k_bankroll:.2f}")
    st.metric("Side", result['side'])
    st.metric("Edge", f"{result['edge']:.3f}")

    # Growth simulation
    if result["kelly_fraction"] > 0:
        paths = kelly_growth_simulation(
            result["kelly_fraction"], k_model,
            (1 - k_market) / k_market, n_bets=50, n_paths=20, initial=k_bankroll
        )
        fig = go.Figure()
        for i, path in enumerate(paths):
            fig.add_trace(go.Scatter(y=path, mode='lines', opacity=0.4,
                                     line=dict(width=1), showlegend=False))
        fig.update_layout(template="plotly_dark", height=350,
                          xaxis_title="Bets", yaxis_title="Bankroll ($)")
        st.plotly_chart(fig, use_container_width=True)

# ── F3: EV Gap ────────────────────────────────────────────────────────────────
with tab_ev:
    st.subheader("F3: EV Gap Detection")

    col1, col2, col3 = st.columns(3)
    with col1:
        ev_model = st.slider("Model Probability", 0.05, 0.95, 0.60, 0.01, key="ev_model")
    with col2:
        ev_market = st.slider("Market Price", 0.05, 0.95, 0.45, 0.01, key="ev_market")
    with col3:
        ev_fee = st.slider("Fee Rate", 0.0, 0.05, 0.02, 0.005, key="ev_fee")

    result = ev_gap(ev_model, ev_market, ev_fee)
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Raw EV", f"{result['raw_ev']:.4f}")
    col_b.metric("Net EV (after fees)", f"{result['net_ev']:.4f}")
    col_c.metric("Opportunity?", "YES ✅" if result['is_opportunity'] else "NO ❌")

# ── F4: KL-Divergence ────────────────────────────────────────────────────────
with tab_kl:
    st.subheader("F4: KL-Divergence — Correlation Arbitrage")

    col1, col2 = st.columns(2)
    with col1:
        market_a = st.slider("Market A probability", 0.05, 0.95, 0.60, 0.01, key="kl_a")
    with col2:
        market_b = st.slider("Market B probability", 0.05, 0.95, 0.45, 0.01, key="kl_b")

    kl_thresh = st.slider("Arb Threshold", 0.05, 1.0, 0.2, 0.05)
    result = kl_arb_signal([market_a, 1 - market_a], [market_b, 1 - market_b], kl_thresh)

    st.metric("KL Divergence", f"{result['kl_divergence']:.4f}")
    st.metric("Signal", result['signal'])

    # Heatmap
    heatmap_data, probs = kl_heatmap(15)
    fig = go.Figure(data=go.Heatmap(
        z=heatmap_data, x=[f"{p:.0%}" for p in probs], y=[f"{p:.0%}" for p in probs],
        colorscale='Viridis',
    ))
    fig.update_layout(template="plotly_dark", height=400,
                      xaxis_title="Market B", yaxis_title="Market A")
    st.plotly_chart(fig, use_container_width=True)

# ── F5: Bayesian ──────────────────────────────────────────────────────────────
with tab_bayes:
    st.subheader("F5: Bayesian Updates — Sequential Evidence")

    prior = st.slider("Prior Probability", 0.05, 0.95, 0.50, 0.01, key="b_prior")

    st.markdown("**Add Evidence (up to 5 pieces):**")
    evidence = []
    for i in range(5):
        col1, col2 = st.columns(2)
        with col1:
            lt = st.slider(f"P(evidence|true) #{i+1}", 0.01, 0.99, 0.70, 0.01, key=f"b_lt_{i}")
        with col2:
            lf = st.slider(f"P(evidence|false) #{i+1}", 0.01, 0.99, 0.30, 0.01, key=f"b_lf_{i}")
        evidence.append((lt, lf))
        if i < 4:
            if not st.checkbox(f"Add evidence #{i+2}", key=f"b_add_{i}"):
                break

    posteriors = bayesian_chain(prior, evidence)
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=posteriors, mode='lines+markers',
                             line=dict(color='#00d4aa', width=2), marker=dict(size=10)))
    fig.update_layout(template="plotly_dark", height=300,
                      xaxis_title="Update Step", yaxis_title="Posterior",
                      yaxis_range=[0, 1])
    st.plotly_chart(fig, use_container_width=True)
    st.metric("Final Posterior", f"{posteriors[-1]:.3f}")

# ── F6: Stoikov ───────────────────────────────────────────────────────────────
with tab_stoikov:
    st.subheader("F6: Stoikov Execution — Reservation Price")

    col1, col2 = st.columns(2)
    with col1:
        s_mid = st.slider("Mid Price", 0.10, 0.90, 0.50, 0.01, key="s_mid")
        s_inv = st.slider("Current Inventory", -10, 10, 0, key="s_inv")
    with col2:
        s_vol = st.slider("Volatility", 0.01, 0.50, 0.10, 0.01, key="s_vol")
        s_gamma = st.slider("Risk Aversion (γ)", 0.01, 1.0, 0.1, 0.01, key="s_gamma")

    result = stoikov_reservation_price(s_mid, s_inv, s_vol, 1.0, s_gamma)
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Reservation Price", f"{result['reservation_price']:.4f}")
    col_b.metric("Optimal Bid", f"{result['bid']:.4f}")
    col_c.metric("Optimal Ask", f"{result['ask']:.4f}")

    inventories, reservations = stoikov_curve(s_mid, s_vol, 10, 1.0, s_gamma)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=inventories, y=reservations, mode='lines',
                             line=dict(color='#00d4aa', width=2)))
    fig.add_hline(y=s_mid, line_dash="dash", line_color="gray", annotation_text="Mid Price")
    fig.update_layout(template="plotly_dark", height=350,
                      xaxis_title="Inventory", yaxis_title="Reservation Price")
    st.plotly_chart(fig, use_container_width=True)
