"""pump.fun launch metadata for Solana tokens.

Provides symbol / name / market cap (SOL-denominated on the curve) and the
bonding-curve progress. pump.fun graduates at 85 SOL of virtual SOL reserves,
so ``bonding_progress = min(1, v_sol_curve / 85)``.
"""

from __future__ import annotations

from typing import Any

from app.trending.ch_client import query_rows

PUMPFUN_SQL = """
SELECT mint, any(symbol) AS symbol, any(name) AS name,
       max(market_cap_sol) AS market_cap_sol,
       max(v_sol_curve) AS v_sol_curve,
       max(seen_at) AS seen_at
FROM wallet_memory.pumpfun_launches
WHERE mint IN %(mints)s
GROUP BY mint
"""

GRADUATION_VSOL = 85.0


def fetch_pumpfun(mints: list[str]) -> dict[str, dict[str, Any]]:
    """Map mint -> pump.fun metadata. Returns {} when nothing matches."""
    if not mints:
        return {}
    rows = query_rows(PUMPFUN_SQL, {"mints": tuple(mints)})
    keys = ("mint", "symbol", "name", "market_cap_sol", "v_sol_curve", "seen_at")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        rec = dict(zip(keys, row, strict=True))
        v_sol = rec.get("v_sol_curve")
        rec["bonding_progress"] = min(1.0, max(0.0, float(v_sol) / GRADUATION_VSOL)) if v_sol is not None else None
        out[rec["mint"]] = rec
    return out
