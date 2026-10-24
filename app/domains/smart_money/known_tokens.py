# ruff: noqa: S608 -- static chain/token constants only
"""Well-known token contracts that the metadata tables do not describe.

Measured 2026-10-23: ``wallet_memory.token_meta_ref`` has a NULL ``decimals``
for 1,219,344 of 1,219,504 rows and an empty ``symbol`` for 1,213,386 of them,
and ``token_prices`` has an empty symbol for the Optimism/Base WETH predeploy.
That made the highest-volume rows on those chains render ``symbol: null``.

Only protocol constants belong here - the address below is the OP-stack
predeploy that is WETH on both chains. Never add a token merely because its
metadata failed to load; that would be a guess dressed as data.
"""

from __future__ import annotations

KNOWN_TOKENS: dict[tuple[str, str], tuple[str, int]] = {
    ("base", "0x4200000000000000000000000000000000000006"): ("WETH", 18),
    ("optimism", "0x4200000000000000000000000000000000000006"): ("WETH", 18),
}

_COLUMNS = "chain String, token_address String, symbol String, decimals UInt8"


def known_cte() -> str:
    """Render the ``known`` CTE. Static literals only, never request data."""
    rows = ",\n".join(
        f"        ('{chain}', '{address}', '{symbol}', {decimals})"
        for (chain, address), (symbol, decimals) in sorted(KNOWN_TOKENS.items())
    )
    return f"known AS (\n    SELECT * FROM VALUES(  # noqa: S608\n        '{_COLUMNS}',\n{rows}\n    )\n)"
