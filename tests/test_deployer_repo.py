"""Unit tests for the deployer reputation repository (app/ai/deployer_repo.py).

All tests inject a fake ClickHouse client — no network, no real ClickHouse.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai import deployer_repo as mod
from app.ai.deployer_repo import DeployerRepo


class FakeCH:
    """In-memory fake of clickhouse_driver.Client for the deployer_repo table."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self.inserts: list[tuple] = []

    def execute(self, query: str, params: Any = None) -> Any:
        q = " ".join(query.split())
        if q.startswith("INSERT"):
            self.inserts.extend(params)
            return []
        if "SELECT token_address" in q:  # get_deployer_history (7 cols)
            addr, chain = params["addr"], params["chain"]
            matched = sorted(
                (r[2:9] for r in self.rows if r[0] == addr and r[1] == chain), key=lambda r: r[2], reverse=True
            )
            return matched
        if "SELECT token_symbol" in q:  # update_outcome lookup (5 cols)
            addr, chain, token = params["addr"], params["chain"], params["token"]
            return [
                (r[3], r[4], r[5], r[6], r[7]) for r in self.rows if r[0] == addr and r[1] == chain and r[2] == token
            ]
        if "GROUP BY deployer_address" in q and "HAVING total_launches > 1" in q:
            return self._repeat_rows(params["chain"])
        if "uniqExact" in q:
            return [self._stats_row()]
        if "count() FROM" in q:
            return [(len([1 for d, rows in self._grouped().items() if len(rows) > 1]),)]
        if q == "SELECT 1":
            return []
        return []

    def _grouped(self) -> dict[str, list[tuple]]:
        grouped: dict[str, list[tuple]] = {}
        for r in self.rows:
            grouped.setdefault(r[0], []).append(r)
        return grouped

    def _repeat_rows(self, chain: str) -> list[tuple]:
        out = []
        for deployer, rows in self._grouped().items():
            rows = [r for r in rows if r[1] == chain]
            if len(rows) < 2:
                continue
            rugs = sum(1 for r in rows if r[8] == "rug")
            out.append(
                (
                    deployer,
                    len(rows),
                    rugs,
                    sum(1 for r in rows if r[8] == "clean"),
                    sum(r[7] for r in rows) / len(rows),
                    min(r[4] for r in rows),
                    max(r[4] for r in rows),
                )
            )
        out.sort(key=lambda x: (-x[2], -x[4]))
        return out

    def _stats_row(self) -> tuple:
        rows = self.rows
        return (
            len(self._grouped()),
            len(rows),
            sum(1 for r in rows if r[8] == "rug"),
            sum(1 for r in rows if r[8] == "clean"),
            sum(1 for r in rows if r[8] == "pending"),
        )


def _row(
    deployer: str = "0xdev",
    chain: str = "ethereum",
    token: str = "0xtoken",  # noqa: S107 — test fixture param, not a credential
    symbol: str = "SCAM",
    launch: str = "2026-01-01 00:00:00",
    funding: str = "0xfunder",
    liquidity: float = 1000.0,
    risk: float = 80.0,
    outcome: str = "pending",
) -> tuple:
    # Column order: deployer, chain, token, symbol, launch_time, funding,
    # liquidity, risk, outcome, updated_at
    return (deployer, chain, token, symbol, launch, funding, liquidity, risk, outcome, launch)


@pytest.fixture
def repo() -> DeployerRepo:
    return DeployerRepo(client=FakeCH())


@pytest.fixture
def fake(repo: DeployerRepo) -> FakeCH:
    assert isinstance(repo._client, FakeCH)
    return repo._client


# ── record_deployer ────────────────────────────────────────────────────────


async def test_record_deployer_inserts_row(repo: DeployerRepo, fake: FakeCH) -> None:
    ok = await repo.record_deployer(
        "0xDEV", "ethereum", "0xtoken", {"risk_score": 75, "symbol": "RUG", "funding_source": "0xf"}
    )
    assert ok is True
    assert len(fake.inserts) == 1
    row = fake.inserts[0]
    assert row[0] == "0xdev"
    assert row[1] == "ethereum"
    assert row[2] == "0xtoken"
    assert row[3] == "RUG"
    assert row[7] == 75.0
    assert row[8] == "pending"


async def test_record_deployer_skips_empty_address(repo: DeployerRepo, fake: FakeCH) -> None:
    assert await repo.record_deployer("", "ethereum", "0xtoken", {}) is False
    assert await repo.record_deployer("0xdev", "ethereum", "", {}) is False
    assert fake.inserts == []


async def test_record_deployer_coerces_bad_risk(repo: DeployerRepo, fake: FakeCH) -> None:
    ok = await repo.record_deployer("0xdev", "ethereum", "0xtoken", {"risk_score": "not-a-number"})
    assert ok is True
    assert fake.inserts[0][7] == 0.0


# ── get_deployer_history ──────────────────────────────────────────────────


async def test_history_empty_when_no_rows(repo: DeployerRepo) -> None:
    h = await repo.get_deployer_history("0xunknown", "ethereum")
    assert h["total_launches"] == 0
    assert h["rug_rate"] == 0.0
    assert h["tokens"] == []


