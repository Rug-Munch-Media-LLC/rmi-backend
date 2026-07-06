#!/usr/bin/env python3
"""
Setup x402 Payment Address Rotation Schedules

Sets 90-day auto-rotation on all x402 receiving wallets.
Run with: python3 setup_x402_rotation.py

Requires WALLET_VAULT_PASSWORD env var.
"""

import os
import sys

sys.path.insert(0, "/root/backend")
os.chdir("/root/backend")

from app.wallet_manager_v2 import get_wallet_manager_v2  # noqa: E402

# Target addresses from fact_store (id=74)
EVM_RECEIVING = "0x5761E0337a49819A8Cd4D35D76E882255Cb113b9".lower()
SOL_RECEIVING = "9JNVRRttW9Mbev78tKinvmgPtZvgNwjs9fCTQs17Ur3k"


def main():
    password = os.getenv("WALLET_VAULT_PASSWORD", "")
    if not password:
        print("ERROR: WALLET_VAULT_PASSWORD not set")
        sys.exit(1)

    mgr = get_wallet_manager_v2(password)

    # Find all x402-enabled wallets
    x402_wallets = []
    for w in mgr._wallets.values():
        if getattr(w, "x402_enabled", False):
            x402_wallets.append(w)

    print(f"Found {len(x402_wallets)} x402-enabled wallets")

    # Also try address matching for the known receiving wallets
    for w in mgr._wallets.values():
        addr = str(getattr(w, "address", "") or "").lower()
        if EVM_RECEIVING in addr or SOL_RECEIVING.lower() in addr:
            if w not in x402_wallets:
                x402_wallets.append(w)
                print(f"Added by address match: {w.wallet_id} ({w.chain})")

    if not x402_wallets:
        print("No x402 wallets found. Setting rotation on ALL wallets instead.")
        x402_wallets = list(mgr._wallets.values())

    schedules = []
    for w in x402_wallets:
        try:
            schedule = mgr.schedule_rotation(w.wallet_id, days=90, auto=True)
            schedules.append(
                {
                    "wallet_id": w.wallet_id,
                    "chain": w.chain,
                    "rotation_days": 90,
                    "auto_rotate": True,
                    "next_rotation": schedule.next_rotation if schedule else None,
                }
            )
            print(f"Rotation scheduled: {w.wallet_id} ({w.chain}) → every 90 days, auto=True")
        except Exception as e:
            print(f"Failed to schedule rotation for {w.wallet_id}: {e}")

    print(f"\nRotation schedules set: {len(schedules)}/{len(x402_wallets)}")
    return schedules


if __name__ == "__main__":
    main()
