"""
eth-labels DataBus Provider - 115K+ labeled addresses across 15+ EVM chains.
Queries the SQLite database built from dawsbot/eth-labels.
"""

import os
import sqlite3

DB_PATH = os.environ.get(
    "ETH_LABELS_DB", os.path.join(os.path.dirname(__file__), "..", "..", "eth-labels.db")
)
# Fallback paths for different environments
if not os.path.exists(DB_PATH):
    alt_paths = [
        os.path.expanduser("~/rmi/eth-labels.db"),
        "/app/eth-labels.db",
        os.path.join(os.path.dirname(__file__), "eth-labels.db"),
    ]
    for p in alt_paths:
        if os.path.exists(p):
            DB_PATH = p
            break


async def fetch_eth_labels(
    address: str | None = None,
    label: str | None = None,
    chain_id: int | None = None,
    limit: int = 20,
) -> dict:
    """Query eth-labels database for address labels, name tags, and entity info."""
    if not os.path.exists(DB_PATH):
        return {"error": "eth-labels database not found", "path": DB_PATH}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        if address:
            # Normalize address
            addr = address.lower()
            cur.execute(
                "SELECT chain_id, address, label, name_tag FROM accounts WHERE lower(address) = ? LIMIT ?",
                (addr, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]

            if not rows:
                # Try partial match
                cur.execute(
                    "SELECT chain_id, address, label, name_tag FROM accounts WHERE lower(address) LIKE ? LIMIT ?",
                    (f"%{addr}%", limit),
                )
                rows = [dict(r) for r in cur.fetchall()]

            return {
                "query": address,
                "matches": len(rows),
                "labels": rows,
                "source": "eth-labels (dawsbot)",
            }

        elif label:
            cur.execute(
                "SELECT chain_id, address, label, name_tag FROM accounts WHERE label LIKE ? OR name_tag LIKE ? LIMIT ?",
                (f"%{label}%", f"%{label}%", limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
            return {
                "query": label,
                "matches": len(rows),
                "results": rows,
                "source": "eth-labels (dawsbot)",
            }

        elif chain_id:
            cur.execute(
                "SELECT label, COUNT(*) as cnt FROM accounts WHERE chain_id = ? GROUP BY label ORDER BY cnt DESC LIMIT ?",
                (chain_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
            return {
                "chain_id": chain_id,
                "label_distribution": rows,
                "source": "eth-labels (dawsbot)",
            }

        else:
            # Stats
            cur.execute("SELECT COUNT(*) as total FROM accounts")
            total = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(DISTINCT label) as labels FROM accounts")
            unique_labels = cur.fetchone()["labels"]
            cur.execute("SELECT COUNT(DISTINCT chain_id) as chains FROM accounts")
            chains = cur.fetchone()["chains"]

            return {
                "total_accounts": total,
                "unique_labels": unique_labels,
                "chains_supported": chains,
                "chains": [
                    1,
                    10,
                    56,
                    137,
                    250,
                    1284,
                    1285,
                    42161,
                    43114,
                    42220,
                    8453,
                    59144,
                    534352,
                    7777777,
                    204,
                ],
                "source": "eth-labels (dawsbot)",
                "license": "MIT",
                "url": "https://github.com/dawsbot/eth-labels",
            }

    finally:
        conn.close()
