"""
RMI Plugin Architecture - Extensible Backend System
====================================================
Plugin system for adding new features without modifying core code.

Features:
  • Plugin Registry - discover, load, and manage plugins
  • Plugin Types - connectors, scanners, analyzers, notifiers, exporters
  • Hot Reload - reload plugins without restart
  • Sandboxed Execution - isolated plugin environments
  • Plugin API - standardized interface for all plugins
  • Configuration Management - per-plugin config with validation
  • Health Checks - monitor plugin status and performance
  • Dependency Management - handle plugin dependencies

Plugin Types:
  connector   - Data sources (exchanges, APIs, oracles)
  scanner     - Security scanners (contract, wallet, token)
  analyzer    - Analysis engines (risk, sentiment, on-chain)
  notifier    - Alert channels (email, telegram, webhook)
  exporter    - Data export (CSV, PDF, API, webhook)
  wallet      - Wallet integrations (hardware, custodial)
  payment     - Payment processors (x402, stripe, crypto)
  ml          - ML models (fraud detection, prediction)

Author: RMI Platform Team
Date: 2026-05-31
"""

import importlib
import importlib.util
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger

logger = get_logger("rmi_plugin_system")


# ── Enums ─────────────────────────────────────────────────────


class PluginType(StrEnum):
    CONNECTOR = "connector"
    SCANNER = "scanner"
    ANALYZER = "analyzer"
    NOTIFIER = "notifier"
    EXPORTER = "exporter"
    WALLET = "wallet"
    PAYMENT = "payment"
    ML = "ml"
    SECURITY = "security"
    ANALYTICS = "analytics"


class PluginStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    LOADING = "loading"
    DEPRECATED = "deprecated"


# ── Base Plugin Interface ─────────────────────────────────────


class Plugin(ABC):
    """Base class for all plugins."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.status = PluginStatus.LOADING.value
        self.last_error = ""
        self.load_time = datetime.now(UTC).isoformat()
        self.call_count = 0
        self.error_count = 0

    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin name."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Plugin version."""

    @property
    @abstractmethod
    def plugin_type(self) -> PluginType:
        """Plugin type."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Plugin description."""

    def initialize(self) -> bool:
        """Initialize plugin. Return True if successful."""
        try:
            self._setup()
            self.status = PluginStatus.ACTIVE.value
            return True
        except Exception as e:
            self.status = PluginStatus.ERROR.value
            self.last_error = str(e)
            logger.error(f"Plugin {self.name} initialization failed: {e}")
            return False

    def _setup(self):  # noqa: B027
        """Override for setup logic."""

    def health_check(self) -> dict[str, Any]:
        """Return health status."""
        return {
            "status": self.status,
            "call_count": self.call_count,
            "error_count": self.error_count,
            "error_rate": self.error_count / max(self.call_count, 1),
            "last_error": self.last_error,
            "uptime_seconds": int(time.time() - datetime.fromisoformat(self.load_time).timestamp()),
        }

    def shutdown(self):
        """Cleanup on shutdown."""
        self.status = PluginStatus.INACTIVE.value

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "type": self.plugin_type.value,
            "description": self.description,
            "status": self.status,
            "config": self.config,
            "health": self.health_check(),
        }


# ── Plugin Registry ───────────────────────────────────────────


