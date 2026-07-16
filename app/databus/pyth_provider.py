"""
Pyth Network Price Feeds Provider
=================================

This provider integrates with Pyth Network's Hermes API to fetch real-time price feeds
for cryptocurrencies and other financial assets.

Pyth provides high-quality, real-time price feeds from 120+ first-party providers
including leading exchanges, banks, and trading venues.

API Documentation:
- https://docs.pyth.network/price-feeds
- https://pyth.dourolabs.app/docs/?urls.primaryName=Hermes+API
"""

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Pyth Hermes API endpoint
PYTH_HERMES_BASE_URL = "https://hermes.pyth.network"


class PythPriceFeedProvider:
    """Pyth Network price feed provider"""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_price_feeds_list(self) -> dict[str, Any]:
        """
        Get the list of all available price feeds from Pyth Network.

        Returns:
            Dict containing price feeds metadata
        """
        try:
            response = await self.client.get(f"{PYTH_HERMES_BASE_URL}/v2/price_feeds")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching price feeds list: {e}")
            return {}

    async def get_latest_price_updates(self, price_feed_ids: list) -> dict[str, Any]:
        """
        Get the latest price updates for specified price feed IDs.

        Args:
            price_feed_ids: List of price feed IDs to fetch

        Returns:
            Dict containing price updates
        """
        if not price_feed_ids:
            return {}

        try:
            # Build query parameters
            params = []
            for feed_id in price_feed_ids:
                # Remove 0x prefix if present
                clean_id = feed_id.replace("0x", "") if feed_id.startswith("0x") else feed_id
                params.append(f"ids[]={clean_id}")

            query_string = "&".join(params)
            url = f"{PYTH_HERMES_BASE_URL}/v2/updates/price/latest?{query_string}"

            logger.info(f"Fetching price updates from: {url}")
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching price updates: {e}")
            return {}

    async def get_single_price_feed(self, price_feed_id: str) -> dict[str, Any]:
        """
        Get a single price feed by ID.

        Args:
            price_feed_id: Price feed ID

        Returns:
            Dict containing price feed data
        """
        try:
            # Remove 0x prefix if present
            clean_id = (
                price_feed_id.replace("0x", "") if price_feed_id.startswith("0x") else price_feed_id
            )
            url = f"{PYTH_HERMES_BASE_URL}/v2/updates/price/latest?ids[]={clean_id}"

            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching single price feed: {e}")
            return {}

    async def parse_price_data(self, price_feed_id: str) -> dict[str, Any] | None:
        """
        Parse price data for a specific feed into a standardized format.

        Args:
            price_feed_id: Price feed ID

        Returns:
            Dict with parsed price data or None if error
        """
        try:
            data = await self.get_single_price_feed(price_feed_id)

            if not data or "parsed" not in data or not data["parsed"]:
                logger.warning(f"No data returned for price feed {price_feed_id}")
                return None

            feed_data = data["parsed"][0]

            # Extract price information
            price_info = feed_data.get("price", {})
            price = price_info.get("price")
            conf = price_info.get("conf")
            expo = price_info.get("expo")
            publish_time = price_info.get("publish_time")

            # Convert price to decimal format
            if price and expo:  # noqa: SIM108
                # Price is stored as integer with exponent
                price_decimal = int(price) * (10**expo)
            else:
                price_decimal = None

            return {
                "id": feed_data.get("id"),
                "price": price_decimal,
                "price_raw": price,
                "confidence": conf,
                "exponent": expo,
                "publish_time": publish_time,
                "timestamp": publish_time,
            }
        except Exception as e:
            logger.error(f"Error parsing price data for {price_feed_id}: {e}")
            return None

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


# Provider functions for DataBus integration
async def _pyth_price_feed_list(**kwargs) -> dict[str, Any]:
    """Get list of all Pyth price feeds"""
    provider = PythPriceFeedProvider()
    try:
        result = await provider.get_price_feeds_list()
        # Return in the expected DataBus format
        return {"source": "pyth", "data": result, "count": len(result) if result else 0}
    finally:
        await provider.close()


async def _pyth_latest_price_updates(price_feed_ids: list, **kwargs) -> dict[str, Any]:
    """Get latest price updates for specified feeds"""
    provider = PythPriceFeedProvider()
    try:
        result = await provider.get_latest_price_updates(price_feed_ids)
        return result
    finally:
        await provider.close()


async def _pyth_single_price_feed(price_feed_id: str, **kwargs) -> dict[str, Any]:
    """Get a single price feed by ID"""
    provider = PythPriceFeedProvider()
    try:
        result = await provider.get_single_price_feed(price_feed_id)
        return result
    finally:
        await provider.close()


async def _pyth_parsed_price(price_feed_id: str, **kwargs) -> dict[str, Any]:
    """Get parsed price data for a single feed"""
    provider = PythPriceFeedProvider()
    try:
        result = await provider.parse_price_data(price_feed_id)
        # Return in the expected DataBus format
        return {"source": "pyth", "data": result} if result else None
    finally:
        await provider.close()


# Example usage functions
async def get_bitcoin_price_feed() -> dict[str, Any]:
    """
    Get Bitcoin/USD price feed.
    This is just an example - you would need to find the actual Pyth ID for BTC/USD
    """
    # This is a placeholder - actual BTC/USD feed ID would need to be looked up
    btc_usd_feed_id = "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b41"
    return await _pyth_parsed_price(btc_usd_feed_id)


async def get_ethereum_price_feed() -> dict[str, Any]:
    """
    Get Ethereum/USD price feed.
    This is just an example - you would need to find the actual Pyth ID for ETH/USD
    """
    # This is a placeholder - actual ETH/USD feed ID would need to be looked up
    eth_usd_feed_id = "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace"
    return await _pyth_parsed_price(eth_usd_feed_id)


if __name__ == "__main__":
    # Test the provider
    async def test_provider():
        provider = PythPriceFeedProvider()
        try:
            # Test getting price feeds list
            logger.info("Getting price feeds list...")
            feeds = await provider.get_price_feeds_list()
            logger.info(f"Found {len(feeds)} price feeds")
            if feeds:
                # Test getting a single feed (using the first one as example)
                first_feed_id = feeds[0]["id"]
                logger.info(f"\nGetting price feed for ID: {first_feed_id}")
                price_data = await provider.parse_price_data(first_feed_id)
                logger.info(f"Price data: {price_data}")
        finally:
            await provider.close()

    # Run the test
    asyncio.run(test_provider())
