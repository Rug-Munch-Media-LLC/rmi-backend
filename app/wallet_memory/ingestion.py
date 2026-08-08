"""
Wallet Ingestion Pipeline - Auto-pull data from Helius (Solana) & Etherscan (EVM).
=================================================================================
Automatically fetches transaction history when a wallet profile is requested and:
  - Profile has no data (never fetched)
  - Profile data is stale (last fetch > 5 min cooldown)

Pipeline flow:
  1. Detect chain family (Solana vs EVM) via chain_registry
  2. Fetch tx history from Helius Enhanced Transactions API (Solana)
     or Etherscan-family API (EVM)
  3. Normalize transactions into a common format
  4. Feed into WalletClusteringEngine via engine.feed_transactions()
  5. Store token transfers in ClickHouse token_transfers table
  6. Detect contract-deployment (deployer) events and record them

Rate limiting:
  Helius: 10 req/s  (bucket capacity 10, refill 10/s)
  Etherscan: 5 req/s (bucket capacity 5, refill 5/s)
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime

import httpx

from app.chain_registry import is_evm, is_solana
from app.core.logging import get_logger
from app.domains.scanners.dev_reputation import ETHERSCAN_NETWORKS

logger = get_logger("wallet_memory.ingestion")


from .models import (  # noqa: E402 — imported after logger init; kept below module header for readability
    NormalizedTransaction,
)

# ── Token-bucket rate limiter (uses UnifiedRateLimiter) ──────────────


class TokenBucketRateLimiter:
    """Token bucket rate limiter wrapper using UnifiedRateLimiter.
    Blocks until a token is available (unlike the non-blocking acquire).
    """

    def __init__(self, rate: float, capacity: int) -> None:
        self.rate = rate
        self.capacity = capacity

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        from app.core.rate_limiter import get_rate_limiter

        limiter = get_rate_limiter()
        key = f"ingestion:token_bucket:{id(self)}"
        while True:
            allowed, _headers = await limiter.acquire(key, rate=self.rate, burst=float(self.capacity))
            if allowed:
                return
            await asyncio.sleep(1.0 / max(self.rate, 0.1))


# ── Cooldown tracker ────────────────────────────────────────────────


class IngestionCooldown:
    """Per-address cooldown: don't re-fetch within COOLDOWN_SECONDS."""

    COOLDOWN_SECONDS = 300  # 5 minutes

    def __init__(self) -> None:
        self._last_fetch: dict[str, float] = {}  # key = "chain:address"

    def is_cooled(self, address: str, chain: str) -> bool:
        """Return True if address was recently fetched and is still within cooldown."""
        key = f"{chain.lower()}:{address.lower()}"
        last = self._last_fetch.get(key, 0)
        return (time.monotonic() - last) < self.COOLDOWN_SECONDS

    def mark_fetched(self, address: str, chain: str) -> None:
        """Mark that an address was just fetched."""
        key = f"{chain.lower()}:{address.lower()}"
        self._last_fetch[key] = time.monotonic()

    def is_stale(self, last_seen_at: datetime | None) -> bool:
        """Check if a stored profile's last_seen_at is stale (> cooldown)."""
        if last_seen_at is None:
            return True
        if isinstance(last_seen_at, str):
            try:
                last_seen_at = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return True
        age = (datetime.now(UTC) - last_seen_at).total_seconds()
        return age > self.COOLDOWN_SECONDS


