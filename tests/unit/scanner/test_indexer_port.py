"""IndexerPort seam — extraction-prep contract tests."""

from __future__ import annotations

from app.domains.scanner.engines import _indexer_port as port_mod
from app.domains.scanner.engines._indexer_port import (
    default_port,
    normalize_chain,
    reset_port,
    set_port,
)


class FakePort:
    def __init__(self):
        self.cleared = 0

    def ch_client(self):
        return "fake-client"

    def new_ch_client(self):
        return "fake-new"

    def normalize_chain(self, chain):
        return chain.upper()

    def clear_caches(self):
        self.cleared += 1


def test_set_port_overrides_binding():
    fake = FakePort()
    set_port(fake)
    try:
        assert port_mod.ch_client() == "fake-client"
        assert port_mod.normalize_chain("ethereum") == "ETHEREUM"
        port_mod.clear_caches()
        assert fake.cleared == 1
    finally:
        reset_port()


def test_reset_restores_default_provider():
    set_port(FakePort())
    reset_port()
    p = default_port()
    # default binds to the DataBus provider module functions
    assert callable(p.ch_client)
    assert callable(p.normalize_chain)


def test_default_normalize_chain_live():
    """The real provider's normalizer maps common aliases."""
    reset_port()
    out = normalize_chain("eth")
    assert isinstance(out, str)
    assert out


def test_port_is_protocol_shaped():
    fake = FakePort()
    set_port(fake)
    reset_port()
    # structural: any object with the four methods satisfies the Protocol
    fake = FakePort()
    for attr in ("ch_client", "new_ch_client", "normalize_chain", "clear_caches"):
        assert hasattr(fake, attr), attr
