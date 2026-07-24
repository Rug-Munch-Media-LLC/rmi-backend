"""Download helpers for wallet label datasets."""

import json
import os

import httpx

from app.core.logging import get_logger
from .config import LABELS_DIR, SOLANA_LABELS_REPO, BSC_LABELS_REPO, ETH_EXCHANGE_LABELS_REPO, ETH_CLUSTER_LABELS_REPO, ETH_CONTRACT_LABELS_REPO, SOLANA_CSV_SPECS, ETH_CSV_SPECS, _EXCHANGE_WALLET_FILES

logger = get_logger("wallet_memory.label_importer")

_http_client: httpx.AsyncClient | None = None


async def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "RMI-LabelImporter/1.0"},
        )
    return _http_client


async def _download_file(url: str, dest: str) -> bool:
    """Download a URL to dest path. Returns True on success."""
    try:
        client = await _get_http()
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"Download failed {url}: HTTP {resp.status_code}")
            return False
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(resp.content)
        logger.info(f"Downloaded {url} -> {dest} ({len(resp.content)} bytes)")
        return True
    except Exception as e:
        logger.warning(f"Download error {url}: {e}")
        return False


async def _download_json(url: str):
    """Download and parse JSON from URL. Returns None on failure."""
    try:
        client = await _get_http()
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"JSON download failed {url}: HTTP {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        logger.warning(f"JSON download error {url}: {e}")
        return None


async def _ensure_solana_labels() -> dict[str, int]:
    """Download Solana label CSVs if not present."""
    os.makedirs(LABELS_DIR, exist_ok=True)
    results = {}
    for filename, _, _, _ in SOLANA_CSV_SPECS:
        dest = os.path.join(LABELS_DIR, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            results[filename] = os.path.getsize(dest)
        else:
            url = f"{SOLANA_LABELS_REPO}/{filename}"
            ok = await _download_file(url, dest)
            results[filename] = os.path.getsize(dest) if ok else 0
    return results


async def _ensure_eth_scam_labels() -> dict[str, int]:
    """Download ETH phishing/scam CSVs if not present."""
    eth_scam_repo = (
        "https://raw.githubusercontent.com/ImMike/"
        "crypto-wallet-address-labels/main/datasets/ethereum-phishing-and-scam-labels"
    )
    os.makedirs(LABELS_DIR, exist_ok=True)
    results = {}
    for filename, _, _, _ in ETH_CSV_SPECS:
        dest = os.path.join(LABELS_DIR, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            results[filename] = os.path.getsize(dest)
        else:
            url = f"{eth_scam_repo}/{filename}"
            ok = await _download_file(url, dest)
            results[filename] = os.path.getsize(dest) if ok else 0
    return results


async def _ensure_bsc_labels() -> bool:
    """Download BSC detail CSV if not present."""
    dest = os.path.join(LABELS_DIR, "2024-01-31-detail.csv")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    url = f"{BSC_LABELS_REPO}/2024-01-31-detail.csv"
    return await _download_file(url, dest)


async def _ensure_exchange_cluster_labels() -> bool:
    """Download exchange cluster CSV."""
    dest = os.path.join(LABELS_DIR, "exchanges_cluster.csv")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    url = f"{ETH_CLUSTER_LABELS_REPO}/exchanges.csv"
    return await _download_file(url, dest)


async def _ensure_contract_name_labels() -> bool:
    """Download contract name dict."""
    dest = os.path.join(LABELS_DIR, "name_dict.txt")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    url = f"{ETH_CONTRACT_LABELS_REPO}/name_dict.txt"
    return await _download_file(url, dest)


async def _ensure_exchange_wallet_labels() -> dict[str, int]:
    """Download all exchange/DeFi wallet label CSVs."""
    os.makedirs(LABELS_DIR, exist_ok=True)
    results = {}
    for filename, _, _ in _EXCHANGE_WALLET_FILES:
        dest = os.path.join(LABELS_DIR, filename)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            results[filename] = os.path.getsize(dest)
        else:
            url = f"{ETH_EXCHANGE_LABELS_REPO}/{filename}"
            ok = await _download_file(url, dest)
            results[filename] = os.path.getsize(dest) if ok else 0
    return results
