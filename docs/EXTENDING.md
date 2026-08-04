# Extending RugMunchScan

RugMunchScan (`app/domains/scanners/`) is extended through **three tiers** —
no code-plugin system needed.  Every extension point is a plain Python file
dropped into the right directory.

---

## Tier 1 — Add a DataBus Provider

DataBus providers supply raw data from external APIs (free or paid).
They live under `app/databus/` and are registered in a `ProviderChain`.

**What to write**

Create a Python module in `app/databus/` or `app/domains/databus/providers/`.
Export an async function with a standard signature:

```python
import httpx

async def my_security_check(address: str, chain: str) -> dict:
    """Return a dict with risk_score, warnings, etc."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"https://free-api.example/check/{address}")
        data = resp.json()
        return {
            "risk_score": data.get("score", 0),
            "warnings": data.get("flags", []),
        }
```

**Where to register it**

Open `app/domains/databus/providers/registry/categories/security.py` and add
your function as a `Provider` inside a `ProviderChain`:

```python
from app.databus.my_provider import my_security_check

chains["my_security"] = ProviderChain(
    "my_security",
    description="My free security check",
    providers=[
        Provider(
            "my_check",
            ProviderTier.FREE_API,
            my_security_check,
            weight=10.0,
            rate_limit_rps=2.0,
        ),
    ],
)
```

**How to test it**

```bash
# Call the endpoint that triggers your chain
curl -X POST https://api.rugmunch.io/api/v1/databus/query \
  -H "X-API-Key: $RMI_API_KEY" \
  -d '{"data_type": "my_security", "params": {"address": "0x..."}}'
```

**Key rules**
- Always prefer free APIs first (see `AGENTS.md` Frugality Principle).
- Register under the correct category file (`security.py`, `market.py`, etc.).
- Set realistic `rate_limit_rps` values — don't hammer free APIs.
- Use `ProviderTier.FREE_API` for zero-cost providers, `ProviderTier.LOCAL` for
  self-hosted (ClickHouse, local services).

---

## Tier 2 — Add a SENTINEL Module

SENTINEL modules are forensic detectors that run inside the scanner pipeline.
Each module is a single class decorated with `@sentinel_module`.

**What to write**

Create a file in `app/domains/scanners/` (e.g. `my_detector.py`):

```python
"""SENTINEL - My Custom Detector"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("my_detector")


@dataclass
class MyReport:
    token_address: str
    chain: str
    risk_score: float = 0.0
    warnings: list[str] = field(default_factory=list)


@sentinel_module(
    key="my_detection",
    tier="premium",           # "free" or "premium"
    description="My custom forensic check",
)
class MyDetector:
    """Detects some specific threat pattern."""

    async def analyze(self, address: str, chain: str) -> MyReport:
        # Your detection logic here
        return MyReport(
            token_address=address,
            chain=chain,
            risk_score=0.0,
        )
```

That's it.  The `@sentinel_module` decorator auto-registers your class in the
central `_SENTINEL_REGISTRY`.  The pipeline picks it up automatically —
**no manual edits to 5 files required**.

**How it works**

- `@sentinel_module(key=..., tier=..., description=...)` registers the class.
- `_ensure_modules()` in `sentinel_pipeline/modules.py` instantiates your class.
- `_get_analyzers()` in `sentinel_phase.py` builds the async callable.
- `SENTINEL_FREE_MODULES` / `SENTINEL_PREMIUM_MODULES` and descriptions are
  derived from the registry.
- `run_sentinel_scan()` and `run_sentinel_phase()` pick up new modules without
  changes.

**Decorator parameters**

| Parameter       | Required | Default    | Description                                     |
|-----------------|----------|------------|-------------------------------------------------|
| `key`           | yes      | —          | Unique module key (snake_case)                  |
| `tier`          | no       | `"premium"`| `"free"` or `"premium"`                         |
| `description`   | no       | `""`       | Human-readable one-liner for premium preview    |
| `method`        | no       | `"analyze"`| Async method name on the class                  |
| `needs_deployer`| no       | `False`    | Set `True` if module needs a deployer address   |
| `chain_gated`   | no       | `None`      | Chain name (e.g. `"solana"`) for chain-specific |

**Non-standard signatures**

Most modules follow `async def analyze(self, address, chain) -> dict`.
If your module needs a different signature (e.g. deployer-dependent, chain-gated,
different arg order), add a lambda builder to the `_ANALYZER_OVERRIDES` dict in
`sentinel_phase.py`:

```python
_register_analyzer_override("my_detection", lambda mod, a, c, d: lambda: mod._my_detector.analyze(a))
```

**How to test it**

```python
# Unit test — call the module directly
from app.domains.scanners.my_detector import MyDetector
detector = MyDetector()
result = await detector.analyze("0x...", "ethereum")
assert result.risk_score >= 0

# Integration test — via the runner
from app.domains.scanners.sentinel_pipeline import run_sentinel_scan
report = await run_sentinel_scan("0x...", "ethereum")
assert "my_detection" in report.modules_run
```

---

## Tier 3 — Add a Scoring Rule

Risk scoring rules live in `sentinel_pipeline/risk.py`.  They calculate
per-module risk scores and contribute to the composite score.

**What to write**

Add an extractor function and a weight in `risk.py`:

