"""Scanner accuracy calibration — aggregate-risk band tests.

Synthetic module outputs are fed through the unified scoring engine
(``app.domains.scanner.unified.scoring.compute_score``) and the resulting
*safety* score (0-100, higher = safer) is asserted to land in the expected
band. The bands mirror the canonical risk-level mapping::

    safety >= 80  → safe        (risk 0-20)
    safety >= 60  → low         (risk 20-40)
    safety >= 40  → medium      (risk 40-60)
    safety >= 20  → high        (risk 60-80)
    safety <  20  → critical    (risk 80-100)

These are calibration tests: they pin the *aggregate* behavior, not the
per-flag deltas. If a future change re-weights individual signals, the bands
must still hold.

Regression covered: a confirmed GoPlus honeypot (``HP01`` fail) used to cost
only the generic -5 per failed security check, leaving a honeypot token at
45 ("medium") — and a honeypot with clean-looking other modules at 65
("low"). A honeypot is the single most decisive rug signal; it must drive
the score to critical regardless of other modules.
"""

from __future__ import annotations

from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.scoring import compute_score

# ── Bands (safety score; 100 = safest) ────────────────────────────────
SAFE_BAND_MIN = 70.0  # all modules clean → risk < 30
HONEYPOT_CRITICAL_MAX = 20.0  # confirmed honeypot → risk > 80
HONEYPOT_OVER_BONUSES_MAX = 60.0  # honeypot dominates clean-module bonuses


def _result(**overrides: object) -> ScanResult:
    """Build a minimal ScanResult for calibration tests."""
    defaults: dict[str, object] = {
        "token_address": "0xCALIB",
        "chain": "solana",
    }
    defaults.update(overrides)
    return ScanResult(**defaults)  # type: ignore[arg-type]


def _honeypot_checks() -> dict[str, object]:
    """GoPlus security checks for a confirmed honeypot (HP01 fail)."""
    return {"checks": {"HP01": {"status": "fail", "details": "GoPlus honeypot=YES"}}}


class TestSafeBand:
    """All modules report clean → aggregate risk lands in the safe band."""

    def test_all_modules_safe_scores_above_safe_band_min(self) -> None:
        out = compute_score(
            _result(
                modules_run=["liquidity", "holders", "deployer", "volume"],
                liquidity={"locked_pct": 95},
                holders={"top_10_pct": 20},
                dev_reputation={"risk_score": 10},
                volume_auth={"fake_volume_pct": 5},
            )
        )
        assert out["score"] >= SAFE_BAND_MIN
        assert out["risk_level"] in ("low", "safe")

    def test_no_data_token_stays_at_neutral_baseline(self) -> None:
        """Guard: no data at all must not be over-penalized (unknown ≠ risky)."""
        out = compute_score(_result())
        assert out["score"] == 50.0
        assert out["risk_level"] == "medium"


class TestHoneypotBand:
    """Confirmed honeypot must land in the critical band, regardless of
    other (clean) module outputs."""

    def test_honeypot_alone_scores_critical(self) -> None:
        out = compute_score(
            _result(
                modules_run=["token_security"],
                security=_honeypot_checks(),
            )
        )
        assert out["score"] < HONEYPOT_CRITICAL_MAX
        assert out["risk_level"] == "critical"
        assert "HONEYPOT_DETECTED" in out["breakdown"]["security"]["flags"]

    def test_honeypot_flips_clean_token_out_of_safe_band(self) -> None:
        """Honeypot + all-clean modules → not low/safe (honeypot dominates)."""
        out = compute_score(
            _result(
                modules_run=["token_security", "liquidity", "holders", "deployer", "volume"],
                security=_honeypot_checks(),
                liquidity={"locked_pct": 95},
                holders={"top_10_pct": 20},
                dev_reputation={"risk_score": 10},
                volume_auth={"fake_volume_pct": 5},
            )
        )
        assert out["score"] < HONEYPOT_OVER_BONUSES_MAX
        assert out["risk_level"] in ("high", "critical")

    def test_honeypot_with_green_security_checks_still_critical(self) -> None:
        """Real-world regression (Carbon/CSIX, BSC, GoPlus is_honeypot=1):
        the -35 penalty alone was eroded by seven passing security checks
        (+14) to safety 29 ("high", risk 71). A confirmed honeypot must land
        in the critical band regardless of green bonuses in other checks."""
        out = compute_score(
            _result(
                modules_run=["token_security"],
                security={
                    "checks": {
                        "HP01": {"status": "fail", "details": "GoPlus honeypot=YES"},
                        "CR01": {"status": "pass", "details": "Verified=YES"},
                        "CR02": {"status": "pass", "details": "Proxy=no"},
                        "CR03": {"status": "pass", "details": "Mintable=no"},
                        "HP04": {"status": "pass", "details": "Pausable=no"},
                        "CR04": {"status": "pass", "details": ""},
                        "HD03": {"status": "pass", "details": "Holders=27583"},
                        "LR03": {"status": "pass", "details": "Anti-whale=no"},
                    }
                },
            )
        )
        assert out["score"] < HONEYPOT_CRITICAL_MAX
        assert out["risk_level"] == "critical"
        assert "HONEYPOT_DETECTED" in out["breakdown"]["security"]["flags"]

    def test_honeypot_override_wins_over_all_clean_modules(self) -> None:
        """Honeypot + every module clean → still critical (override beats
        bonuses from liquidity/holders/deployer/volume too)."""
        out = compute_score(
            _result(
                modules_run=["token_security", "liquidity", "holders", "deployer", "volume"],
                security=_honeypot_checks(),
                liquidity={"locked_pct": 95},
                holders={"top_10_pct": 20},
                dev_reputation={"risk_score": 10},
                volume_auth={"fake_volume_pct": 5},
            )
        )
        assert out["score"] < HONEYPOT_CRITICAL_MAX
        assert out["risk_level"] == "critical"
        assert "HONEYPOT_DETECTED" in out["breakdown"]["security"]["flags"]
