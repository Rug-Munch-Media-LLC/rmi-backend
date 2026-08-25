"""SERVICE consumer tier tests — verified X-RMI-Key service-to-service access.

Regression coverage for the rugmaps empty-graph fix: first-party services
presenting a valid X-RMI-Key must map to ConsumerType.SERVICE and receive
"full" packaging on graph data types (entity_intel, wallet_labels), instead
of being denied as PUBLIC_WEB.

Invalid/absent keys must NOT grant SERVICE — fail closed.
"""

from __future__ import annotations

import os

os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"
os.environ.setdefault("RMI_INTERNAL_KEY", "test-service-key-1234567890abcdef")


from app.databus.access_control import (
    AccessController,
    ConsumerType,
    _is_valid_service_key,
)
from app.databus.security import SecurityGate

VALID_KEY = os.environ["RMI_INTERNAL_KEY"]


class TestServiceKeyValidation:
    def test_valid_key_returns_true(self) -> None:
        assert _is_valid_service_key(VALID_KEY) is True

    def test_empty_key_fails_closed(self) -> None:
        assert _is_valid_service_key("") is False
        assert _is_valid_service_key(None) is False

    def test_wrong_key_fails_closed(self) -> None:
        assert _is_valid_service_key("wrong-key-totally") is False


class TestIdentifyConsumerService:
    def test_valid_service_key_maps_to_service(self) -> None:
        consumer = AccessController.identify_consumer(service_key=VALID_KEY)
        assert consumer == ConsumerType.SERVICE

    def test_invalid_service_key_stays_public(self) -> None:
        consumer = AccessController.identify_consumer(service_key="bogus")
        assert consumer == ConsumerType.PUBLIC_WEB

    def test_admin_key_still_wins_over_service(self) -> None:
        os.environ["ADMIN_API_KEY"] = "admin-test-key"
        try:
            consumer = AccessController.identify_consumer(admin_key="admin-test-key", service_key=VALID_KEY)
            assert consumer == ConsumerType.ADMIN
        finally:
            del os.environ["ADMIN_API_KEY"]

    def test_client_declared_consumer_type_ignored(self) -> None:
        """The old bypass: declaring consumer_type=admin must not work."""
        consumer = AccessController.identify_consumer(consumer_type="admin", service_key=VALID_KEY)
        assert consumer == ConsumerType.SERVICE


class TestServicePackaging:
    def test_entity_intel_full_for_service(self) -> None:
        assert AccessController.check_access("entity_intel", ConsumerType.SERVICE) == "full"

    def test_wallet_labels_full_for_service(self) -> None:
        assert AccessController.check_access("wallet_labels", ConsumerType.SERVICE) == "full"

    def test_entity_intel_denied_for_public(self) -> None:
        assert AccessController.check_access("entity_intel", ConsumerType.PUBLIC_WEB) == "denied"

    def test_unknown_type_full_for_service(self) -> None:
        assert AccessController.check_access("some_future_type", ConsumerType.SERVICE) == "full"


class TestSecurityGateService:
    def test_gate_allows_admin_type_with_service_key(self) -> None:
        assert SecurityGate.check_access("entity_intel", service_key=VALID_KEY) is True

    def test_gate_denies_admin_type_without_credentials(self) -> None:
        assert SecurityGate.check_access("entity_intel") is False

    def test_gate_denies_admin_type_with_bad_service_key(self) -> None:
        assert SecurityGate.check_access("entity_intel", service_key="nope") is False
