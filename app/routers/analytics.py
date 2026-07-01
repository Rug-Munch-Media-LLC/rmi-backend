"""
RMI Analytics API Router
==========================
REST API for real-time analytics and dashboard data.

Endpoints:
  GET  /api/v1/analytics/metrics              — List all metrics
  GET  /api/v1/analytics/metrics/{name}       — Get metric data
  POST /api/v1/analytics/metrics/{name}       — Record metric
  GET  /api/v1/analytics/dashboards           — List dashboards
  GET  /api/v1/analytics/dashboards/{id}     — Get dashboard data
  POST /api/v1/analytics/dashboards            — Create dashboard
  GET  /api/v1/analytics/trends/{metric}      — Get trend analysis
  GET  /api/v1/analytics/stats                — System stats
  GET  /api/v1/analytics/prometheus           — Prometheus export
  GET  /api/v1/analytics/export/{metric}      — Export metric
  GET  /api/v1/analytics/realtime/{dashboard} — WebSocket-compatible data
"""

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.admin_backend import AuditLogger, require_admin
from app.analytics_engine import AnalyticsEngine, DashboardWidget, get_analytics_engine

logger = logging.getLogger("analytics_api")

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

# ── Models ────────────────────────────────────────────────────


class RecordMetricRequest(BaseModel):
    value: float
    labels: dict[str, str] | None = None


class CreateDashboardRequest(BaseModel):
    name: str
    description: str = ""


class AddWidgetRequest(BaseModel):
    widget_type: str = Field(..., description="line, bar, gauge, counter, table, pie")
    title: str
    metric_name: str
    width: int = 6
    height: int = 4
    refresh_interval: int = 30
    config: dict[str, Any] | None = None


# ── Helper ────────────────────────────────────────────────────


def _get_engine() -> AnalyticsEngine:
    return get_analytics_engine()


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/metrics")
async def list_metrics(request: Request):
    """List all tracked metrics."""
    await require_admin(request, "analytics.read")

    engine = _get_engine()
    metrics = []
    for name in engine.get_metric_names():
        metric = engine.get_metric(name)
        if metric:
            metrics.append(metric.to_dict())

    return {"metrics": metrics, "total": len(metrics)}


@router.get("/metrics/{name}")
async def get_metric(request: Request, name: str, limit: int = 100):
    """Get metric data points."""
    await require_admin(request, "analytics.read")

    engine = _get_engine()
    metric = engine.get_metric(name)
    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")

    points = [{"timestamp": p.timestamp, "value": p.value, "labels": p.labels} for p in metric.points[-limit:]]

    return {
        "name": metric.name,
        "description": metric.description,
        "unit": metric.unit,
        "latest": metric.latest(),
        "avg_1m": metric.avg(60),
        "trend": metric.trend(),
        "points": points,
    }


@router.post("/metrics/{name}")
async def record_metric(request: Request, name: str, body: RecordMetricRequest):
    """Record a metric data point."""
    # Allow public recording for system metrics (from middleware)
    engine = _get_engine()
    engine.record_metric(name, body.value, body.labels or {})
    return {"success": True}


@router.get("/dashboards")
async def list_dashboards(request: Request):
    """List all dashboards."""
    await require_admin(request, "analytics.read")

    engine = _get_engine()
    dashboards = engine.list_dashboards()
    return {
        "dashboards": [
            {
                "dashboard_id": d.dashboard_id,
                "name": d.name,
                "description": d.description,
                "widget_count": len(d.widgets),
                "is_default": d.is_default,
            }
            for d in dashboards
        ]
    }


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard_data(request: Request, dashboard_id: str):
    """Get current data for a dashboard."""
    await require_admin(request, "analytics.read")

    engine = _get_engine()
    data = engine.get_dashboard_data(dashboard_id)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@router.post("/dashboards")
async def create_dashboard(request: Request, body: CreateDashboardRequest):
    """Create a new dashboard."""
    auth = await require_admin(request, "analytics.read")
    admin = auth["admin"]

    engine = _get_engine()
    dashboard = engine.create_dashboard(body.name, body.description, admin["id"])

    await AuditLogger.log(
        admin_id=admin["id"],
        admin_email=admin["email"],
        action="dashboard.create",
        resource_type="dashboard",
        resource_id=dashboard.dashboard_id,
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )

    return {
        "success": True,
        "dashboard": {"dashboard_id": dashboard.dashboard_id, "name": dashboard.name},
    }


@router.post("/dashboards/{dashboard_id}/widgets")
async def add_widget(request: Request, dashboard_id: str, body: AddWidgetRequest):
    """Add widget to dashboard."""
    auth = await require_admin(request, "analytics.read")
    auth["admin"]

    engine = _get_engine()
    widget = DashboardWidget(
        widget_id=f"wid_{int(time.time())}_{os.urandom(4).hex()}",
        widget_type=body.widget_type,
        title=body.title,
        metric_name=body.metric_name,
        width=body.width,
        height=body.height,
        refresh_interval=body.refresh_interval,
        config=body.config or {},
    )

    result = engine.add_widget(dashboard_id, widget)
    if not result:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    return {"success": True, "widget": {"widget_id": widget.widget_id, "title": widget.title}}


@router.get("/trends/{metric_name}")
async def get_trends(request: Request, metric_name: str, window: int = 60):
    """Get trend analysis for a metric."""
    await require_admin(request, "analytics.read")

    engine = _get_engine()
    trends = engine.detect_trends(metric_name, window)
    return trends


@router.get("/stats")
async def get_stats(request: Request):
    """Get analytics system statistics."""
    await require_admin(request, "analytics.read")

    engine = _get_engine()
    return engine.get_system_stats()


@router.get("/prometheus")
async def prometheus_export(request: Request):
    """Export metrics in Prometheus format."""
    # Public endpoint for Prometheus scraping
    engine = _get_engine()
    return engine.to_prometheus()


@router.get("/export/{metric_name}")
async def export_metric(
    request: Request,
    metric_name: str,
    format: str = Query("json", regex="^(json|csv)$"),
):
    """Export metric data."""
    await require_admin(request, "analytics.read")

    engine = _get_engine()
    data = engine.export_metric(metric_name, format)
    if data is None:
        raise HTTPException(status_code=404, detail="Metric not found")

    if format == "csv":
        return {"data": data, "format": "csv"}
    return data


@router.get("/realtime/{dashboard_id}")
async def realtime_data(request: Request, dashboard_id: str):
    """Get real-time dashboard data (WebSocket-compatible)."""
    await require_admin(request, "analytics.read")

    engine = _get_engine()
    data = engine.get_dashboard_data(dashboard_id)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])

    # Add timestamp for client sync
    data["server_time"] = time.time()
    data["refresh_interval"] = 30

    return data
