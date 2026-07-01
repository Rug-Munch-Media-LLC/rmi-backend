from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/protection", tags=["protection"])


@router.get("/health")
async def check_health() -> dict:
    """Check health of all protection modules.

    Returns:
        {"status": "ok|degraded|down", "modules": {...}}
    """
    status = "ok"
    modules = {
        "rag": {"status": "ok"},
        "scanner": {"status": "ok"},
        "blocklist": {"status": "ok"},
        "wallet_labels": {"status": "ok"},
        "entity_intel": {"status": "ok"},
    }

    # Check RAG
    try:
        from app.rag_service import search_similar

        await search_similar("health_check", "known_scams", limit=1, min_similarity=0.1)
    except Exception as e:
        modules["rag"]["status"] = "down"
        modules["rag"]["error"] = str(e)
        status = "degraded"

    # Check scanner
    try:
        from app.degen_security_scanner import DegenSecurityScanner

        scanner = DegenSecurityScanner()
        await scanner.scan_token("health_check", "solana")
    except Exception as e:
        modules["scanner"]["status"] = "down"
        modules["scanner"]["error"] = str(e)
        status = "degraded"

    # Check blocklist
    try:
        await get_blocklist()
    except Exception as e:
        modules["blocklist"]["status"] = "down"
        modules["blocklist"]["error"] = str(e)
        status = "degraded"

    # Check wallet labels
    try:
        from app.wallet_label_loader import lookup_wallet_label

        lookup_wallet_label("health_check", "solana")
    except Exception as e:
        modules["wallet_labels"]["status"] = "down"
        modules["wallet_labels"]["error"] = str(e)
        status = "degraded"

    # Check entity intel
    try:
        from app.entity_intel import get_entity_intel

        await get_entity_intel("health_check", "solana")
    except Exception as e:
        modules["entity_intel"]["status"] = "down"
        modules["entity_intel"]["error"] = str(e)
        status = "degraded"

    return {"status": status, "modules": modules}


async def check_url(url: str) -> dict:
    """Stub for URL checking."""
    return {"safe": True, "url": url}


async def check_wallet(address: str, chain: str = "solana") -> dict:
    """Stub for wallet checking."""
    return {"safe": True, "address": address, "chain": chain}


async def check_token(address: str, chain: str = "solana") -> dict:
    """Stub for token checking."""
    return {"safe": True, "address": address, "chain": chain, "risk_score": 0, "flags": []}


async def get_blocklist_domains() -> dict:
    """Stub for getting blocklist domains."""
    return {"domains": [], "last_updated": "2024-01-01T00:00:00Z"}


async def get_protection_health() -> dict:
    """Stub for getting protection health."""
    return {"status": "ok", "modules": {"protection": "ok"}}
