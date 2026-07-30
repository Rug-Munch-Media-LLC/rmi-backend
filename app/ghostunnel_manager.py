"""
Ghostunnel SSL/TLS Tunnel Integration
======================================

Secure SSL/TLS proxy with mutual authentication for non-TLS services.
Features:
- TLS termination
- Client certificate verification
- Proxying to TCP services
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GhostunnelConfig:
    """Ghostunnel configuration."""

    listen: str
    target: str
    cacert: str | None = None
    cert: str | None = None
    key: str | None = None
    clientcert: str | None = None
    acceptonly: str | None = None
    status: str | None = "127.0.0.1:8080"


# ─── GHOSTUNNEL MANAGER ───────────────────────────────────────────


class GhostunnelManager:
    """Manages Ghostunnel processes."""

    def __init__(self, ghostunnel_bin: str = "ghostunnel") -> None:
        self.ghostunnel_bin = ghostunnel_bin
        self._available = self._check_available()
        self.processes: dict[str, subprocess.Popen] = {}

    def _check_available(self) -> bool:
        """Check if ghostunnel is installed."""
        try:
            result = subprocess.run([self.ghostunnel_bin, "version"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def start_tunnel(self, config: GhostunnelConfig) -> str:
        """
        Start a new tunnel.

        Args:
            config: Ghostunnel configuration

        Returns:
            Process ID of the tunnel
        """
        if not self._available:
            return "stub_tunnel"

        cmd = [
            self.ghostunnel_bin,
            "server",
            "--listen",
            config.listen,
            "--target",
            config.target,
        ]

        if config.cacert:
            cmd.extend(["--cacert", config.cacert])
        if config.cert:
            cmd.extend(["--cert", config.cert])
        if config.key:
            cmd.extend(["--key", config.key])
        if config.clientcert:
            cmd.extend(["--clientcert", config.clientcert])
        if config.acceptonly:
            cmd.extend(["--acceptonly", config.acceptonly])
        if config.status:
            cmd.extend(["--status", config.status])

        try:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.processes[config.listen] = process
            return str(process.pid)
        except Exception as e:
            logger.error(f"Failed to start ghostunnel: {e}")
            return ""

    def stop_tunnel(self, listen_addr: str) -> bool:
        """
        Stop a tunnel by listening address.

        Args:
            listen_addr: Listening address of the tunnel

        Returns:
            Success status
        """
        process = self.processes.get(listen_addr)
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
                del self.processes[listen_addr]
                return True
            except subprocess.TimeoutExpired:
                process.kill()
                del self.processes[listen_addr]
                return True
        return False

    def stop_all(self) -> int:
        """Stop all tunnels. Returns count of stopped processes."""
        count = 0
        for addr in list(self.processes.keys()):
            if self.stop_tunnel(addr):
                count += 1
        return count

    def get_status(self) -> dict[str, str]:
        """Get status of all tunnels."""
        status = {}
        for addr, process in self.processes.items():
            status[addr] = {
                "pid": process.pid,
                "alive": process.poll() is None,
                "started_at": datetime.now(UTC).isoformat(),
            }
        return status


# ─── GLOBAL SINGLETON ─────────────────────────────────────────────

_manager: GhostunnelManager | None = None


def get_ghostunnel_manager() -> GhostunnelManager:
    """Get or create Ghostunnel manager."""
    global _manager
    if _manager is None:
        _manager = GhostunnelManager()
    return _manager


def start_tunnel(listen: str, target: str, **kwargs) -> str:
    """
    Convenience function to start a tunnel.

    Args:
        listen: Listen address (host:port)
        target: Target address (host:port)
        **kwargs: Extra config options
    """
    config = GhostunnelConfig(listen=listen, target=target, **kwargs)
    manager = get_ghostunnel_manager()
    return manager.start_tunnel(config)


def stop_tunnel(listen: str) -> bool:
    """Convenience function to stop a tunnel."""
    manager = get_ghostunnel_manager()
    return manager.stop_tunnel(listen)


def stop_all_tunnels() -> int:
    """Convenience function to stop all tunnels."""
    manager = get_ghostunnel_manager()
    return manager.stop_all()
