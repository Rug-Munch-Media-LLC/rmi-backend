"""
Portfolio aggregator: calculates total value and 24h change from holdings.
"""


def build_portfolio(holdings: list) -> dict:
    """
    Aggregate token holdings into a portfolio summary.

    Args:
        holdings: Raw list from get_wallet_holdings()

    Returns:
        {
          "tokens": [...enriched tokens with usd_value, qty],
          "total_value": float,
          "change_24h_usd": float,
          "change_24h_pct": float,
          "token_count": int,
        }
    """
    tokens = []
    total_value = 0.0
    total_value_yesterday = 0.0

    for item in holdings:
        price = float(item.get("price") or 0)
        qty_raw = item.get("remainQty") or "0"
        qty = float(qty_raw) if qty_raw else 0.0
        change_24h = float(item.get("percentChange24h") or 0)

        usd_value = price * qty
        if usd_value < 0.01:
            continue  # skip dust

        usd_value_yesterday = usd_value / (1 + change_24h / 100) if change_24h != -100 else usd_value

        total_value += usd_value
        total_value_yesterday += usd_value_yesterday

        tokens.append(
            {
                "symbol": item.get("symbol", "?"),
                "name": item.get("name", "?"),
                "contractAddress": item.get("contractAddress", ""),
                "qty": qty,
                "price": price,
                "usd_value": usd_value,
                "change_24h_pct": change_24h,
                "risk_level": item.get("riskLevel", "UNKNOWN"),
            }
        )

    tokens.sort(key=lambda t: t["usd_value"], reverse=True)

    change_24h_usd = total_value - total_value_yesterday
    change_24h_pct = (change_24h_usd / total_value_yesterday * 100) if total_value_yesterday > 0 else 0.0

    return {
        "tokens": tokens,
        "total_value": total_value,
        "change_24h_usd": change_24h_usd,
        "change_24h_pct": change_24h_pct,
        "token_count": len(tokens),
    }
