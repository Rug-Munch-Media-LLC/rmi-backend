"""
Wallet Monitor — modular package.
Re-exports all public symbols for backward compatibility.
"""

from app.domains.wallet.monitor.alert_engine import AlertEngine
from app.domains.wallet.monitor.burst import BurstDetector
from app.domains.wallet.monitor.channel import AlertChannel
from app.domains.wallet.monitor.labeler import AutoLabeler
from app.domains.wallet.monitor.manager import WalletMonitorManager
from app.domains.wallet.monitor.models import BurstSignal, LabelSuggestion, WalletAlert, WalletTx
from app.domains.wallet.monitor.monitor import WalletMonitor
from app.domains.wallet.monitor.watchlist import WatchlistManager

__all__ = [
    "AlertChannel",
    "AlertEngine",
    "AutoLabeler",
    "BurstDetector",
    "BurstSignal",
    "LabelSuggestion",
    "WalletAlert",
    "WalletMonitor",
    "WalletMonitorManager",
    "WalletTx",
    "WatchlistManager",
]
