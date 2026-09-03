"""The ~25 risk-factor builders for the risk_report package.

``_build_factors`` computes every factor with a per-factor WHY-it's-risky
explanation, grouped by section (deployer / holders / launch / cluster /
honeypot-contract / liquidity-market). ``_extract_top25_pcts`` feeds the
whale/share math.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domains.scanner.unified.risk_report._extract import _f, _factor, _i
from app.domains.scanner.unified.risk_report._model import ClusterInfo, RiskFactor

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

def _build_factors(
    r: ScanResult,
    deployer_section: dict[str, Any],
    holders_section: dict[str, Any],
    first_buyer: dict[str, Any],
    bundle: dict[str, Any],
    cluster_info: ClusterInfo,
    honeypot: dict[str, Any],
    liquidity: dict[str, Any],
    fresh_pct: float | None,
    cex_pct: float | None,
) -> list[RiskFactor]:
    """Compute the ~25 factors with per-factor WHY-it's-risky explanations."""
    factors: list[RiskFactor] = []
    add = factors.append

    # ── Deployer (4) ──
    repeat = deployer_section.get("repeat_deployer")
    total = _i(deployer_section.get("total_deployments"))
    if repeat is None:
        add(_factor("deployer_repeat", None, "No deployer history data available."))
    else:
        score = 45.0 if repeat else 5.0
        add(
            _factor(
                "deployer_repeat",
                score,
                f"Deployer has launched {total} token(s); repeat deployers can recycle wallets across launches "
                + ("(risky — same wallet, new token)." if repeat else "but shows no repeat-launch pattern."),
            )
        )

    rugged = deployer_section.get("previously_rugged")
    bucket = deployer_section.get("risk_bucket")
    if rugged is None:
        add(_factor("deployer_rugged_before", None, "No prior-deployment outcome data available."))
    else:
        add(
            _factor(
                "deployer_rugged_before",
                80.0 if rugged else 10.0,
                f"Deployer fingerprint bucket '{bucket}': prior launches that died within 7 days "
                + ("indicate a serial-rug pattern." if rugged else "survived past the 7-day window."),
            )
        )

    first_seen = deployer_section.get("first_seen")
    if first_seen:
        add(
            _factor(
                "deployer_new_wallet",
                40.0,
                f"Deployer first seen {first_seen} — recent first activity on a fresh wallet is a farm signal.",
            )
        )
    else:
        add(_factor("deployer_new_wallet", None, "Deployer wallet age unknown."))

    funder = deployer_section.get("funder") or {}
    if funder.get("address"):
        is_cex = bool(funder.get("is_cex"))
        is_fresh = bool(funder.get("is_fresh"))
        score = 10.0 if is_cex else (60.0 if is_fresh else 30.0)
        add(
            _factor(
                "deployer_funding",
                score,
                f"Deployer funded by {'CEX ' + str(funder.get('cex_label')) if is_cex else 'a non-exchange wallet'} "
                + ("(fresh funder with <10 txs)." if is_fresh and not is_cex else "."),
            )
        )
    else:
        add(_factor("deployer_funding", None, "Deployer funding source unknown."))

    # ── Holders (5) ──
    top10 = _f(holders_section.get("top_10_pct"))
    if top10 is None:
        add(_factor("holder_concentration", None, "Top-10 holder share unavailable."))
    else:
        score = 0.0 if top10 < 30 else (35.0 if top10 < 50 else (65.0 if top10 < 80 else 90.0))
        add(
            _factor(
                "holder_concentration",
                score,
                f"Top-10 holders control {top10:.1f}% of tracked supply — "
                + ("distributed." if score == 0 else "a rug/exit-liquidity risk if they coordinate."),
            )
        )

    dev_team = _f(holders_section.get("dev_team_share"))
    if dev_team is None:
        add(_factor("dev_team_share", None, "Dev/team wallet share unavailable."))
    else:
        add(
            _factor(
                "dev_team_share",
                0.0 if dev_team < 10 else (50.0 if dev_team < 25 else 85.0),
                f"Deployer + team wallets hold {dev_team:.1f}% — "
                + (
                    "large insider share enables coordinated dumps." if dev_team >= 25 else "moderate insider presence."
                ),
            )
        )

    top25 = _extract_top25_pcts(r, holders_section)
    whale = top25[0] if top25 else None
    if whale is None:
        add(_factor("top_holder_whale", None, "Top holder share unavailable."))
    else:
        add(
            _factor(
                "top_holder_whale",
                0.0 if whale < 15 else (55.0 if whale < 30 else 85.0),
                f"Largest holder controls {whale:.1f}% — "
                + ("normal." if whale < 15 else "a single wallet selling is a price-crash risk."),
            )
        )

    fresh_pct = fresh_pct
    if fresh_pct is None:
        add(_factor("fresh_wallet_pct", None, "Fresh-wallet % unavailable."))
    else:
        score = 0.0 if fresh_pct < 20 else (40.0 if fresh_pct < 40 else 75.0)
        add(
            _factor(
                "fresh_wallet_pct",
                score,
                f"{fresh_pct:.1f}% of analyzed holders are fresh wallets (<30 days) — "
                + ("a bot-farm / airdrop-farming concentration signal." if score >= 40 else "within normal bounds."),
            )
        )

    cex_pct = cex_pct
    if cex_pct is None:
        cex_pct = _f((r.wallet_metrics or {}).get("cex_funded_pct"))
    if cex_pct is None:
        cex_pct = _f((r.funding_analysis or {}).get("exchange_funded_pct"))
    if cex_pct is None:
        add(_factor("cex_funded_wallet_pct", None, "CEX-funded wallet % unavailable."))
    else:
        # High CEX funding reads as institutional backing (positive); very low
        # with high fresh % is the farm pattern.
        score = 15.0 if cex_pct >= 40 else (40.0 if cex_pct < 10 else 20.0)
        add(
            _factor(
                "cex_funded_wallet_pct",
                score,
                f"{cex_pct:.1f}% of holders were funded by CEX wallets — "
                + (
                    "institutional backing." if score <= 15 else "low exchange backing leaves the holder base unvetted."
                ),
            )
        )

    # ── Launch (4) ──
    fb_addr = first_buyer.get("address")
    if not fb_addr:
        add(_factor("first_buyer_deployer", None, "First-buyer data unavailable."))
    else:
        is_deployer = bool(first_buyer.get("is_deployer"))
        add(
            _factor(
                "first_buyer_deployer",
                70.0 if is_deployer else 5.0,
                f"First buyer is {'the deployer' if is_deployer else 'not the deployer'} ({str(fb_addr)[:10]}…).",
            )
        )

    in_bundle = first_buyer.get("in_bundle")
    if in_bundle is None:
        add(_factor("first_buyer_bundle", None, "First-buyer bundle membership unknown."))
    else:
        add(
            _factor(
                "first_buyer_bundle",
                70.0 if in_bundle else 5.0,
                "First buyer entered in the same launch cluster as other wallets — "
                + ("classic bundle-buy opening." if in_bundle else "bought independently."),
            )
        )

    is_bundled = bundle.get("is_bundled")
    if is_bundled is None:
        add(_factor("bundle_launch", None, "Bundle-launch data unavailable."))
    else:
        launch_score = _f(bundle.get("launch_cluster_score")) or 0.0
        score = 80.0 if is_bundled else (45.0 if launch_score >= 50 else 5.0)
        if is_bundled:
            explanation = (
                "Launch bundle DETECTED (coordinated buys at deployment) — "
                "a top rug vector where the same group controls the opening supply."
            )
        else:
            explanation = f"Launch bundle not detected (launch cluster score {launch_score:.0f}/100)."
        add(_factor("bundle_launch", score, explanation))

    snipers = _i(bundle.get("sniper_first_buyers"))
    if bundle.get("sniper_first_buyers") is None:
        add(_factor("sniper_first_buyers", None, "Sniper first-buyer data unavailable."))
    else:
        score = 0.0 if snipers == 0 else (40.0 if snipers < 5 else 70.0)
        add(
            _factor(
                "sniper_first_buyers",
                score,
                f"{snipers} sniper first-buyers in launch clusters — "
                + (
                    "dev-controlled or MEV bots opening positions." if score >= 40 else "no meaningful sniper presence."
                ),
            )
        )

    # ── Cluster (3) ──
    cluster_score = cluster_info.cluster_risk_score
    if cluster_score is None:
        add(_factor("funder_cluster", None, "Cluster analysis unavailable."))
    else:
        roots = [c for c in cluster_info.funder_tree if c.get("funder_is_deployer")]
        add(
            _factor(
                "funder_cluster",
                min(90.0, 35.0 * len(roots) + 20.0 * max(len(cluster_info.funder_tree) - len(roots), 0)),
                f"{len(cluster_info.funder_tree)} shared-funder root(s) "
                + (
                    f"({len(roots)} deployer-funded) — a coordinated wallet group."
                    if cluster_info.funder_tree
                    else "— no shared funders."
                ),
            )
        )

    if cluster_score is None:
        add(_factor("cross_token_cluster", None, "Cross-token cluster data unavailable."))
    else:
        n = len(cluster_info.cross_token_clusters)
        add(
            _factor(
                "cross_token_cluster",
                min(75.0, 15.0 * n),
                f"{n} other launch(es) share >=2 of this token's holders — "
                + ("wallets move between launches together (sybil ring)." if n else "no cross-token co-appearance."),
            )
        )

    if cluster_score is None:
        add(_factor("shared_deployer_network", None, "Shared-deployer network data unavailable."))
    else:
        n = len(cluster_info.shared_deployer_network)
        add(
            _factor(
                "shared_deployer_network",
                min(90.0, 25.0 * n),
                f"{n} repeat-buyer ring(s) across launches by the same deployer — "
                + ("same wallets, new tokens = wash-deploy pattern." if n else "no shared-deployer ring detected."),
            )
        )

    # ── Deepened cluster factors (cross-token funder graph, lockstep, bot/MEV/wash) ──
    if cluster_score is None:
        add(_factor("cross_token_funder", None, "Cross-token funder graph data unavailable."))
    else:
        n = len(cluster_info.cross_token_funders)
        add(
            _factor(
                "cross_token_funder",
                min(80.0, 20.0 * n),
                f"{n} funder(s) seeded wallets across >=2 other launches — "
                + ("a capital source behind a coordinated launch network." if n else "no cross-token funder graph."),
            )
        )

    if cluster_score is None:
        add(_factor("temporal_cluster", None, "Temporal (lockstep) cluster data unavailable."))
    else:
        n = len(cluster_info.temporal_clusters)
        max_members = max((int(c.get("members") or 0) for c in cluster_info.temporal_clusters), default=0)
        add(
            _factor(
                "temporal_cluster",
                min(80.0, 15.0 * n + 8.0 * max_members),
                f"{n} lockstep group(s) (largest {max_members} wallets) entered in the same launch blocks — "
                + ("wallets acting in lockstep." if n else "no lockstep entry groups."),
            )
        )

    if cluster_score is None:
        add(_factor("sniper_bot_pattern", None, "Sniper/bot pattern data unavailable."))
    else:
        patterns = list(cluster_info.sniper_patterns)
        n = len(patterns)
        bot_score = 0.0
        for p in patterns:
            ptype = p.get("pattern")
            if ptype == "sniper_wave":
                bot_score += 25.0
            elif ptype == "same_block_snipe":
                bot_score += 20.0
            elif ptype in ("uniform_bot_amounts", "fresh_wallet_bots"):
                bot_score += 15.0
            elif ptype == "tip_cluster":
                bot_score += 10.0
            else:
                bot_score += 10.0
        bot_score = min(90.0, bot_score)
        names = ", ".join(str(p.get("pattern")) for p in patterns[:5])
        add(
            _factor(
                "sniper_bot_pattern",
                bot_score,
                f"{n} sniper/bot signature(s) at launch ({names or 'none'}) — "
                + ("bot-driven opening distribution." if bot_score >= 40 else "no strong bot-launch signature."),
            )
        )

    if cluster_score is None:
        add(_factor("mev_bundle", None, "MEV-bundle data unavailable."))
    else:
        n = len(cluster_info.mev_bundles)
        add(
            _factor(
                "mev_bundle",
                min(85.0, 20.0 * n),
                f"{n} MEV-bundle pattern(s) (atomic multi-wallet buys / buy-then-distribute) — "
                + ("bundled transactions simulate organic interest." if n else "no MEV-bundle signature."),
            )
        )

    if cluster_score is None:
        add(_factor("wash_trading_cluster", None, "Wash-trading data unavailable."))
    else:
        wash = cluster_info.wash_clusters if isinstance(cluster_info.wash_clusters, dict) else {}
        cycles = wash.get("cycles")
        n_cycles = len(cycles) if isinstance(cycles, list) else 0
        ratio = _f(wash.get("cycle_volume_ratio"))
        wash_score_val = min(90.0, 12.0 * n_cycles + 60.0 * (ratio or 0.0))
        ratio_txt = f" covering {ratio * 100:.1f}% of transfer volume" if ratio else ""
        add(
            _factor(
                "wash_trading_cluster",
                wash_score_val,
                f"{n_cycles} wash-trade cycle(s){ratio_txt} — "
                + (
                    "circular trading inflates volume and fakes activity."
                    if wash_score_val >= 40
                    else "no meaningful circular trading."
                ),
            )
        )

    # ── Honeypot / contract (4) ──
    hp = honeypot.get("is_honeypot")
    if hp is None:
        add(_factor("honeypot", None, "Honeypot status unknown (no simulation data)."))
    else:
        add(
            _factor(
                "honeypot",
                95.0 if hp else 0.0,
                "Honeypot DETECTED — the token can be bought but not sold; "
                + "holders cannot exit, a definitive scam vector."
                if hp
                else "Sell-side simulation passes — not a honeypot.",
            )
        )

    mintable = honeypot.get("mintable")
    if mintable is None:
        add(_factor("mint_authority", None, "Mint authority unknown."))
    else:
        add(
            _factor(
                "mint_authority",
                85.0 if mintable else 5.0,
                "Mint authority is ACTIVE — the deployer can mint unlimited supply and dump on holders."
                if mintable
                else "Mint authority renounced — supply cannot be inflated.",
            )
        )

    freezable = honeypot.get("freezable")
    if freezable is None:
        add(_factor("freeze_authority", None, "Freeze authority unknown."))
    else:
        add(
            _factor(
                "freeze_authority",
                70.0 if freezable else 5.0,
                "Freeze authority is ACTIVE — holders can be frozen out of their positions."
                if freezable
                else "Freeze authority renounced.",
            )
        )

    renounced = honeypot.get("ownership_renounced")
    if renounced is None:
        add(_factor("ownership_renounced", None, "Ownership renunciation unknown."))
    else:
        add(
            _factor(
                "ownership_renounced",
                5.0 if renounced else 50.0,
                "Ownership renounced — contract owner cannot change rules."
                if renounced
                else "Ownership NOT renounced — the owner retains control (tax changes, blacklist, upgrade).",
            )
        )

    # ── Liquidity / tax / market (5) ──
    locked = liquidity.get("locked_pct")
    if locked is None:
        add(_factor("lp_locked", None, "LP lock status unavailable."))
    else:
        add(
            _factor(
                "lp_locked",
                0.0 if locked >= 50 else (55.0 if locked >= 20 else 85.0),
                f"{locked:.1f}% of LP is locked/burned — "
                + (
                    "liquidity cannot be pulled."
                    if locked >= 50
                    else "unlocked LP is a rug vector (dev can remove it)."
                ),
            )
        )

    liq_usd = liquidity.get("liquidity_usd")
    if liq_usd is None:
        add(_factor("lp_liquidity_amount", None, "Liquidity amount unavailable."))
    else:
        add(
            _factor(
                "lp_liquidity_amount",
                0.0 if liq_usd >= 100_000 else (45.0 if liq_usd >= 20_000 else 75.0),
                f"Liquidity is ~${liq_usd:,.0f} — "
                + (
                    "deep enough to absorb sells."
                    if liq_usd >= 100_000
                    else "thin liquidity amplifies exit-sell price impact."
                ),
            )
        )

    buy_tax = honeypot.get("buy_tax")
    sell_tax = honeypot.get("sell_tax")
    if buy_tax is None and sell_tax is None:
        add(_factor("buy_sell_tax", None, "Tax data unavailable."))
    else:
        b = _f(buy_tax) or 0.0
        s = _f(sell_tax) or 0.0
        combined = b + s
        score = 0.0 if combined < 10 else (40.0 if combined < 25 else 80.0)
        add(
            _factor(
                "buy_sell_tax",
                score,
                f"Buy tax {b:.1f}% / sell tax {s:.1f}% — "
                + ("combined tax is a profit-trap for holders." if score >= 40 else "taxes within normal bounds."),
            )
        )

    va = r.volume_auth if isinstance(r.volume_auth, dict) else {}
    fake_pct = _f(va.get("fake_volume_pct"))
    if fake_pct is None:
        add(_factor("volume_authenticity", None, "Volume authenticity unavailable."))
    else:
        add(
            _factor(
                "volume_authenticity",
                0.0 if fake_pct < 10 else (50.0 if fake_pct < 50 else 85.0),
                f"{fake_pct:.1f}% of volume is estimated fake — "
                + (
                    "washed volume creates a false liquidity/momentum picture."
                    if fake_pct >= 50
                    else "volume looks organic."
                ),
            )
        )

    overlap = r.holder_overlap if isinstance(r.holder_overlap, dict) else {}
    max_overlap = _f(overlap.get("max_overlap_pct"))
    worst_scam = bool(overlap.get("worst_token_is_scam"))
    if max_overlap is None:
        add(_factor("holder_overlap_scam", None, "Holder-overlap-vs-scams data unavailable."))
    else:
        score = 0.0 if max_overlap < 30 else (60.0 if not worst_scam else 85.0)
        add(
            _factor(
                "holder_overlap_scam",
                score,
                f"{max_overlap:.1f}% holder overlap with known scam tokens"
                + (" — the same wallets appear in confirmed scams." if worst_scam else " — elevated cross-holding."),
            )
        )

    return factors


def _extract_top25_pcts(r: ScanResult, holders_section: dict[str, Any]) -> list[float]:
    """Sorted top-holder percentages (descending) for whale/share math."""
    pcts: list[float] = []
    top_25 = holders_section.get("_top_25")
    if not isinstance(top_25, list):
        return pcts
    for h in top_25:
        if not isinstance(h, dict):
            continue
        p = h.get("share_pct")
        f = _f(p)
        if f is not None:
            pcts.append(f)
    return sorted(pcts, reverse=True)
