"""
x402 Facilitator Startup Module
================================
Registers all facilitators with the global registry at app startup.
Called from main.py app startup handler.

Usage (in main.py):
    from app.domains.billing.facilitators.startup import register_all_facilitators
    await register_all_facilitators()
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger("facilitator.startup")


async def register_all_facilitators() -> None:
    """
    Register all available facilitators with the global registry.
    Skips facilitators whose required config (API keys) is missing.

    Called once at app startup.
    """
    from app.domains.billing.facilitators.base import get_registry
    from app.domains.billing.facilitators.config import get_config

    registry = get_registry()
    get_config()

    # ── 1. Primev FastRPC (fee-free, highest priority for Ethereum) ──
    try:
        from app.domains.billing.facilitators.primev import PrimevFacilitator

        registry.register(PrimevFacilitator())
        logger.info("Registered: Primev FastRPC (fee-free Ethereum)")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("register_all_facilitators_error", exc_info=True)
        logger.warning(f"Failed to register Primev: {e}")

    # ── 2. Coinbase CDP (highest priority for Base) ──
    # Always register - health check will mark unavailable if no API key
    try:
        from app.domains.billing.facilitators.coinbase_cdp import CoinbaseCDPFacilitator

        registry.register(CoinbaseCDPFacilitator())
        logger.info("Registered: Coinbase CDP (Base + Solana, fee-free)")
    except (ConnectionError, TimeoutError, ValueError) as e:
        logger.debug("register_all_facilitators_error", exc_info=True)
        logger.warning(f"Failed to register Coinbase CDP: {e}")

    # ── 3. BNB Pieverse (BNB Chain) - OFFLINE: api.pieverse.xyz NXDOMAIN ──
    # Skipped: dead facilitator, DNS does not resolve
    # try:
    #     from app.domains.billing.facilitators.pieverse import PieverseFacilitator
    #     registry.register(PieverseFacilitator())
    #     logger.info("Registered: BNB Pieverse (BNB Chain, instant)")
    # except Exception as e:
    #     logger.warning(f"Failed to register Pieverse: {e}")
    logger.info("SKIPPED: BNB Pieverse - OFFLINE (api.pieverse.xyz NXDOMAIN)")

    # ── 4. MERX TRON - OFFLINE: api.merx.finance NXDOMAIN ──
    # Skipped: dead facilitator, DNS does not resolve
    # try:
    #     from app.domains.billing.facilitators.merx_tron import MerxTronFacilitator
    #     registry.register(MerxTronFacilitator())
    #     logger.info("Registered: MERX x402 for TRON (USDT/USDC/USDD, sub-3s)")
    # except Exception as e:
    #     logger.warning(f"Failed to register MERX TRON: {e}")
    logger.info("SKIPPED: MERX TRON - OFFLINE (api.merx.finance NXDOMAIN)")

    # ── 4b. TRON Self-Verify (replaces dead MERX - uses TronGrid API) ──
    try:
        from app.domains.billing.facilitators.tron_selfverify import TronSelfVerifyFacilitator

        tron_fac = TronSelfVerifyFacilitator()
        if tron_fac._tron_pay_to:
            registry.register(tron_fac)
            logger.info("Registered: TRON Self-Verify (USDT/USDC/USDD via TronGrid, fee-free)")
        else:
            logger.info("TRON Self-Verify: skipped (no X402_TRON_PAY_TO wallet configured)")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Failed to register TRON Self-Verify: {e}")

    # ── 5. PayAI (Base + Solana, always available - no key needed) ──
    try:
        from app.domains.billing.facilitators.payai import PayAIFacilitator

        registry.register(PayAIFacilitator())
        logger.info("Registered: PayAI (Base + Solana, deferred)")
    except (ConnectionError, TimeoutError, ValueError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Failed to register PayAI: {e}")

    # ── 6. AsterPay (EUR/SEPA Europe) ──
    # Always register - health check marks unavailable if no key
    try:
        from app.domains.billing.facilitators.asterpay import AsterPayFacilitator

        registry.register(AsterPayFacilitator())
        logger.info("Registered: AsterPay (European, EUR/SEPA off-ramp)")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Failed to register AsterPay: {e}")

    # ── 7. Cloudflare x402 (Base Sepolia fallback) ──
    try:
        from app.domains.billing.facilitators.cloudflare_x402 import CloudflareX402Facilitator

        registry.register(CloudflareX402Facilitator())
        logger.info("Registered: Cloudflare x402 (Base Sepolia/Ethereum, deferred)")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Failed to register Cloudflare x402: {e}")

    # ── 8. Satoshi (Bitcoin) - OFFLINE: api.satoshi.dev NXDOMAIN ──
    # Skipped: dead facilitator, DNS does not resolve
    # try:
    #     from app.domains.billing.facilitators.satoshi import SatoshiFacilitator
    #     registry.register(SatoshiFacilitator())
    #     logger.info("Registered: Satoshi Facilitator (Bitcoin → Base/Solana)")
    # except Exception as e:
    #     logger.warning(f"Failed to register Satoshi: {e}")
    logger.info("SKIPPED: Satoshi - OFFLINE (api.satoshi.dev NXDOMAIN)")

    # ── 8b. Bitcoin Self-Verify (replaces dead Satoshi - uses Mempool.space API) ──
    try:
        from app.domains.billing.facilitators.bitcoin_selfverify import BitcoinSelfVerifyFacilitator

        btc_fac = BitcoinSelfVerifyFacilitator()
        if btc_fac._btc_pay_to:
            registry.register(btc_fac)
            logger.info("Registered: Bitcoin Self-Verify (BTC via Mempool.space, fee-free)")
        else:
            logger.info("Bitcoin Self-Verify: skipped (no X402_BTC_PAY_TO wallet configured)")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Failed to register Bitcoin Self-Verify: {e}")

    # ── 9. Self-hosted x402-rs (optional, check if container is running) ──
    try:
        from app.domains.billing.facilitators.x402_rs import X402RsFacilitator

        facilitator = X402RsFacilitator()
        # Only register if container is reachable
        healthy = await facilitator.health()
        if healthy:
            registry.register(facilitator)
            logger.info("Registered: x402-rs (self-hosted, multi-chain)")
        else:
            logger.info("x402-rs: container not running - skipped")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.info(f"x402-rs: not available - {e}")

    # ── 10. EIP-7702 Universal EVM (always available - no key needed) ──
    try:
        from app.domains.billing.facilitators.eip7702 import EIP7702Facilitator

        registry.register(EIP7702Facilitator())
        logger.info("Registered: EIP-7702 Universal EVM (all chains, all tokens)")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Failed to register EIP-7702: {e}")

    # ── Summary ─────────────────────────────────────────────────
    all_f = registry.get_all()
    coverage = registry.chain_coverage
    logger.info(
        f"x402 Facilitator Registry: {len(all_f)} facilitators loaded, "
        f"covering {len(coverage)} chains: {list(coverage.keys())}"
    )
    for f in all_f:
        logger.info(
            f"  - {f.name} ({f.facilitator_type.value}/{f.settlement_type.value}): "
            f"{f.supported_networks.chains} [priority={f.priority}]"
        )
