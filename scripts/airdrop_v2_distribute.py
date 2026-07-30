#!/usr/bin/env python3
"""
RMI Airdrop V2 Distribution Script
Qualifies holders from CRM (Solana) and $cryptorugmunch (Base)
Excludes blacklisted wallets from Wallet Memory Bank

Usage: python airdrop_v2_distribute.py --token NEW_TOKEN --amount TOTAL_AMOUNT --chain solana
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import redis


@dataclass
class QualifiedHolder:
    address: str
    chain: str
    amount: float
    original_token: str
    percentage: float = 0.0
    is_blacklisted: bool = False
    blacklist_reason: str | None = None


class AirdropV2Distributor:
    def __init__(self, snapshot_path: str = "/root/backend/data/airdrop_v2_snapshot.json"):
        self.snapshot = json.load(open(snapshot_path))  # noqa: SIM115
        self.qualification = json.load(open("/root/backend/data/airdrop_v2_qualification.json"))  # noqa: SIM115
        try:
            self.redis = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
            self.redis.ping()
        except:  # noqa: E722
            self.redis = None
            print("Warning: Redis not available, using local checks only")

        # Load blacklist patterns
        self.blacklist_patterns = self._load_blacklist_patterns()

    def _load_blacklist_patterns(self) -> list[str]:
        """Load known scammer patterns from local files"""
        patterns = ["scammer", "blacklist", "rug", "honeypot", "bot", "sniper"]
        blacklist_file = Path("/root/backend/data/blacklist_patterns.json")
        if blacklist_file.exists():
            data = json.load(open(blacklist_file))  # noqa: SIM115
            patterns.extend(data.get("patterns", []))
        return patterns

    def check_blacklist(self, address: str, chain: str) -> tuple[bool, str | None]:
        """Check if address is blacklisted"""
        # Check Redis Wallet Memory Bank
        if self.redis:
            try:
                labels = self.redis.hgetall(f"wallet:labels:{chain}:{address}")
                if labels:
                    for key, value in labels.items():
                        value_str = str(value).lower()
                        for pattern in self.blacklist_patterns:
                            if pattern in value_str:
                                return True, f"Redis label: {key}={value}"
            except:  # noqa: E722
                pass

        # Check local scam lists
        scam_dir = Path("/root/backend/data/scam_lists")
        if scam_dir.exists():
            for f in scam_dir.glob("*.json"):
                try:
                    data = json.load(open(f))  # noqa: SIM115
                    if address in str(data):
                        return True, f"Found in {f.name}"
                except:  # noqa: E722
                    pass

        return False, None

    def qualify_holders(self, min_amount: float = 0) -> list[QualifiedHolder]:
        """Qualify all holders from both tokens"""
        qualified = []

        # CRM holders (Solana)
        for holder in self.snapshot["crm_holders"]["holders"]:
            addr = holder["Account"]
            amount = float(holder.get("Quantity", 0))

            if amount < min_amount:
                continue

            is_blacklisted, reason = self.check_blacklist(addr, "solana")

            qualified.append(
                QualifiedHolder(
                    address=addr,
                    chain="solana",
                    amount=amount,
                    original_token="CRM",
                    percentage=float(holder.get("Percentage", 0)),
                    is_blacklisted=is_blacklisted,
                    blacklist_reason=reason,
                )
            )

        # Base holders
        for holder in self.snapshot["base_holders"]["holders"]:
            addr = holder["HolderAddress"]
            amount_str = holder.get("Balance", "0").replace(",", "")
            amount = float(amount_str)

            if amount < min_amount:
                continue

            is_blacklisted, reason = self.check_blacklist(addr, "base")

            qualified.append(
                QualifiedHolder(
                    address=addr,
                    chain="base",
                    amount=amount,
                    original_token="$cryptorugmunch",
                    percentage=0,  # Not provided in base CSV
                    is_blacklisted=is_blacklisted,
                    blacklist_reason=reason,
                )
            )

        return qualified

    def calculate_distribution(self, total_amount: float, distribution_type: str = "proportional") -> dict[str, float]:
        """Calculate airdrop amounts per holder"""
        qualified = [h for h in self.qualify_holders() if not h.is_blacklisted]

        if distribution_type == "equal":
            per_holder = total_amount / len(qualified)
            return {h.address: per_holder for h in qualified}

        elif distribution_type == "proportional":
            # Proportional to holding amount
            total_holding = sum(h.amount for h in qualified)
            return {h.address: (h.amount / total_holding) * total_amount for h in qualified}

        elif distribution_type == "tiered":
            # Tiered: whales get more per token
            distribution = {}
            for h in qualified:
                if h.percentage >= 5:
                    multiplier = 2.0
                elif h.percentage >= 1:
                    multiplier = 1.5
                elif h.percentage >= 0.1:
                    multiplier = 1.2
                else:
                    multiplier = 1.0

                distribution[h.address] = h.amount * multiplier

            # Normalize to total amount
            total_weighted = sum(distribution.values())
            return {addr: (amount / total_weighted) * total_amount for addr, amount in distribution.items()}

        return {}

    def generate_distribution_list(self, token_address: str, total_amount: float, chain: str = "solana") -> dict:
        """Generate final distribution list for token dispersal"""
        distribution = self.calculate_distribution(total_amount)
        qualified = self.qualify_holders()

        result = {
            "airdrop_version": "v2",
            "snapshot_date": self.snapshot["snapshot_date"],
            "distribution_token": token_address,
            "distribution_chain": chain,
            "total_amount": total_amount,
            "total_qualified": len(distribution),
            "total_excluded": len([h for h in qualified if h.is_blacklisted]),
            "distribution_type": "proportional",
            "recipients": [
                {"address": addr, "amount": round(amount, 6), "chain": chain} for addr, amount in distribution.items()
            ],
            "excluded": [
                {"address": h.address, "reason": h.blacklist_reason, "chain": h.chain}
                for h in qualified
                if h.is_blacklisted
            ],
        }

        return result

    def save_distribution(self, output_path: str, token_address: str, total_amount: float, chain: str = "solana"):
        """Save distribution list to file"""
        result = self.generate_distribution_list(token_address, total_amount, chain)
        json.dump(result, open(output_path, "w"), indent=2)  # noqa: SIM115
        print(f"Distribution saved to: {output_path}")
        print(f"Total recipients: {result['total_qualified']}")
        print(f"Total excluded: {result['total_excluded']}")
        print(f"Total amount: {total_amount:,.6f}")
        return result


def main():
    parser = argparse.ArgumentParser(description="RMI Airdrop V2 Distributor")
    parser.add_argument("--token", required=True, help="New token address to distribute")
    parser.add_argument("--amount", type=float, required=True, help="Total amount to distribute")
    parser.add_argument("--chain", default="solana", choices=["solana", "base", "ethereum"])
    parser.add_argument("--output", default="/root/backend/data/airdrop_v2_distribution.json")
    parser.add_argument("--min-hold", type=float, default=0, help="Minimum holding to qualify")
    parser.add_argument("--type", default="proportional", choices=["equal", "proportional", "tiered"])

    args = parser.parse_args()

    distributor = AirdropV2Distributor()

    # Show qualification stats
    qualified = distributor.qualify_holders(min_amount=args.min_hold)
    print("\n=== Airdrop V2 Qualification ===")
    print(f"Total holders checked: {len(qualified)}")
    print(f"Qualified: {len([h for h in qualified if not h.is_blacklisted])}")
    print(f"Blacklisted: {len([h for h in qualified if h.is_blacklisted])}")

    # Generate and save distribution
    distributor.save_distribution(args.output, args.token, args.amount, args.chain)

    print("\n=== Distribution Complete ===")
    print(f"Output: {args.output}")
    print("Ready for token dispersal")


if __name__ == "__main__":
    main()
