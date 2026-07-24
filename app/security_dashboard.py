"""Stub security_dashboard - security alert aggregation"""

from __future__ import annotations

from typing import Any


class SecurityDashboard:
    """Security dashboard for monitoring alerts."""

    def __init__(self) -> None:
        self.alerts: list[dict[str, Any]] = []

    def add_alert(self, alert: dict[str, Any]) -> None:
        self.alerts.append(alert)

    def get_alerts(self, status: str = "all", limit: int = 10) -> list[dict[str, Any]]:
        return self.alerts[:limit]

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_alerts": len(self.alerts),
            "by_status": {"all": len(self.alerts)},
            "by_type": {},
        }

def create_alert_from_detection(detection: dict[str, Any]) -> dict[str, Any]:
    """Create an alert from a detection."""
    return {
        "id": f"alert_{detection.get('hash', 'unknown')}",
        "detection": detection,
        "status": "pending",
        "created_at": "2026-05-12T00:00:00Z",
    }

def get_dashboard() -> SecurityDashboard:
    """Get the global dashboard instance."""
    return SecurityDashboard()
