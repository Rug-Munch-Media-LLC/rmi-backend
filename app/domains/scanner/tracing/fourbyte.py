"""4byte directory client — event signature resolution with Redis caching.

For each receipt log we extract topic0 (the event signature hash) and query
the 4byte directory API (https://www.4byte.directory) — free, no auth — to
map it to a human-readable signature such as::

    Transfer(address indexed from, address indexed to, uint256 value)

Matches are cached in Redis (``tracing:4byte:{hash}``) with a 7-day TTL so
repeat lookups never touch the network. Redis failure degrades gracefully
to direct API calls (see app.core.redis — callers check degraded mode).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from eth_abi.abi import decode
from eth_utils.address import to_checksum_address

from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

FOURBYTE_API = "https://www.4byte.directory/api/v1/event-signatures/"
CACHE_PREFIX = "tracing:4byte:"
CACHE_TTL = 7 * 24 * 3600  # 7 days
REQUEST_TIMEOUT = 8.0

# Static ABI types that can be decoded directly from a 32-byte topic.
_STATIC_TYPES = (
    "address",
    "bool",
    "uint",
    "int",
    "bytes1",
    "bytes2",
    "bytes3",
    "bytes4",
    "bytes5",
    "bytes6",
    "bytes7",
    "bytes8",
    "bytes9",
    "bytes10",
    "bytes11",
    "bytes12",
    "bytes13",
    "bytes14",
    "bytes15",
    "bytes16",
    "bytes17",
    "bytes18",
    "bytes19",
    "bytes20",
    "bytes21",
    "bytes22",
    "bytes23",
    "bytes24",
    "bytes25",
    "bytes26",
    "bytes27",
    "bytes28",
    "bytes29",
    "bytes30",
    "bytes31",
    "bytes32",
    "fixed",
    "ufixed",
)


@dataclass
class ParamSpec:
    """A single event input parameter."""

    type: str
    name: str | None = None
    indexed: bool = False


@dataclass
class ParsedSignature:
    """Parsed human-readable event signature."""

    name: str
    inputs: list[ParamSpec]
    raw: str


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on separator, ignoring separators inside () and [] groups."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def parse_event_signature(text: str) -> ParsedSignature | None:
    """Parse ``Name(type indexed name, ...)`` into a structured signature.

    Handles tuples ``(address,uint256)[]``, arrays, and the ``indexed``
    keyword. Returns None for malformed signatures.
    """
    stripped = text.strip()
    match = re.match(r"^(\w+)\((.*)\)$", stripped, flags=re.DOTALL)
    if not match:
        return None
    name = match.group(1)
    inner = match.group(2)
    inputs: list[ParamSpec] = []
    for raw_param in _split_top_level(inner):
        param = raw_param.strip()
        if not param:
            continue
        # Remove whitespace inside tuple groups (e.g. "(address, uint256)")
        normalized = re.sub(r"\s+", " ", param).strip()
        parts = normalized.split(" ")
        if not parts or not parts[0]:
            continue
        typ = parts[0]
        indexed = "indexed" in parts[1:]
        arg_name: str | None = None
        for part in parts[1:]:
            if part != "indexed":
                arg_name = part
        inputs.append(ParamSpec(type=typ, name=arg_name, indexed=indexed))
    return ParsedSignature(name=name, inputs=inputs, raw=stripped)


def _is_static_type(typ: str) -> bool:
    """True for ABI types whose value fits in a single 32-byte word."""
    if typ in _STATIC_TYPES:
        return True
    # uintN / intN for any bit width 8..256 (multiple of 8)
    if re.match(
        r"^(u?int)(8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)$",
        typ,
    ):
        return True
    # bytesN handled in _STATIC_TYPES; fixed/ufixed via prefix match
    return typ.startswith(("fixed", "ufixed"))


def _value_to_jsonable(value: Any) -> Any:
    """Convert eth_abi decoded values to JSON-safe primitives."""
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, (tuple, list)):
        return [_value_to_jsonable(v) for v in value]
    if isinstance(value, str):
        try:
            return to_checksum_address(value)
        except Exception:
            return value
    return value


async def _fetch_from_api(hex_signature: str) -> str | None:
    """Query the 4byte directory API for a topic0 hash. Returns text_signature or None."""
    url = FOURBYTE_API
    params = {"hex_signature": hex_signature}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.debug("4byte http_%s for %s", resp.status_code, hex_signature)
                return None
            data = resp.json()
            results = data.get("results") or []
            if not results:
                return None
            first = results[0]
            return first.get("text_signature") or None
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError) as e:
        logger.debug("4byte fetch failed for %s: %s", hex_signature, e)
        return None


async def _redis_get(key: str) -> str | None:
    """Best-effort Redis GET. Returns None when Redis is unavailable."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return None
        value = await r.get(key)
        if value is None:
            return None
        decoded = value.decode("utf-8") if isinstance(value, bytes) else value
        return json.loads(decoded) if decoded else None
    except Exception as e:
        logger.debug("4byte redis get failed: %s", e)
        return None


