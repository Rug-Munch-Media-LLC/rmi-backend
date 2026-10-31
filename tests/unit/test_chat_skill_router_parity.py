"""Router ↔ executor parity: every tool a lane names must exist.

``chat_tools`` cannot be imported here (the package ``__init__`` builds
``Settings`` and needs real secrets), so the executor registry is read by
AST-parsing ``dispatcher.py`` — the same technique
``tests/unit/test_chat_personas.py`` uses to keep ``DATA_TOOLS`` honest.

A lane that names a tool with no executor is a silent fall-through: the router
promises a data card and the answer arrives with no data. This module makes that
a failing test instead of a production surprise.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.domains.chat._crypto_skills import FAMILY_SKILLS
from app.domains.chat.skill_router import _PLANS, UNKNOWN, route_skill

DISPATCHER = Path(__file__).resolve().parents[2] / "app" / "domains" / "chat_tools" / "dispatcher.py"


def _dispatcher_tree() -> ast.Module:
    return ast.parse(DISPATCHER.read_text(encoding="utf-8"))


def _registered_intents() -> set[str]:
    """Executor keys from ``_EXECUTORS``, read without importing the package."""
    for node in ast.walk(_dispatcher_tree()):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_EXECUTORS"
            and isinstance(node.value, ast.Dict)
        ):
            return {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
    raise AssertionError("_EXECUTORS not found in chat_tools/dispatcher.py")


def _imported_executors() -> set[str]:
    """Executor symbols imported from the per-tool submodules into the dispatcher."""
    names: set[str] = set()
    for node in ast.walk(_dispatcher_tree()):
        if isinstance(node, ast.ImportFrom) and node.module and node.module != "__future__":
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("intent", sorted(_PLANS))
def test_every_lane_tool_has_an_executor(intent: str) -> None:
    """Each tool a lane names resolves in the dispatcher registry."""
    missing = set(_PLANS[intent].tools) - _registered_intents()
    assert not missing, f"{intent} names tool(s) with no executor: {sorted(missing)}"


@pytest.mark.parametrize("intent", sorted(_PLANS))
def test_every_registered_tool_is_imported(intent: str) -> None:
    """A registry entry must point at a real, imported executor symbol."""
    for tool in _PLANS[intent].tools:
        assert f"_exec_{tool}" in _imported_executors(), f"{tool} has no _exec_{tool} import"


def test_every_routed_intent_has_a_provenance_family() -> None:
    """Every intent the router can return names the corpus skills behind it."""
    routed = {route_skill(m).intent for m in ("", "gm", "$BTC", "market", "scan 0x" + "a1" * 20)}
    # ``unknown`` is the fail-safe lane: no data tool, no family, generic corpus only.
    assert routed - {UNKNOWN} <= set(FAMILY_SKILLS)


@pytest.mark.parametrize("family", sorted(FAMILY_SKILLS))
def test_family_skills_are_plausible_skill_names(family: str) -> None:
    names = FAMILY_SKILLS[family]
    assert names, f"{family} has no provenance"
    for name in names:
        assert name == name.lower()
        assert " " not in name
        assert "_" not in name, "skill names are hyphenated slugs"
