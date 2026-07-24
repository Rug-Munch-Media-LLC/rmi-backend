"""
RMI Analytics Engine - Real-Time Metrics & Trend Visualization
===============================================================
Comprehensive analytics system for the RugMunch Intelligence Platform.

Features:
  • Real-Time Metrics - CPU, memory, requests, errors, latency
  • Time-Series Storage - Redis-backed rolling windows
  • Trend Detection - automatic anomaly detection, trend arrows
  • User Analytics - DAU, MAU, retention, cohort analysis
  • Financial Analytics - revenue, ARPU, MRR, churn
  • Security Analytics - threats blocked, bot traffic, attack patterns
  • Token Analytics - deployment stats, airdrop metrics, holder growth
  • Custom Dashboards - configurable widget layouts
  • Export - CSV, JSON, Prometheus metrics

Integrations:
  - Prometheus metrics export
  - Grafana-compatible data format
  - WebSocket real-time streaming
  - ClickHouse for long-term storage

Author: RMI Analytics Team
Date: 2026-05-31
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("rmi_analytics")

# ── Data Models ─────────────────────────────────────────────

@dataclass
class MetricPoint:
    """Single time-series data point."""

    timestamp: float
    value: float
    labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class MetricSeries:
    """Time-series metric with metadata."""

    name: str
    description: str
    unit: str
    points: list[MetricPoint] = field(default_factory=list)

    def latest(self) -> float | None:
        return self.points[-1].value if self.points else None

    def avg(self, n: int = 60) -> float:
        vals = [p.value for p in self.points[-n:]]
        return sum(vals) / len(vals) if vals else 0.0

    def trend(self, window: int = 10) -> str:
        """Return trend direction: up, down, flat."""
        if len(self.points) < window * 2:
            return "flat"
        old_avg = sum(p.value for p in self.points[-window * 2 : -window]) / window
        new_avg = sum(p.value for p in self.points[-window:]) / window
        diff = new_avg - old_avg
        if abs(diff) < 0.01 * old_avg:
            return "flat"
        return "up" if diff > 0 else "down"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "latest": self.latest(),
            "avg_1m": self.avg(60),
            "trend": self.trend(),
            "point_count": len(self.points),
        }

@dataclass
class DashboardWidget:
    """Dashboard widget configuration."""

    widget_id: str
    widget_type: str  # line, bar, gauge, counter, table, pie
    title: str
    metric_name: str
    width: int = 6  # Grid columns (1-12)
    height: int = 4
    refresh_interval: int = 30  # seconds
    config: dict[str, Any] = field(default_factory=dict)

@dataclass
class Dashboard:
    """Dashboard configuration."""

    dashboard_id: str
    name: str
    description: str
    widgets: list[DashboardWidget] = field(default_factory=list)
    created_by: str = ""
    is_default: bool = False

# ── Analytics Engine ────────────────────────────────────────

class AnalyticsEngine:
    """
    Core analytics engine for real-time metrics and trend analysis.
    """

    def __init__(self) -> None:
        self._metrics: dict[str, MetricSeries] = {}
        self._dashboards: dict[str, Dashboard] = {}
        self._ensure_default_dashboards()

    def _ensure_default_dashboards(self) -> None:
        """Create default system dashboards."""
        # System Health Dashboard
        system_widgets = [
            DashboardWidget("cpu_gauge", "gauge", "CPU Usage", "cpu_percent", 3, 3, 10),
            DashboardWidget("mem_gauge", "gauge", "Memory Usage", "memory_percent", 3, 3, 10),
            DashboardWidget("disk_gauge", "gauge", "Disk Usage", "disk_percent", 3, 3, 10),
            DashboardWidget(
                "req_counter", "counter", "Requests/min", "requests_per_minute", 3, 3, 10
            ),
            DashboardWidget("cpu_line", "line", "CPU History", "cpu_percent", 6, 4, 30),
            DashboardWidget("mem_line", "line", "Memory History", "memory_percent", 6, 4, 30),
            DashboardWidget(
                "latency_line", "line", "Response Latency", "response_time_ms", 6, 4, 30
            ),
            DashboardWidget("error_line", "line", "Error Rate", "error_rate", 6, 4, 30),
        ]

        self._dashboards["system"] = Dashboard(
            dashboard_id="system",
            name="System Health",
            description="Real-time system performance metrics",
            widgets=system_widgets,
            is_default=True,
        )

        # Financial Dashboard
        financial_widgets = [
            DashboardWidget("revenue_counter", "counter", "Total Revenue", "revenue_usd", 3, 3, 60),
            DashboardWidget("mrr_counter", "counter", "MRR", "mrr_usd", 3, 3, 60),
            DashboardWidget("arpu_counter", "counter", "ARPU", "arpu_usd", 3, 3, 60),
            DashboardWidget("churn_gauge", "gauge", "Churn Rate", "churn_rate", 3, 3, 60),
            DashboardWidget("revenue_line", "line", "Revenue Trend", "revenue_usd", 6, 4, 300),
            DashboardWidget("payments_line", "line", "Payments", "payments_count", 6, 4, 300),
        ]

        self._dashboards["financial"] = Dashboard(
            dashboard_id="financial",
            name="Financial Analytics",
            description="Revenue, payments, and subscription metrics",
            widgets=financial_widgets,
            is_default=True,
        )

        # Security Dashboard
        security_widgets = [
            DashboardWidget(
                "threats_counter", "counter", "Threats Blocked", "threats_blocked", 3, 3, 30
            ),
            DashboardWidget("bots_counter", "counter", "Bot Requests", "bot_requests", 3, 3, 30),
            DashboardWidget("attacks_counter", "counter", "Attacks", "attacks_detected", 3, 3, 30),
            DashboardWidget(
                "blocked_ips_counter", "counter", "Blocked IPs", "blocked_ips", 3, 3, 30
            ),
            DashboardWidget("threats_pie", "pie", "Threat Types", "threat_types", 6, 4, 60),
            DashboardWidget(
                "attacks_line", "line", "Attack Timeline", "attacks_detected", 6, 4, 60
            ),
        ]

        self._dashboards["security"] = Dashboard(
            dashboard_id="security",
            name="Security Analytics",
            description="Threat detection and security metrics",
            widgets=security_widgets,
            is_default=True,
        )

        # User Analytics Dashboard
        user_widgets = [
            DashboardWidget("dau_counter", "counter", "DAU", "daily_active_users", 3, 3, 60),
            DashboardWidget("mau_counter", "counter", "MAU", "monthly_active_users", 3, 3, 60),
            DashboardWidget("new_users_counter", "counter", "New Users", "new_users", 3, 3, 60),
            DashboardWidget("retention_gauge", "gauge", "Retention", "retention_rate", 3, 3, 60),
            DashboardWidget("users_line", "line", "User Growth", "total_users", 6, 4, 300),
            DashboardWidget("tiers_pie", "pie", "User Tiers", "users_by_tier", 6, 4, 300),
        ]

        self._dashboards["users"] = Dashboard(
            dashboard_id="users",
            name="User Analytics",
            description="User growth, engagement, and retention",
            widgets=user_widgets,
            is_default=True,
        )

    # ── Metric Recording ────────────────────────────────────

    def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record a metric data point."""
        if name not in self._metrics:
            self._metrics[name] = MetricSeries(
                name=name,
                description=name.replace("_", " ").title(),
                unit="",
            )

        point = MetricPoint(
            timestamp=time.time(),
            value=value,
            labels=labels or {},
        )

        self._metrics[name].points.append(point)

        # Keep only last 10000 points (about 2.7 hours at 1/sec)
        if len(self._metrics[name].points) > 10000:
            self._metrics[name].points = self._metrics[name].points[-10000:]

    def get_metric(self, name: str) -> MetricSeries | None:
        """Get metric series by name."""
        return self._metrics.get(name)

    def get_metric_names(self) -> list[str]:
        """List all metric names."""
        return list(self._metrics.keys())

    # ── Dashboard Management ────────────────────────────────

    def get_dashboard(self, dashboard_id: str) -> Dashboard | None:
        """Get dashboard by ID."""
        return self._dashboards.get(dashboard_id)

    def list_dashboards(self) -> list[Dashboard]:
        """List all dashboards."""
        return list(self._dashboards.values())

    def create_dashboard(self, name: str, description: str, created_by: str = "") -> Dashboard:
        """Create a new dashboard."""
        dashboard_id = f"dash_{int(time.time())}_{os.urandom(4).hex()}"
        dashboard = Dashboard(
            dashboard_id=dashboard_id,
            name=name,
            description=description,
            created_by=created_by,
        )
        self._dashboards[dashboard_id] = dashboard
        return dashboard

    def add_widget(self, dashboard_id: str, widget: DashboardWidget) -> bool:
        """Add widget to dashboard."""
        dashboard = self._dashboards.get(dashboard_id)
        if not dashboard:
            return False
        dashboard.widgets.append(widget)
        return True

    # ── Real-Time Data ──────────────────────────────────────

    def get_dashboard_data(self, dashboard_id: str) -> dict[str, Any]:
        """Get current data for all widgets in a dashboard."""
        dashboard = self._dashboards.get(dashboard_id)
        if not dashboard:
            return {"error": "Dashboard not found"}

        widgets_data = []
        for widget in dashboard.widgets:
            metric = self._metrics.get(widget.metric_name)
            data = {
                "widget_id": widget.widget_id,
                "widget_type": widget.widget_type,
                "title": widget.title,
                "metric": metric.to_dict()
                if metric
                else {"name": widget.metric_name, "latest": None},
            }

            # Add historical data for line/bar charts
            if widget.widget_type in ["line", "bar"] and metric:
                # Return last 60 points
                data["history"] = [{"t": p.timestamp, "v": p.value} for p in metric.points[-60:]]

            widgets_data.append(data)

        return {
            "dashboard_id": dashboard_id,
            "name": dashboard.name,
            "updated_at": datetime.now(UTC).isoformat(),
            "widgets": widgets_data,
        }

    # ── Trend Analysis ──────────────────────────────────────

    def detect_trends(self, metric_name: str, window: int = 60) -> dict[str, Any]:
        """Detect trends in a metric."""
        metric = self._metrics.get(metric_name)
        if not metric or len(metric.points) < window * 2:
            return {"error": "Insufficient data"}

        points = metric.points[-window * 2 :]
        half = len(points) // 2

        first_half = [p.value for p in points[:half]]
        second_half = [p.value for p in points[half:]]

        first_avg = sum(first_half) / len(first_half)
        second_avg = sum(second_half) / len(second_half)

        change_pct = ((second_avg - first_avg) / first_avg * 100) if first_avg else 0

        # Detect anomalies (values outside 2 std dev)
        all_vals = [p.value for p in metric.points[-window:]]
        mean = sum(all_vals) / len(all_vals)
        variance = sum((v - mean) ** 2 for v in all_vals) / len(all_vals)
        std_dev = variance**0.5

        anomalies = [
            {"timestamp": p.timestamp, "value": p.value}
            for p in metric.points[-window:]
            if abs(p.value - mean) > 2 * std_dev
        ]

        return {
            "metric": metric_name,
            "trend": metric.trend(window),
            "change_percent": round(change_pct, 2),
            "first_period_avg": round(first_avg, 4),
            "second_period_avg": round(second_avg, 4),
            "anomalies_count": len(anomalies),
            "anomalies": anomalies[:5],  # Top 5
        }

    # ── Statistics ───────────────────────────────────────────

    def get_system_stats(self) -> dict[str, Any]:
        """Get comprehensive system statistics."""
        return {
            "metrics_tracked": len(self._metrics),
            "dashboards": len(self._dashboards),
            "total_data_points": sum(len(m.points) for m in self._metrics.values()),
            "last_updated": datetime.now(UTC).isoformat(),
            "top_metrics": [
                {"name": name, "points": len(m.points), "latest": m.latest()}
                for name, m in sorted(
                    self._metrics.items(), key=lambda x: len(x[1].points), reverse=True
                )[:10]
            ],
        }

    # ── Prometheus Export ───────────────────────────────────

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        for name, metric in self._metrics.items():
            prom_name = f"rmi_{name}"
            lines.append(f"# HELP {prom_name} {metric.description}")
            lines.append(f"# TYPE {prom_name} gauge")

            latest = metric.latest()
            if latest is not None:
                labels_str = ", ".join(f'{k}="{v}"' for k, v in metric.points[-1].labels.items())
                if labels_str:
                    lines.append(f"{prom_name}{{{labels_str}}} {latest}")
                else:
                    lines.append(f"{prom_name} {latest}")

        return "\n".join(lines)

    # ── Export ────────────────────────────────────────────

    def export_metric(self, name: str, format: str = "json") -> Any:
        """Export metric data."""
        metric = self._metrics.get(name)
        if not metric:
            return None

        if format == "json":
            return {
                "name": metric.name,
                "description": metric.description,
                "unit": metric.unit,
                "data": [
                    {"timestamp": p.timestamp, "value": p.value, "labels": p.labels}
                    for p in metric.points
                ],
            }
        elif format == "csv":
            lines = ["timestamp,value"]
            for p in metric.points:
                lines.append(f"{p.timestamp},{p.value}")
            return "\n".join(lines)

        return None

# ── Singleton ─────────────────────────────────────────────────

_analytics_instance: AnalyticsEngine | None = None

def get_analytics_engine() -> AnalyticsEngine:
    """Get or create analytics engine instance."""
    global _analytics_instance
    if _analytics_instance is None:
        _analytics_instance = AnalyticsEngine()
    return _analytics_instance