async def _redis_set(key: str, value: str, ttl: int = CACHE_TTL) -> None:
    """Best-effort Redis SETEX. Never raises."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return
        await r.setex(key, ttl, json.dumps(value))
    except Exception as e:
        logger.debug("4byte redis set failed: %s", e)


async def resolve_event_signature(hex_signature: str) -> str | None:
    """Resolve a topic0 hash to a human-readable event signature.

    Checks Redis first (``tracing:4byte:{hash}``), falls back to the 4byte
    directory API, and caches the result for 7 days.
    """
    if not hex_signature or not hex_signature.startswith("0x"):
        return None
    key = f"{CACHE_PREFIX}{hex_signature.lower()}"
    cached = await _redis_get(key)
    if cached:
        return cached or None
    signature = await _fetch_from_api(hex_signature)
    await _redis_set(key, signature or "")
    return signature


def decode_log(
    log: dict[str, Any],
    signature: str | None,
) -> tuple[str | None, str | None, dict[str, Any]]:
    """Decode a receipt log into (event_name, full_signature, args).

    Indexed static args are decoded from topics[1:]; indexed dynamic args
    (string/bytes/arrays) are keccak hashes and returned as hex. Non-indexed
    args are ABI-decoded from ``data``.
    """
    topics = log.get("topics") or []
    data = log.get("data") or "0x"
    if not topics:
        return None, None, {}

    parsed = parse_event_signature(signature) if signature else None
    if parsed is None:
        return None, signature, {}

    indexed_topics = topics[1:]
    non_indexed_specs = [s for s in parsed.inputs if not s.indexed]

    args: dict[str, Any] = {}
    topic_idx = 0
    data_types: list[str] = []

    for spec in parsed.inputs:
        key = spec.name or f"arg{len(args)}"
        if spec.indexed:
            if topic_idx < len(indexed_topics):
                topic_hex = indexed_topics[topic_idx]
                topic_idx += 1
                if _is_static_type(spec.type):
                    try:
                        topic_bytes = bytes.fromhex(topic_hex[2:])
                        if len(topic_bytes) == 32:
                            decoded = decode([spec.type], topic_bytes)[0]
                            args[key] = _value_to_jsonable(decoded)
                        else:
                            args[key] = topic_hex
                    except Exception as e:
                        logger.debug("indexed arg decode failed %s: %s", spec.type, e)
                        args[key] = topic_hex
                else:
                    # Dynamic indexed value: topic is keccak256(value) — cannot reverse.
                    args[key] = topic_hex
            else:
                args[key] = None
        else:
            data_types.append(spec.type)

    if data_types:
        try:
            data_bytes = bytes.fromhex(data[2:]) if data.startswith("0x") else bytes.fromhex(data)
            if data_bytes:
                decoded_values = decode(data_types, data_bytes)
                di = 0
                for spec in non_indexed_specs:
                    key = spec.name or f"arg{len(args)}"
                    if di < len(decoded_values):
                        args[key] = _value_to_jsonable(decoded_values[di])
                        di += 1
                    else:
                        args[key] = None
        except Exception as e:
            logger.debug("data decode failed (%s): %s", data_types, e)

    return parsed.name, parsed.raw, args
