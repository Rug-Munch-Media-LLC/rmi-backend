"""Curated knowledge base data for the RAG ingest pipeline.

Split from the former 916-line ``_knowledge_bases.py`` module (pure data,
no I/O). Sub-modules:

- ``exploits``  — scam intel + historical exploit entries
- ``patterns``  — contract audit attack patterns
- ``protocols`` — protocol/DeFi + wallet label entries
- ``helpers``   — the ``KNOWLEDGE_BASES`` collection map

The collection map is consumed by ``ingest_all`` (in ``_orchestrator``)
and re-exported by the ``ingest_crypto_knowledge`` shim.
"""

from app.rag.ingest._knowledge_bases.exploits import DEPLOYER_HISTORY, SCAM_INTEL
from app.rag.ingest._knowledge_bases.helpers import KNOWLEDGE_BASES
from app.rag.ingest._knowledge_bases.patterns import CONTRACT_AUDIT
from app.rag.ingest._knowledge_bases.protocols import PHISHING_DB, WALLET_LABELS

__all__ = [
    "CONTRACT_AUDIT",
    "DEPLOYER_HISTORY",
    "KNOWLEDGE_BASES",
    "PHISHING_DB",
    "SCAM_INTEL",
    "WALLET_LABELS",
]
