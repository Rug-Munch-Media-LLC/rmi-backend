"""Token rater — thin adapter over the canonical rugmunch-scanner engine.

Cutover: rmi-backend `app/scoring/token_rater.py` was superseded by
`rugmunch_scanner.scoring.token_rater`. This module exists only to keep the
backward-compatible `app.scoring.token_rater` import path alive for
`app/routers/scoring_router.py` and the `app.scoring` package re-exports.

The scanner package is the single source of truth. All public symbols are
re-exported explicitly; private (`_*`) helpers live in the scanner package.
"""

from __future__ import annotations

from rugmunch_scanner.scoring.token_rater import (
    CategoryScore,
    RatingBreakdown,
    RatingGrade,
    TokenRater,
    TokenRatingResult,
    compute_rating,
)

__all__ = [
    "CategoryScore",
    "RatingBreakdown",
    "RatingGrade",
    "TokenRater",
    "TokenRatingResult",
    "compute_rating",
]
