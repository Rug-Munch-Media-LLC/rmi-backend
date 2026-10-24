"""Resolution honesty: a deployment must not score on the example silently.

The seam (``app.domains.scanner.risk_policy``) is allowed to fall back to
``example_policy()`` so a bare clone runs. It is NOT allowed to hide that:
``policy_report()`` names what resolved, and ``require_calibrated_policy()``
turns the fallback into a refused start in a strict (production) deployment.

The entry point is stubbed out in the fixture: whether this environment has
the private provider installed must not decide what these tests assert.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from app.domains.scanner import risk_policy as seam
from app.domains.scanner.risk_policy import (
    PolicyUnavailable,
    example_policy,
    load_policy,
    policy_report,
    require_calibrated_policy,
    reset_policy,
)


def _spec(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "rules": (("alpha", 40, "alpha fired", "CONTRACT", (((("a",),), lambda v: v is True),)),),
        "hard_floor_flags": ("alpha",),
        "bands": {"critical": 70, "high": 45, "medium": 20},
        "constants": {"CRITICAL_MIN": 70},
        "groups": {"alpha": "CONTRACT"},
    }


def _provider(monkeypatch: pytest.MonkeyPatch, name: str, spec: dict[str, Any]) -> None:
    module = types.ModuleType(f"rmi_{name}_provider")
    module.default_policy = lambda: spec  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("RMI_RISK_POLICY", f"{module.__name__}:default_policy")
    reset_policy()


@pytest.fixture(autouse=True)
def _no_entry_point(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("RMI_RISK_POLICY", raising=False)
    monkeypatch.setattr(seam, "_provider_from_entry_point", lambda: None)
    reset_policy()
    yield
    reset_policy()


def test_report_names_the_example_and_flags_it() -> None:
    assert policy_report() == {
        "name": example_policy().name,
        "source": "example",
        "illustrative": True,
    }


def test_strict_resolution_refuses_the_example() -> None:
    with pytest.raises(PolicyUnavailable):
        require_calibrated_policy(strict=True)


def test_non_strict_resolution_warns_but_returns() -> None:
    assert require_calibrated_policy(strict=False).illustrative is True


def test_env_provider_is_reported_as_calibrated(monkeypatch: pytest.MonkeyPatch) -> None:
    _provider(monkeypatch, "calibrated", _spec("rmi-test-v9"))
    report = policy_report()
    assert report == {
        "name": "rmi-test-v9",
        "source": "RMI_RISK_POLICY",
        "illustrative": False,
    }
    assert load_policy().name == "rmi-test-v9"


def test_provider_returning_the_example_is_still_illustrative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = example_policy()
    spec = {
        "name": example.name,
        "rules": tuple(example.rules),
        "hard_floor_flags": tuple(example.hard_floor_flags),
        "bands": dict(example.bands),
        "constants": dict(example.constants),
        "groups": dict(example.groups),
    }
    _provider(monkeypatch, "exampleish", spec)
    assert policy_report()["illustrative"] is True
