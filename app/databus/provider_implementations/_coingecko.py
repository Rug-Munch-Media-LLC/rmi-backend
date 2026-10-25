"""CoinGecko price provider — reference implementation of the provider contract."""

from __future__ import annotations

from typing import Any

from app.databus.provider_contract import DatabusProvider, ProviderConfigError, ProviderContractError


class CoinGeckoPriceProvider(DatabusProvider):
    """CoinGecko simple token price. FREEMIUM: works without a key, better with one."""

    name = "coingecko_price"
    base_url = "https://api.coingecko.com/api/v3"
    timeout = 10.0
    tier = "freemium"
    requires_key = False
    key_env = "COINGECKO_API_KEY"

    async def _call(self, *, mint: str = "", token: str = "", **_: Any) -> dict[str, Any] | None:
        asset = mint or token
        if not asset:
            raise ProviderConfigError(self.name, "missing mint/token argument")
        headers = {"x-cg-pro-api-key": self.api_key} if self.api_key else {}
        resp = await self._get(f"/simple/token_price/{asset}", headers=headers)
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderContractError(self.name, "response is not JSON") from exc
        if not isinstance(data, dict):
            raise ProviderContractError(self.name, f"expected object, got {type(data).__name__}")
        return {"token": asset, "prices": data, "source": self.name}
