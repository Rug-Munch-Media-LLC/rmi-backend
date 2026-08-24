"""IndexerPort — the scanner's contract with lite-chain-indexer.

Extraction-prep seam (scanner-as-own-repo, phase 0): every indexer
touchpoint in ``engines/`` funnels through the small surface defined
here. Today the adapter is the DataBus provider module
(``app.domains.databus.providers.chains.lite_chain_indexer``); after a
repo split the adapter becomes an HTTP/gRPC client against the indexer
service and NOTHING in ``engines/`` changes.

Rules for engines/ code:
    * import indexer access ONLY from this module
    * never import the provider module directly
    * new indexer capabilities: add a method to ``IndexerPort`` + wire it
      in ``default_port()`` — do not reach around the port

The port is intentionally narrow: a ClickHouse client factory, chain-name
normalization, and cache invalidation. Query helpers stay in engines/
because they are scanner domain logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class IndexerPort(Protocol):
    """What the scanner needs from lite-chain-indexer. Nothing more."""

    def ch_client(self) -> Any:
        """Thread-safe ClickHouse client (per-thread connection)."""
        ...

    def new_ch_client(self) -> Any:
        """Fresh client for one-off/parallel queries."""
        ...

    def normalize_chain(self, chain: str) -> str:
        """Map user-facing chain names to indexer storage names."""
        ...

    def clear_caches(self) -> None:
        """Invalidate provider-level caches (label pools etc.)."""
        ...


@dataclass(frozen=True)
class _ProviderAdapter:
    """Adapter binding the port to the current DataBus provider module.

    Lazy attribute access via callables keeps import cost at first use
    (the provider module pulls clickhouse_driver).
    """

    _ch_client: Callable[[], Any]
    _new_ch_client: Callable[[], Any]
    _normalize_chain: Callable[[str], str]
    _clear_caches: Callable[[], Any]

    def ch_client(self) -> Any:
        return self._ch_client()

    def new_ch_client(self) -> Any:
        return self._new_ch_client()

    def normalize_chain(self, chain: str) -> str:
        return self._normalize_chain(chain)

    def clear_caches(self) -> None:
        self._clear_caches()


_adapter: IndexerPort | None = None


def default_port() -> IndexerPort:
    """Bind the port to the DataBus provider (current implementation)."""
    global _adapter
    if _adapter is None:
        from app.domains.databus.providers.chains import (
            lite_chain_indexer as _provider,
        )

        _adapter = _ProviderAdapter(
            _ch_client=_provider._get_ch_client,
            _new_ch_client=getattr(_provider, "_new_ch_client", _provider._get_ch_client),
            _normalize_chain=_provider._normalize_chain,
            _clear_caches=getattr(_provider, "_common_clear_cache", lambda: None),
        )
    return _adapter


def set_port(port: IndexerPort) -> None:
    """Override the port (tests, future HTTP adapter)."""
    global _adapter
    _adapter = port


def reset_port() -> None:
    """Back to the default provider binding."""
    global _adapter
    _adapter = None


# ── Convenience accessors (the only sanctioned imports for engines/) ──


def ch_client() -> Any:
    return default_port().ch_client()


def new_ch_client() -> Any:
    return default_port().new_ch_client()


def normalize_chain(chain: str) -> str:
    return default_port().normalize_chain(chain)


def clear_caches() -> None:
    default_port().clear_caches()


# ── Transitional aliases (migration period) ───────────────────────────
# Engines historically used the provider's private names. These aliases
# keep every call site working while call sites migrate to the public
# names above. Do NOT use in new code.
_get_ch_client = ch_client
_new_ch_client = new_ch_client
_normalize_chain = normalize_chain
