"""Pure-Python token risk analysis. No I/O."""

from __future__ import annotations

from app.domains.token.models import (
    RiskLevel,
    TokenHolder,
    TokenLiquidity,
    TokenRisk,
)

class TokenAnalyzer:
    """Pure logic - given holders + liquidity + flags, return risk."""

    @staticmethod
    def compute_risk(
        address: str,
        chain: str,
        holders: list[TokenHolder] | None = None,
        liquidity: TokenLiquidity | None = None,
        honeypot: bool = False,
        can_sell: bool = True,
        can_buy: bool = True,
        owner_change_balance: bool = False,
        transfer_pausable: bool = False,
    ) -> TokenRisk:
        """Compute TokenRisk from raw inputs. Pure function of inputs."""
        flags: list[dict] = []
        score = 0

        # Honeypot = critical
        if honeypot or not can_sell:
            score += 100
            flags.append(
                {
                    "code": "honeypot",
                    "severity": "critical",
                    "message": "Cannot sell - likely honeypot.",
                }
            )

        # Owner can change balance = rug pull risk
        if owner_change_balance:
            score += 50
            flags.append(
                {
                    "code": "owner_change_balance",
                    "severity": "critical",
                    "message": "Owner can modify balances.",
                }
            )

        # Transfer pausable = rug risk
        if transfer_pausable:
            score += 30
            flags.append(
                {
                    "code": "transfer_pausable",
                    "severity": "high",
                    "message": "Transfers can be paused.",
                }
            )

        # Holder concentration
        if holders:
            top_holder_pct = max((h.percentage for h in holders), default=0)
            if top_holder_pct > 50:
                score += 30
                flags.append(
                    {
                        "code": "concentrated_holders",
                        "severity": "high",
                        "message": f"Top holder owns {top_holder_pct:.1f}% of supply.",
                    }
                )
            elif top_holder_pct > 25:
                score += 15
                flags.append(
                    {
                        "code": "moderate_concentration",
                        "severity": "medium",
                        "message": f"Top holder owns {top_holder_pct:.1f}% of supply.",
                    }
                )

            # Locked holders reduce risk
            locked_pct = sum(h.percentage for h in holders if h.is_locked)
            if locked_pct < 20 and len(holders) < 100:
                score += 5
                flags.append(
                    {
                        "code": "few_holders",
                        "severity": "low",
                        "message": f"Only {len(holders)} holders, {locked_pct:.1f}% locked.",
                    }
                )

        # Liquidity lock
        if liquidity:
            if liquidity.total_liquidity_usd < 1000:
                score += 20
                flags.append(
                    {
                        "code": "low_liquidity",
                        "severity": "high",
                        "message": f"Total liquidity only ${liquidity.total_liquidity_usd:.0f}.",
                    }
                )
            elif liquidity.locked_percentage < 50:
                score += 15
                flags.append(
                    {
                        "code": "liquidity_unlocked",
                        "severity": "medium",
                        "message": f"Only {liquidity.locked_percentage:.1f}% of liquidity locked.",
                    }
                )

        score = max(0, min(100, score))
        return TokenRisk(
            address=address,
            chain=chain,
            risk_score=score,
            risk_level=RiskLevel.from_score(score),
            flags=flags,
            honeypot=honeypot,
            can_sell=can_sell,
            can_buy=can_buy,
            owner_change_balance=owner_change_balance,
            transfer_pausable=transfer_pausable,
        )
