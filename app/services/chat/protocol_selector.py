"""Protocol Selection Logic — automatically chooses protocols when creating DMs.

When two users want to chat privately:
1. Checks which protocols BOTH users have
2. Creates Matrix room as the canonical chat space
3. Bridges to each user's preferred protocol
4. User A sees messages on Nostr, User B sees them on Farcaster
5. Both can chat seamlessly, encryption maintained end-to-end
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.services.chat.models import ChatUser, Protocol

logger = get_logger(__name__)

# Protocol priority order (most private/Web3-native first)
PROTOCOL_PRIORITY: list[Protocol] = [
    Protocol.MATRIX,  # Canonical — always first
    Protocol.SIGNAL,  # Gold standard privacy
    Protocol.XMTP,  # Wallet-native messaging
    Protocol.WEB3,  # Wallet-to-wallet
    Protocol.NOSTR,  # NIP-17 private DMs
    Protocol.FARCASTER,  # Farcaster DMs
    Protocol.XMPP,  # Mature, federated
    Protocol.TELEGRAM,  # Ubiquitous
    Protocol.DISCORD,  # Gaming/community
    Protocol.BLUESKY,  # AT Protocol
    Protocol.ACTIVITYPUB,  # Fediverse
    Protocol.LENS,  # Lens Protocol
    Protocol.IRC,  # Old guard
]

# Protocol capabilities
_PROTOCOL_CAPABILITIES: dict[Protocol, dict[str, bool]] = {
    Protocol.MATRIX: {"encrypted": True, "federated": True, "self_hosted": True, "web3": False},
    Protocol.NOSTR: {"encrypted": True, "federated": True, "self_hosted": True, "web3": True},
    Protocol.FARCASTER: {"encrypted": True, "federated": False, "self_hosted": False, "web3": True},
    Protocol.BLUESKY: {"encrypted": False, "federated": True, "self_hosted": True, "web3": False},
    Protocol.ACTIVITYPUB: {"encrypted": False, "federated": True, "self_hosted": True, "web3": False},
    Protocol.LENS: {"encrypted": True, "federated": False, "self_hosted": False, "web3": True},
    Protocol.XMTP: {"encrypted": True, "federated": True, "self_hosted": True, "web3": True},
    Protocol.TELEGRAM: {"encrypted": True, "federated": False, "self_hosted": False, "web3": False},
    Protocol.DISCORD: {"encrypted": False, "federated": False, "self_hosted": False, "web3": False},
    Protocol.IRC: {"encrypted": False, "federated": True, "self_hosted": True, "web3": False},
    Protocol.XMPP: {"encrypted": True, "federated": True, "self_hosted": True, "web3": False},
    Protocol.SIGNAL: {"encrypted": True, "federated": False, "self_hosted": False, "web3": False},
    Protocol.WEB3: {"encrypted": True, "federated": True, "self_hosted": True, "web3": True},
}


class ProtocolSelector:
    """Intelligently selects which protocols to use for a conversation."""

    def get_supported_protocols(self, user: ChatUser) -> list[Protocol]:
        """Get all protocols a user has configured."""
        return list(user.identities.keys())

    def find_common_protocols(self, user_a: ChatUser, user_b: ChatUser) -> list[Protocol]:
        """Find protocols that both users have in common.

        Results are ordered by priority (most private first).
        """
        user_a_protocols = set(self.get_supported_protocols(user_a))
        user_b_protocols = set(self.get_supported_protocols(user_b))
        common = user_a_protocols & user_b_protocols

        # Sort by priority
        result = [p for p in PROTOCOL_PRIORITY if p in common]

        # Matrix is always included (canonical fabric)
        if Protocol.MATRIX not in result:
            result.insert(0, Protocol.MATRIX)

        logger.debug(
            "protocol_common user_a=%s user_b=%s common=%s",
            user_a.id,
            user_b.id,
            [p.value for p in result],
        )
        return result

    def select_bridge_plan(self, user_a: ChatUser, user_b: ChatUser) -> dict[str, Any]:
        """Create a bridge plan for a DM between two users.

        Returns:
            {
                "canonical_protocol": "matrix",
                "bridges": [
                    {"protocol": "nostr", "users": ["user_a"], "type": "primary"},
                    {"protocol": "farcaster", "users": ["user_b"], "type": "primary"},
                    ...
                ],
                "users_on_matrix": ["user_a", "user_b"],
                "common_protocols": [...]
            }
        """
        common = self.find_common_protocols(user_a, user_b)
        user_a_protos = set(self.get_supported_protocols(user_a))
        user_b_protos = set(self.get_supported_protocols(user_b))

        # Build bridge assignment
        bridges: list[dict[str, Any]] = []

        # Matrix is the canonical — both users can always use it
        matrix_bridge = {
            "protocol": Protocol.MATRIX,
            "users": [user_a.id, user_b.id],
            "type": "canonical",
            "encrypted": True,
        }
        bridges.append(matrix_bridge)

        # For each common protocol beyond Matrix, bridge both
        for protocol in common:
            if protocol == Protocol.MATRIX:
                continue
            users_on_protocol = []
            if protocol in user_a_protos:
                users_on_protocol.append(user_a.id)
            if protocol in user_b_protos:
                users_on_protocol.append(user_b.id)
            bridges.append(
                {
                    "protocol": protocol,
                    "users": users_on_protocol,
                    "type": "bridged",
                    "encrypted": _PROTOCOL_CAPABILITIES.get(protocol, {}).get("encrypted", False),
                }
            )

        # For protocols only one user has, add one-way bridge
        all_user_a_only = user_a_protos - user_b_protos - {Protocol.MATRIX}
        all_user_b_only = user_b_protos - user_a_protos - {Protocol.MATRIX}

        # Only add if user has at least 2 protocols (their "primary" non-Matrix)
        if len(user_a_protos - {Protocol.MATRIX}) > 0:
            best_extra_a = next(
                (p for p in PROTOCOL_PRIORITY if p in all_user_a_only),
                None,
            )
            if best_extra_a:
                bridges.append(
                    {
                        "protocol": best_extra_a,
                        "users": [user_a.id],
                        "type": "bridged",
                        "encrypted": _PROTOCOL_CAPABILITIES.get(best_extra_a, {}).get("encrypted", False),
                    }
                )

        if len(user_b_protos - {Protocol.MATRIX}) > 0:
            best_extra_b = next(
                (p for p in PROTOCOL_PRIORITY if p in all_user_b_only),
                None,
            )
            if best_extra_b:
                bridges.append(
                    {
                        "protocol": best_extra_b,
                        "users": [user_b.id],
                        "type": "bridged",
                        "encrypted": _PROTOCOL_CAPABILITIES.get(best_extra_b, {}).get("encrypted", False),
                    }
                )

        return {
            "canonical_protocol": Protocol.MATRIX,
            "bridges": bridges,
            "users_on_matrix": [user_a.id, user_b.id],
            "common_protocols": [p.value for p in common],
            "total_bridges": len(bridges),
        }

    def can_bridge(self, source: Protocol, target: Protocol) -> bool:
        """Check if bridging from source to target is supported.

        All bridges go through Matrix (Matrix is the hub).
        So we check: can source -> Matrix -> target?
        """
        known_pairs = {
            (Protocol.MATRIX, Protocol.NOSTR),
            (Protocol.MATRIX, Protocol.FARCASTER),
            (Protocol.MATRIX, Protocol.BLUESKY),
            (Protocol.MATRIX, Protocol.ACTIVITYPUB),
            (Protocol.MATRIX, Protocol.LENS),
            (Protocol.MATRIX, Protocol.XMTP),
            (Protocol.MATRIX, Protocol.TELEGRAM),
            (Protocol.MATRIX, Protocol.DISCORD),
            (Protocol.MATRIX, Protocol.IRC),
            (Protocol.MATRIX, Protocol.XMPP),
            (Protocol.MATRIX, Protocol.SIGNAL),
            (Protocol.MATRIX, Protocol.WEB3),
        }
        return (source, target) in known_pairs or (target, source) in known_pairs

    def get_protocol_capabilities(self, protocol: Protocol) -> dict[str, bool]:
        """Get feature flags for a protocol."""
        return _PROTOCOL_CAPABILITIES.get(
            protocol,
            {
                "encrypted": False,
                "federated": False,
                "self_hosted": False,
                "web3": False,
            },
        )

    def select_preferred_client_protocol(self, user: ChatUser) -> Protocol | None:
        """Select the best protocol for a user to use as their chat client.

        Picks the highest-priority protocol they have configured.
        If they only have Matrix, returns Matrix.
        """
        protocols = self.get_supported_protocols(user)
        if not protocols:
            return Protocol.MATRIX  # Default fallback

        for p in PROTOCOL_PRIORITY:
            if p in protocols:
                return p
        return Protocol.MATRIX
