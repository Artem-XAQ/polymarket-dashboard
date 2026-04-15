"""
Quant engine — 6 hedge fund formulas for prediction market analysis.

F1: LMSR Pricing (Logarithmic Market Scoring Rule)
F2: Kelly Criterion (optimal position sizing)
F3: EV Gap Detection (expected value edge)
F4: KL-Divergence (correlation arbitrage)
F5: Bayesian Updates (posterior probability)
F6: Stoikov Execution (Avellaneda-Stoikov reservation price)
"""
from __future__ import annotations

import numpy as np
from typing import Optional


# ── F1: LMSR Pricing ─────────────────────────────────────────────────────────

def lmsr_cost(q_before: float, q_after: float, b: float) -> float:
    """
    LMSR cost function.
    q_before/q_after: quantity of shares before/after trade
    b: liquidity parameter (higher = more liquid, less price impact)
    Returns: cost of the trade in USD
    """
    cost = b * (np.log(np.exp(q_after / b) + 1) - np.log(np.exp(q_before / b) + 1))
    return float(cost)


def lmsr_price(q: float, b: float) -> float:
    """
    LMSR instantaneous price (probability).
    q: current quantity
    b: liquidity parameter
    Returns: price/probability between 0 and 1
    """
    return float(np.exp(q / b) / (np.exp(q / b) + 1))


def lmsr_price_impact(current_q: float, trade_size: float, b: float) -> dict:
    """
    Calculate price impact of a trade.
    Returns: dict with before_price, after_price, impact, cost
    """
    p_before = lmsr_price(current_q, b)
    p_after = lmsr_price(current_q + trade_size, b)
    cost = lmsr_cost(current_q, current_q + trade_size, b)
    return {
        "before_price": p_before,
        "after_price": p_after,
        "impact": p_after - p_before,
        "impact_pct": (p_after - p_before) / p_before if p_before > 0 else 0,
        "cost": cost,
    }


def lmsr_price_curve(b: float, q_range: tuple = (-10, 10), points: int = 100) -> tuple:
    """Generate the LMSR price curve for visualization."""
    qs = np.linspace(q_range[0], q_range[1], points)
    prices = [lmsr_price(q, b) for q in qs]
    return list(qs), prices


# ── F2: Kelly Criterion ──────────────────────────────────────────────────────

def kelly_fraction(win_prob: float, win_odds: float, loss_odds: float = 1.0) -> float:
    """
    Kelly Criterion for optimal bet sizing.
    win_prob: probability of winning (0-1)
    win_odds: payout ratio on win (e.g., 2.0 means 2:1)
    loss_odds: fraction lost on loss (usually 1.0)
    Returns: optimal fraction of bankroll to bet
    """
    if win_odds <= 0 or win_prob <= 0 or win_prob >= 1:
        return 0.0
    f = (win_prob * win_odds - (1 - win_prob) * loss_odds) / win_odds
    return max(0.0, float(f))


def kelly_for_binary_market(model_prob: float, market_price: float) -> dict:
    """
    Kelly for a binary prediction market.
    model_prob: your estimated true probability
    market_price: current market price (what you pay)
    Returns: dict with kelly_fraction, bet_size_pct, edge
    """
    if market_price <= 0 or market_price >= 1:
        return {"kelly_fraction": 0, "bet_size_pct": 0, "edge": 0, "side": "none"}

    # Buy YES: win (1 - market_price) per share, lose market_price
    edge_yes = model_prob - market_price
    win_odds_yes = (1 - market_price) / market_price
    kelly_yes = kelly_fraction(model_prob, win_odds_yes)

    # Buy NO: win market_price per share, lose (1 - market_price)
    edge_no = (1 - model_prob) - (1 - market_price)
    win_odds_no = market_price / (1 - market_price)
    kelly_no = kelly_fraction(1 - model_prob, win_odds_no)

    if kelly_yes > kelly_no:
        return {
            "kelly_fraction": kelly_yes,
            "bet_size_pct": kelly_yes * 100,
            "edge": edge_yes,
            "side": "YES",
        }
    elif kelly_no > 0:
        return {
            "kelly_fraction": kelly_no,
            "bet_size_pct": kelly_no * 100,
            "edge": edge_no,
            "side": "NO",
        }
    else:
        return {"kelly_fraction": 0, "bet_size_pct": 0, "edge": 0, "side": "none"}


def kelly_growth_simulation(kelly_frac: float, win_prob: float, win_payout: float,
                            n_bets: int = 100, n_paths: int = 20, initial: float = 1000.0) -> list:
    """Simulate bankroll growth paths using Kelly sizing."""
    paths = []
    for _ in range(n_paths):
        bankroll = [initial]
        for _ in range(n_bets):
            bet = bankroll[-1] * kelly_frac
            if np.random.random() < win_prob:
                bankroll.append(bankroll[-1] + bet * win_payout)
            else:
                bankroll.append(bankroll[-1] - bet)
        paths.append(bankroll)
    return paths


