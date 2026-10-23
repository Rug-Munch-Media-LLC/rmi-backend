"""Regression: malformed CT timestamps must not abort the rundown."""

from app.databus.x_intel.analyzers import score_ct_post


def test_score_ct_post_malformed_created_at_does_not_raise() -> None:
    score = score_ct_post(
        {"created_at": "not-a-date", "text": "hello", "likes": 1, "retweets": 0},
        {"handle": "test"},
    )
    assert isinstance(score, float)
