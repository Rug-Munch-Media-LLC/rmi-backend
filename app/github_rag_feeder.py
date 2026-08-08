"""
RAG GitHub Feeder v2 - Declarative, Config-Driven
==================================================
Add any GitHub repo as a RAG data source with minimal config.
Auto-clones, extracts markdown/solidity/text, chunks, and ingests.

DEPRECATED (v3): this standalone feeder targeted the legacy RAG ingest
endpoint (``http://localhost:8000/api/v1/rag/ingest``) which was removed.
The live facade is now ``/api/v1/rag/v2/ingest`` / ``/api/v1/rag/v2/bulk-ingest``
(see ``app/api/v1/rag/search.py``), with a different payload shape. This
module is not imported anywhere and is kept only as a historical reference;
``ingest_repo`` is a no-op ``return`` guard so it never calls a dead endpoint.

Config format (add to SOURCES list):
{
    "repo": "owner/repo",
    "collection": "defi_hacks|vuln_patterns|contract_audits|...",
    "category": "defi_hack|audit|scam_report|...",
    "tags": ["tag1", "tag2"],
    "file_patterns": ["*.md", "*.sol", "*.py"],  # files to extract
    "max_files": 50,  # limit per run
    "enabled": True,
}
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# DEPRECATED (v3): legacy RAG ingest endpoint removed — constant dropped.
# DO NOT restore; use app/api/v1/rag/search.py (POST /api/v1/rag/v2/ingest).
BACKEND = "http://localhost:8000"

# ═══════════════════════════════════════════════════
# DECLARATIVE SOURCE CONFIG - add repos here
# ═══════════════════════════════════════════════════
SOURCES = [
    {
        "repo": "SunWeb3Sec/DeFiHackLabs",
        "collection": "defi_hacks",
        "category": "defi_hack",
        "tags": ["DeFi", "hack_reproduction", "Foundry", "education"],
        "file_patterns": ["*.md"],
        "max_files": 50,
        "enabled": True,
    },
    {
        "repo": "TradMod/awesome-audits-checklists",
        "collection": "contract_audits",
        "category": "audit_checklist",
        "tags": ["audit", "checklist", "smart_contract", "security"],
        "file_patterns": ["*.md"],
        "max_files": 20,
        "enabled": True,
    },
    {
        "repo": "alt-research/SolidityGuard",
        "collection": "vuln_patterns",
        "category": "vuln_pattern",
        "tags": ["OWASP", "Solidity", "security", "vulnerability"],
        "file_patterns": ["*.md", "*.sol"],
        "max_files": 40,
        "enabled": True,
    },
    {
        "repo": "forta-network/labelled-datasets",
        "collection": "known_scams",
        "category": "scam_report",
        "tags": ["Forta", "labelled_data", "threat_intel", "web3_security"],
        "file_patterns": ["*.md", "*.json", "*.csv"],
        "max_files": 30,
        "enabled": True,
    },
    {
        "repo": "Messi-Q/Smart-Contract-Dataset",
        "collection": "vuln_patterns",
        "category": "vuln_pattern",
        "tags": ["dataset", "smart_contract", "vulnerability", "academic"],
        "file_patterns": ["*.md", "*.sol", "*.csv"],
        "max_files": 30,
        "enabled": True,
    },
    {
        "repo": "nurkiewicz/crypto-hall-of-shame",
        "collection": "rug_timeline",
        "category": "scam_report",
        "tags": ["scam_timeline", "historical", "hall_of_shame"],
        "file_patterns": ["*.md", "*.adoc"],
        "max_files": 5,
        "enabled": True,
    },
]

CLONE_DIR = "/tmp/rag_sources"  # nosec B108 — persistent clone cache across runs, not a temp artifact  # noqa: S108


def ensure_cloned(repo: str) -> str | None:
    """Clone repo if not already present. Returns path or None."""
    name = repo.split("/")[-1]
    path = os.path.join(CLONE_DIR, name)
    if os.path.exists(path):
        return path
    os.makedirs(CLONE_DIR, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", path],
            capture_output=True,
            timeout=60,
            shell=False,
        )
        return path if os.path.exists(path) else None
    except subprocess.TimeoutExpired:
        return None


def extract_from_repo(source: dict) -> list[dict]:
    """Extract documents from a cloned GitHub repo."""
    path = ensure_cloned(source["repo"])
    if not path:
        logger.warning("  %s: clone failed", source["repo"])
        return []

    docs = []
    patterns = source.get("file_patterns", ["*.md"])
    max_files = source.get("max_files", 50)

    files = []
    for pat in patterns:
        files.extend(glob.glob(f"{path}/**/{pat}", recursive=True))

    for fpath in files[:max_files]:
        try:
            # Skip binary/large files
            size = os.path.getsize(fpath)
            if size > 500_000 or size < 50:
                continue

            with open(fpath, errors="ignore") as f:
                content = f.read()

            if not content.strip():
                continue

            # Extract filename for title
            relpath = os.path.relpath(fpath, path)
            name = os.path.splitext(os.path.basename(fpath))[0]

            # Try to extract a title from markdown heading
            title_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
            title = title_match.group(1) if title_match else name

            # For .sol files, extract contract/function structure
            if fpath.endswith(".sol"):
                contracts = re.findall(r"(?:contract|interface|library)\s+(\w+)", content)
                funcs = re.findall(r"function\s+(\w+)", content)[:10]
                summary = f"Solidity: {', '.join(contracts[:5])}. Functions: {', '.join(funcs)}"
                text = f"Smart Contract: {title} ({relpath})\n\n{summary}\n\n{content[:3000]}"
            elif fpath.endswith(".json"):
                try:
                    data = json.loads(content)
                    text = f"Dataset: {title}\n\n{json.dumps(data, indent=2)[:3000]}"
                except json.JSONDecodeError:
                    text = f"Data File: {title}\n\n{content[:2000]}"
            elif fpath.endswith(".csv"):
                lines = content.split("\n")[:50]
                text = f"CSV Data: {title}\n\n" + "\n".join(lines)
            else:
                text = f"{title}\n\n{content[:3000]}"

            docs.append(
                {
                    "text": text[:4000],
                    "source": source["repo"],
                    "url": f"https://github.com/{source['repo']}/blob/main/{relpath}",
                    "category": source["category"],
                    "tags": source.get("tags", []),
                }
            )
        except (ConnectionError, OSError, TimeoutError):
            pass

    logger.info("  %s: %d docs from %d files", source["repo"], len(docs), len(files[:max_files]))
    return docs


async def ingest_repo(source: dict) -> Any | int:
    """Clone, extract, and ingest a single repo.

    DEPRECATED (v3): the legacy RAG ingest endpoint was removed. No-op
    ``return`` guard — this feeder must not call a dead endpoint.
    """
    logger.warning("github_rag_feeder.ingest_repo deprecated (legacy RAG API removed in v3) — skipping")
    return 0


async def ingest_all() -> Any:
    """Run all enabled sources."""
    logger.info("=== RAG GitHub Feeder v2 ===\n")
    results = {}
    total = 0

    for src in SOURCES:
        if not src.get("enabled", True):
            continue
        logger.info("Ingesting: %s → %s", src["repo"], src["collection"])
        n = await ingest_repo(src)
        results[src["repo"]] = n
        total += n
        logger.info("  ingested: %d\n", n)

    logger.info("Total: %d docs across %d sources", total, len(results))
    return results


if __name__ == "__main__":
    asyncio.run(ingest_all())
