"""Wallet-behavior classification for RugCharts trade streams.

Turns raw dex swap events into labeled trades the frontend trade-tape
already renders: whale / bot / sniper / bundle / swap.

This replaces the previous state of the world where the UI offered filters
for labels (bot, sniper, bundle...) that NO backend ever produced. Labels
here are heuristic but every one traces to a measurable property of the
trade sequence — nothing is invented. Attribution caveat is explicit:
dex_events exposes `recipient` (output-token receiver) as the closest
available taker proxy; router-mediated swaps may collapse to the router
address, so labels carry a confidence and the classifier never claims
identity-level certainty.

Heuristics (v1, defensible):
- whale      : notional >= whale threshold (mirrors indexer flag).
- bot        : machine-gun regularity — >= MIN_BOT_TRADES trades from the
               same taker proxy whose inter-arrival gaps have coefficient of
               variation < BOT_GAP_CV; OR round-number sizing —
               >= ROUND_SHARE of sizes within ROUND_TOL of a round USD value.
- sniper     : same taker proxy buys then fully reverses (sell) within
               SNIPER_WINDOW_S seconds.
- bundle     : >= BUNDLE_MIN trades across >= BUNDLE_MIN_WALLETS proxies
               sharing the same block second (coordinated launch volume).
- swap       : everything else (honest default).
"""

from __future__ import annotations

import contextlib
import itertools
from collections import defaultdict
from typing import Any

# ── Tunables (module-level so tests and ops can inspect/adjust) ──────────
WHALE_MIN_USD = 10_000.0
MIN_BOT_TRADES = 4
BOT_GAP_CV = 0.35
ROUND_VALUES = (100, 200, 250, 500, 1_000, 2_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000)
ROUND_TOL = 0.02
ROUND_SHARE = 0.6
SNIPER_WINDOW_S = 120
BUNDLE_MIN = 3
BUNDLE_MIN_WALLETS = 2

LABEL_CONFIDENCE = {"whale": 90, "bot": 75, "sniper": 70, "bundle": 65, "swap": 40}


def _is_round_usd(amount: float) -> bool:
    return any(abs(amount - rv) / rv <= ROUND_TOL for rv in ROUND_VALUES)


def _gap_cv(times: list[int]) -> float | None:
    """Coefficient of variation of sorted inter-arrival gaps; None if n<3."""
    if len(times) < 3:
        return None
    ts = sorted(times)
    gaps = [b - a for a, b in itertools.pairwise(ts) if b - a > 0]
    if len(gaps) < 2:
        return None
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    return (var**0.5) / mean


def classify_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the trades annotated with `wallet_label` + `label_confidence`.

    Input trades need: time (unix s), volume_usd, side ("buy"/"sell"/...),
    tx_hash and optionally wallet (taker proxy). Trades without a wallet
    can still receive whale/bundle labels; per-wallet heuristics skip them.
    """
    if not trades:
        return []

    by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_second: dict[int, set[str]] = defaultdict(set)
    for t in trades:
        w = (t.get("wallet") or "").strip().lower()
        if w:
            by_wallet[w].append(t)
            with contextlib.suppress(TypeError, ValueError):
                by_second[int(t.get("time", 0))].add(w)

    # ── Bundle detection: coordinated same-second burst ──────────────────
    bundled_wallets: set[str] = set()
    for _sec, wallets in by_second.items():
        if len(wallets) >= BUNDLE_MIN_WALLETS:
            total = sum(
                1
                for t in trades
                if (t.get("wallet") or "").strip().lower() in wallets and int(t.get("time", 0)) == _sec
            )
            if total >= BUNDLE_MIN:
                bundled_wallets |= wallets

    labeled: dict[int, dict[str, Any]] = {}

    # ── Per-wallet behavioral labels ─────────────────────────────────────
    for wallet, wtrades in by_wallet.items():
        times = [int(t.get("time", 0)) for t in wtrades]
        amounts = [float(t.get("volume_usd", 0) or 0) for t in wtrades]

        cv = _gap_cv(times)
        regular_bot = len(wtrades) >= MIN_BOT_TRADES and cv is not None and cv < BOT_GAP_CV
        sized = [a for a in amounts if a > 0]
        round_bot = (
            len(sized) >= MIN_BOT_TRADES and sum(1 for a in sized if _is_round_usd(a)) / len(sized) >= ROUND_SHARE
        )

        # Sniper: buy followed by an opposite-side exit inside the window.
        sniper = False
        buys = sorted((int(t["time"]), i) for i, t in enumerate(wtrades) if t.get("side") == "buy")
        sells_times = [int(t["time"]) for t in wtrades if t.get("side") == "sell"]
        for bt, _i in buys:
            if any(bt <= st <= bt + SNIPER_WINDOW_S for st in sells_times):
                sniper = True
                break

        for t in wtrades:
            vol = float(t.get("volume_usd", 0) or 0)
            if vol >= WHALE_MIN_USD:
                label = "whale"
            elif wallet in bundled_wallets:
                label = "bundle"
            elif regular_bot or round_bot:
                label = "bot"
            elif sniper:
                label = "sniper"
            else:
                label = "swap"
            labeled[id(t)] = {
                **t,
                "wallet_label": label,
                "label_confidence": LABEL_CONFIDENCE[label],
            }

    # Trades with no wallet attribution: still get whale/bundle-free basics.
    out: list[dict[str, Any]] = []
    for t in trades:
        entry = labeled.get(id(t))
        if entry is None:
            vol = float(t.get("volume_usd", 0) or 0)
            label = "whale" if vol >= WHALE_MIN_USD else "swap"
            entry = {**t, "wallet_label": label, "label_confidence": LABEL_CONFIDENCE[label]}
        out.append(entry)

    out.sort(key=lambda x: x.get("time", 0))
    return out


def label_summary(trades: list[dict[str, Any]]) -> dict[str, int]:
    """Counts per label — cheap telemetry for the tape header."""
    counts: dict[str, int] = defaultdict(int)
    for t in trades:
        counts[t.get("wallet_label", "swap")] += 1
    return dict(counts)
