"""CryptoPanic-style story grouping.

The first article of a story gets a stable ``grp:{sha256(url)[:12]}`` id;
duplicate variations are recorded as members of that group so every source
URL maps back to the story that is represented once in the corpus.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.utils.dedup_config import (
    CONTENT_GROUP_HASH_PREFIX,
    GROUP_MEMBERS_PREFIX,
    GROUP_MEMBERS_TTL_DAYS,
    URL_GROUP_HASH,
    get_redis,
)
from app.utils.dedup_hashing import url_hash

log = get_logger(__name__)


def new_group_id(url: str) -> str:
    """Create a stable group_id for a new story from its first URL."""
    return f"grp:{url_hash(url)}"


def record_group_membership(group_id: str, url: str, collection: str) -> None:
    """Map a URL to its story group and add it to the group's member set."""
    try:
        r = get_redis()
        if r is None:
            return
        pipe = r.pipeline()
        pipe.hset(URL_GROUP_HASH, url_hash(url), group_id)
        pipe.sadd(f"{GROUP_MEMBERS_PREFIX}{group_id}", url)
        pipe.expire(f"{GROUP_MEMBERS_PREFIX}{group_id}", GROUP_MEMBERS_TTL_DAYS * 86400)
        pipe.execute()
    except Exception as e:
        log.debug("dedup_group_membership_failed err=%s", e)


def record_fp_group(fp: str, group_id: str, collection: str) -> None:
    """Map a content fingerprint to its story group."""
    try:
        r = get_redis()
        if r is None:
            return
        r.hset(f"{CONTENT_GROUP_HASH_PREFIX}{collection}", fp, group_id)
    except Exception as e:
        log.debug("dedup_fp_group_failed err=%s", e)
