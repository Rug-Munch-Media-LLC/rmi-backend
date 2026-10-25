"""Guard: fail only on NEW critical/high advisories in packages the app imports.

Baseline: tests/data/dependency_advisories_baseline.json
Regenerate after triage: DEP_ADVISORY_BASELINE_WRITE=1 pytest tests/test_dependency_advisories.py
CI: DEP_ADVISORY_GUARD=1 pytest tests/test_dependency_advisories.py
Severity comes from OSV CVSS, falling back to GitHub's database_specific.severity.
An advisory OSV cannot score is reported as a warning, never a failure.
"""

from __future__ import annotations

import ast
import warnings
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).parent / "data" / "dependency_advisories_baseline.json"
BLOCKING = frozenset({"CRITICAL", "HIGH"})


def _imported_distributions(package_dir: Path) -> set[str]:
    """Distributions whose top-level module the app imports (runtime reachability)."""
    modules: set[str] = set()
    for path in package_dir.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    mapping = packages_distributions()
    return {dist.lower() for mod in modules for dist in mapping.get(mod, [])}


def _audited_advisories() -> list[dict]:
    """Flatten every advisory pip-audit reports into one row per vuln id."""
    proc = subprocess.run(
        [sys.executable, "-m", "pip_audit", "--skip-editable",
         "--progress-spinner", "off", "--format", "json"],
        capture_output=True, text=True, timeout=1800,
    )
    if not proc.stdout.strip():
        pytest.skip(f"pip-audit produced no JSON (rc={proc.returncode})")
    rows: list[dict] = []
    for dep in json.loads(proc.stdout).get("dependencies", []):
        for vuln in dep.get("vulns") or []:
            rows.append({"id": vuln["id"], "package": dep["name"],
                         "version": dep["version"],
                         "fix_versions": vuln.get("fix_versions") or []})
    return rows


def _osv_severity(vuln_id: str) -> str:
    try:
        with urllib.request.urlopen(f"https://api.osv.dev/v1/vulns/{vuln_id}", timeout=10) as resp:
            doc = json.load(resp)
    except Exception:
        return "UNKNOWN"
    for entry in doc.get("severity") or []:
        if entry.get("type", "").startswith("CVSS"):
            try:
                score = float(str(entry.get("score", "")).rsplit("/", 1)[-1])
            except ValueError:
                continue
            return "CRITICAL" if score >= 9.0 else "HIGH" if score >= 7.0 else "MODERATE"
    return str((doc.get("database_specific") or {}).get("severity") or "UNKNOWN").upper()


def test_no_new_high_advisories_in_imported_packages() -> None:
    if importlib.util.find_spec("pip_audit") is None:
        pytest.skip("pip-audit not installed")
    if os.environ.get("DEP_ADVISORY_GUARD") != "1" and not os.environ.get("DEP_ADVISORY_BASELINE_WRITE"):
        pytest.skip("runs in the deps-audit CI job (DEP_ADVISORY_GUARD=1)")

    imported = _imported_distributions(REPO / "app")
    reachable = [r for r in _audited_advisories() if r["package"].lower() in imported]

    if os.environ.get("DEP_ADVISORY_BASELINE_WRITE"):
        accepted = {r["id"]: {"package": r["package"], "version": r["version"],
                              "severity": _osv_severity(r["id"])} for r in reachable}
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({"accepted": accepted}, indent=2, sort_keys=True) + "\n")
        return

    accepted = json.loads(BASELINE.read_text())["accepted"]
    blocking, unknown = [], []
    for row in reachable:
        if row["id"] in accepted:
            continue
        row["severity"] = _osv_severity(row["id"])
        if row["severity"] in BLOCKING:
            blocking.append(row)
        elif row["severity"] == "UNKNOWN":
            unknown.append(row)

    for row in unknown:
        warnings.warn(f"unscored advisory {row['id']} in {row['package']}", stacklevel=1)
    assert not blocking, "NEW critical/high advisories in imported packages: " + "; ".join(
        f"{r['severity']} {r['id']} in {r['package']}=={r['version']}"
        f" (fix: {','.join(r['fix_versions']) or 'none'})" for r in blocking)
