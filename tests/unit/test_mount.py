"""Tests for app/mount.py - Router mounting logic.

Tests mount_all and _try_mount functions that wire up all routers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI


class TestTryMount:
    """Tests for _try_mount function."""

    def test_mounts_valid_router_module(self):
        """_try_mount should return True for a valid module with router attr."""
        from app.mount import _try_mount

        app = FastAPI()
        result = _try_mount(app, "app.homepage")
        assert result is True

    def test_returns_false_for_missing_router_attr(self):
        """Module without router attr should return False."""
        from app.mount import _try_mount

        app = FastAPI()
        result = _try_mount(app, "app.config")
        # app.config has no `router` attribute
        assert result is False

    def test_returns_false_for_nonexistent_module(self):
        """Nonexistent module should return False without crashing."""
        from app.mount import _try_mount

        app = FastAPI()
        result = _try_mount(app, "app.nonexistent_module")
        assert result is False


class TestMountAll:
    """Tests for mount_all function."""

    def test_returns_mount_count(self):
        """mount_all should return count of successful mounts."""
        from app.mount import mount_all

        app = FastAPI()
        count = mount_all(app)
        assert isinstance(count, int)
        assert count > 0

    def test_mounts_are_isolated(self):
        """One failed mount should not break the rest."""
        from app.mount import ROUTER_MODULES, mount_all

        app = FastAPI()
        original = list(ROUTER_MODULES)

        try:
            # Add a non-existent module in the middle
            ROUTER_MODULES.insert(5, "app.does_not_exist")
            count = mount_all(app)
            # Should still mount successfully
            assert count >= 1
        finally:
            # Restore
            ROUTER_MODULES[:] = original


class TestROUTER_MODULES:
    """Tests for the ROUTER_MODULES list."""

    def test_contains_core_routers(self):
        """ROUTER_MODULES should include core system routes."""
        from app.mount import ROUTER_MODULES

        core_paths = [
            "app.core.health_route",
            "app.homepage",
            "app.api.v1.public.wallet",
        ]
        for path in core_paths:
            assert path in ROUTER_MODULES, f"Missing: {path}"

    def test_all_modules_importable(self):
        """All router modules should be importable."""
        from app.mount import ROUTER_MODULES

        import importlib

        failed = []
        for module_path in ROUTER_MODULES:
            try:
                importlib.import_module(module_path)
            except Exception as e:
                failed.append(f"{module_path}: {e}")

        assert len(failed) == 0, f"Failed imports: {failed}"

    def test_all_modules_have_router_attr(self):
        """All router modules should have a 'router' attribute."""
        from app.mount import ROUTER_MODULES

        import importlib

        missing = []
        for module_path in ROUTER_MODULES:
            module = importlib.import_module(module_path)
            if not hasattr(module, "router"):
                missing.append(module_path)

        assert len(missing) == 0, f"Missing router attr: {missing}"