class WalletIngestionPipeline:
    """
    Auto-pulls transaction data from Helius and Etherscan when a wallet
    profile is requested and the existing profile is empty or stale.

    Usage:
        pipeline = WalletIngestionPipeline(engine)
        await pipeline.ingest_if_needed("7xKXtg2CW87d97TXJSDpbD5jBkA2qGr9x2jsn4QK", "solana")
    """

    # Pagination limits
    HELIUS_PAGE_LIMIT = 100
    HELIUS_MAX_PAGES = 10
    ETHERSCAN_PAGE_SIZE = 200
    ETHERSCAN_MAX_PAGES = 5

    def __init__(self, engine=None, storage=None) -> None:
        """
        Args:
            engine: WalletMemoryEngine instance (for feed_transactions + deployer events)
            storage: WalletStorage instance (for token transfers + deployer events)
        """
        self.engine = engine
        self.storage = storage
        self._http_client: httpx.AsyncClient | None = None
        self._helius_limiter = TokenBucketRateLimiter(rate=10, capacity=10)
        self._etherscan_limiter = TokenBucketRateLimiter(rate=5, capacity=5)
        self._cooldown = IngestionCooldown()

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=20.0)
        return self._http_client

    async def close(self) -> None:
        """Gracefully close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    # ── Public API ────────────────────────────────────────────────

    async def ingest_if_needed(self, address: str, chain: str) -> bool:
        """
        Main entry point. Fetches and processes transaction history
        if the wallet is on cooldown and the profile is stale/empty.

        Returns True if ingestion was performed, False if skipped.
        """
        addr = address.lower().strip()

        # Check cooldown - don't re-fetch within 5 minutes
        if self._cooldown.is_cooled(addr, chain):
            logger.debug("Ingestion cooldown active for %s on %s", addr, chain)
            return False

        # Determine chain family and fetch
        try:
            if is_solana(chain):
                txs = await self._fetch_solana_transactions(addr)
            elif is_evm(chain):
                txs = await self._fetch_evm_transactions(addr, chain)
            else:
                logger.warning("Unsupported chain for ingestion: %s", chain)
                return False

            if not txs:
                logger.info("No transactions fetched for %s on %s", addr, chain)
                self._cooldown.mark_fetched(addr, chain)
                return False

            # Normalize
            normalized = self._normalize_transactions(txs, addr, chain)

            # Feed into clustering engine
            if self.engine:
                clustering_dicts = [t.to_clustering_dict() for t in normalized]
                self.engine.feed_transactions(clustering_dicts, chain_id=chain)
                logger.info("Fed %s txs into clustering for %s", len(clustering_dicts), addr)

            # Store token transfers in ClickHouse
            await self._store_token_transfers(normalized)

            # Detect and record deployer events
            deployer_events = [t for t in normalized if t.is_contract_creation]
            await self._record_deployer_events(deployer_events)

            # Mark as fetched
            self._cooldown.mark_fetched(addr, chain)
            logger.info(
                f"Ingestion complete for {addr} on {chain}: {len(normalized)} txs, {len(deployer_events)} deploys"
            )
            return True

        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning("error: %s", e)
            logger.error("Ingestion failed for %s on %s: %s", addr, chain, e)
            # Still mark cooldown to avoid hammering on error
            self._cooldown.mark_fetched(addr, chain)
            return False

    def should_ingest(self, address: str, chain: str, stored_profile: dict | None = None) -> bool:
        """
        Determine if ingestion should run for this address/chain.
        Used by engine.get_wallet_profile() to decide whether to trigger ingestion.
        """
        addr = address.lower().strip()

        # If on cooldown, don't ingest
        if self._cooldown.is_cooled(addr, chain):
            return False

        # If no stored profile, definitely ingest
        if not stored_profile:
            return True

        # If stored profile is stale, ingest
        if self._cooldown.is_stale(stored_profile.get("last_seen_at")):
            return True

        # If stored profile has zero transactions, ingest
        return bool(not stored_profile.get("total_transactions"))

    # ── Solana: Helius Enhanced Transactions API ───────────────────

    async def _fetch_solana_transactions(self, address: str) -> list[dict]:
        """Fetch transaction history from Helius Enhanced Transactions API."""
        api_key = os.getenv("HELIUS_API_KEY", "")
        if not api_key:
            logger.warning("HELIUS_API_KEY not set - cannot fetch Solana transactions")
            return []

        client = await self._get_http_client()
        all_txs = []
        cursor = None

        for page in range(self.HELIUS_MAX_PAGES):
            await self._helius_limiter.acquire()

            url = f"https://mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
            params = {"api-key": api_key}
            if cursor:
                params["cursor"] = cursor

            try:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning("Helius API returned %s for %s", resp.status_code, address)
                    break

                data = resp.json()
                txs = data if isinstance(data, list) else data.get("transactions", [])
                if not txs:
                    break

                all_txs.extend(txs)

                # Check for pagination cursor
                # Helius uses pagination in the response
                cursor = data.get("cursor") if isinstance(data, dict) else None
                if not cursor or len(txs) < self.HELIUS_PAGE_LIMIT:
                    break

            except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
                logger.warning("error: %s", e)
                logger.warning("Helius fetch error (page %s): %s", page, e)
                break

        logger.info("Fetched %s Solana txs from Helius for %s", len(all_txs), address)
        return all_txs

    # ── EVM: Etherscan-family API ──────────────────────────────────

    async def _fetch_evm_transactions(self, address: str, chain: str) -> list[dict]:
        """Fetch transaction history from Etherscan-family API."""
        net = ETHERSCAN_NETWORKS.get(chain.lower())
        if not net:
            logger.warning("No Etherscan network config for chain: %s", chain)
            return []

        api_key = net.get("key", "")
        if not api_key:
            logger.warning("No Etherscan API key for %s", chain)
            return []

        client = await self._get_http_client()
        all_txs = []

        # Fetch normal transactions
        for page in range(1, self.ETHERSCAN_MAX_PAGES + 1):
            await self._etherscan_limiter.acquire()

            params = {
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "page": page,
                "offset": self.ETHERSCAN_PAGE_SIZE,
                "sort": "asc",
                "apikey": api_key,
            }

            try:
                resp = await client.get(net["url"], params=params)
                if resp.status_code != 200:
                    logger.warning("Etherscan returned %s for %s", resp.status_code, chain)
                    break

                data = resp.json()
                if data.get("status") != "1":
                    # "No transactions found" or rate limit
                    if page == 1:
                        logger.debug("Etherscan: no txs for %s on %s", address, chain)
                    break

                result = data.get("result", [])
                if isinstance(result, list):
                    all_txs.extend(result)
                    if len(result) < self.ETHERSCAN_PAGE_SIZE:
                        break
                else:
                    break

            except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
                logger.warning("error: %s", e)
                logger.warning("Etherscan fetch error (page %s, %s): %s", page, chain, e)
                break

        # Fetch internal transactions (first page only - these are secondary)
        await self._etherscan_limiter.acquire()
        int_params = {
            "module": "account",
            "action": "txlistinternal",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": self.ETHERSCAN_PAGE_SIZE,
            "sort": "asc",
            "apikey": api_key,
        }
        try:
            resp = await client.get(net["url"], params=int_params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    int_txs = data.get("result", [])
                    if isinstance(int_txs, list):
                        # Tag internal txs so we can identify them later
                        for itx in int_txs:
                            itx["_internal"] = True
                        all_txs.extend(int_txs)
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning("error: %s", e)
            logger.debug("Etherscan internal tx fetch failed for %s: %s", chain, e)

        # Fetch ERC-20 token transfers
        await self._etherscan_limiter.acquire()
        token_params = {
            "module": "account",
            "action": "tokentx",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": self.ETHERSCAN_PAGE_SIZE,
            "sort": "asc",
            "apikey": api_key,
        }
        try:
            resp = await client.get(net["url"], params=token_params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    token_txs = data.get("result", [])
                    if isinstance(token_txs, list):
                        for ttx in token_txs:
                            ttx["_token_transfer"] = True
                        all_txs.extend(token_txs)
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning("error: %s", e)
            logger.debug("Etherscan token tx fetch failed for %s: %s", chain, e)

        logger.info("Fetched %s EVM txs from Etherscan for %s on %s", len(all_txs), address, chain)
        return all_txs

    # ── Normalization ──────────────────────────────────────────────

    def _normalize_transactions(self, raw_txs: list[dict], address: str, chain: str) -> list[NormalizedTransaction]:
        """Convert raw API responses into NormalizedTransaction objects."""
        normalized = []
        addr_lower = address.lower().strip()

        if is_solana(chain):
            normalized = self._normalize_helius(raw_txs, addr_lower, chain)
        elif is_evm(chain):
            normalized = self._normalize_etherscan(raw_txs, addr_lower, chain)

        return normalized

    def _normalize_helius(self, txs: list[dict], address: str, chain: str) -> list[NormalizedTransaction]:
        """Parse Helius Enhanced Transactions API response."""
        normalized = []

        for tx in txs:
            try:
                # Helius Enhanced Transaction schema
                tx_hash = tx.get("signature", "")
                timestamp_ms = tx.get("timestamp", 0)
                ts = datetime.fromtimestamp(timestamp_ms, tz=UTC) if timestamp_ms else datetime.now(UTC)

                block_slot = tx.get("slot", 0)
                tx.get("fee", 0) or 0
                fee_payer = (tx.get("feePayer", "") or "").lower()

                # Check if this is a contract deploy (Solana program deployment)
                is_deploy = tx.get("transactionType") == "PROGRAM_DEPLOY"
                # Also check for token mint/create instructions
                instructions = tx.get("instructions", [])
                programs_involved = set()

                for ix in instructions:
                    prog = ix.get("programId", "") or ix.get("program", "")
                    if prog:
                        programs_involved.add(prog)
                    # Detect SPL token mint/create via program
                    inner = ix.get("innerInstructions", [])
                    for inner_ix in inner:
                        ip = inner_ix.get("programId", "") or inner_ix.get("program", "")
                        if ip:
                            programs_involved.add(ip)

                # Detect token deployments: create/mint instructions
                spl_token_program = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105 — config default
                token_metadata_program = "metaqbxxU9dKaqAta4M99M7C5pWm7adgGpSxq9BDpL4"  # noqa: S105 — config default
                token_deploy_programs = {spl_token_program, token_metadata_program}
                if token_deploy_programs & programs_involved:
                    is_deploy = True

                # Parse native SOL transfers
                tx.get("events", {})
                native_transfers = tx.get("nativeTransfers", [])

                if native_transfers:
                    for nt in native_transfers:
                        from_addr = (nt.get("fromUserAccount", "") or nt.get("from", "")).lower()
                        to_addr = (nt.get("toUserAccount", "") or nt.get("to", "")).lower()
                        amount_lamports = nt.get("amount", 0) or 0
                        amount_sol = amount_lamports / 1e9

                        # Only include if our address is involved
                        if from_addr == address or to_addr == address:
                            is_send = from_addr == address
                            counterparty = to_addr if is_send else from_addr

                            normalized.append(
                                NormalizedTransaction(
                                    address=address,
                                    counterparty=counterparty,
                                    timestamp=ts,
                                    amount=amount_sol,
                                    chain_id=chain,
                                    program=",".join(programs_involved) if programs_involved else "system",
                                    tx_hash=tx_hash,
                                    tx_type="send" if is_send else "receive",
                                    transfer_type="native",
                                    block_number=block_slot,
                                    is_contract_creation=is_deploy and is_send,
                                )
                            )

                # Parse token transfers
                token_transfers = tx.get("tokenTransfers", [])
                for tt in token_transfers:
                    from_addr = (tt.get("fromUserAccount", "") or tt.get("from", "")).lower()
                    to_addr = (tt.get("toUserAccount", "") or tt.get("to", "")).lower()
                    token_addr = tt.get("mint", "") or tt.get("tokenAddress", "")
                    raw_amount = tt.get("tokenAmount", 0) or tt.get("amount", 0) or 0
                    decimals = tt.get("tokenStandard", "") or ""

                    if from_addr == address or to_addr == address:
                        is_send = from_addr == address
                        counterparty = to_addr if is_send else from_addr

                        # Try to calculate decimal value
                        dec_val = 0.0
                        try:
                            dec_str = str(decimals) if isinstance(decimals, (int, float)) else "0"
                            dec_num = int(float(dec_str)) if dec_str else 0
                            dec_val = raw_amount / (10**dec_num) if dec_num else float(raw_amount)
                        except (ValueError, ZeroDivisionError):
                            dec_val = float(raw_amount)

                        normalized.append(
                            NormalizedTransaction(
                                address=address,
                                counterparty=counterparty,
                                timestamp=ts,
                                amount=dec_val,
                                chain_id=chain,
                                program=",".join(programs_involved) if programs_involved else "spl-token",
                                tx_hash=tx_hash,
                                tx_type="send" if is_send else "receive",
                                token_address=token_addr,
                                token_value_raw=str(raw_amount),
                                token_value_decimal=dec_val,
                                transfer_type="spl",
                                block_number=block_slot,
                                is_contract_creation=False,
                            )
                        )

                # If no transfers were parsed, create a baseline entry from fee payer
                if not native_transfers and not token_transfers and fee_payer.lower() == address:
                    normalized.append(
                        NormalizedTransaction(
                            address=address,
                            counterparty="",
                            timestamp=ts,
                            amount=0,
                            chain_id=chain,
                            program=",".join(programs_involved) if programs_involved else "unknown",
                            tx_hash=tx_hash,
                            tx_type="fee",
                            transfer_type="native",
                            block_number=block_slot,
                            is_contract_creation=is_deploy,
                        )
                    )

            except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
                logger.warning("error: %s", e)
                logger.debug("Failed to normalize Helius tx: %s", e)
                continue

        return normalized

    def _normalize_etherscan(self, txs: list[dict], address: str, chain: str) -> list[NormalizedTransaction]:
        """Parse Etherscan-family API response."""
        normalized = []

        for tx in txs:
            try:
                is_token_transfer = tx.get("_token_transfer", False)
                is_internal = tx.get("_internal", False)

                from_addr = (tx.get("from", "") or "").lower()
                to_addr = (tx.get("to", "") or "").lower()
                tx_hash = tx.get("hash", "") or tx.get("transactionHash", "")
                block_num = int(tx.get("blockNumber", 0) or 0)
                timestamp_str = tx.get("timeStamp", "") or tx.get("timestamp", "")

                # Parse timestamp
                if timestamp_str:
                    try:
                        ts = datetime.fromtimestamp(int(timestamp_str), tz=UTC)
                    except (ValueError, OSError):
                        ts = datetime.now(UTC)
                else:
                    ts = datetime.now(UTC)

                # Determine direction from our address perspective
                is_send = from_addr == address
                counterparty = to_addr if is_send else from_addr

                # Detect contract creation (to address is empty = deploy)
                is_deploy = to_addr == "" or tx.get("contractAddress", "")

                # For token transfers, the value field is the token amount
                if is_token_transfer:
                    token_addr = tx.get("contractAddress", "")
                    raw_value = tx.get("value", "0")
                    decimals = int(tx.get("tokenDecimal", 0) or 0)
                    try:
                        dec_val = int(raw_value) / (10**decimals) if decimals else float(raw_value)
                    except (ValueError, ZeroDivisionError):
                        dec_val = 0.0

                    normalized.append(
                        NormalizedTransaction(
                            address=address,
                            counterparty=counterparty,
                            timestamp=ts,
                            amount=dec_val,
                            chain_id=chain,
                            program="erc20",
                            tx_hash=tx_hash,
                            tx_type="send" if is_send else "receive",
                            token_address=token_addr,
                            token_value_raw=raw_value,
                            token_value_decimal=dec_val,
                            transfer_type="erc20",
                            block_number=block_num,
                            is_contract_creation=False,
                        )
                    )
                else:
                    # Native ETH transfer or internal tx
                    raw_value = tx.get("value", "0")
                    try:
                        value_wei = int(raw_value)
                        value_eth = value_wei / 1e18
                    except (ValueError, OverflowError):
                        value_eth = 0.0

                    transfer_type = "native"
                    if is_internal:
                        transfer_type = "internal"

                    normalized.append(
                        NormalizedTransaction(
                            address=address,
                            counterparty=counterparty,
                            timestamp=ts,
                            amount=value_eth,
                            chain_id=chain,
                            program="evm",
                            tx_hash=tx_hash,
                            tx_type="send" if is_send else "receive",
                            transfer_type=transfer_type,
                            block_number=block_num,
                            is_contract_creation=is_deploy,
                        )
                    )

                    # If contract creation, also create a deployer event record
                    if is_deploy and is_send:
                        contract_addr = tx.get("contractAddress", "")
                        if contract_addr:
                            # Redundant store: set counterparty to the deployed contract
                            normalized[-1].counterparty = contract_addr
                            normalized[-1].is_contract_creation = True

            except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
                logger.warning("error: %s", e)
                logger.debug("Failed to normalize Etherscan tx: %s", e)
                continue

        return normalized

    # ── Storage: token transfers ────────────────────────────────────

    async def _store_token_transfers(self, txs: list[NormalizedTransaction]) -> None:
        """Store token transfers in ClickHouse token_transfers table."""
        if not self.storage:
            logger.debug("No storage available - skipping token transfer persistence")
            return

        try:
            await self.storage._ensure_connections()
            ch = self.storage._ch
            if not ch:
                logger.debug("ClickHouse unavailable - skipping token transfer save")
                return

            rows = []
            for tx in txs:
                row = tx.to_transfer_row()
                if row:
                    rows.append(
                        (
                            row["chain_id"],
                            row["block_number"],
                            row["block_timestamp"],
                            row["transaction_hash"],
                            row["log_index"],
                            row["from_address"].lower(),
                            row["to_address"].lower(),
                            row["token_address"].lower(),
                            row["value_raw"],
                            row["value_decimal"],
                            row["transfer_type"],
                            0,  # is_scam_related
                        )
                    )

            if rows:
                # Batch insert
                ch.execute(
                    """INSERT INTO token_transfers
                    (chain_id, block_number, block_timestamp, transaction_hash,
                     log_index, from_address, to_address, token_address,
                     value_raw, value_decimal, transfer_type, is_scam_related)
                    VALUES""",
                    rows,
                )
                logger.info("Stored %s token transfers in ClickHouse", len(rows))

        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning("error: %s", e)
            logger.warning("Token transfer storage failed: %s", e)

    # ── Storage: deployer events ────────────────────────────────────

    async def _record_deployer_events(self, deploy_txs: list[NormalizedTransaction]) -> None:
        """Record detected contract deployment events for deployer tracking."""
        if not self.storage:
            return

        for tx in deploy_txs:
            try:
                token_addr = tx.counterparty or tx.token_address
                if not token_addr:
                    logger.debug("Skipping deployer event with no contract address: %s", tx.tx_hash)
                    continue

                await self.storage.save_deployer_event(
                    deployer=tx.address,
                    chain_id=tx.chain_id,
                    token_address=token_addr,
                    deployed_at=tx.timestamp,
                    outcome="unknown",
                    is_scam=False,
                    risk_score=0.0,
                )
                logger.info(f"Recorded deployer event: {tx.address} deployed {token_addr} on {tx.chain_id}")

            except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
                logger.warning("error: %s", e)
                logger.debug("Deployer event record failed: %s", e)

    # ── Cleanup ─────────────────────────────────────────────────────

    async def __aenter__(self) -> None:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


# ── Global singleton ────────────────────────────────────────────────

_pipeline: WalletIngestionPipeline | None = None


def get_ingestion_pipeline(engine=None, storage=None) -> WalletIngestionPipeline:
    """Get global ingestion pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = WalletIngestionPipeline(engine=engine, storage=storage)
    else:
        # Update engine/storage references if provided
        if engine is not None:
            _pipeline.engine = engine
        if storage is not None:
            _pipeline.storage = storage
    return _pipeline
