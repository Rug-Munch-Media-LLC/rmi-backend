#!/usr/bin/env python3
"""methodology_rules.py — the five detector functions (data-driven).

Each function receives file text plus its config block from
methodology-patterns.json and returns [(line, detail)]. No thresholds, words or
paths are hardcoded here: the JSON is the single source of truth.
"""

from __future__ import annotations

import re

_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*$")
_ENTRY = re.compile(r"""["']?([A-Za-z][A-Za-z0-9_ \-]{2,40})["']?\s*[:=]\s*(-?\d*\.?\d+)""")
_STRING = re.compile(r'("""|\'\'\'|"|\')(.{40,6000}?)\1', re.DOTALL)
_ARRAY = re.compile(r"\[([^\[\]]{10,3000})\]", re.DOTALL)
_PY_IMPORT = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+\(?([A-Za-z_][\w,\s]*)\)?")
_JS_IMPORT = re.compile(r"""^\s*import\s*\{([^}]+)\}\s*from\s*['"]([^'"]+)['"]""")
_CMP = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(>=|<=|>|<)\s*(\d*\.\d+|\d+)\b")


def _weight_map(text: str, rule: dict) -> list[tuple[int, str]]:
    hits, vocab = [], rule["vocabulary"]
    for m in re.finditer(r"\{([^{}]{20,4000})\}", text, re.DOTALL):
        _block, entries = m.group(1), _ENTRY.findall(m.group(1))
        nums = [(k, float(v)) for k, v in entries if 0 < float(v) <= 1]
        if len(nums) < rule["min_keys"] or len(nums) < rule["min_share"] * max(len(entries), 1):
            continue
        if not rule["sum_low"] <= sum(v for _, v in nums) <= rule["sum_high"]:
            continue
        head = text[max(0, m.start() - 120) : m.start()]
        named = bool(re.search(rule["name_hint"], head))
        keyed = sum(1 for k, _ in nums if any(v in k.lower() for v in vocab))
        if named or keyed >= rule["min_vocab_keys"]:
            hits.append(
                (
                    text[: m.start()].count("\n") + 1,
                    "weight map: " + ", ".join(k for k, _ in nums[:4]),
                )
            )
    return hits


def _magic_threshold(text: str, rule: dict) -> list[tuple[int, str]]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("#", "//", "*")):
            continue
        for m in _CMP.finditer(line):
            name, lit = m.group(1), float(m.group(3))
            if rule["ignore_literals"].count(lit):
                continue
            # A NAMED threshold may use any literal; a SCORING variable must be
            # compared against a fraction (0 < x < 1). Percentages such as
            # `avg_risk >= 80` are display banding, not a hidden cut-off, and
            # letting them through made the gate cry wolf 342 times.
            named = re.search(rule["name_hint"], name)
            scored = 0 < lit < 1 and re.search(rule["score_name_hint"], name)
            if named or scored:
                hits.append((i, f"threshold compared: {name} {m.group(2)} {m.group(3)}"))
        m = re.match(
            r"^\s*(?:export\s+)?(?:const|let|var|final|val)?\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(\d*\.\d+)\s*;?\s*$",
            line,
        )
        if m and re.search(rule["name_hint"], m.group(1)):
            hits.append((i, f"threshold constant: {m.group(1)} = {m.group(2)}"))
    return hits


def _signal_list(text: str, rule: dict) -> list[tuple[int, str]]:
    hits, vocab = [], rule["vocabulary"]
    for m in _ARRAY.finditer(text):
        items = [s.strip().strip("'\"") for s in m.group(1).split(",")]
        items = [s for s in items if re.fullmatch(r"[A-Za-z][A-Za-z0-9_ \-]{2,40}", s)]
        if len(items) < rule["min_items"]:
            continue
        shaped = sum(1 for s in items if re.fullmatch(rule["item_pattern"], s))
        if shaped < rule["min_pattern_share"] * len(items):
            continue
        score = sum(1 for s in items if set(re.split(r"[_ \-]", s.lower())) & set(vocab))
        if score >= rule["min_vocab_hits"]:
            hits.append(
                (
                    text[: m.start()].count("\n") + 1,
                    f"signal list: {', '.join(items[:5])}",
                )
            )
    return hits


def _prompt_judgement(text: str, rule: dict) -> list[tuple[int, str]]:
    hits = []
    for m in _STRING.finditer(text):
        body = m.group(2)
        low = body.lower()
        n = sum(1 for p in rule["phrases"] if p in low)
        if len(body) >= rule["min_chars"] and n >= rule["min_phrases"]:
            hits.append(
                (
                    text[: m.start()].count("\n") + 1,
                    f"judgement prompt ({n} phrases, {len(body)} chars)",
                )
            )
    return hits


def _packaged_constants(text: str, rule: dict) -> list[tuple[int, str]]:
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        m = _PY_IMPORT.match(line) or _JS_IMPORT.match(line)
        if not m:
            continue
        mod, syms = (m.group(1), m.group(2)) if _PY_IMPORT.match(line) else (m.group(2), m.group(1))
        # Module must be a scoring MODULE (segment match, not substring:
        # `app.wallet_factory` is not a scoring module) and the symbol must be a
        # packaged CONSTANT, so importing a score_* function is not a finding.
        if not any(re.fullmatch(rule["module_segment"], seg) for seg in mod.replace("/", ".").split(".")):
            continue
        consts = [s.strip() for s in re.split(r"[,\s]+", syms) if s.strip()]
        if any(re.search(rule["symbol_hint"], c) and re.fullmatch(rule["constant_pattern"], c) for c in consts):
            hits.append((i, f"packaged scoring constants from {mod}"))
    return hits


RULE_FUNCS = {
    "weight-map": _weight_map,
    "magic-threshold": _magic_threshold,
    "signal-list": _signal_list,
    "prompt-judgement": _prompt_judgement,
    "packaged-constants": _packaged_constants,
}
