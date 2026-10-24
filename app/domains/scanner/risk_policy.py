"""The seam: public scoring engine, private scoring method.

Open code, closed recipe. This module is the *whole* public surface of the
rug-risk method:

* ``RulePolicy`` — the engine. It iterates a rule table and sums weights. On
  its own it reveals nothing: it is ``sum(weights)``, clamp, floor, band.
* ``RiskPolicy`` — the contract a provider must satisfy.
* ``load_policy()`` — resolves the calibrated policy from a private package.
* ``example_policy()`` — a deliberately trivial, obviously illustrative
  policy so the repository is runnable and testable on its own.

What is NOT here: the per-signal weights, the firing thresholds, the feature
set (which signals exist and how they group), and the verdict bands. Those
live in the private package named by ``RMI_RISK_POLICY`` (or the
``rmi.risk_policy`` entry point) and are injected at runtime.

The public contract is the *shape* of a policy spec::

    {
      "name": str,
      "rules": tuple[(flag, weight, reason, group, sources), ...],
      "hard_floor_flags": tuple[str, ...],
      "bands": {"critical": int, "high": int, "medium": int},
      "constants": {name: number, ...},   # re-published by ``_weights``
      "groups": {signal: group, ...},     # used by the text renderers
    }

where a rule's ``sources`` is ``((key_paths, predicate), ...)`` — probed in
order, first hit wins.

A reader of this repository can therefore understand, test and re-implement
the *interface* and the *plumbing*, and can see that a calibration exists,
without being able to reproduce the calibration.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

ENV_VAR = "RMI_RISK_POLICY"
ENTRY_POINT_GROUP = "rmi.risk_policy"

#: (key-paths, predicate) probe pair.
RuleSource = tuple[tuple[tuple[str, ...], ...], Callable[[Any], bool]]
#: (flag, weight, reason, group, sources) — the rule-table row shape.
Rule = tuple[str, int, str, str, tuple[RuleSource, ...]]


class PolicyUnavailable(RuntimeError):
    """A provider was named but could not be loaded or is malformed."""


@runtime_checkable
class RiskPolicy(Protocol):
    """What the scorer needs from a calibration provider."""

    name: str
    rules: Sequence[Rule]
    hard_floor_flags: Sequence[str]
    bands: Mapping[str, int]
    constants: Mapping[str, int | float]
    groups: Mapping[str, str]

    def evaluate(self, signals: Mapping[str, Any]) -> dict[str, Any]: ...

    def verdict_for(self, score: int) -> str: ...


def probe(signals: Mapping[str, Any], paths: Sequence[tuple[str, ...]]) -> Any:
    """Return the first present value among ``paths``; ``None`` when absent."""
    for path in paths:
        cursor: Any = signals
        found = True
        for key in path:
            if isinstance(cursor, Mapping) and key in cursor:
                cursor = cursor[key]
            else:
                found = False
                break
        if found and cursor is not None:
            return cursor
    return None


def _hit(rule: Rule, signals: Mapping[str, Any]) -> bool:
    flag, _weight, _reason, _group, sources = rule
    for paths, predicate in sources:
        try:
            if bool(predicate(probe(signals, paths))):
                return True
        except Exception:
            logger.debug("risk_policy: predicate failed for %s", flag, exc_info=True)
    return False


@dataclass(frozen=True)
class RulePolicy:
    """Generic weighted-rule engine. Holds no calibration of its own."""

    name: str
    rules: tuple[Rule, ...]
    hard_floor_flags: tuple[str, ...]
    bands: Mapping[str, int]
    constants: Mapping[str, int | float]
    groups: Mapping[str, str]

    def evaluate(self, signals: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(signals, Mapping):
            return {"score": 0, "verdict": self.verdict_for(0), "flags": [], "reasons": [], "analysis": []}

        score = 0
        flags: list[str] = []
        reasons: list[str] = []
        analysis: list[dict[str, Any]] = []

        for rule in self.rules:
            flag, weight, reason = rule[0], rule[1], rule[2]
            if not _hit(rule, signals):
                continue
            score += weight
            flags.append(flag)
            reasons.append(reason)
            analysis.append({"signal": flag, "weight": weight, "detail": reason})

        score = max(0, min(100, score))
        # A confirmed identity fact is not probabilistic — it is floored.
        if any(flag in flags for flag in self.hard_floor_flags):
            score = max(score, self.bands["critical"])

        return {"score": score, "verdict": self.verdict_for(score), "flags": flags, "reasons": reasons, "analysis": analysis}

    def verdict_for(self, score: int) -> str:
        if score >= self.bands["critical"]:
            return "critical"
        if score >= self.bands["high"]:
            return "high"
        if score >= self.bands["medium"]:
            return "medium"
        return "low"

    @classmethod
    def from_spec(cls, spec: Mapping[str, Any]) -> RulePolicy:
        """Validate a provider spec and wrap it in the engine."""
        required = ("name", "rules", "hard_floor_flags", "bands", "constants", "groups")
        missing = [key for key in required if key not in spec]
        if missing:
            raise PolicyUnavailable(f"policy spec missing keys: {missing}")
        bands = dict(spec["bands"])
        if not {"critical", "high", "medium"} <= set(bands):
            raise PolicyUnavailable("policy spec bands must define critical/high/medium")
        return cls(
            name=str(spec["name"]),
            rules=tuple(spec["rules"]),
            hard_floor_flags=tuple(spec["hard_floor_flags"]),
            bands=bands,
            constants=dict(spec["constants"]),
            groups=dict(spec["groups"]),
        )


def example_policy() -> RulePolicy:
    """Deliberately trivial policy: 3 invented signals, round numbers.

    Illustrative only — it is NOT a reduced copy of the calibrated method and
    must never be presented as production scoring. It exists so the public
    repository runs, and so the interface is demonstrated by example.
    """
    return RulePolicy.from_spec(
        {
            "name": "example-v0",
            "rules": (
                ("honeypot", 50, "example: sell is blocked", "CONTRACT",
                 ((("honeypot",),), lambda value: value is True)),
                ("lp_removed", 30, "example: liquidity withdrawn", "LIQUIDITY",
                 ((("lp_removed",),), lambda value: value is True)),
                ("very_young", 20, "example: token is brand new", "MOMENTUM",
                 ((("age_seconds",),), lambda value: isinstance(value, (int, float)) and value < 3600)),
            ),
            "hard_floor_flags": ("honeypot",),
            "bands": {"critical": 70, "high": 45, "medium": 20},
            "constants": {"EXAMPLE_POLICY": 1},
            "groups": {"honeypot": "CONTRACT", "lp_removed": "LIQUIDITY", "very_young": "MOMENTUM"},
        }
    )


def _provider_from_env() -> Callable[[], Mapping[str, Any]] | None:
    target = os.environ.get(ENV_VAR, "").strip()
    if not target:
        return None
    module_name, _, attr = target.partition(":")
    attr = attr or "default_policy"
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise PolicyUnavailable(f"{ENV_VAR}={target!r} could not be imported: {exc}") from exc
    provider = getattr(module, attr, None)
    if not callable(provider):
        raise PolicyUnavailable(f"{ENV_VAR}={target!r} has no callable {attr!r}")
    return provider


def _provider_from_entry_point() -> Callable[[], Mapping[str, Any]] | None:
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        return entry.load()
    return None


@lru_cache(maxsize=1)
def load_policy() -> RulePolicy:
    """Resolve the calibrated policy; fall back to the example, loudly."""
    provider = _provider_from_env()
    source = ENV_VAR
    if provider is None:
        provider = _provider_from_entry_point()
        source = f"entry point {ENTRY_POINT_GROUP!r}"
    if provider is None:
        policy = example_policy()
        logger.warning(
            "risk_policy: no calibrated provider (%s unset, no %r entry point). "
            "Using %s — scores are ILLUSTRATIVE. Install the private policy package "
            "or a public deployment is misconfigured.",
            ENV_VAR, ENTRY_POINT_GROUP, policy.name,
        )
        return policy
    spec = provider()
    if isinstance(spec, RulePolicy):
        return spec
    logger.info("risk_policy: loaded %r from %s", str(spec.get("name")), source)
    return RulePolicy.from_spec(spec)


def reset_policy() -> None:
    """Drop the cached policy (tests and hot-reload)."""
    load_policy.cache_clear()


__all__ = [
    "ENTRY_POINT_GROUP",
    "ENV_VAR",
    "PolicyUnavailable",
    "RiskPolicy",
    "RulePolicy",
    "example_policy",
    "load_policy",
    "probe",
    "reset_policy",
]