async def test_history_aggregates(repo: DeployerRepo, fake: FakeCH) -> None:
    fake.rows = [
        _row(token="0xa", launch="2026-01-02 00:00:00", outcome="rug", risk=90.0),
        _row(token="0xb", launch="2026-01-01 00:00:00", outcome="clean", risk=10.0),
        _row(token="0xc", launch="2026-01-03 00:00:00", outcome="pending", risk=50.0),
    ]
    h = await repo.get_deployer_history("0xdev", "ethereum")
    assert h["total_launches"] == 3
    assert h["confirmed_rugs"] == 1
    assert h["confirmed_clean"] == 1
    assert h["pending"] == 1
    assert h["rug_rate"] == pytest.approx(33.3)
    assert h["avg_risk_score"] == pytest.approx(50.0)
    assert h["first_seen"] == "2026-01-01 00:00:00"
    assert h["last_seen"] == "2026-01-03 00:00:00"
    assert h["funding_patterns"] == ["0xfunder"]
    assert len(h["tokens"]) == 3
    assert h["tokens"][0]["token"] == "0xc"  # newest first


async def test_history_fail_open_on_client_error(repo: DeployerRepo) -> None:
    class Boom:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("clickhouse down")

    r = DeployerRepo(client=Boom())
    h = await r.get_deployer_history("0xdev", "ethereum")
    assert h["total_launches"] == 0


# ── update_outcome ────────────────────────────────────────────────────────


async def test_update_outcome_rewrites_row(repo: DeployerRepo, fake: FakeCH) -> None:
    fake.rows = [_row()]
    ok = await repo.update_outcome("0xdev", "ethereum", "0xtoken", "rug")
    assert ok is True
    assert len(fake.inserts) == 1
    assert fake.inserts[0][8] == "rug"
    # Preserved fields
    assert fake.inserts[0][3] == "SCAM"
    assert fake.inserts[0][7] == 80.0


async def test_update_outcome_rejects_invalid(repo: DeployerRepo, fake: FakeCH) -> None:
    assert await repo.update_outcome("0xdev", "ethereum", "0xtoken", "exploded") is False
    assert fake.inserts == []


async def test_update_outcome_no_existing_row(repo: DeployerRepo, fake: FakeCH) -> None:
    assert await repo.update_outcome("0xdev", "ethereum", "0xmissing", "rug") is False
    assert fake.inserts == []


# ── find_repeat_deployers ─────────────────────────────────────────────────


async def test_find_repeat_deployers_sorted_by_rugs(repo: DeployerRepo, fake: FakeCH) -> None:
    fake.rows = [
        # serial rugger: 3 launches, 2 rugs
        _row(deployer="0xrugger", token="0x1", outcome="rug", risk=95.0),
        _row(deployer="0xrugger", token="0x2", outcome="rug", risk=90.0),
        _row(deployer="0xrugger", token="0x3", outcome="pending", risk=85.0),
        # repeat but clean: 2 launches, 0 rugs
        _row(deployer="0xclean", token="0x4", outcome="clean", risk=5.0),
        _row(deployer="0xclean", token="0x5", outcome="clean", risk=5.0),
        # single launch — excluded
        _row(deployer="0xonce", token="0x6", outcome="rug", risk=99.0),
        # other chain — excluded
        _row(deployer="0xrugger", chain="solana", token="0x7", outcome="rug", risk=99.0),
    ]
    out = await repo.find_repeat_deployers("ethereum")
    assert [d["deployer"] for d in out] == ["0xrugger", "0xclean"]
    top = out[0]
    assert top["total_launches"] == 3
    assert top["confirmed_rugs"] == 2
    assert top["rug_rate"] == pytest.approx(66.7)


async def test_find_repeat_deployers_empty(repo: DeployerRepo) -> None:
    assert await repo.find_repeat_deployers("ethereum") == []


# ── repo_stats ────────────────────────────────────────────────────────────


async def test_repo_stats_counts(repo: DeployerRepo, fake: FakeCH) -> None:
    fake.rows = [
        _row(deployer="0xa", token="0x1", outcome="rug"),
        _row(deployer="0xa", token="0x2", outcome="clean"),
        _row(deployer="0xb", token="0x3", outcome="pending"),
    ]
    s = await repo.repo_stats()
    assert s["total_deployers"] == 2
    assert s["total_launches_tracked"] == 3
    assert s["confirmed_rugs"] == 1
    assert s["confirmed_clean"] == 1
    assert s["pending_outcomes"] == 1
    assert s["repeat_offenders"] == 1  # only 0xa has >1 launch
    assert s["available"] is True


async def test_repo_stats_fail_open() -> None:
    class Boom:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("down")

    s = await DeployerRepo(client=Boom()).repo_stats()
    assert s["available"] is False
    assert s["total_deployers"] == 0


# ── singleton ─────────────────────────────────────────────────────────────


def test_get_deployer_repo_singleton() -> None:
    a = mod.get_deployer_repo()
    b = mod.get_deployer_repo()
    assert a is b
