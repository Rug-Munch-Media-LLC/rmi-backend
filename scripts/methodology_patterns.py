#!/usr/bin/env python3
"""methodology_patterns.py — data-driven detectors for "code, not methods".

Every threshold, vocabulary word and skip path lives in methodology-patterns.json;
this module only applies them. It returns FINDINGS (rule, file, line, detail).
The ratchet/baseline decision lives in check-methodology.py.

Detectors live in methodology_rules.py; this module loads the data, applies the
path-aware skips, walks the tree and aggregates counts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from methodology_rules import RULE_FUNCS

DEFAULT_PATTERNS = Path(__file__).with_name("methodology-patterns.json")


def load_patterns(path: str | Path | None = None) -> dict:
    """Load the pattern data. Missing/empty data is a hard error (fail closed)."""
    p = Path(path) if path else DEFAULT_PATTERNS
    if not p.is_file():
        raise FileNotFoundError(f"pattern data missing: {p}")
    data = json.loads(p.read_text())
    if not data.get("rules"):
        raise ValueError(f"pattern data has no rules: {p}")
    return data


def _fixture_marked(text: str, marker: str) -> bool:
    return marker in "".join(text.splitlines(True)[:5])


def is_skipped(path: Path, root: Path, cfg: dict) -> bool:
    """Path-aware skip: tests/fixtures/examples and explicit fixture markers."""
    rel = path.relative_to(root)
    if any(part in cfg.get("skip_dirs", []) for part in rel.parts[:-1]):
        return True
    if any(path.name.endswith(s) for s in cfg.get("skip_suffixes", [])):
        return True
    for glob in cfg.get("skip_globs", []):
        if path.match(glob):
            return True
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return True
    return _fixture_marked(text, cfg.get("fixture_marker", "methodology-check: fixture"))


def scan_repo(root: Path, patterns: dict) -> list[dict]:
    """Apply every rule across a repo tree; return all findings.

    os.walk with in-place pruning: rglob() would descend node_modules/.git and
    is_skipped() only filters files, which made a full public-repo scan take
    minutes instead of seconds (measured 2026-10-24).
    """
    findings: list[dict] = []
    exts = tuple(patterns.get("extensions", [".py"]))
    skip_dirs = set(patterns.get("skip_dirs", []))
    engine = {
        "check-methodology.py",
        "methodology_patterns.py",
        "methodology_fleet.py",
        "methodology_policy.py",
    }
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".git")]
        for name in filenames:
            if name in engine or not name.endswith(exts):
                continue
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            findings.extend(scan_file(path, root, patterns))
    return findings


def counts_of(findings: list[dict]) -> dict[str, int]:
    """Aggregate findings into per-rule counts."""
    out: dict[str, int] = {}
    for f in findings:
        out[f["rule"]] = out.get(f["rule"], 0) + 1
    return out


def scan_file(path: Path, root: Path, patterns: dict) -> list[dict]:
    """Apply every enabled rule to one file; return findings."""
    cfg = patterns
    if is_skipped(path, root, cfg):
        return []
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return []
    findings = []
    for name, rule in cfg["rules"].items():
        if not rule.get("enabled", True) or name not in RULE_FUNCS:
            continue
        for line, detail in RULE_FUNCS[name](text, rule):
            findings.append(
                {
                    "rule": name,
                    "file": str(path.relative_to(root)),
                    "line": line,
                    "detail": detail,
                }
            )
    return findings
