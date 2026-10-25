"""Hashing helpers: full-body hash, normalisation, tokenisation, near-dup."""

from __future__ import annotations

from app.utils.dedup import content_hash, near_duplicate, normalize_text, tokenize

BODY = "Uniswap and Aave rally as $UNI holders watch Vitalik Buterin speak."


def test_content_hash_keeps_full_body() -> None:
    long_text = "word " * 400
    assert content_hash(long_text + "tail") != content_hash(long_text + "other")
    assert len(content_hash(BODY)) == 64


def test_normalize_text_and_tokenize() -> None:
    assert normalize_text("  Foo\n\nBAR  ") == "foo bar"
    assert tokenize("Foo, bar_baz 42!") == {"foo", "bar", "baz", "42"}
    assert normalize_text("") == ""
    assert tokenize("") == set()


def test_near_duplicate_boundaries() -> None:
    assert near_duplicate("alpha beta gamma", "alpha beta gamma") is True
    assert near_duplicate("alpha beta gamma", "one two three") is False
    assert near_duplicate("", "") is False
    assert near_duplicate("alpha beta", "") is False
    assert near_duplicate("alpha beta gamma delta", "alpha beta", threshold=0.4) is True
