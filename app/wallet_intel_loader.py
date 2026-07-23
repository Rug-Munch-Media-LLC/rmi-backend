"""
Wallet Intelligence Loader - loads investigation data into Redis at startup.
Sources:
  - omega_forensic_v5 wallet_database.json (15 labeled criminal network wallets)
  - SOSANA-CRM-2024.json (full investigation: wallets, persons, orgs, tokens, financials)
  - crm_scam_2025_001 (secondary case)
"""

import json
import os

from app.core.logging import get_logger
from typing import Any

logger = get_logger(__name__)

WALLET_DB_PATH = "/app/data/wallet_database.json"
CRM_SOSANA_PATH = "/app/data/SOSANA-CRM-2024.json"


def load_all_wallet_intel(redis_client) -> Any:
    """Load all investigation data into Redis. Call at startup."""
    loaded = {"wallets": 0, "entities": 0, "cases": 0}

    # 1. Load labeled wallet database
    if os.path.exists(WALLET_DB_PATH):
        try:
            with open(WALLET_DB_PATH) as f:
                wallets = json.load(f)
            if isinstance(wallets, list):
                for w in wallets:
                    addr = w.get("address", "")
                    if addr:
                        redis_client.set(f"rmi:wallet:labeled:{addr}", json.dumps(w))
                        cat = w.get("category", "unknown")
                        redis_client.sadd(f"rmi:wallet:cat:{cat}", addr)
                redis_client.set("rmi:wallet:labeled:all", json.dumps(wallets))
                redis_client.set("rmi:wallet:labeled:count", len(wallets))
                loaded["wallets"] = len(wallets)
                logger.info(f"Loaded {len(wallets)} labeled wallets")
        except Exception as e:
            logger.warning(f"Failed to load wallet DB: {e}")

    # 2. Load SOSANA CRM case
    if os.path.exists(CRM_SOSANA_PATH):
        try:
            with open(CRM_SOSANA_PATH) as f:
                crm = json.load(f)

            # Store summary
            redis_client.set(
                "rmi:crm:sosana:summary",
                json.dumps(
                    {
                        "case_id": crm.get("case_id"),
                        "title": crm.get("title"),
                        "status": crm.get("status"),
                        "created_at": crm.get("created_at"),
                    }
                ),
            )

            # Store entities
            entities = crm.get("entities", {})
            for etype in ["wallets", "persons", "organizations", "tokens"]:
                elist = entities.get(etype, [])
                redis_client.set(f"rmi:crm:sosana:{etype}", json.dumps(elist))
                loaded["entities"] += len(elist)

            # Store financial analysis
            fin = crm.get("financial_analysis", {})
            redis_client.set("rmi:crm:sosana:financial", json.dumps(fin))

            # Evidence
            evidence = crm.get("evidence", {})
            redis_client.set(
                "rmi:crm:sosana:evidence",
                json.dumps({k: len(v) if isinstance(v, list) else v for k, v in evidence.items()}),
            )

            # Risk assessment
            risk = crm.get("risk_assessment", {})
            redis_client.set("rmi:crm:sosana:risk", json.dumps(risk))

            loaded["cases"] += 1
            logger.info(
                f"Loaded SOSANA CRM: {loaded['entities']} entities, ${fin.get('total_extracted_usd', 0):,.0f} extracted"
            )
        except Exception as e:
            logger.warning(f"Failed to load SOSANA CRM: {e}")

    return loaded
