"""Sentiment and trending checks — social metrics, market sentiment signals."""

from __future__ import annotations

from typing import Any

from app.domains.scanner.modules._core import logger


async def _check_nansen(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Nansen data for token."""
    logger.info("nansen_check", token=token_address, chain=chain)
    return {"check": "nansen", "result": "pending"}


async def _check_chainaware(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check ChainAware data for token."""
    logger.info("chainaware_check", token=token_address, chain=chain)
    return {"check": "chainaware", "result": "pending"}


async def _check_santiment(symbol: str) -> dict[str, Any] | None:
    """Check Santiment for token."""
    logger.info("santiment_check", symbol=symbol)
    return {"check": "santiment", "result": "pending"}


async def _check_fear_greed() -> dict[str, Any] | None:
    """Check Crypto Fear & Greed index."""
    logger.info("fear_greed_check")
    return {"check": "fear_greed", "result": "pending"}


async def _check_news_sentiment(symbol: str) -> dict[str, Any] | None:
    """Check news sentiment for token."""
    logger.info("news_sentiment_check", symbol=symbol)
    return {"check": "news", "result": "pending"}


async def _check_augmento(symbol: str) -> dict[str, Any] | None:
    """Check Augmento for token."""
    logger.info("augmento_check", symbol=symbol)
    return {"check": "augmento", "result": "pending"}


async def _check_coingecko_trending() -> dict[str, Any] | None:
    """Check CoinGecko trending tokens."""
    logger.info("coingecko_trending")
    return {"check": "trending", "result": "pending"}


async def _check_dune(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check Dune analytics for token."""
    logger.info("dune_check", token=token_address, chain=chain)
    return {"check": "dune", "result": "pending"}


async def _check_arkham(address: str, chain: str) -> dict[str, Any] | None:
    """Check Arkham for address."""
    logger.info("arkham_check", address=address, chain=chain)
    return {"check": "arkham", "result": "pending"}


async def _check_thegraph(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check The Graph for token."""
    logger.info("thegraph_check", token=token_address, chain=chain)
    return {"check": "thegraph", "result": "pending"}
