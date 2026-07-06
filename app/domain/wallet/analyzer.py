"""Pure risk-scoring logic. No I/O, no external calls.

This is the testable, deterministic core. Given a portfolio + recent
transactions + known labels, return a risk score and flags.
"""
from __future__ import annotations

from app.domain.wallet.models import (
    RiskLevel,
    ScanFlag,
    TokenHolding,
    Transaction,
    WalletAnalysis,
)


class WalletAnalyzer:
    """Pure-Python risk analysis. No external dependencies."""

    # Truncation for display: first 6 + "..." + last 4
    TRUNCATE_PREFIX = 6
    TRUNCATE_SUFFIX = 4

    def analyze(
        self,
        address: str,
        chain: str,
        tokens: list[TokenHolding] | None = None,
        recent_transactions: list[Transaction] | None = None,
        labels: list[str] | None = None,
    ) -> WalletAnalysis:
        """Produce a full WalletAnalysis from raw inputs.

        Pure function of inputs. No I/O. Easily unit-testable.
        """
        tokens = tokens or []
        recent = recent_transactions or []
        labels = labels or []

        risk_score = self._compute_risk_score(tokens, recent, labels)
        risk_level = RiskLevel.from_score(risk_score)
        flags = self._compute_flags(tokens, recent, labels, risk_score)
        total_value = sum(t.value_usd for t in tokens)

        return WalletAnalysis(
            address=address,
            chain=chain,
            truncated_address=self._truncate(address),
            risk_score=risk_score,
            risk_level=risk_level,
            tokens=tokens,
            recent_transactions=recent,
            labels=labels,
            flags=flags,
            total_value_usd=round(total_value, 2),
        )

    @staticmethod
    def _truncate(address: str) -> str:
        if len(address) <= WalletAnalyzer.TRUNCATE_PREFIX + WalletAnalyzer.TRUNCATE_SUFFIX + 3:
            return address
        return (
            address[: WalletAnalyzer.TRUNCATE_PREFIX]
            + "..."
            + address[-WalletAnalyzer.TRUNCATE_SUFFIX :]
        )

    @staticmethod
    def _compute_risk_score(
        tokens: list[TokenHolding],
        recent: list[Transaction],
        labels: list[str],
    ) -> int:
        """Score 0-100. Higher = riskier.

        Heuristic:
        - Each token = +2 (diversification proxy)
        - Each recent tx = +1 (activity proxy)
        - Each suspicious label (mixer, drainer, exploit) = +30
        - Negative balances / huge values = clamp 0-100
        """
        score = len(tokens) * 2 + len(recent)
        suspicious = {"mixer", "drainer", "exploit", "hack", "phishing", "ransomware"}
        for label in labels:
            if any(s in label.lower() for s in suspicious):
                score += 30
        return max(0, min(100, score))

    @staticmethod
    def _compute_flags(
        tokens: list[TokenHolding],
        recent: list[Transaction],
        labels: list[str],
        risk_score: int,
    ) -> list[ScanFlag]:
        """Generate human-readable risk flags from inputs."""
        flags: list[ScanFlag] = []

        if risk_score >= 80:
            flags.append(ScanFlag(
                code="critical_risk",
                severity=RiskLevel.CRITICAL,
                message="Wallet shows patterns consistent with high-risk activity.",
            ))
        elif risk_score >= 50:
            flags.append(ScanFlag(
                code="elevated_risk",
                severity=RiskLevel.HIGH,
                message="Elevated risk indicators present.",
            ))

        suspicious = {"mixer", "drainer", "exploit", "hack", "phishing", "ransomware"}
        for label in labels:
            if any(s in label.lower() for s in suspicious):
                flags.append(ScanFlag(
                    code="flagged_entity",
                    severity=RiskLevel.CRITICAL,
                    message=f"Wallet linked to flagged entity: {label}",
                    evidence={"label": label},
                ))

        # Dust tokens (many low-value tokens) signal airdrop farming or spam
        dust_count = sum(1 for t in tokens if 0 < t.value_usd < 1)
        if dust_count >= 10:
            flags.append(ScanFlag(
                code="dust_tokens",
                severity=RiskLevel.MEDIUM,
                message=f"{dust_count} dust tokens detected - possible airdrop farm or spam exposure.",
                evidence={"dust_count": dust_count},
            ))

        return flags