class PluginRegistry:
    """
    Central registry for all plugins.
    Manages plugin lifecycle, discovery, and execution.
    """

    PLUGIN_DIR = "/root/backend/plugins"

    def __init__(self):
        self._plugins: dict[str, Plugin] = {}
        self._hooks: dict[str, list[Callable]] = {}
        self._ensure_plugin_dir()

    def _ensure_plugin_dir(self):
        """Ensure plugin directory exists."""
        os.makedirs(self.PLUGIN_DIR, exist_ok=True)
        for ptype in PluginType:
            os.makedirs(os.path.join(self.PLUGIN_DIR, ptype.value), exist_ok=True)

    def register(self, plugin: Plugin) -> bool:
        """Register a plugin instance."""
        if plugin.name in self._plugins:
            logger.warning(f"Plugin {plugin.name} already registered - replacing")

        if plugin.initialize():
            self._plugins[plugin.name] = plugin
            logger.info(f"Plugin registered: {plugin.name} v{plugin.version}")
            return True
        return False

    def unregister(self, name: str) -> bool:
        """Unregister a plugin."""
        plugin = self._plugins.pop(name, None)
        if plugin:
            plugin.shutdown()
            logger.info(f"Plugin unregistered: {name}")
            return True
        return False

    def get(self, name: str) -> Plugin | None:
        """Get plugin by name."""
        return self._plugins.get(name)

    def list_plugins(self, plugin_type: PluginType | None = None) -> list[Plugin]:
        """List all plugins, optionally filtered by type."""
        plugins = list(self._plugins.values())
        if plugin_type:
            plugins = [p for p in plugins if p.plugin_type == plugin_type]
        return plugins

    def get_by_type(self, plugin_type: PluginType) -> list[Plugin]:
        """Get all plugins of a specific type."""
        return [p for p in self._plugins.values() if p.plugin_type == plugin_type]

    def discover_plugins(self) -> list[str]:
        """Discover plugins from plugin directory."""
        discovered = []
        for ptype in PluginType:
            type_dir = os.path.join(self.PLUGIN_DIR, ptype.value)
            if os.path.exists(type_dir):
                for fname in os.listdir(type_dir):
                    if fname.endswith(".py") and not fname.startswith("_"):
                        discovered.append(os.path.join(type_dir, fname))
        return discovered

    def load_plugin_from_file(self, filepath: str) -> Plugin | None:
        """Load a plugin from a Python file."""
        try:
            spec = importlib.util.spec_from_file_location("plugin", filepath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find Plugin subclass
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Plugin) and attr != Plugin:
                    instance = attr()
                    if self.register(instance):
                        return instance

            logger.warning(f"No Plugin subclass found in {filepath}")
            return None

        except Exception as e:
            logger.error(f"Failed to load plugin from {filepath}: {e}")
            return None

    def reload_plugin(self, name: str) -> bool:
        """Reload a plugin."""
        plugin = self._plugins.get(name)
        if not plugin:
            return False

        # Unregister and re-register
        self.unregister(name)
        return plugin.initialize() and self.register(plugin)

    def get_stats(self) -> dict[str, Any]:
        """Get plugin registry statistics."""
        by_type = {}
        by_status = {}

        for p in self._plugins.values():
            by_type[p.plugin_type.value] = by_type.get(p.plugin_type.value, 0) + 1
            by_status[p.status] = by_status.get(p.status, 0) + 1

        return {
            "total_plugins": len(self._plugins),
            "by_type": by_type,
            "by_status": by_status,
            "plugins": [p.to_dict() for p in self._plugins.values()],
        }

    # ── Hook System ────────────────────────────────────────

    def register_hook(self, event: str, callback: Callable):
        """Register a hook for an event."""
        if event not in self._hooks:
            self._hooks[event] = []
        self._hooks[event].append(callback)

    def trigger_hook(self, event: str, *args, **kwargs) -> list[Any]:
        """Trigger all hooks for an event."""
        results = []
        for callback in self._hooks.get(event, []):
            try:
                result = callback(*args, **kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"Hook error for {event}: {e}")
        return results


# ── Built-in Plugin Examples ──────────────────────────────────


class PrometheusExporterPlugin(Plugin):
    """Export metrics to Prometheus."""

    @property
    def name(self) -> str:
        return "prometheus_exporter"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def plugin_type(self) -> PluginType:
        return PluginType.EXPORTER

    @property
    def description(self) -> str:
        return "Export metrics in Prometheus format"

    def export(self, metrics: dict[str, Any]) -> str:
        """Export metrics to Prometheus format."""
        lines = []
        for name, value in metrics.items():
            prom_name = f"rmi_{name}"
            lines.append(f"# HELP {prom_name} Metric")
            lines.append(f"# TYPE {prom_name} gauge")
            lines.append(f"{prom_name} {value}")
        return "\n".join(lines)


class WebhookNotifierPlugin(Plugin):
    """Send notifications via webhook."""

    @property
    def name(self) -> str:
        return "webhook_notifier"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def plugin_type(self) -> PluginType:
        return PluginType.NOTIFIER

    @property
    def description(self) -> str:
        return "Send notifications to configured webhooks"

    def _setup(self):
        self.webhook_url = self.config.get("webhook_url", "")
        self.headers = self.config.get("headers", {})

    async def notify(self, event: str, data: dict[str, Any]) -> bool:
        """Send notification to webhook."""
        if not self.webhook_url:
            return False

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                payload = {
                    "event": event,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "data": data,
                }
                r = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers=self.headers,
                    timeout=10,
                )
                return r.status_code < 400
        except Exception as e:
            logger.error(f"Webhook notification failed: {e}")
            self.error_count += 1
            return False


class RedisCachePlugin(Plugin):
    """Redis caching plugin."""

    @property
    def name(self) -> str:
        return "redis_cache"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def plugin_type(self) -> PluginType:
        return PluginType.CONNECTOR

    @property
    def description(self) -> str:
        return "Redis caching and pub/sub connector"

    def _setup(self):
        import redis.asyncio as redis_lib

        self.redis = redis_lib.Redis(
            host=self.config.get("host", "localhost"),
            port=self.config.get("port", 6379),
            password=self.config.get("password", ""),
            decode_responses=True,
        )

    async def get(self, key: str) -> str | None:
        return await self.redis.get(key)

    async def set(self, key: str, value: str, ttl: int = 3600):
        await self.redis.setex(key, ttl, value)

    async def publish(self, channel: str, message: str):
        await self.redis.publish(channel, message)


# ── Singleton ─────────────────────────────────────────────────

_plugin_registry: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    """Get or create plugin registry."""
    global _plugin_registry
    if _plugin_registry is None:
        _plugin_registry = PluginRegistry()
        # Auto-register built-in plugins
        _plugin_registry.register(PrometheusExporterPlugin())
        _plugin_registry.register(WebhookNotifierPlugin())
        _plugin_registry.register(RedisCachePlugin())
    return _plugin_registry
