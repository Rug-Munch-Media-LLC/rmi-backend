"""Custom snapshot loader for JSON, CSV, and manual inputs."""

import csv
import io
import json
from app.core.logging import get_logger
from app.domains.airdrop.multichain.models import CrossChainHolder

logger = get_logger("darkroom_multichain_airdrop")

class CustomSnapshotLoader:
    """
    Load custom snapshot data from uploaded files or manual input.
    Supports JSON, CSV, and manual address lists.
    """

    @staticmethod
    def parse_json_snapshot(data: str) -> dict[str, CrossChainHolder]:
        """Parse JSON snapshot data."""
        holders = {}
        parsed = json.loads(data)

        for entry in parsed:
            addr = entry.get("address", entry.get("wallet", entry.get("holder", "")))
            if not addr:
                continue

            amount = str(entry.get("amount", entry.get("balance", entry.get("holdings", "0"))))
            chain = entry.get("chain", "unknown")
            token = entry.get("token", entry.get("token_symbol", "UNKNOWN"))

            if addr not in holders:
                holders[addr] = CrossChainHolder(
                    primary_address=addr,
                    chain_addresses={chain: addr},
                    holdings_per_source={},
                )

            source_key = f"{chain}:{token}"
            # Sum amounts if same address appears multiple times
            if source_key in holders[addr].holdings_per_source:
                existing = int(holders[addr].holdings_per_source[source_key])
                holders[addr].holdings_per_source[source_key] = str(existing + int(amount))
            else:
                holders[addr].holdings_per_source[source_key] = amount

            holders[addr].chain_addresses[chain] = addr

        logger.info(f"Parsed JSON snapshot: {len(holders)} holders")
        return holders

    @staticmethod
    def parse_csv_snapshot(data: str) -> dict[str, CrossChainHolder]:
        """Parse CSV snapshot data."""
        import csv
        import io

        holders = {}
        reader = csv.DictReader(io.StringIO(data))

        for row in reader:
            addr = row.get("address", row.get("wallet", row.get("holder", "")))
            if not addr:
                continue

            amount = str(row.get("amount", row.get("balance", row.get("holdings", "0"))))
            chain = row.get("chain", "unknown")
            token = row.get("token", row.get("token_symbol", "UNKNOWN"))

            if addr not in holders:
                holders[addr] = CrossChainHolder(
                    primary_address=addr,
                    chain_addresses={chain: addr},
                    holdings_per_source={},
                )

            source_key = f"{chain}:{token}"
            if source_key in holders[addr].holdings_per_source:
                existing = int(holders[addr].holdings_per_source[source_key])
                holders[addr].holdings_per_source[source_key] = str(existing + int(amount))
            else:
                holders[addr].holdings_per_source[source_key] = amount

            holders[addr].chain_addresses[chain] = addr

        logger.info(f"Parsed CSV snapshot: {len(holders)} holders")
        return holders

    @staticmethod
    def parse_manual_holders(holders_list: list[dict[str, str]]) -> dict[str, CrossChainHolder]:
        """Parse manual holder list."""
        holders = {}

        for entry in holders_list:
            addr = entry.get("address", "")
            if not addr:
                continue

            amount = str(entry.get("amount", "0"))
            chain = entry.get("chain", "unknown")
            token = entry.get("token", "UNKNOWN")

            if addr not in holders:
                holders[addr] = CrossChainHolder(
                    primary_address=addr,
                    chain_addresses={chain: addr},
                    holdings_per_source={},
                )

            source_key = f"{chain}:{token}"
            if source_key in holders[addr].holdings_per_source:
                existing = int(holders[addr].holdings_per_source[source_key])
                holders[addr].holdings_per_source[source_key] = str(existing + int(amount))
            else:
                holders[addr].holdings_per_source[source_key] = amount

            holders[addr].chain_addresses[chain] = addr

        logger.info(f"Parsed manual holders: {len(holders)} holders")
        return holders

    @staticmethod
    def deduplicate_holders(holders: dict[str, CrossChainHolder]) -> dict[str, CrossChainHolder]:
        """Deduplicate by address (case-insensitive)."""
        deduped = {}
        for addr, holder in holders.items():
            lower_addr = addr.lower()
            if lower_addr in deduped:
                # Merge holdings
                for source_key, amount in holder.holdings_per_source.items():
                    if source_key in deduped[lower_addr].holdings_per_source:
                        existing = int(deduped[lower_addr].holdings_per_source[source_key])
                        deduped[lower_addr].holdings_per_source[source_key] = str(
                            existing + int(amount)
                        )
                    else:
                        deduped[lower_addr].holdings_per_source[source_key] = amount
                # Merge chain addresses
                deduped[lower_addr].chain_addresses.update(holder.chain_addresses)
            else:
                deduped[lower_addr] = holder

        logger.info(f"Deduplicated: {len(holders)} -> {len(deduped)} holders")
        return deduped