# ── F3: EV Gap Detection ─────────────────────────────────────────────────────

def ev_gap(model_prob: float, market_price: float, fee_rate: float = 0.02) -> dict:
    """
    Calculate Expected Value gap between model and market.
    model_prob: your estimated true probability
    market_price: current market price
    fee_rate: trading fee (Polymarket ~1-2%)
    Returns: dict with raw_ev, net_ev (after fees), edge_pct, is_opportunity
    """
    if market_price <= 0 or market_price >= 1:
        return {"raw_ev": 0, "net_ev": 0, "edge_pct": 0, "is_opportunity": False, "side": "none"}

    # EV of buying YES
    ev_yes = model_prob * (1 - market_price) - (1 - model_prob) * market_price
    # EV of buying NO
    ev_no = (1 - model_prob) * market_price - model_prob * (1 - market_price)

    if ev_yes > ev_no and ev_yes > 0:
        raw_ev = ev_yes
        side = "YES"
    elif ev_no > 0:
        raw_ev = ev_no
        side = "NO"
    else:
        return {"raw_ev": 0, "net_ev": 0, "edge_pct": 0, "is_opportunity": False, "side": "none"}

    net_ev = raw_ev - fee_rate
    price = market_price if side == "YES" else (1 - market_price)
    edge_pct = net_ev / price if price > 0 else 0

    return {
        "raw_ev": raw_ev,
        "net_ev": net_ev,
        "edge_pct": edge_pct,
        "is_opportunity": net_ev > 0,
        "side": side,
    }


# ── F4: KL-Divergence ────────────────────────────────────────────────────────

def kl_divergence(p: list[float], q: list[float]) -> float:
    """
    KL-Divergence: D_KL(P || Q) — how much P diverges from Q.
    Lower = more similar. Used to detect correlation arb between related markets.
    """
    p = np.array(p, dtype=float)
    q = np.array(q, dtype=float)
    # Avoid log(0)
    p = np.clip(p, 1e-10, 1)
    q = np.clip(q, 1e-10, 1)
    # Normalize
    p = p / p.sum()
    q = q / q.sum()
    return float(np.sum(p * np.log(p / q)))


def symmetric_kl(p: list[float], q: list[float]) -> float:
    """Symmetric KL divergence (Jensen-Shannon like)."""
    return (kl_divergence(p, q) + kl_divergence(q, p)) / 2


def kl_arb_signal(market_a_probs: list[float], market_b_probs: list[float],
                  threshold: float = 0.2) -> dict:
    """
    Check if two correlated markets have diverged enough for an arb.
    threshold: KL-divergence above this = arb opportunity
    """
    kl = symmetric_kl(market_a_probs, market_b_probs)
    return {
        "kl_divergence": kl,
        "is_arb": kl > threshold,
        "signal": "ARB" if kl > threshold else "ALIGNED",
        "threshold": threshold,
    }


def kl_heatmap(resolution: int = 20) -> np.ndarray:
    """Generate KL divergence heatmap for all probability combinations."""
    probs = np.linspace(0.05, 0.95, resolution)
    heatmap = np.zeros((resolution, resolution))
    for i, p in enumerate(probs):
        for j, q in enumerate(probs):
            heatmap[i, j] = symmetric_kl([p, 1 - p], [q, 1 - q])
    return heatmap, list(probs)


# ── F5: Bayesian Updates ─────────────────────────────────────────────────────

def bayesian_update(prior: float, likelihood_if_true: float, likelihood_if_false: float) -> float:
    """
    Single Bayesian update.
    prior: P(H)
    likelihood_if_true: P(E|H)
    likelihood_if_false: P(E|not H)
    Returns: posterior P(H|E)
    """
    numerator = likelihood_if_true * prior
    denominator = likelihood_if_true * prior + likelihood_if_false * (1 - prior)
    if denominator == 0:
        return prior
    return float(numerator / denominator)


def bayesian_chain(prior: float, evidence_list: list[tuple[float, float]]) -> list[float]:
    """
    Sequential Bayesian updates.
    evidence_list: list of (likelihood_if_true, likelihood_if_false)
    Returns: list of posterior probabilities after each update
    """
    posteriors = [prior]
    current = prior
    for lt, lf in evidence_list:
        current = bayesian_update(current, lt, lf)
        posteriors.append(current)
    return posteriors


