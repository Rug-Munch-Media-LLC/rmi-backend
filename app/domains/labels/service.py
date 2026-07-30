"""Labels service - Federated Label API.

Consistent domain pattern: service.py provides the business logic interface.
Re-exports FederatedLabelAPI from federated.py for backward compatibility.

See federated.py for implementation details.
"""

from __future__ import annotations

from app.domains.labels.federated import (
    FederatedLabelAPI,
    get_federated_api,
)

__all__ = [
    "FederatedLabelAPI",
    "get_federated_api",
]
