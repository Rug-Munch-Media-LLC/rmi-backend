"""Body-quality scoring for the X lane, without importing the app package.

The canonical scorer is `app/domains/news/quality.py`. Importing it as a module
(`app.domains.news.quality`) drags in `app/config.py`, which refuses to build
Settings without JWT_SECRET — so the lane could not run in a bare shell or
under systemd. This module loads the SAME FILE by path, which avoids the
package import entirely and keeps one source of truth (no vendored duplicate).

One X-specific override lives here and only here: a short post is a COMPLETE
post, not a truncated article, so a `stub` verdict is floored to `thin`.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

MIN_POST = 40
_QUALITY_PY = Path(__file__).resolve().parents[2] / "app" / "domains" / "news" / "quality.py"


def _load_canonical():
    """Load app/domains/news/quality.py by path; fail loudly, never silently."""
    spec = importlib.util.spec_from_file_location("news_quality_canonical", _QUALITY_PY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the canonical scorer at {_QUALITY_PY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


_CANON = _load_canonical()


@dataclass(frozen=True, slots=True)
class Scored:
    """Scoring outcome the store writes into news_items."""

    body_source: str
    score: int
    verdict: str
    truncated: bool


def score(text: str, url: str) -> Scored:
    """Score one post with the canonical scorer plus the X short-post floor."""
    result = _CANON.score_body(text, url, text[:120])
    verdict = "thin" if result.verdict == "stub" and len(text) >= MIN_POST else result.verdict
    return Scored(result.body_source, result.score, verdict, result.truncated)
