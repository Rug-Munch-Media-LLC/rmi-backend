#!/usr/bin/env python3
"""One-shot sweep — no interactive prompt. For cron/automation.
Usage: docker exec rmi_backend python3 /app/app/sweep_now.py [--chain sol|eth] [--to ADDR] [--min 1.00]
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, "/app")

parser = argparse.ArgumentParser()
parser.add_argument("--chain", default="")
parser.add_argument("--to", default="")
parser.add_argument("--min", type=float, default=1.0)
args = parser.parse_args()

from app.wallet_manager_v2 import get_wallet_manager_v2

mgr = get_wallet_manager_v2(os.environ["WALLET_VAULT_PASSWORD"])
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

swept = 0
errors = 0
for w in mgr._wallets.values():
    if not w.x402_enabled:
        continue
    if args.chain and w.chain != args.chain:
        continue
    if (w.balance_usd or 0) < args.min:
        continue
    if w.chain not in evm_chains and w.chain != "sol":
        continue

    mnemonic = mgr.get_mnemonic(w.wallet_id)
    if not mnemonic:
        continue

    try:
        if w.chain in evm_chains:
            from eth_account import Account
            from web3 import Web3

            owner = args.to or "0x1E3AC01d0fdb976179790BDD02823196A92705C9"
            rpc = os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com")
            acct = Account.from_mnemonic(mnemonic)
            w3 = Web3(Web3.HTTPProvider(rpc))
            bal = w3.eth.get_balance(w.address)
            if bal <= w3.to_wei(0.0002, "ether"):
                continue
            sweep = bal - w3.to_wei(0.0001, "ether")
            nonce = w3.eth.get_transaction_count(w.address)
            tx = {
                "nonce": nonce,
                "to": Web3.to_checksum_address(owner),
                "value": sweep,
                "gas": 21000,
                "gasPrice": w3.eth.gas_price,
                "chainId": w3.eth.chain_id,
            }
            signed = w3.eth.account.sign_transaction(tx, acct.key)
            h = w3.eth.send_raw_transaction(signed.rawTransaction).hex()
            print(f"SWEPT eth {w.address[:12]} -> {owner[:12]} tx={h[:16]}")
            swept += 1
        elif w.chain == "sol":
            from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins
            from solana.rpc.async_api import AsyncClient
            from solana.rpc.commitment import Confirmed
            from solana.transaction import Transaction
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            from solders.system_program import TransferParams, transfer

            owner = args.to or "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv"
            rpc = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
            seed = Bip39SeedGenerator(mnemonic).Generate()
            ctx = Bip44.FromSeed(seed, Bip44Coins.SOLANA).Purpose().Coin().Account(0).Change(0).AddressIndex(0)
            kp = Keypair.from_bytes(ctx.PrivateKey().Raw().ToBytes())

            async def _do():
                async with AsyncClient(rpc) as c:
                    resp = await c.get_balance(kp.pubkey(), commitment=Confirmed)
                    if resp.value <= 2_000_000:
                        return
                    sweep = resp.value - 1_000_000
                    ix = transfer(
                        TransferParams(
                            from_pubkey=kp.pubkey(),
                            to_pubkey=Pubkey.from_string(owner),
                            lamports=sweep,
                        )
                    )
                    tx = Transaction().add(ix)
                    tx.sign(kp)
                    r = await c.send_transaction(tx, kp)
                    return str(r.value)

            sig = asyncio.run(_do())
            if sig:
                print(f"SWEPT sol {w.address[:12]} -> {owner[:12]} tx={sig[:16]}")
                swept += 1
    except Exception as e:
        print(f"ERR {w.chain}: {str(e)[:80]}")
        errors += 1

mgr._save_vault()
print("Done: %d swept, %d errors" % (swept, errors))
