"""
Wallet Memory Storage - ClickHouse + Redis unified interface.
=============================================================
ClickHouse: persistent analytical store (transfers, entities, risk scores).
Redis: hot cache for real-time lookups (5min TTL on profiles, 1hr on labels).

Graceful degradation: if ClickHouse is unavailable, falls back to Redis-only
(what the old system did). If Redis is down, falls back to ClickHouse direct.
If both are down, returns empty results with a warning.
"""

import contextlib
import json
import os
from datetime import UTC, datetime

import redis.asyncio as aioredis

from app.core.logging import get_logger
from typing import Any

logger = get_logger("wallet_memory.storage")

# ── ClickHouse connection (lazy, optional) ──────────────────────────

_ch_client = None


async def _get_clickhouse() -> Any | None:
    """Lazy-init ClickHouse client. Returns None if unavailable.

    Win #14: Degradation-aware — if ClickHouse is down, the degradation
    registry is notified so X-Degraded-Stores header is set and DuckDB
    fallback can be used.
    """
    global _ch_client
    if _ch_client is not None:
        return _ch_client
    try:
        import clickhouse_driver

        ch_host = os.getenv("CLICKHOUSE_HOST", "localhost")
        ch_port = int(os.getenv("CLICKHOUSE_PORT", "9000"))
        ch_user = os.getenv("CLICKHOUSE_USER", "default")
        ch_pass = os.getenv("CLICKHOUSE_PASSWORD", "")
        ch_db = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")
        _ch_client = clickhouse_driver.Client(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=5,
            send_receive_timeout=30,
        )
        _ch_client.execute("SELECT 1")
        logger.info(f"ClickHouse connected: {ch_host}:{ch_port}/{ch_db}")
        _try_mark_healthy("clickhouse")
        return _ch_client
    except (ValueError, ConnectionError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.warning(f"ClickHouse unavailable, running Redis-only: {e}")
        _ch_client = None
        _try_mark_down("clickhouse")
        return None


def _try_mark_healthy(store: str) -> None:
    try:
        from app.core.degradation import get_registry

        get_registry().mark_healthy(store)
    except Exception:
        pass


def _try_mark_down(store: str) -> None:
    try:
        from app.core.degradation import get_registry

        get_registry().mark_down(store)
    except Exception:
        pass


# ── Redis connection (lazy) ─────────────────────────────────────────

_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis | None:
    """Lazy-init Redis client. Returns None if unavailable."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        redis_url = os.getenv("REDIS_URL", "")
        if redis_url and "://" in redis_url:
            url = redis_url
        else:
            r_host = os.getenv("REDIS_HOST", "localhost")
            r_port = os.getenv("REDIS_PORT", "6379")
            r_db = os.getenv("REDIS_DB", "0")
            r_pass = os.getenv("REDIS_PASSWORD", "")
            url = (
                f"redis://:{r_pass}@{r_host}:{r_port}/{r_db}"
                if r_pass
                else f"redis://{r_host}:{r_port}/{r_db}"
            )
        _redis_client = aioredis.from_url(url, decode_responses=True)
        await _redis_client.ping()
        logger.info(f"Redis connected for wallet memory: {url.split('@')[-1]}")
        return _redis_client
    except (ValueError, ConnectionError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.warning(f"Redis unavailable for wallet memory: {e}")
        _redis_client = None
        return None


# ── Schema DDL ──────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Core fact table: all token transfers across all chains
CREATE TABLE IF NOT EXISTS token_transfers (
    chain_id      String,
    block_number   UInt64,
    block_timestamp DateTime,
    transaction_hash String,
    log_index      UInt32,
    from_address   String,
    to_address     String,
    token_address  String,
    value_raw      String,
    value_decimal  Float64,
    transfer_type  LowCardinality(String),  -- erc20, erc721, native, spl
    is_scam_related UInt8 DEFAULT 0
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(block_timestamp)
ORDER BY (chain_id, block_number, transaction_hash, log_index);

-- Entity resolution: clustered wallets
CREATE TABLE IF NOT EXISTS wallet_entities (
    entity_id       String,
    wallet_address  String,
    chain_id        String,
    heuristic_type  LowCardinality(String),
    confidence_score Float32,
    entity_label    String DEFAULT '',
    entity_category LowCardinality(String) DEFAULT 'unknown',
    first_linked_at DateTime DEFAULT now(),
    last_updated_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(last_updated_at)
ORDER BY (wallet_address, chain_id);

-- Scam intelligence
CREATE TABLE IF NOT EXISTS scam_addresses (
    address     String,
    chain_id    String,
    source      LowCardinality(String),
    threat_type LowCardinality(String),
    confidence  Float32,
    first_seen_at DateTime,
    evidence    String DEFAULT ''
) ENGINE = MergeTree()
ORDER BY (address, chain_id);

-- Aggregated wallet profiles (materialized)
CREATE TABLE IF NOT EXISTS wallet_profiles (
    wallet_address     String,
    chain_id           String,
    entity_id          String DEFAULT '',
    entity_label       String DEFAULT '',
    entity_category    LowCardinality(String) DEFAULT 'unknown',
    total_transactions UInt64 DEFAULT 0,
    total_volume       Float64 DEFAULT 0,
    unique_counterparties UInt32 DEFAULT 0,
    first_seen_at      DateTime,
    last_seen_at       DateTime,
    risk_score         Float32 DEFAULT 0,
    risk_level         LowCardinality(String) DEFAULT 'unknown',
    updated_at         DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (wallet_address, chain_id);

-- Deployer history: tokens deployed by a wallet
CREATE TABLE IF NOT EXISTS deployer_history (
    deployer_address String,
    chain_id         String,
    token_address    String,
    token_name       String DEFAULT '',
    token_symbol     String DEFAULT '',
    deployed_at      DateTime,
    outcome          LowCardinality(String) DEFAULT 'unknown',
    lifespan_days    Float32 DEFAULT 0,
    max_market_cap   Float64 DEFAULT 0,
    is_scam_related  UInt8 DEFAULT 0,
    scan_risk_score  Float32 DEFAULT 0
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(deployed_at)
ORDER BY (deployer_address, chain_id, deployed_at);

-- Cross-chain bridge mappings
CREATE TABLE IF NOT EXISTS bridge_mappings (
    source_address String,
    source_chain   String,
    dest_address   String,
    dest_chain     String,
    bridge_name    LowCardinality(String),
    tx_hash        String,
    bridged_at     DateTime,
    confidence     Float32 DEFAULT 1.0
) ENGINE = MergeTree()
ORDER BY (source_address, source_chain);
"""


async def ensure_schema() -> bool:
    """Create ClickHouse tables if they don't exist. Safe to call on startup."""
    ch = await _get_clickhouse()
    if not ch:
        logger.warning("Cannot ensure ClickHouse schema - ClickHouse unavailable")
        return False
    try:
        # Create database first
        ch.execute("CREATE DATABASE IF NOT EXISTS wallet_memory")
        for stmt in SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                ch.execute(stmt)
        logger.info("ClickHouse schema ensured")
        return True
    except (ValueError, ConnectionError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.error(f"ClickHouse schema creation failed: {e}")
        return False


# ── Storage interface ────────────────────────────────────────────────


class WalletStorage:
    """Unified storage with ClickHouse + Redis, transparent fallback."""

    # Cache TTLs
    PROFILE_TTL = 300  # 5 minutes for wallet profiles
    LABEL_TTL = 3600  # 1 hour for labels
    RISK_TTL = 300  # 5 minutes for risk scores
    ENTITY_TTL = 600  # 10 minutes for entity lookups

    def __init__(self) -> None:
        self._ch = None
        self._redis = None

    async def _ensure_connections(self) -> None:
        """Lazy connect on first use."""
        if self._ch is None:
            self._ch = await _get_clickhouse()
        if self._redis is None:
            self._redis = await _get_redis()

    # ── Wallet profile ───────────────────────────────────────────

    async def get_wallet_profile(self, address: str, chain_id: str) -> dict | None:
        """Get wallet profile with cache-first strategy."""
        await self._ensure_connections()
        cache_key = f"wm:profile:{chain_id}:{address.lower()}"

        # Try Redis cache
        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")

        # Try ClickHouse
        profile = None
        if self._ch:
            try:
                rows = self._ch.execute(
                    "SELECT * FROM wallet_profiles WHERE wallet_address = %(addr)s AND chain_id = %(chain)s",
                    {"addr": address.lower(), "chain": chain_id},
                )
                if rows:
                    cols = [d[0] for d in self._ch.execute("DESCRIBE wallet_profiles")]
                    profile = dict(zip(cols, rows[0], strict=False))
                _try_mark_healthy("clickhouse")
            except (ValueError, ConnectionError, RuntimeError) as e:
                logger.warning(f"error: {e}")
                logger.debug(f"ClickHouse profile query failed: {e}")
                _try_mark_down("clickhouse")

        # Cache the result (even None for negative caching, short TTL)
        if self._redis:
            try:
                ttl = self.PROFILE_TTL if profile else 60  # Negative cache: 1 min
                await self._redis.set(cache_key, json.dumps(profile), ex=ttl)
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")

        return profile

    async def save_wallet_profile(self, profile: dict) -> bool:
        """Persist wallet profile to both ClickHouse and Redis cache."""
        await self._ensure_connections()
        addr = profile.get("wallet_address", "").lower()
        chain = profile.get("chain_id", "")

        # Save to ClickHouse
        if self._ch:
            try:
                self._ch.execute(
                    """INSERT INTO wallet_profiles
                    (wallet_address, chain_id, entity_id, entity_label, entity_category,
                     total_transactions, total_volume, unique_counterparties,
                     first_seen_at, last_seen_at, risk_score, risk_level, updated_at)
                    VALUES""",
                    [
                        (
                            addr,
                            chain,
                            profile.get("entity_id", ""),
                            profile.get("entity_label", ""),
                            profile.get("entity_category", "unknown"),
                            profile.get("total_transactions", 0),
                            profile.get("total_volume", 0.0),
                            profile.get("unique_counterparties", 0),
                            profile.get("first_seen_at", datetime(2020, 1, 1)),
                            profile.get("last_seen_at", datetime.now(UTC)),
                            profile.get("risk_score", 0.0),
                            profile.get("risk_level", "unknown"),
                            datetime.now(UTC),
                        )
                    ],
                )
            except (ValueError, ConnectionError, RuntimeError) as e:
                logger.warning(f"error: {e}")
                logger.warning(f"ClickHouse profile save failed: {e}")

        # Update Redis cache
        if self._redis:
            try:
                cache_key = f"wm:profile:{chain}:{addr}"
                await self._redis.set(cache_key, json.dumps(profile), ex=self.PROFILE_TTL)
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")

        return True

    # ── Entity lookups ──────────────────────────────────────────

    async def get_entity_for_wallet(self, address: str, chain_id: str) -> dict | None:
        """Get entity membership for a wallet address."""
        await self._ensure_connections()
        cache_key = f"wm:entity:{chain_id}:{address.lower()}"

        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")

        result = None
        if self._ch:
            try:
                rows = self._ch.execute(
                    """SELECT entity_id, entity_label, entity_category, confidence_score, heuristic_type
                    FROM wallet_entities
                    WHERE wallet_address = %(addr)s AND chain_id = %(chain)s
                    ORDER BY confidence_score DESC LIMIT 1""",
                    {"addr": address.lower(), "chain": chain_id},
                )
                if rows:
                    result = {
                        "entity_id": rows[0][0],
                        "entity_label": rows[0][1],
                        "entity_category": rows[0][2],
                        "confidence_score": rows[0][3],
                        "heuristic_type": rows[0][4],
                    }
            except (ValueError, ConnectionError, RuntimeError) as e:
                logger.warning(f"error: {e}")
                logger.debug(f"ClickHouse entity query failed: {e}")
                _try_mark_down("clickhouse")

        if self._redis and result:
            with contextlib.suppress(Exception):
                await self._redis.set(cache_key, json.dumps(result), ex=self.ENTITY_TTL)

        return result

    async def get_entity_wallets(self, entity_id: str) -> list[dict]:
        """Get all wallets linked to an entity across all chains."""
        await self._ensure_connections()
        if not self._ch:
            return []

        try:
            rows = self._ch.execute(
                """SELECT wallet_address, chain_id, confidence_score, heuristic_type, entity_category
                FROM wallet_entities WHERE entity_id = %(eid)s
                ORDER BY confidence_score DESC""",
                {"eid": entity_id},
            )
            return [
                {
                    "wallet_address": r[0],
                    "chain_id": r[1],
                    "confidence_score": r[2],
                    "heuristic_type": r[3],
                    "entity_category": r[4],
                }
                for r in rows
            ]
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            logger.debug(f"ClickHouse entity wallets query failed: {e}")
            return []

    async def save_entity_link(
        self,
        entity_id: str,
        wallet_address: str,
        chain_id: str,
        heuristic_type: str,
        confidence: float,
        label: str = "",
        category: str = "unknown",
    ) -> bool:
        """Save an entity-wallet link to ClickHouse."""
        if not self._ch:
            # Fallback: store in Redis set
            if self._redis:
                try:
                    key = f"wm:entity_members:{entity_id}"
                    await self._redis.sadd(
                        key,
                        json.dumps(
                            {
                                "wallet_address": wallet_address.lower(),
                                "chain_id": chain_id,
                                "heuristic_type": heuristic_type,
                                "confidence": confidence,
                            }
                        ),
                    )
                except (ValueError, ConnectionError, RuntimeError):
                    logger.warning("error")
            return True

        try:
            self._ch.execute(
                """INSERT INTO wallet_entities
                (entity_id, wallet_address, chain_id, heuristic_type,
                 confidence_score, entity_label, entity_category, first_linked_at, last_updated_at)
                VALUES""",
                [
                    (
                        entity_id,
                        wallet_address.lower(),
                        chain_id,
                        heuristic_type,
                        confidence,
                        label,
                        category,
                        datetime.now(UTC),
                        datetime.now(UTC),
                    )
                ],
            )
            # Invalidate cache
            if self._redis:
                try:
                    cache_key = f"wm:entity:{chain_id}:{wallet_address.lower()}"
                    await self._redis.delete(cache_key)
                except (ValueError, ConnectionError, RuntimeError):
                    logger.warning("error")
            return True
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            logger.warning(f"Entity link save failed: {e}")
            return False

    # ── Scam addresses ───────────────────────────────────────────

    async def check_scam_address(self, address: str, chain_id: str) -> dict | None:
        """Check if an address is in the scam database."""
        await self._ensure_connections()
        cache_key = f"wm:scam:{chain_id}:{address.lower()}"

        if self._redis:
            try:
                cached = await self._redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")

        result = None
        if self._ch:
            try:
                rows = self._ch.execute(
                    """SELECT source, threat_type, confidence, first_seen_at, evidence
                    FROM scam_addresses
                    WHERE address = %(addr)s AND chain_id = %(chain)s""",
                    {"addr": address.lower(), "chain": chain_id},
                )
                if rows:
                    result = {
                        "source": rows[0][0],
                        "threat_type": rows[0][1],
                        "confidence": rows[0][2],
                        "first_seen_at": str(rows[0][3]),
                        "evidence": rows[0][4],
                    }
            except (ValueError, ConnectionError, RuntimeError) as e:
                logger.warning(f"error: {e}")
                logger.debug(f"ClickHouse scam query failed: {e}")

        if self._redis:
            with contextlib.suppress(Exception):
                await self._redis.set(cache_key, json.dumps(result), ex=self.LABEL_TTL)

        return result

    async def save_scam_address(
        self,
        address: str,
        chain_id: str,
        source: str,
        threat_type: str,
        confidence: float = 1.0,
        evidence: str = "",
    ) -> bool:
        """Add address to scam database."""
        if self._ch:
            try:
                self._ch.execute(
                    """INSERT INTO scam_addresses
                    (address, chain_id, source, threat_type, confidence, first_seen_at, evidence)
                    VALUES""",
                    [
                        (
                            address.lower(),
                            chain_id,
                            source,
                            threat_type,
                            confidence,
                            datetime.now(UTC),
                            evidence,
                        )
                    ],
                )
            except (ValueError, ConnectionError, RuntimeError) as e:
                logger.warning(f"error: {e}")
                logger.warning(f"Scam address save failed: {e}")

        # Update Redis
        if self._redis:
            try:
                cache_key = f"wm:scam:{chain_id}:{address.lower()}"
                data = {"source": source, "threat_type": threat_type, "confidence": confidence}
                await self._redis.set(cache_key, json.dumps(data), ex=self.LABEL_TTL)
                # Also add to Redis set for fast batch checks
                await self._redis.sadd(f"wm:scam_set:{chain_id}", address.lower())
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")
        return True

    # ── Deployer history ─────────────────────────────────────────

    async def get_deployer_history(self, address: str, chain_id: str = "") -> list[dict]:
        """Get all tokens deployed by a wallet across chains."""
        await self._ensure_connections()
        if not self._ch:
            return []

        try:
            if chain_id:
                rows = self._ch.execute(
                    """SELECT chain_id, token_address, token_name, token_symbol,
                              deployed_at, outcome, lifespan_days, max_market_cap,
                              is_scam_related, scan_risk_score
                    FROM deployer_history
                    WHERE deployer_address = %(addr)s AND chain_id = %(chain)s
                    ORDER BY deployed_at DESC LIMIT 50""",
                    {"addr": address.lower(), "chain": chain_id},
                )
            else:
                rows = self._ch.execute(
                    """SELECT chain_id, token_address, token_name, token_symbol,
                              deployed_at, outcome, lifespan_days, max_market_cap,
                              is_scam_related, scan_risk_score
                    FROM deployer_history
                    WHERE deployer_address = %(addr)s
                    ORDER BY deployed_at DESC LIMIT 100""",
                    {"addr": address.lower()},
                )
            return [
                {
                    "chain_id": r[0],
                    "token_address": r[1],
                    "token_name": r[2],
                    "token_symbol": r[3],
                    "deployed_at": str(r[4]),
                    "outcome": r[5],
                    "lifespan_days": r[6],
                    "max_market_cap": r[7],
                    "is_scam_related": r[8],
                    "scan_risk_score": r[9],
                }
                for r in rows
            ]
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            logger.debug(f"ClickHouse deployer history query failed: {e}")
            return []

    async def save_deployer_event(
        self,
        deployer: str,
        chain_id: str,
        token_address: str,
        token_name: str = "",
        token_symbol: str = "",
        deployed_at: datetime | None = None,
        outcome: str = "unknown",
        is_scam: bool = False,
        risk_score: float = 0.0,
    ) -> bool:
        """Record a token deployment by a wallet."""
        if self._ch:
            try:
                self._ch.execute(
                    """INSERT INTO deployer_history
                    (deployer_address, chain_id, token_address, token_name, token_symbol,
                     deployed_at, outcome, is_scam_related, scan_risk_score)
                    VALUES""",
                    [
                        (
                            deployer.lower(),
                            chain_id,
                            token_address,
                            token_name,
                            token_symbol,
                            deployed_at or datetime.now(UTC),
                            outcome,
                            1 if is_scam else 0,
                            risk_score,
                        )
                    ],
                )
            except (ValueError, ConnectionError, RuntimeError) as e:
                logger.warning(f"error: {e}")
                logger.warning(f"Deployer event save failed: {e}")
        return True

    # ── Bridge mappings ──────────────────────────────────────────

    async def get_bridge_links(self, address: str, chain_id: str) -> list[dict]:
        """Get cross-chain bridge mappings for a wallet."""
        await self._ensure_connections()
        if not self._ch:
            return []
        try:
            rows = self._ch.execute(
                """SELECT dest_address, dest_chain, bridge_name, tx_hash, bridged_at, confidence
                FROM bridge_mappings
                WHERE source_address = %(addr)s AND source_chain = %(chain)s""",
                {"addr": address.lower(), "chain": chain_id},
            )
            return [
                {
                    "dest_address": r[0],
                    "dest_chain": r[1],
                    "bridge_name": r[2],
                    "tx_hash": r[3],
                    "bridged_at": str(r[4]),
                    "confidence": r[5],
                }
                for r in rows
            ]
        except (ValueError, ConnectionError, RuntimeError) as e:
            logger.warning(f"error: {e}")
            logger.debug(f"ClickHouse bridge query failed: {e}")
            return []

    async def save_bridge_link(
        self,
        source_addr: str,
        source_chain: str,
        dest_addr: str,
        dest_chain: str,
        bridge_name: str,
        tx_hash: str,
        confidence: float = 1.0,
    ) -> bool:
        """Save a cross-chain bridge mapping."""
        if self._ch:
            try:
                self._ch.execute(
                    """INSERT INTO bridge_mappings
                    (source_address, source_chain, dest_address, dest_chain,
                     bridge_name, tx_hash, bridged_at, confidence)
                    VALUES""",
                    [
                        (
                            source_addr.lower(),
                            source_chain,
                            dest_addr.lower(),
                            dest_chain,
                            bridge_name,
                            tx_hash,
                            datetime.now(UTC),
                            confidence,
                        )
                    ],
                )
            except (ValueError, ConnectionError, RuntimeError) as e:
                logger.warning(f"error: {e}")
                logger.warning(f"Bridge link save failed: {e}")
        return True

    # ── Quick Redis-only lookups (backward compat) ────────────────

    async def redis_get(self, key: str) -> str | None:
        """Direct Redis get for backward compatibility with wallet_label_loader."""
        if self._redis:
            try:
                return await self._redis.get(key)
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")
        return None

    async def redis_set(self, key: str, value: str, ttl: int = 0) -> bool:
        """Direct Redis set with optional TTL."""
        if self._redis:
            try:
                if ttl:
                    await self._redis.set(key, value, ex=ttl)
                else:
                    await self._redis.set(key, value)
                return True
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")
        return False

    async def redis_sadd(self, key: str, *values: str) -> bool:
        """Add to Redis set."""
        if self._redis:
            try:
                await self._redis.sadd(key, *values)
                return True
            except (ValueError, ConnectionError, RuntimeError):
                logger.warning("error")
        return False


# ── Global singleton ─────────────────────────────────────────────────

_storage: WalletStorage | None = None


def get_storage() -> WalletStorage:
    """Get global storage instance."""
    global _storage
    if _storage is None:
        _storage = WalletStorage()
    return _storage
