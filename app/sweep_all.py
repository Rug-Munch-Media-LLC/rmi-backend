#!/usr/bin/env python3
"""
Universal wallet sweep tool for RMI x402 payment system.

Usage:
  docker exec rmi_backend python3 /app/sweep_all.py                    # sweep all to owner
  docker exec rmi_backend python3 /app/sweep_all.py --chain sol        # sweep Solana only
  docker exec rmi_backend python3 /app/sweep_all.py --chain eth        # sweep EVM only
  docker exec rmi_backend python3 /app/sweep_all.py --to 0xYOURADDR   # sweep to custom address
  docker exec rmi_backend python3 /app/sweep_all.py --dry-run          # preview only
  docker exec rmi_backend python3 /app/sweep_all.py --min 0.50         # override $5 threshold
  docker exec rmi_backend python3 /app/sweep_all.py --force            # skip threshold check

Environment:
  WALLET_VAULT_PASSWORD: required (auto-detected from env)
"""

import argparse
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, "/app")

# ── CLI ──
parser = argparse.ArgumentParser(description="Sweep x402 wallets to owner or custom address")
parser.add_argument("--chain", default="", help="Only sweep specific chain (eth/sol/base/etc)")
parser.add_argument("--to", default="", help="Custom destination address (overrides default owner)")
parser.add_argument("--min", type=float, default=5.0, help="Minimum USD threshold (default $5)")
parser.add_argument("--dry-run", action="store_true", help="Preview only, no transactions")
parser.add_argument("--force", action="store_true", help="Skip threshold (sweeps everything)")
args = parser.parse_args()

from app.wallet_manager_v2 import get_wallet_manager_v2  # noqa: E402

mgr = get_wallet_manager_v2(os.environ.get("WALLET_VAULT_PASSWORD", ""))

print("=" * 60)
print("RMI WALLET SWEEP TOOL")
print("=" * 60)
print("Time:", datetime.now(UTC).isoformat())
print("Mode:", "DRY RUN" if args.dry_run else "LIVE")
print("Chain filter:", args.chain or "ALL")
print("Threshold: ${:.2f}{}".format(args.min, " (BYPASSED)" if args.force else ""))
print()

# ── Find all x402-enabled wallets ──
x402_wallets = []
for w in mgr._wallets.values():
    if not w.x402_enabled:
        continue
    if args.chain and w.chain != args.chain:
        continue
    x402_wallets.append(w)

print("x402 wallets found: %d" % len(x402_wallets))  # noqa: UP031

evm_chains = {
    "eth",
    "base",
    "polygon",
    "arbitrum",
    "optimism",
    "avalanche",
    "bsc",
    "fantom",
    "gnosis",
}

# ── Check each wallet ──
sweepable = []
for w in x402_wallets:
    balance = w.balance_usd or 0
    has_key = bool(mgr.get_mnemonic(w.wallet_id))

    if w.chain not in evm_chains and w.chain != "sol":
        continue  # unsupported chain for sweep

    if not has_key:
        print("  SKIP %-6s %s (no key)" % (w.chain, w.address[:16]))  # noqa: UP031
        continue

    if not args.force and balance < args.min:
        continue

    sweepable.append(w)

print("Sweepable wallets: %d" % len(sweepable))  # noqa: UP031
print()

if not sweepable:
    print("Nothing to sweep.")
    sys.exit(0)

# ── Preview / Execute ──
total = 0.0
for w in sweepable:
    balance = w.balance_usd or 0
    print("  %-6s %s...  \033[33m$%.2f\033[0m" % (w.chain, w.address[:24], balance))  # noqa: UP031
    total += balance

print()
print(f"Total sweepable: \033[33m${total:.2f}\033[0m")

if args.dry_run:
    print("\nDry run complete. Use without --dry-run to execute.")
    sys.exit(0)

# ── Confirm ──
owner_fallback = args.to or "(default owner addresses)"
print(f"\nDestination: {owner_fallback}")
print("Type \033[1mYES\033[0m to broadcast %d transactions:" % len(sweepable))  # noqa: UP031
confirm = input("> ").strip()
if confirm != "YES":
    print("Aborted.")
    sys.exit(0)

# ── Execute ──
print("\nBroadcasting transactions...")
results = {"swept": [], "errors": [], "total_usd": 0.0}

