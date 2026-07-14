"""DataBus Key Affinity - Consistent Hashing for API Key Selection"""

import hashlib
import logging

logger = logging.getLogger("databus.key_affinity")


class KeyAffinitySelector:
    """Select API keys with consistent affinity based on request params.

    Same entity (address, token) always uses the same key.
    This maximizes cache locality in downstream rate-limited APIs.
    When a key is exhausted, remaining traffic redistributes to healthy keys.
    """

    def __init__(self):
        self._assignments: dict[str, str] = {}  # entity_hash -> key_id

    def select_key(self, pool_name: str, available_keys: list, **kwargs) -> object | None:
        """Select a key using consistent hashing of request params."""
        if not available_keys:
            return None
        if len(available_keys) == 1:
            return available_keys[0]
        # Hash the most relevant parameter
        entity = kwargs.get("address") or kwargs.get("mint") or kwargs.get("token") or kwargs.get("entity") or ""
        if entity:
            affinity_key = f"{pool_name}:{entity}"
            # Check cached assignment
            assigned = self._assignments.get(affinity_key)
            if assigned:
                # Verify the assigned key is still available
                for k in available_keys:
                    if k.key_id == assigned and k.is_available():
                        return k
            # New assignment via consistent hash
            h = int(hashlib.sha256(affinity_key.encode()).hexdigest(), 16)
            idx = h % len(available_keys)
            selected = available_keys[idx]
            if selected.is_available():
                self._assignments[affinity_key] = selected.key_id
                return selected
        # Fallback: first available
        for k in available_keys:
            if k.is_available():
                return k
        return None

    def stats(self) -> dict:
        return {"affinity_assignments": len(self._assignments)}


# Module-level singleton instance
key_affinity = KeyAffinitySelector()
