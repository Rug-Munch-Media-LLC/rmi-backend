"""Keywords and token extraction for prediction markets."""
from __future__ import annotations

_COMMON_TOKENS = {"BTC","ETH","SOL","USDT","USDC","DAI","BNB","XRP","ADA","MATIC","POL","DOT","AVAX","LINK","UNI","AAVE","ARB","OP","SUI","APT","TIA","SEI","STRK","WLD","PEPE","SHIB","BONK","WIF","JUP","PYTH","RNDR","FET","AGIX","OCEAN","IMX","INJ","EIGEN","ENA","ETHFI"}
_COMMON_PROJECTS = {"polymarket","kalshi","manifold","uniswap","sushiswap","aave","compound","makerdao","maker","lido","eigenlayer","chainlink","arbitrum","optimism","polygon","avalanche","fantom","near","celestia","worldcoin","tether","circle","coinbase","binance","kraken","ftx","celcius","blockfi","three arrows","alameda","jump crypto","wintermute","curve","balancer","thorchain","osmosis","dydx","gmx","hyperliquid","jupiter","raydium","orca","wormhole","layerzero","zksync","starknet","scroll","linea","base","mantle","mode","blast"}

def _extract_token_symbols(text: str) -> list[str]:
    found = []
    text_upper = text.upper()
    text_lower = text.lower()
    for token in _COMMON_TOKENS:
        if (f" {token} " in f" {text_upper} " or f"${token}" in text_upper or text_upper.startswith(f"{token} ") or text_upper.endswith(f" {token}")) and token not in found:
            found.append(token)
    for project in _COMMON_PROJECTS:
        if project in text_lower and project.upper() not in found:
            found.append(project)
    return found
