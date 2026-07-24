"""
Liquidation Cascade — Core Analyzer
=====================================
LiquidationCascadeAnalyzer class — main analysis orchestration.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.analysis.liquidation.fetcher import (
    PROTOCOL_CONFIGS,
    SUPPORTED_CHAINS,
    _estimate_asset_ltv,
    _estimate_asset_price,
    _try_import_web3,
    _validate_address,
)
from app.domains.analysis.liquidation.models import (
    CollateralPosition,
    DebtPosition,
    LiquidationAnalysis,
    ProtocolPosition,
)

logger = get_logger(__name__)

class LiquidationCascadeAnalyzer:
    """Main analyzer class for liquidation cascade risk."""

    def __init__(self, rpc_overrides: dict[str, str] | None = None) -> None:
        self.rpc_overrides = rpc_overrides or {}
        self.web3_module = _try_import_web3()
        self._web3_instances: dict[str, Any] = {}

    def _get_web3(self, chain: str) -> Any | None:
        """Get or create a Web3 instance for a chain."""
        if not self.web3_module:
            return None
        if chain in self._web3_instances:
            return self._web3_instances[chain]
        chain_config = SUPPORTED_CHAINS.get(chain)
        if not chain_config:
            return None
        rpc = self.rpc_overrides.get(chain, chain_config["rpc"])
        try:
            w3 = self.web3_module.Web3(
                self.web3_module.HTTPProvider(rpc, request_kwargs={"timeout": 10})
            )
            if w3.is_connected():
                self._web3_instances[chain] = w3
                return w3
        except (ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e :
            logger.debug("_get_web3_error", exc_info=True)
            logger.warning(f"Failed to connect to {chain}: {e}")
        return None

    async def _scan_chain_balances(
        self, wallet: str, chain: str
    ) -> tuple[list[CollateralPosition], list[str]]:
        """Scan a wallet's token balances on a chain to detect collateral."""
        collateral: list[CollateralPosition] = []
        warnings: list[str] = []
        if not _validate_address(wallet):
            warnings.append(f"Invalid address: {wallet}")
            return collateral, warnings
        w3 = self._get_web3(chain)
        if not w3:
            warnings.append(f"No Web3 available for {chain}, using heuristic estimation")
            return collateral, warnings
        try:
            native_balance_wei = w3.eth.get_balance(wallet)
            native_symbol = "ETH" if chain != "bsc" else "BNB"
            if chain == "polygon":
                native_symbol = "MATIC"
            if native_balance_wei > 0:
                native_price = _estimate_asset_price(native_symbol)
                native_amount = float(w3.from_wei(native_balance_wei, "ether"))
                native_usd = native_amount * native_price
                ltv, liq_thresh = _estimate_asset_ltv(native_symbol)
                if native_usd > 1.0:
                    collateral.append(
                        CollateralPosition(
                            asset=native_symbol,
                            asset_address="0x0000000000000000000000000000000000000000",
                            amount_usd=native_usd, amount_token=native_amount,
                            ltv=ltv, liquidation_threshold=liq_thresh, price_usd=native_price,
                        )
                    )
        except (ConnectionError, TimeoutError, ValueError) as e :
            logger.debug("_scan_chain_balances_error", exc_info=True)
            warnings.append(f"Failed to fetch native balance on {chain}: {e}")
        return collateral, warnings

    async def _estimate_lending_positions(self, wallet: str, chain: str) -> ProtocolPosition | None:
        """Estimate lending positions for a wallet on a chain."""
        if not _validate_address(wallet):
            return None
        collateral, _warnings = await self._scan_chain_balances(wallet, chain)
        if not collateral:
            return None
        total_collateral = sum(c.amount_usd for c in collateral)
        debt_positions: list[DebtPosition] = []
        total_debt_usd = 0.0
        if total_collateral > 10000:
            estimated_debt = total_collateral * 0.40
            debt_positions.append(
                DebtPosition(
                    asset="USDC",
                    asset_address="0x0000000000000000000000000000000000000001",
                    amount_usd=estimated_debt, amount_token=estimated_debt, variable_rate=5.0,
                )
            )
            total_debt_usd = estimated_debt
        protocol_name = "Aave V3"
        if chain == "bsc":
            protocol_name = "Radiant V2"
        position = ProtocolPosition(
            protocol=protocol_name, chain=chain, wallet=wallet,
            collateral=collateral, debt=debt_positions,
            total_collateral_usd=total_collateral, total_debt_usd=total_debt_usd,
        )
        position.compute_health()
        return position

    async def _fetch_aave_position(
        self, wallet: str, chain: str, pool_address: str
    ) -> ProtocolPosition | None:
        """Fetch Aave V3 position using on-chain data."""
        w3 = self._get_web3(chain)
        if not w3:
            return None
        pool_abi = [{
            "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
            "name": "getUserAccountData",
            "outputs": [
                {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
                {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
                {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
                {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
                {"internalType": "uint256", "name": "ltv", "type": "uint256"},
                {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
            ],
            "stateMutability": "view", "type": "function",
        }]
        try:
            pool = w3.eth.contract(address=w3.to_checksum_address(pool_address), abi=pool_abi)
            try:
                data = pool.functions.getUserAccountData(wallet).call()
                total_collateral_raw, total_debt_raw = data[0], data[1]
                liq_threshold_raw, health_factor_raw = data[3], data[5]
            except (ConnectionError, OSError, TimeoutError):
                return None
            total_collateral_usd = total_collateral_raw / 1e18 if total_collateral_raw > 0 else 0.0
            total_debt_usd = total_debt_raw / 1e18 if total_debt_raw > 0 else 0.0
            liq_threshold_pct = liq_threshold_raw / 10000 if liq_threshold_raw > 0 else 0.0
            health_factor = health_factor_raw / 1e18 if health_factor_raw > 0 else None
            if total_collateral_usd <= 0:
                return None
            collateral_details = [
                CollateralPosition(
                    asset="MIXED",
                    asset_address="0x0000000000000000000000000000000000000000",
                    amount_usd=total_collateral_usd, amount_token=total_collateral_usd,
                    ltv=liq_threshold_pct * 0.95, liquidation_threshold=liq_threshold_pct,
                    price_usd=1.0,
                )
            ]
            debt_details = []
            if total_debt_usd > 0:
                debt_details.append(
                    DebtPosition(
                        asset="USDC",
                        asset_address="0x0000000000000000000000000000000000000001",
                        amount_usd=total_debt_usd, amount_token=total_debt_usd,
                        variable_rate=5.0,
                    )
                )
            position = ProtocolPosition(
                protocol="Aave V3", chain=chain, wallet=wallet,
                collateral=collateral_details, debt=debt_details,
                total_collateral_usd=total_collateral_usd, total_debt_usd=total_debt_usd,
                health_factor=health_factor,
            )
            position.compute_health()
            return position
        except (KeyError, TypeError) as e :
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"Aave V3 fetch error on {chain}: {e}")
            return None

    async def analyze(
        self,
        wallet: str,
        chains: list[str] | None = None,
        use_onchain: bool = True,
    ) -> LiquidationAnalysis:
        """Run full liquidation cascade analysis."""
        if chains is None:
            chains = list(SUPPORTED_CHAINS.keys())
        if not _validate_address(wallet):
            return LiquidationAnalysis(
                wallet=wallet, chains_analyzed=[],
                errors=[f"Invalid wallet address: {wallet}"],
            )
        analysis = LiquidationAnalysis(wallet=wallet, chains_analyzed=chains)
        for chain in chains:
            if chain not in SUPPORTED_CHAINS:
                analysis.warnings.append(f"Unsupported chain: {chain}")
                continue
            position: ProtocolPosition | None = None
            if (
                use_onchain and self.web3_module
                and chain in PROTOCOL_CONFIGS.get("aave_v3", {}).get("pools", {})
            ):
                pool_config = PROTOCOL_CONFIGS["aave_v3"]["pools"]
                if chain in pool_config:
                    position = await self._fetch_aave_position(wallet, chain, pool_config[chain])
            if position is None:
                position = await self._estimate_lending_positions(wallet, chain)
            if position and (position.total_collateral_usd > 0 or position.debt):
                analysis.positions.append(position)
        analysis.analyze()
        return analysis
