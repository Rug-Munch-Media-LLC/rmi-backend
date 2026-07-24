"""Deployment record and parameter data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class TokenDeployment:
    """Record of a deployed token."""

    deployment_id: str
    chain: str
    name: str
    symbol: str
    decimals: int
    total_supply: str
    contract_address: str
    deployer_address: str
    tx_hash: str
    block_number: int | None = None
    status: str = "deployed"
    owner_address: str = ""
    mintable: bool = True
    burnable: bool = True
    pausable: bool = False
    blacklist_enabled: bool = False
    max_wallet_limit: str | None = None
    max_tx_limit: str | None = None
    trading_enabled: bool = True
    anti_bot_delay: int = 0
    max_supply: str | None = None
    metadata_uri: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at
        d["updated_at"] = self.updated_at
        return d

@dataclass
class DeployParams:
    """Parameters for token deployment."""

    chain: str
    name: str
    symbol: str
    decimals: int = 18
    initial_supply: str = "1000000"
    max_supply: str | None = None
    mintable: bool = True
    burnable: bool = True
    pausable: bool = False
    owner_address: str | None = None
    metadata_uri: str | None = None
    evm_version: str = "paris"
    freeze_authority: str | None = None
    token_type: str = "trc20"
    tax_rate: float | None = None
    tax_recipient: str | None = None
    deflationary: bool = False
    blacklist_enabled: bool = True
    blacklist_addresses: list[str] = field(default_factory=list)
    max_wallet_limit: str | None = None
    max_tx_limit: str | None = None
    trading_enabled: bool = True
    anti_bot_delay: int = 0
