"""DataBus domain — multi-provider data ingestion gateway.

DataBus is the canonical data ingestion layer for all RMI subsystems.
Every external API call flows through DataBus provider chains.

See:
  - providers/   — Provider implementations (base, chains, registry)
  - _generated/  — Auto-generated provider chain definitions

Architecture:
  No service.py — DataBus is a *gateway* not a business domain.
  No router.py — DataBus ingests via the dedicated databus_ingest router
                 at app.routers.databus_ingest.
"""

from __future__ import annotations

