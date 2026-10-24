"""Alias registry: prompts, grants, and citation requirement."""

from __future__ import annotations

import pytest

from app.assistant_gateway.aliases import _GUARDRAILS, ALIASES, resolve_alias
from app.assistant_gateway.tools import TOOL_SPECS, is_allowed, select_tools


def test_four_page_aliases_are_registered() -> None:
    assert set(ALIASES) == {"rugcharts", "rugmaps", "scanner", "trades"}


@pytest.mark.parametrize("name", ["rugcharts", "rugmaps", "scanner", "trades"])
def test_each_alias_owns_a_guarded_system_prompt(name: str) -> None:
    spec = resolve_alias(name)
    assert spec.system_prompt.startswith(f"You are the assistant embedded in {spec.product}")
    assert "Hard rules" in spec.system_prompt
    assert _GUARDRAILS in spec.system_prompt
    assert "never act" in spec.system_prompt


def test_prompts_are_page_specific_not_shared() -> None:
    prompts = {spec.system_prompt for spec in ALIASES.values()}
    assert len(prompts) == 4


def test_alias_tools_all_exist() -> None:
    for spec in ALIASES.values():
        for tool in spec.tools:
            assert tool in TOOL_SPECS, f"{spec.name} grants unknown tool {tool}"


def test_only_scanner_requires_citations() -> None:
    required = {spec.name for spec in ALIASES.values() if spec.requires_citations}
    assert required == {"scanner"}
    assert "rag_search" in ALIASES["scanner"].tools


def test_scanner_citation_rule_is_in_its_prompt() -> None:
    assert "cite sources inline as [n]" in ALIASES["scanner"].system_prompt.lower()


def test_unknown_alias_raises() -> None:
    with pytest.raises(Exception, match="unknown page alias"):
        resolve_alias("admin")


def test_missing_alias_raises() -> None:
    with pytest.raises(Exception, match="alias is required"):
        resolve_alias(None)


def test_client_cannot_widen_the_tool_grant() -> None:
    spec = resolve_alias("rugcharts")
    granted = {tool.name for tool in select_tools(spec, ["scanner_full", "shell_exec"])}
    assert granted == {"scanner_price", "scanner_risk", "trades_recent"}
    assert is_allowed(spec, "scanner_full") is False


def test_client_can_narrow_the_tool_grant() -> None:
    spec = resolve_alias("rugcharts")
    granted = [tool.name for tool in select_tools(spec, ["scanner_risk"])]
    assert granted == ["scanner_risk"]


def test_cross_alias_tool_is_denied() -> None:
    assert is_allowed(resolve_alias("trades"), "rag_search") is False
    assert is_allowed(resolve_alias("scanner"), "trades_recent") is False
