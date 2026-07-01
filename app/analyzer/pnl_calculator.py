"""
Token-level PnL calculator.
Calculates profit/loss when user provides their average buy price.
"""


def calculate_token_pnl(token: dict, avg_cost: float) -> dict:
    """
    Calculate PnL for a specific token given user's average buy price.

    Args:
        token: Enriched token dict from build_portfolio()
        avg_cost: User's average buy price in USD

    Returns:
        {
          "symbol": str,
          "qty": float,
          "avg_cost": float,
          "current_price": float,
          "cost_basis": float,
          "current_value": float,
          "pnl_usd": float,
          "pnl_pct": float,
          "is_profit": bool,
        }
    """
    qty = token.get("qty", 0)
    current_price = token.get("price", 0)

    cost_basis = avg_cost * qty
    current_value = current_price * qty
    pnl_usd = current_value - cost_basis
    pnl_pct = (pnl_usd / cost_basis * 100) if cost_basis > 0 else 0.0

    return {
        "symbol": token.get("symbol", "?"),
        "name": token.get("name", "?"),
        "qty": qty,
        "avg_cost": avg_cost,
        "current_price": current_price,
        "cost_basis": cost_basis,
        "current_value": current_value,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "is_profit": pnl_usd >= 0,
    }
