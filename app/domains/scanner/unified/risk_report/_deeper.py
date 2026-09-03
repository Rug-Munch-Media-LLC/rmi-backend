"""Deeper-level expanded analysis for the risk_report package.

``_build_deeper_analysis`` produces a per-section natural-language narrative
explaining the evidence behind each factor, grouped by report section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.domains.scanner.unified.risk_report._model import RiskFactor


def _build_deeper_analysis(
    report_sections: dict[str, Any],
    factors: list[RiskFactor],
) -> dict[str, Any]:
    """Expanded natural-language analysis grouped by section.

    This is the "deeper-level" pass: every section gets a narrative that
    explains the evidence, not just the verdict. Callers may post-process
    ``deeper_analysis`` with an AI model (``ai_assisted=True``) to reword /
    extend it — the structure is stable for downstream rendering.
    """
    deployer = report_sections["deployer"]
    holders = report_sections["holders"]
    first_buyer = report_sections["first_buyer"]
    bundle = report_sections["bundle"]
    honeypot = report_sections["honeypot"]
    liquidity = report_sections["liquidity"]

    factor_map = {f.key: f for f in factors}

    def _line(key: str) -> str:
        f = factor_map.get(key)
        if f is None:
            return ""
        return f"- {f.label}: {f.explanation}"

    deployer_text = (
        f"Deployer {str(deployer.get('address') or 'unknown')[:12]}… has "
        f"{deployer.get('total_deployments', '?')} deployment(s); repeat={deployer.get('repeat_deployer')}, "
        f"previously_rugged={deployer.get('previously_rugged')}."
    )
    holders_text = (
        f"{holders.get('total_holders', '?')} holders; top-10 share "
        f"{holders.get('top_10_pct', '?')}%; concentration {holders.get('concentration', 'unknown')}."
    )
    first_buyer_text = (
        f"First buyer {str(first_buyer.get('address') or 'unknown')[:12]}… — "
        f"deployer={first_buyer.get('is_deployer')}, in_bundle={first_buyer.get('in_bundle')}."
    )
    bundle_text = (
        f"Launch bundle={bundle.get('is_bundled')} (cluster score {bundle.get('launch_cluster_score') or 0}/100, "
        f"deployer-funded {bundle.get('deployer_funded_pct') or 0}%, first-time buyers {bundle.get('first_time_buyer_pct') or 0}%)."
    )
    honeypot_text = (
        f"Honeypot={honeypot.get('is_honeypot')}; buy tax {honeypot.get('buy_tax') or 0}%, sell tax "
        f"{honeypot.get('sell_tax') or 0}%; mintable={honeypot.get('mintable')}, "
        f"freezable={honeypot.get('freezable')}, ownership_renounced={honeypot.get('ownership_renounced')}."
    )
    liquidity_text = (
        f"LP locked {liquidity.get('locked_pct') or 0}% (locked={liquidity.get('is_locked')}); "
        f"liquidity ~${liquidity.get('liquidity_usd') or 0:,.0f}."
    )

    return {
        "deployer": {
            "summary": deployer_text,
            "factors": [
                ln
                for ln in [
                    _line("deployer_repeat"),
                    _line("deployer_rugged_before"),
                    _line("deployer_new_wallet"),
                    _line("deployer_funding"),
                ]
                if ln
            ],
        },
        "holders": {
            "summary": holders_text,
            "factors": [
                ln
                for ln in [
                    _line("holder_concentration"),
                    _line("dev_team_share"),
                    _line("top_holder_whale"),
                    _line("fresh_wallet_pct"),
                    _line("cex_funded_wallet_pct"),
                ]
                if ln
            ],
        },
        "first_buyer": {
            "summary": first_buyer_text,
            "factors": [ln for ln in [_line("first_buyer_deployer"), _line("first_buyer_bundle")] if ln],
        },
        "launch": {
            "summary": bundle_text,
            "factors": [ln for ln in [_line("bundle_launch"), _line("sniper_first_buyers")] if ln],
        },
        "cluster": {
            "summary": (
                "Cluster graph: funder tree + shared-deployer networks + cross-token "
                "clusters + cross-token funder graphs + temporal lockstep + "
                "sniper/bot patterns + MEV bundles + wash-trading clusters."
            ),
            "factors": [
                ln
                for ln in [
                    _line("funder_cluster"),
                    _line("shared_deployer_network"),
                    _line("cross_token_cluster"),
                    _line("cross_token_funder"),
                    _line("temporal_cluster"),
                    _line("sniper_bot_pattern"),
                    _line("mev_bundle"),
                    _line("wash_trading_cluster"),
                ]
                if ln
            ],
        },
        "honeypot_contract": {
            "summary": honeypot_text,
            "factors": [
                ln
                for ln in [
                    _line("honeypot"),
                    _line("mint_authority"),
                    _line("freeze_authority"),
                    _line("ownership_renounced"),
                ]
                if ln
            ],
        },
        "liquidity_market": {
            "summary": liquidity_text,
            "factors": [
                ln
                for ln in [
                    _line("lp_locked"),
                    _line("lp_liquidity_amount"),
                    _line("buy_sell_tax"),
                    _line("volume_authenticity"),
                    _line("holder_overlap_scam"),
                ]
                if ln
            ],
        },
    }
