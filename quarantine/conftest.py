"""Quarantine conftest - loads the quarantine plugin."""

import sys
from pathlib import Path

# Ensure the quarantine module is importable
QUARANTINE_DIR = Path(__file__).parent
if str(QUARANTINE_DIR) not in sys.path:
    sys.path.insert(0, str(QUARANTINE_DIR))

# Import the quarantine plugin
import pytest_quarantine  # noqa: F401

# Register it with pytest
pytest_plugins = ["pytest_quarantine"]
