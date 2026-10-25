"""Level 4: entity extraction, overlap matching and the six-hour window."""

from __future__ import annotations

from app.utils.dedup import (
    KNOWN_TOKENS,
    extract_crypto_entities,
    is_duplicate_by_entities,
    parse_published_at,
    within_window,
)

STORY = "Uniswap and Aave rally as $UNI holders watch Vitalik Buterin speak."


def test_extracts_tickers_vocabulary_and_people() -> None:
    entities = extract_crypto_entities(STORY)
    assert "uni" in entities  # $UNI ticker
    assert "uniswap" in entities
    assert "aave" in entities
    assert "vitalik buterin" in entities


def test_extracts_addresses() -> None:
    evm = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
    sol = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
    entities = extract_crypto_entities(f"wallet {evm} and {sol}")
    assert evm.lower() in entities
    assert sol in entities


def test_empty_and_entity_free_input() -> None:
    assert extract_crypto_entities("") == set()
    assert extract_crypto_entities("the weather is nice today") == set()


def test_known_tokens_are_lowercase() -> None:
    assert {t.lower() for t in KNOWN_TOKENS} == KNOWN_TOKENS
    assert "btc" in KNOWN_TOKENS


def test_same_story_overlaps() -> None:
    assert is_duplicate_by_entities(STORY, STORY) is True
    assert is_duplicate_by_entities(STORY, "Aave and Uniswap: $UNI holders react") is True


def test_different_story_does_not_overlap() -> None:
    assert is_duplicate_by_entities(STORY, "Solana outage hits $SOL validators") is False
    assert is_duplicate_by_entities(STORY, "the weather is nice today") is False
    assert is_duplicate_by_entities("", STORY) is False


def test_overlap_threshold_is_configurable() -> None:
    a = "uniswap aave"
    b = "uniswap curve"
    assert is_duplicate_by_entities(a, b) is False  # overlap 0.5 < 0.8
    assert is_duplicate_by_entities(a, b, threshold=0.4) is True


def test_parse_published_at_formats() -> None:
    iso = parse_published_at("2026-10-25T10:00:00Z")
    rss = parse_published_at("Sun, 25 Oct 2026 10:00:00 GMT")
    naive = parse_published_at("2026-10-25T10:00:00")
    assert iso is not None
    assert rss is not None
    assert naive is not None
    assert iso == rss == naive
    assert iso.utcoffset() is not None
    assert parse_published_at("") is None
    assert parse_published_at("not a date") is None


def test_within_window_boundaries() -> None:
    t0 = parse_published_at("2026-10-25T10:00:00Z")
    near = parse_published_at("2026-10-25T15:59:00Z")
    edge = parse_published_at("2026-10-25T16:00:00Z")
    far = parse_published_at("2026-10-25T17:00:00Z")
    assert within_window(t0, near) is True
    assert within_window(t0, edge) is True
    assert within_window(t0, far) is False
    assert within_window(t0, None) is False
    assert within_window(None, t0) is False