# ── F6: Stoikov Execution (Avellaneda-Stoikov) ────────────────────────────────

def stoikov_reservation_price(mid_price: float, inventory: float, volatility: float,
                              time_remaining: float = 1.0, risk_aversion: float = 0.1) -> dict:
    """
    Avellaneda-Stoikov reservation price.
    mid_price: current midpoint price
    inventory: current inventory (positive = long, negative = short)
    volatility: price volatility (annualized or per-period)
    time_remaining: fraction of trading period remaining (0-1)
    risk_aversion: gamma parameter (higher = more risk averse)
    Returns: dict with reservation_price, optimal_spread, bid, ask
    """
    # Reservation price: r = s - q * gamma * sigma^2 * T
    reservation = mid_price - inventory * risk_aversion * (volatility ** 2) * time_remaining

    # Optimal spread: delta = gamma * sigma^2 * T + (2/gamma) * ln(1 + gamma/k)
    k = 1.5  # order arrival intensity parameter
    spread = risk_aversion * (volatility ** 2) * time_remaining
    if risk_aversion > 0:
        spread += (2 / risk_aversion) * np.log(1 + risk_aversion / k)

    half_spread = spread / 2
    bid = reservation - half_spread
    ask = reservation + half_spread

    return {
        "reservation_price": float(reservation),
        "optimal_spread": float(spread),
        "bid": float(bid),
        "ask": float(ask),
        "inventory_adjustment": float(mid_price - reservation),
    }


def stoikov_curve(mid_price: float, volatility: float, max_inventory: int = 10,
                  time_remaining: float = 1.0, risk_aversion: float = 0.1) -> tuple:
    """Generate reservation price curve across inventory levels."""
    inventories = list(range(-max_inventory, max_inventory + 1))
    reservations = []
    for inv in inventories:
        result = stoikov_reservation_price(mid_price, inv, volatility, time_remaining, risk_aversion)
        reservations.append(result["reservation_price"])
    return inventories, reservations


# ── Trade Scorecard (Composite) ──────────────────────────────────────────────

def run_scorecard(model_prob: float, market_price: float, bankroll: float = 1000.0,
                  fee_rate: float = 0.02, volatility: float = 0.05,
                  inventory: float = 0, liquidity_b: float = 5.0) -> dict:
    """
    Run all 6 formulas and produce a composite trade signal.
    Returns: dict with all formula results + composite signal
    """
    # F1: LMSR
    lmsr = lmsr_price_impact(0, 100, liquidity_b)  # 100-share buy impact

    # F2: Kelly
    kelly = kelly_for_binary_market(model_prob, market_price)

    # F3: EV Gap
    ev = ev_gap(model_prob, market_price, fee_rate)

    # F5: Bayesian (single update from model vs market)
    posterior = bayesian_update(market_price, model_prob, 1 - model_prob)

    # F6: Stoikov
    stoikov = stoikov_reservation_price(market_price, inventory, volatility)

    # Composite scoring
    score = 0
    reasons = []

    if ev["is_opportunity"] and ev["net_ev"] > 0:
        score += 2
        reasons.append(f"EV gap: +{ev['net_ev']:.3f} after fees")

    if kelly["kelly_fraction"] > 0.01:
        score += 1
        reasons.append(f"Kelly: {kelly['bet_size_pct']:.1f}% of bankroll")

    if kelly["kelly_fraction"] > 0.05:
        score += 1
        reasons.append("Strong Kelly signal")

    if abs(model_prob - market_price) > 0.05:
        score += 1
        reasons.append(f"Model divergence: {abs(model_prob - market_price):.1%}")

    if stoikov["reservation_price"] > market_price and kelly["side"] == "YES":
        score += 1
        reasons.append("Stoikov confirms entry")
    elif stoikov["reservation_price"] < market_price and kelly["side"] == "NO":
        score += 1
        reasons.append("Stoikov confirms fade")

    # Determine signal
    if score >= 5:
        signal = "STRONG BUY" if kelly["side"] == "YES" else "STRONG FADE"
    elif score >= 3:
        signal = "BUY" if kelly["side"] == "YES" else "FADE"
    elif score >= 2:
        signal = "CONDITIONAL"
    else:
        signal = "PASS"

    # Position sizing
    capped_kelly = min(kelly["kelly_fraction"], 0.25)  # Quarter Kelly cap
    position_size = bankroll * capped_kelly

    return {
        "signal": signal,
        "score": score,
        "max_score": 6,
        "reasons": reasons,
        "side": kelly["side"],
        "position_size_usd": position_size,
        "kelly": kelly,
        "ev": ev,
        "lmsr": lmsr,
        "bayesian_posterior": posterior,
        "stoikov": stoikov,
    }
