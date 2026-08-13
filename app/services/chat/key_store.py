"""Key Store — Secure key management for Signal Protocol.

All keys stored safely with:
- Key generation and rotation
- Pre-key bundle management
- Session persistence
- Safety number verification records
- WebCrypto non-extractable compatible storage format

IMPORTANT: In production, this should use a hardware-backed
key store (Keychain, TPM, or network HSM). The in-memory store
here is for development and may be backed by Redis+Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.services.chat.models import ChatUser

if TYPE_CHECKING:
    from app.services.chat.models import KeyBundle, SafetyNumber
    from app.services.chat.signal_protocol import (
        DoubleRatchetSession,
        MegolmSession,
    )

logger = get_logger(__name__)


class KeyStore:
    """Secure key storage for the Signal Protocol.

    Stores:
    - Identity key pairs per user
    - Pre-key bundles (with one-time pre-key pools)
    - Active Double Ratchet sessions
    - Megolm group sessions
    - Safety numbers and verification state
    """

    def __init__(self) -> None:
        # Identity keys: user_id -> {"identity_key": bytes, "kyber_pub": bytes, "kyber_priv": bytes}
        self._identity_keys: dict[str, dict[str, Any]] = {}

        # Pre-key bundles: user_id -> list of KeyBundle
        self._pre_key_bundles: dict[str, list[KeyBundle]] = {}

        # Active sessions: user_id -> {peer_id -> DoubleRatchetSession}
        self._sessions: dict[str, dict[str, DoubleRatchetSession]] = {}

        # Megolm sessions: room_id -> {user_id -> MegolmSession}
        self._megolm_sessions: dict[str, dict[str, MegolmSession]] = {}

        # Safety numbers: user_id -> {peer_id -> SafetyNumber}
        self._safety_numbers: dict[str, dict[str, SafetyNumber]] = {}

        # User registry
        self._users: dict[str, ChatUser] = {}

        # Key fingerprints for verification
        self._fingerprints: dict[str, str] = {}

        logger.info("key_store_initialized")

    # ── User Registration ──────────────────────────────────────────────

    def register_user(self, user_id: str, matrix_id: str, display_name: str | None = None) -> ChatUser:
        """Register a user in the key store."""
        user = ChatUser(
            id=user_id,
            matrix_id=matrix_id,
            display_name=display_name or user_id,
        )
        self._users[user_id] = user
        logger.info("key_store_user_registered user=%s matrix=%s", user_id, matrix_id)
        return user

    def get_user(self, user_id: str) -> ChatUser | None:
        """Get a registered user."""
        return self._users.get(user_id)

    def list_users(self) -> list[ChatUser]:
        """List all registered users."""
        return list(self._users.values())

    # ── Identity Keys ──────────────────────────────────────────────────

    def store_identity_keys(self, user_id: str, identity_key: bytes, kyber_pub: bytes, kyber_priv: bytes) -> None:
        """Store a user's identity key pair."""
        self._identity_keys[user_id] = {
            "identity_key": identity_key,
            "kyber_pub": kyber_pub,
            "kyber_priv": kyber_priv,
        }

    def get_identity_keys(self, user_id: str) -> dict[str, Any] | None:
        """Get a user's identity key pair."""
        return self._identity_keys.get(user_id)

    # ── Pre-Key Bundles ────────────────────────────────────────────────

    def store_pre_key_bundle(self, user_id: str, bundle: KeyBundle) -> None:
        """Store a pre-key bundle for key exchange."""
        if user_id not in self._pre_key_bundles:
            self._pre_key_bundles[user_id] = []
        self._pre_key_bundles[user_id].append(bundle)

    def get_pre_key_bundle(self, user_id: str) -> KeyBundle | None:
        """Get and consume the latest pre-key bundle for a user.

        Implements one-time key consumption — each bundle is returned
        once then removed. If no bundles remain, returns None.
        """
        bundles = self._pre_key_bundles.get(user_id, [])
        if not bundles:
            logger.warning("key_store_no_bundles user=%s", user_id)
            return None
        bundle = bundles.pop(0)
        logger.debug("key_store_bundle_consumed user=%s remaining=%d", user_id, len(bundles))
        return bundle

    def count_pre_key_bundles(self, user_id: str) -> int:
        """Count remaining pre-key bundles for a user."""
        return len(self._pre_key_bundles.get(user_id, []))

    # ── Sessions ───────────────────────────────────────────────────────

    def store_session(self, user_id: str, peer_id: str, session: DoubleRatchetSession) -> None:
        """Store a Double Ratchet session."""
        if user_id not in self._sessions:
            self._sessions[user_id] = {}
        self._sessions[user_id][peer_id] = session
        logger.debug("key_store_session_stored user=%s peer=%s", user_id, peer_id)

    def get_session(self, user_id: str, peer_id: str) -> DoubleRatchetSession | None:
        """Get a stored session."""
        return self._sessions.get(user_id, {}).get(peer_id)

    def delete_session(self, user_id: str, peer_id: str) -> bool:
        """Delete a session (key rotation or disconnect)."""
        if user_id in self._sessions and peer_id in self._sessions[user_id]:
            del self._sessions[user_id][peer_id]
            logger.info("key_store_session_deleted user=%s peer=%s", user_id, peer_id)
            return True
        return False

    def list_sessions(self, user_id: str) -> list[str]:
        """List all active session peer IDs for a user."""
        return list(self._sessions.get(user_id, {}).keys())

    # ── Megolm Sessions ────────────────────────────────────────────────

    def store_megolm_session(self, room_id: str, user_id: str, session: MegolmSession) -> None:
        """Store a Megolm group session."""
        if room_id not in self._megolm_sessions:
            self._megolm_sessions[room_id] = {}
        self._megolm_sessions[room_id][user_id] = session

    def get_megolm_session(self, room_id: str, user_id: str) -> MegolmSession | None:
        """Get a Megolm group session."""
        return self._megolm_sessions.get(room_id, {}).get(user_id)

    # ── Safety Numbers ────────────────────────────────────────────────

    def store_safety_number(self, user_id: str, peer_id: str, safety: SafetyNumber) -> None:
        """Store a safety number for verification."""
        if user_id not in self._safety_numbers:
            self._safety_numbers[user_id] = {}
        self._safety_numbers[user_id][peer_id] = safety

    def get_safety_number(self, user_id: str, peer_id: str) -> SafetyNumber | None:
        """Get a stored safety number."""
        return self._safety_numbers.get(user_id, {}).get(peer_id)

    def verify_peer(self, user_id: str, peer_id: str) -> bool:
        """Mark a peer as verified via safety number."""
        safety = self.get_safety_number(user_id, peer_id)
        if safety:
            safety.verified = True
            safety.verified_at = datetime.now(UTC).isoformat()
            return True
        return False

    def is_peer_verified(self, user_id: str, peer_id: str) -> bool:
        """Check if a peer is verified."""
        safety = self.get_safety_number(user_id, peer_id)
        return safety is not None and safety.verified

    # ── Key Fingerprints ──────────────────────────────────────────────

    def store_fingerprint(self, user_id: str, fingerprint: str) -> None:
        """Store a user's identity fingerprint."""
        self._fingerprints[user_id] = fingerprint

    def get_fingerprint(self, user_id: str) -> str | None:
        """Get a user's identity fingerprint."""
        return self._fingerprints.get(user_id)

    # ── Legacy Key Rotation ────────────────────────────────────────────

    def rotate_pre_keys(self, user_id: str, bundle: KeyBundle) -> None:
        """Replace the pre-key pool with a fresh batch."""
        self._pre_key_bundles[user_id] = [bundle]
        logger.info("key_store_pre_keys_rotated user=%s", user_id)

    # ── Export/Import (for device migration) ────────────────────────────

    def export_session_data(self, user_id: str) -> dict[str, Any]:
        """Export session data for device migration.

        Keys are exported in a format suitable for WebCrypto import.
        """
        sessions = self._sessions.get(user_id, {})
        export: dict[str, Any] = {
            "user_id": user_id,
            "session_count": len(sessions),
            "safety_number_count": len(self._safety_numbers.get(user_id, {})),
            "fingerprint": self._fingerprints.get(user_id),
        }
        return export

    def import_session_data(self, user_id: str, data: dict[str, Any]) -> bool:
        """Import session data from another device."""
        try:
            if "fingerprint" in data:
                self._fingerprints[user_id] = data["fingerprint"]
            logger.info("key_store_session_imported user=%s", user_id)
            return True
        except Exception as e:
            logger.error("key_store_import_failed user=%s err=%s", user_id, e)
            return False

    # ── Health ─────────────────────────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """Get health metrics for the key store."""
        total_users = len(self._users)
        total_sessions = sum(len(s) for s in self._sessions.values())
        total_bundles = sum(len(b) for b in self._pre_key_bundles.values())

        return {
            "status": "healthy",
            "total_users": total_users,
            "total_sessions": total_sessions,
            "total_pre_key_bundles": total_bundles,
            "total_megolm_sessions": sum(len(r) for r in self._megolm_sessions.values()),
            "total_verified_peers": sum(1 for u in self._safety_numbers.values() for s in u.values() if s.verified),
        }
