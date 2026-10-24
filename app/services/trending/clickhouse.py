"""Factor extraction from our own ClickHouse (``wallet_memory``).

Every factor is measured, never inferred. A factor that has no coverage for a
chain is simply absent; the scorer omits it, renormalises the remaining
weights and the API reports the component as ``null`` with a status.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.services.trending import sql
from app.services.trending.config import QUOTE_TOKENS, SOLANA_QUOTES

logger = get_logger("trending.factors")

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _client() -> Any | None:
    from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client

    return _get_ch_client()


def _run(statement: str, params: dict[str, Any]) -> list[tuple]:
    client = _client()
    if client is None:
        return []
    try:
        return client.execute(statement, params)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("trending factor query failed: %s", str(exc).splitlines()[0][:180])
        return []


def _quotes(chain: str) -> tuple[str, ...]:
    return tuple(QUOTE_TOKENS.get(chain) or SOLANA_QUOTES)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _market_rows(chain: str, hours: int, limit: int) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    quotes = _quotes(chain)
    for tok, swaps, senders, top_swaps, volume in _run(
        sql._MARKET_SQL, {"chain": chain, "hours": hours, "limit": limit, "quotes": quotes}
    ):
        rows[tok] = {
            "chain": chain,
            "token_address": tok,
            "symbol": "",
            "swaps": int(swaps or 0),
            "senders": int(senders or 0),
            "top_sender_swaps": int(top_swaps or 0),
            "volume_usd": round(float(volume), 2) if volume else None,
            "sources": ["dex_events"],
        }
    return rows


def _enrich(chain: str, rows: dict[str, dict[str, Any]], hours: int) -> None:
    """Price momentum, bundles, transfer activity, symbol and launch age."""
    tokens = tuple(list(rows)[:200])
    if not tokens:
        return
    quotes = _quotes(chain)
    for tok, p_open, p_last in _run(
        sql._PRICE_MOVE_SQL, {"chain": chain, "hours": hours, "tokens": tokens, "quotes": quotes}
    ):
        if tok in rows and p_open:
            rows[tok]["price_change"] = round(float(p_last) / float(p_open) - 1.0, 6)
            rows[tok]["sources"].append("prices")
    for tok, blocks in _run(sql._BUNDLE_SQL, {"chain": chain, "hours": hours, "tokens": tokens, "quotes": quotes}):
        if tok in rows:
            rows[tok]["bundled_blocks"] = int(blocks or 0)
    for tok, symbol in _run(sql._SYMBOL_SQL, {"chain": chain, "tokens": tokens}):
        if tok in rows and symbol:
            rows[tok]["symbol"] = symbol
    for tok, launched_at in _run(sql._LAUNCH_SQL, {"chain": chain, "tokens": tokens}):
        if tok in rows:
            rows[tok]["launched_at"] = _iso(launched_at)
            rows[tok]["sources"].append("launches")
    for tok, t_now, t_prev, h_now, h_prev in _run(
        sql._ACTIVITY_SQL, {"chain": chain, "hours": hours, "window": hours * 2, "tokens": tokens}
    ):
        if tok in rows:
            rows[tok].update(
                {
                    "transfers_24h": int(t_now or 0),
                    "transfers_prev": int(t_prev or 0),
                    "holders_now": int(h_now or 0),
                    "holders_prev": int(h_prev or 0),
                }
            )
            rows[tok]["sources"].append("transfers")


def _solana_rows(chain: str, hours: int, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mint, venue, launch_signal, grad_signal, launched_at in _run(
        sql._SOLANA_SQL, {"window": hours * 2, "limit": limit}
    ):
        out.append(
            {
                "chain": chain,
                "token_address": mint,
                "symbol": "",
                "venue": venue or "",
                "launch_signal": float(launch_signal or 0),
                "graduation_signal": float(grad_signal or 0),
                "launched_at": _iso(launched_at),
                "sources": ["launchpad"],
            }
        )
    if out:
        mints = tuple(r["token_address"] for r in out)
        symbols = dict(_run(sql._SYMBOL_SQL, {"chain": chain, "tokens": mints}))
        for row in out:
            row["symbol"] = symbols.get(row["token_address"]) or ""
    return out


def fetch_factors(chain: str, hours: int, limit: int) -> list[dict[str, Any]]:
    """Measured factor rows for one chain, ranked by real swap count."""
    if chain in ("solana",):
        rows = _market_rows(chain, hours, limit)
        for row in _solana_rows(chain, hours, limit):
            existing = rows.get(row["token_address"])
            if existing is None:
                rows[row["token_address"]] = row
                continue
            existing.update(
                {k: v for k, v in row.items() if k in ("launch_signal", "graduation_signal", "launched_at", "venue")}
            )
            existing["sources"] = sorted({*existing.get("sources", []), "launchpad"})
        if not rows:
            return []
        _enrich(chain, rows, hours)
        return list(rows.values())
    rows = _market_rows(chain, hours, limit)
    if not rows:
        return []
    _enrich(chain, rows, hours)
    return list(rows.values())
