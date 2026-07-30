"""Fuzz tests for blockchain address validation."""

import pytest
from hypothesis import given, settings, strategies as st

# Generate valid-looking hex addresses
hex_address = st.text(
    alphabet="0123456789abcdefABCDEFx",
    min_size=10,
    max_size=100,
)


@given(address=hex_address)
@settings(max_examples=500, deadline=1000)
def test_eth_address_validation_never_crashes(address: str) -> None:
    """ETH address validation should never crash on any hex string."""
    try:
        from app.services.web3_chat import validate_address

        validate_address(address, "ethereum")
    except ImportError:
        pytest.skip("validate_address not available from app.services.web3_chat")
    except (ValueError, TypeError):
        pass
    except Exception as e:
        pytest.fail(f"Unexpected crash on address {address!r}: {e}")


@given(
    chain=st.sampled_from(["eth", "bsc", "polygon", "arbitrum", "solana", "lightning"]),
    address=st.text(min_size=1, max_size=200),
)
@settings(max_examples=300, deadline=2000)
def test_multi_chain_validation_resilient(chain: str, address: str) -> None:
    """Multi-chain address validation must handle any chain+address combo."""
    try:
        from app.services.web3_chat import validate_address

        validate_address(address, chain)
    except ImportError:
        pytest.skip("validate_address not available")
    except (ValueError, TypeError, NotImplementedError):
        pass
    except Exception as e:
        pytest.fail(f"Unexpected crash: chain={chain}, address={address!r}: {e}")


@given(
    address=st.text(alphabet="0123456789abcdefABCDEFx:", min_size=1, max_size=100),
)
@settings(max_examples=500, deadline=1000)
def test_liquidation_address_validator_never_crashes(address: str) -> None:
    """Liquidation analyzer's _validate_address must handle any input."""
    try:
        from app.domains.analysis.liquidation.fetcher import _validate_address

        _validate_address(address)
    except ImportError:
        pytest.skip("_validate_address not available from liquidation.fetcher")
    except (ValueError, TypeError):
        pass
    except Exception as e:
        pytest.fail(f"Unexpected crash on address {address!r}: {e}")


@given(address=st.text(min_size=1, max_size=200))
@settings(max_examples=200, deadline=1000)
def test_bundler_detector_validate_never_crashes(address: str) -> None:
    """Bundler detector's _validate_address must handle any input."""
    try:
        from app.bundler_detect.detector import BundlerDetector

        detector = BundlerDetector()
        detector._validate_address(address, "ethereum")
    except ImportError:
        pytest.skip("BundlerDetector not available")
    except (ValueError, TypeError):
        pass
    except Exception as e:
        pytest.fail(f"BundlerDetector crash on address {address!r}: {e}")