```python
# ── Step 1: Risk extractor (maps module output → 0-100) ──────────

def _extract_risk_my_detection(report) -> float:
    """Convert my_detection report to a 0-100 risk score."""
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    # Custom heuristic
    if hasattr(report, "threat_count"):
        return min(float(report.threat_count) * 20, 100)
    return 0.0
```

**Where to wire it**

In `runners.py`, add your extractor to the `extractors` dict and the weight to
`_compute_composite()` in `risk.py`:

```python
# In _compute_composite() weights dict:
weights = {
    # ...existing weights...
    "my_detection": 0.05,   # ← add your weight here
}
```

```python
# In run_sentinel_scan() extractors dict:
extractors = {
    # ...existing extractors...
    "my_detection": _extract_risk_my_detection,  # ← add here
}
```

**Custom red flags**

Red flags are pulled from `warnings`, `red_flags`, `flags`, and `risks` keys in
your report dict.  Return a list of strings in your report and they will be
aggregated automatically:

```python
@dataclass
class MyReport:
    warnings: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
```

**How to test it**

```python
from app.domains.scanners.sentinel_pipeline.risk import (
    _compute_composite,
    _extract_risk_my_detection,
)
from app.domains.scanners.my_detector import MyReport

report = MyReport(risk_score=75)
score = _extract_risk_my_detection(report)
assert score == 75.0

composite, level = _compute_composite({"my_detection": 75.0})
assert 0 <= composite <= 100
```

---

## Quick Reference — File Map

| Extension Type       | File to Create/Edit                          | What Touches It                                          |
|----------------------|----------------------------------------------|----------------------------------------------------------|
| DataBus provider     | `app/databus/my_provider.py`                 | `app/domains/databus/providers/registry/categories/`     |
| SENTINEL module      | `app/domains/scanners/my_detector.py`        | **Just one file** (decorator handles the rest)           |
| Scoring rule         | `app/domains/scanners/sentinel_pipeline/risk.py` | `app/domains/scanners/sentinel_pipeline/runners.py` |

---

## Full Example — Holder Concentration Check

Here's a complete walkthrough of adding a "holder concentration" module.

### 1. Create the module file

`app/domains/scanners/holder_concentration.py`:

```python
"""SENTINEL - Holder Concentration (HHI) Check"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("holder_concentration")


@dataclass
class ConcentrationReport:
    token_address: str
    chain: str
    hhi: float = 0.0           # Herfindahl-Hirschman Index
    top_10_pct: float = 0.0     # % held by top 10 wallets
    whale_count: int = 0        # wallets holding > 5%
    risk_score: float = 0.0
    risk_level: str = "low"
    warnings: list[str] = field(default_factory=list)


@sentinel_module(
    key="holder_concentration",
    tier="free",
    description="Holder HHI analysis for wallet concentration risk",
)
class HolderConcentrationAnalyzer:
    """Calculate concentration risk using the HHI metric."""

    async def analyze(self, address: str, chain: str) -> ConcentrationReport:
        # Your holder analysis logic here
        return ConcentrationReport(
            token_address=address,
            chain=chain,
            hhi=0.0,
        )
```

### 2. Add scoring weight

In `risk.py`:

```python
def _extract_risk_holder_concentration(report) -> float:
    if hasattr(report, "risk_score"):
        return float(report.risk_score)
    if hasattr(report, "hhi"):
        return min(float(report.hhi) / 100, 100)
    return 0.0
```

In `runners.py`:

```python
# Add to extractors dict inside run_sentinel_scan():
"holder_concentration": _extract_risk_holder_concentration,

# Add to field_map dict:
"holder_concentration": "holder_concentration",
```

In the `_compute_composite()` weights dict in `risk.py`:

```python
"holder_concentration": 0.15,  # High weight — concentration is critical
```

### 3. Run tests

```bash
pytest tests/test_sentinel_pipeline.py -q
```

The module is now live.  Free-tier users get it automatically.

---

## Architecture Diagram

```
                     ┌──────────────────────────────────┐
                     │      RugMunchScan Pipeline       │
                     └──────────────────────────────────┘
                                      │
             ┌────────────────────────┼────────────────────────┐
             ▼                        ▼                        ▼
     ┌───────────────┐      ┌─────────────────┐      ┌────────────────┐
     │  DataBus      │      │  SENTINEL       │      │  Risk Scoring  │
     │  Providers    │      │  Modules        │      │  Engine        │
     │  (Tier 1)     │      │  (Tier 2)       │      │  (Tier 3)      │
     └───────────────┘      └─────────────────┘      └────────────────┘
             │                        │                        │
     ┌───────┴───────┐        ┌───────┴───────┐        ┌───────┴───────┐
     │ API calls     │        │ @sentinel_    │        │ extract_risk  │
     │ ProviderChain │        │ module        │        │ _compute_     │
     │ CircuitBreak  │        │ decorator     │        │ _composite    │
     └───────────────┘        └───────────────┘        └───────────────┘
             │                        │                        │
             └────────────────────────┼────────────────────────┘
                                      ▼
                     ┌──────────────────────────────────┐
                     │     Unified Scan Result          │
                     │  (safety_score, risk_level,      │
                     │   modules_run, sentinel, etc.)   │
                     └──────────────────────────────────┘
```