for w in sweepable:
    try:
        mnemonic = mgr.get_mnemonic(w.wallet_id)
        if not mnemonic:
            raise RuntimeError("Key missing")

        if w.chain in evm_chains:
            # ── EVM sweep ──
            from eth_account import Account
            from web3 import Web3

            owner = args.to or "0x1E3AC01d0fdb976179790BDD02823196A92705C9"
            rpc = (
                os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
                if w.chain == "base"
                else os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com")
            )

            acct = Account.from_mnemonic(mnemonic)
            if acct.address.lower() != w.address.lower():
                raise RuntimeError("Address mismatch")

            w3 = Web3(Web3.HTTPProvider(rpc))
            if not w3.is_connected():
                raise RuntimeError("RPC connection failed")

            balance_wei = w3.eth.get_balance(w.address)
            if balance_wei == 0:
                raise RuntimeError("On-chain balance is 0")

            gas_reserve = w3.to_wei(0.0001, "ether")
            if balance_wei <= gas_reserve:
                raise RuntimeError("Balance too low for gas")

            sweep_wei = balance_wei - gas_reserve
            nonce = w3.eth.get_transaction_count(w.address)

            tx = {
                "nonce": nonce,
                "to": Web3.to_checksum_address(owner),
                "value": sweep_wei,
                "gas": 21000,
                "gasPrice": w3.eth.gas_price,
                "chainId": w3.eth.chain_id,
            }
            signed = w3.eth.account.sign_transaction(tx, acct.key)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)

            results["swept"].append(
                {
                    "chain": w.chain,
                    "address": w.address,
                    "tx_hash": tx_hash.hex(),
                    "amount_wei": sweep_wei,
                }
            )
            sweep_usd = float(w3.from_wei(sweep_wei, "ether")) * 2500
            results["total_usd"] += sweep_usd
            print("  \033[32mOK\033[0m  %-6s %s -> %s ($%.2f)" % (w.chain, w.address[:12], owner[:12], sweep_usd))  # noqa: UP031

        elif w.chain == "sol":
            # ── Solana sweep ──
            from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins
            from solana.rpc.async_api import AsyncClient
            from solana.rpc.commitment import Confirmed
            from solana.transaction import Transaction
            from solders.keypair import Keypair as SoldersKeypair
            from solders.pubkey import Pubkey as SoldersPubkey
            from solders.system_program import TransferParams, transfer

            owner = args.to or "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv"
            rpc = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

            seed = Bip39SeedGenerator(mnemonic).Generate()
            bip44 = Bip44.FromSeed(seed, Bip44Coins.SOLANA)
            ctx = bip44.Purpose().Coin().Account(0).Change(0).AddressIndex(0)
            keypair = SoldersKeypair.from_bytes(ctx.PrivateKey().Raw().ToBytes())

            if str(keypair.pubkey()) != w.address:
                raise RuntimeError("Address mismatch")

            async def _sweep_sol():
                async with AsyncClient(rpc) as client:  # noqa: B023
                    resp = await client.get_balance(keypair.pubkey(), commitment=Confirmed)  # noqa: B023
                    lamports = resp.value
                    if lamports == 0:
                        raise RuntimeError("On-chain balance is 0")

                    reserve = 1_000_000
                    if lamports <= reserve:
                        raise RuntimeError("Too low for gas")

                    sweep = lamports - reserve
                    ix = transfer(
                        TransferParams(
                            from_pubkey=keypair.pubkey(),  # noqa: B023
                            to_pubkey=SoldersPubkey.from_string(owner),  # noqa: B023
                            lamports=sweep,
                        )
                    )
                    tx = Transaction().add(ix)
                    tx.sign(keypair)  # noqa: B023
                    result_tx = await client.send_transaction(tx, keypair)  # noqa: B023
                    return str(result_tx.value), sweep

            import asyncio

            sig, swept_lamports = asyncio.run(_sweep_sol())
            sweep_sol = swept_lamports / 1_000_000_000

            results["swept"].append(
                {
                    "chain": w.chain,
                    "address": w.address,
                    "tx_sig": sig,
                    "amount_lamports": swept_lamports,
                }
            )
            results["total_usd"] += sweep_sol * 75  # Approx SOL price
            print("  \033[32mOK\033[0m  %-6s %s -> %s (%.4f SOL)" % (w.chain, w.address[:12], owner[:12], sweep_sol))  # noqa: UP031

    except Exception as e:
        results["errors"].append({"chain": w.chain, "address": w.address, "error": str(e)})
        print("  \033[31mERR\033[0m %-6s %s: %s" % (w.chain, w.address[:12], str(e)[:80]))  # noqa: UP031

# ── Summary ──
print()
print("=" * 60)
print("SWEEP COMPLETE")
print("  Swept:  %d transactions" % len(results["swept"]))  # noqa: UP031
print("  Errors: %d" % len(results["errors"]))  # noqa: UP031
print("  Total:  ${:.2f}".format(round(results["total_usd"], 2)))

mgr._save_vault()
print("  Vault:  saved")
