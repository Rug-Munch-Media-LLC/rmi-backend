"""Tests for app/databus/news_source_health.py (pure, tmp_path, no network)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.databus.news_source_health import (
    DEAD,
    STALE,
    VERIFIED,
    apply_prune,
    classify_sources,
    health_report,
    load_status,
    prune_plan,
)


def make_source(source_id: str, category: str = "crypto_news") -> dict:
    return {
        "id": source_id,
        "name": source_id.replace("-", " ").title(),
        "url": f"https://example.com/{source_id}/feed",
        "category": category,
        "type": "rss",
        "priority": 3,
        "quality_tier": "established",
        "language": "en",
        "requires_api_key": False,
        "verified": True,
        "needs_verification": False,
    }


def make_status(source_id: str, status: str, *, error: str | None = None) -> dict:
    return {
        "id": source_id,
        "status": status,
        "type": "rss",
        "last_good": None if status == DEAD else "2026-07-01T00:00:00+00:00",
        "item_count": 0 if status == DEAD else 5,
        "error": error,
    }


def sample_sources() -> list[dict]:
    return [
        make_source("verified-1"),
        make_source("stale-1"),
        make_source("dead-1"),
        make_source("unknown-1"),
    ]


def build_dataset(
    *,
    n_verified: int = 1,
    n_stale: int = 1,
    n_dead: int = 1,
    n_unknown: int = 1,
    dead_error: str = "HTTP 404",
) -> tuple[list[dict], dict]:
    """Build (sources, status) with the requested mix; ids are namespaced."""
    sources: list[dict] = []
    status: dict = {}
    for i in range(n_verified):
        sources.append(make_source(f"v-{i}"))
        status[f"v-{i}"] = make_status(f"v-{i}", VERIFIED)
    for i in range(n_stale):
        sources.append(make_source(f"s-{i}"))
        status[f"s-{i}"] = make_status(f"s-{i}", STALE, error="newest item 40d old")
    for i in range(n_dead):
        sources.append(make_source(f"d-{i}"))
        status[f"d-{i}"] = make_status(f"d-{i}", DEAD, error=dead_error)
    for i in range(n_unknown):
        sources.append(make_source(f"u-{i}"))  # deliberately missing from status
    return sources, status


def sample_status() -> dict:
    return {
        "verified-1": make_status("verified-1", VERIFIED),
        "stale-1": make_status("stale-1", STALE, error="newest item 40d old"),
        "dead-1": make_status("dead-1", DEAD, error="HTTP 404"),
    }


def write_source_file(path, sources: list[dict], categories: dict | None = None) -> None:
    payload = {
        "version": "1.0",
        "updated": "2026-08-01",
        "total_sources": len(sources),
        "categories": categories or {},
        "sources": sources,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def test_load_status_fail_open_missing_path(tmp_path) -> None:
    assert load_status(tmp_path / "does-not-exist.json") == {}


def test_load_status_fail_open_bad_json(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_status(path) == {}


def test_load_status_returns_sources_map(tmp_path) -> None:
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"sources": {"verified-1": {"status": VERIFIED}}}), encoding="utf-8")
    assert load_status(path) == {"verified-1": {"status": VERIFIED}}


def test_classify_sources_buckets(tmp_path) -> None:
    status = sample_status()
    buckets = classify_sources(sample_sources(), status)
    assert [s["id"] for s in buckets["active"]] == ["verified-1"]
    assert [s["id"] for s in buckets["stale"]] == ["stale-1"]
    assert [s["id"] for s in buckets["dead"]] == ["dead-1"]
    assert [s["id"] for s in buckets["unknown"]] == ["unknown-1"]
    # every source lands in exactly one bucket
    assert sum(len(v) for v in buckets.values()) == len(sample_sources())


def test_prune_plan_removes_dead_keeps_stale() -> None:
    # enough sources that the 20-source floor does not trigger
    sources, status = build_dataset(n_verified=20, n_stale=2, n_dead=2, n_unknown=1)
    plan = prune_plan(sources, status)
    assert [s["id"] for s in plan["remove"]] == ["d-0", "d-1"]
    assert sorted(s["id"] for s in plan["keep"]) == sorted(
        [f"v-{i}" for i in range(20)] + [f"s-{i}" for i in range(2)] + ["u-0"]
    )
    assert plan["counts"]["removed"] == 2
    assert plan["counts"]["kept"] == 23
    assert plan["counts"]["active"] == 20
    assert plan["counts"]["stale"] == 2
    assert plan["counts"]["dead"] == 2
    assert plan["counts"]["unknown"] == 1


def test_prune_plan_keep_stale_false_drops_stale() -> None:
    sources, status = build_dataset(n_verified=20, n_stale=2, n_dead=2, n_unknown=1)
    plan = prune_plan(sources, status, keep_stale=False)
    assert sorted(s["id"] for s in plan["remove"]) == ["d-0", "d-1", "s-0", "s-1"]
    assert sorted(s["id"] for s in plan["keep"]) == sorted([f"v-{i}" for i in range(20)] + ["u-0"])
    assert plan["counts"]["removed"] == 4
    assert plan["counts"]["kept"] == 21


def test_prune_plan_remove_dead_false_keeps_dead() -> None:
    sources, status = build_dataset(n_verified=20, n_stale=1, n_dead=2, n_unknown=1)
    plan = prune_plan(sources, status, remove_dead=False)
    assert plan["remove"] == []
    assert len(plan["keep"]) == 24


def test_prune_plan_respects_default_floor(tmp_path) -> None:
    # 10 verified + 15 dead = 25 sources; default floor is 20.
    sources = [make_source(f"v-{i}") for i in range(10)] + [make_source(f"d-{i}") for i in range(15)]
    status = {f"v-{i}": make_status(f"v-{i}", VERIFIED) for i in range(10)}
    status.update({f"d-{i}": make_status(f"d-{i}", DEAD, error="HTTP 404") for i in range(15)})
    plan = prune_plan(sources, status)
    assert plan["counts"]["kept"] == 20
    assert plan["counts"]["removed"] == 5
    # restored sources come from the dead bucket
    restored = [s["id"] for s in plan["keep"] if s["id"].startswith("d-")]
    assert len(restored) == 10


def test_prune_plan_min_verified_raises_floor(tmp_path) -> None:
    sources = [make_source(f"v-{i}") for i in range(10)] + [make_source(f"d-{i}") for i in range(15)]
    status = {f"v-{i}": make_status(f"v-{i}", VERIFIED) for i in range(10)}
    status.update({f"d-{i}": make_status(f"d-{i}", DEAD, error="HTTP 404") for i in range(15)})
    plan = prune_plan(sources, status, min_verified=22)
    assert plan["counts"]["kept"] == 22
    assert plan["counts"]["removed"] == 3
    # a small min_verified still floors at the default of 20
    plan_low = prune_plan(sources, status, min_verified=5)
    assert plan_low["counts"]["kept"] == 20


def test_apply_prune_dry_run_does_not_write(tmp_path) -> None:
    sources = [make_source(f"v-{i}") for i in range(10)] + [make_source(f"d-{i}") for i in range(15)]
    status = {f"v-{i}": make_status(f"v-{i}", VERIFIED) for i in range(10)}
    status.update({f"d-{i}": make_status(f"d-{i}", DEAD, error="HTTP 404") for i in range(15)})
    path = tmp_path / "unified_news_sources.json"
    write_source_file(path, sources, categories={"crypto_news": [s["id"] for s in sources]})
    before = path.read_text(encoding="utf-8")

    plan = prune_plan(sources, status)
    result = apply_prune(plan, path, dry_run=True)
    assert result == {"removed": 5, "kept": 20, "wrote": False}
    assert path.read_text(encoding="utf-8") == before


def test_apply_prune_writes_pruned_list_with_metadata(tmp_path) -> None:
    sources = [make_source(f"v-{i}") for i in range(10)] + [make_source(f"d-{i}") for i in range(15)]
    status = {f"v-{i}": make_status(f"v-{i}", VERIFIED) for i in range(10)}
    status.update({f"d-{i}": make_status(f"d-{i}", DEAD, error="HTTP 404") for i in range(15)})
    path = tmp_path / "unified_news_sources.json"
    write_source_file(
        path,
        sources,
        categories={
            "crypto_news": [s["id"] for s in sources],
            "security": ["d-14", "v-0"],
        },
    )

    plan = prune_plan(sources, status)
    result = apply_prune(plan, path, dry_run=False)
    assert result == {"removed": 5, "kept": 20, "wrote": True}

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["total_sources"] == 20
    assert data["updated"] == datetime.now().date().isoformat()
    written_ids = {s["id"] for s in data["sources"]}
    assert written_ids == {s["id"] for s in plan["keep"]}
    # floor restores d-0..d-9; only d-10..d-14 are pruned
    pruned_ids = {f"d-{i}" for i in range(10, 15)}
    assert written_ids == {f"v-{i}" for i in range(10)} | {f"d-{i}" for i in range(10)}
    # categories reconciled: no pruned ids remain, kept ids preserved
    assert "d-14" not in data["categories"]["security"]
    assert "v-0" in data["categories"]["security"]
    assert all(sid not in data["categories"]["crypto_news"] for sid in pruned_ids)
    assert "d-0" in data["categories"]["crypto_news"]  # restored source stays referenced
    assert len(data["categories"]["crypto_news"]) == 20  # 25 - 5 pruned


def test_apply_prune_rejects_malformed_plan(tmp_path) -> None:
    path = tmp_path / "unified_news_sources.json"
    write_source_file(path, sample_sources())
    with pytest.raises(ValueError, match="keep' and 'remove' lists"):
        apply_prune({"keep": [], "remove": "nope"}, path, dry_run=False)


def test_health_report_counts_and_offenders() -> None:
    status = {
        "sources": {
            "verified-1": make_status("verified-1", VERIFIED),
            "stale-1": make_status("stale-1", STALE, error="newest item 40d old"),
            "dead-1": make_status("dead-1", DEAD, error="HTTP 404"),
            "dead-2": make_status("dead-2", DEAD, error="parse error: no entries in feed"),
        }
    }
    report = health_report(status)
    assert isinstance(report, str)
    assert "VERIFIED: 1" in report
    assert "STALE: 1" in report
    assert "DEAD: 2" in report
    assert "dead-1" in report
    assert "HTTP 404" in report


def test_health_report_accepts_flat_map() -> None:
    # load_status() returns the id -> entry map; health_report must accept it too
    flat = {
        "verified-1": make_status("verified-1", VERIFIED),
        "dead-1": make_status("dead-1", DEAD, error="HTTP 404"),
    }
    report = health_report(flat)
    assert "VERIFIED: 1" in report
    assert "DEAD: 1" in report


def test_health_report_empty_status() -> None:
    assert "No source status available" in health_report({})
