"""Unit tests for ``app.ml_datasets.eval`` (precision/recall/F1 harness).

Pure, offline, fast: the evaluation harness is exercised with tiny in-memory
datasets and inline fixtures.  The loaders are tested against small local files
(Real-CATS TSV / DeFiHackLabs README) and a pre-seeded cache dir — nothing is
downloaded.  The only path that would touch the network (``_cached_download``
with a missing artifact) has its HTTP helper monkeypatched to raise, proving
the graceful offline-failure contract.
"""

from __future__ import annotations

import pytest

from app.ml_datasets.eval import (
    DEFAULT_POSITIVE_LABELS,
    EvaluationResult,
    _classify_prediction,
    evaluate,
    precision_recall_f1,
)
from app.ml_datasets.loaders import (
    DatasetDownloadError,
    DatasetInfo,
    LabeledDataset,
    LabeledRecord,
    _cached_download,
    _parse_defihacklabs_readme,
    _parse_realcats_tsv,
    load_dataset,
    load_realcats,
)

# ── Fixtures / helpers ───────────────────────────────────────────────────────


def rec(
    rid: str, *, address: str | None = None, positive: bool | None = True, label: str | None = None
) -> LabeledRecord:
    return LabeledRecord(id=rid, address=address, label=label, positive=positive, source="unit-test")


def make_dataset(records: list[LabeledRecord], name: str = "unit-test") -> LabeledDataset:
    return LabeledDataset(
        info=DatasetInfo(name=name, description="inline test fixture", source_url="", license="test"),
        records=records,
    )


# ── precision_recall_f1 ──────────────────────────────────────────────────────


def test_precision_recall_f1_normal_case() -> None:
    p, r, f1, p_defined, r_defined = precision_recall_f1(tp=8, fp=2, fn=4)
    assert p == pytest.approx(0.8)
    assert r == pytest.approx(8 / 12)
    assert f1 == pytest.approx(0.727272727)
    assert p_defined is True
    assert r_defined is True


def test_precision_recall_f1_zero_precision_denominator() -> None:
    p, r, f1, p_defined, r_defined = precision_recall_f1(tp=0, fp=0, fn=5)
    assert p == 0.0
    assert r == 0.0
    assert f1 == 0.0
    assert p_defined is False  # no predicted positives
    assert r_defined is True


def test_precision_recall_f1_zero_recall_denominator() -> None:
    p, r, f1, p_defined, r_defined = precision_recall_f1(tp=0, fp=5, fn=0)
    assert p == 0.0
    assert r == 0.0
    assert f1 == 0.0
    assert p_defined is True
    assert r_defined is False  # no ground-truth positives


def test_precision_recall_f1_all_zero() -> None:
    p, r, f1, p_defined, r_defined = precision_recall_f1(tp=0, fp=0, fn=0)
    assert p == 0.0
    assert r == 0.0
    assert f1 == 0.0
    assert p_defined is False
    assert r_defined is False


# ── _classify_prediction mapping ─────────────────────────────────────────────


def test_classify_prediction_none_is_abstain() -> None:
    assert _classify_prediction(None, DEFAULT_POSITIVE_LABELS, 0.5) is None


def test_classify_prediction_bool_passthrough() -> None:
    assert _classify_prediction(True, DEFAULT_POSITIVE_LABELS, 0.5) is True
    assert _classify_prediction(False, DEFAULT_POSITIVE_LABELS, 0.5) is False


def test_classify_prediction_probability_threshold() -> None:
    assert _classify_prediction(0.9, DEFAULT_POSITIVE_LABELS, 0.5) is True
    assert _classify_prediction(0.5, DEFAULT_POSITIVE_LABELS, 0.5) is True  # >= threshold
    assert _classify_prediction(0.499, DEFAULT_POSITIVE_LABELS, 0.5) is False
    assert _classify_prediction(1, DEFAULT_POSITIVE_LABELS, 0.5) is True  # ints count as probs


def test_classify_prediction_label_mapping() -> None:
    assert _classify_prediction("high", DEFAULT_POSITIVE_LABELS, 0.5) is True
    assert _classify_prediction(" HIGH ", DEFAULT_POSITIVE_LABELS, 0.5) is True  # case/whitespace insensitive
    assert _classify_prediction("scam", DEFAULT_POSITIVE_LABELS, 0.5) is True
    assert _classify_prediction("1", DEFAULT_POSITIVE_LABELS, 0.5) is True
    assert _classify_prediction("yes", DEFAULT_POSITIVE_LABELS, 0.5) is True
    assert _classify_prediction("low", DEFAULT_POSITIVE_LABELS, 0.5) is False
    assert _classify_prediction("benign", DEFAULT_POSITIVE_LABELS, 0.5) is False
    assert _classify_prediction("medium", DEFAULT_POSITIVE_LABELS, 0.5) is False


def test_classify_prediction_custom_positive_labels() -> None:
    custom = frozenset({"medium", "flagged"})
    assert _classify_prediction("medium", custom, 0.5) is True
    assert _classify_prediction("flagged", custom, 0.5) is True
    assert _classify_prediction("high", custom, 0.5) is False  # not in custom set


# ── evaluate ─────────────────────────────────────────────────────────────────


def test_evaluate_perfect_classifier() -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True), rec("b", address="0xbbb", positive=False)])
    res = evaluate(lambda addr: "high" if addr == "0xaaa" else "low", ds, verbose=False)
    assert res.name == "unit-test"
    assert (res.tp, res.fp, res.fn, res.tn) == (1, 0, 0, 1)
    assert res.precision == 1.0
    assert res.recall == 1.0
    assert res.f1 == 1.0
    assert res.accuracy == 1.0
    assert res.n_total == 2
    assert res.n_labeled == 2
    assert res.n_unlabeled == 0
    assert res.precision_defined is True
    assert res.recall_defined is True


def test_evaluate_imperfect_classifier_metrics() -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True), rec("b", address="0xbbb", positive=False)])
    res = evaluate(lambda addr: "high", ds, verbose=False)  # predicts positive for everything
    assert (res.tp, res.fp, res.fn, res.tn) == (1, 1, 0, 0)
    assert res.precision == 0.5
    assert res.recall == 1.0
    assert res.f1 == pytest.approx(0.6666667)
    assert res.accuracy == 0.5


def test_evaluate_abstention_counts_as_negative() -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True), rec("b", address="0xbbb", positive=False)])
    res = evaluate(lambda addr: None if addr == "0xaaa" else "low", ds, verbose=False)
    assert res.n_abstained == 1
    assert (res.tp, res.fp, res.fn, res.tn) == (0, 0, 1, 1)
    assert res.precision_defined is False  # no predicted positives


def test_evaluate_unlabeled_records_excluded_from_metrics() -> None:
    ds = make_dataset(
        [
            rec("a", address="0xaaa", positive=True),
            rec("b", address="0xbbb", positive=False),
            rec("c", address="0xccc", positive=None),  # unlabeled background corpus
        ]
    )
    res = evaluate(lambda addr: "high" if addr == "0xaaa" else "low", ds, verbose=False)
    assert res.n_total == 3
    assert res.n_unlabeled == 1
    assert res.n_labeled == 2
    assert (res.tp, res.fp, res.fn, res.tn) == (1, 0, 0, 1)
    assert res.f1 == 1.0


def test_evaluate_predict_errors_are_counted_and_negative() -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True), rec("b", address="0xbbb", positive=False)])

    def flaky(addr: str) -> str:
        if addr == "0xaaa":
            raise RuntimeError("detector down")
        return "low"

    res = evaluate(flaky, ds, verbose=False)
    assert res.n_errors == 1
    assert (res.tp, res.fp, res.fn, res.tn) == (0, 0, 1, 1)


def test_evaluate_bool_predictions() -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True), rec("b", address="0xbbb", positive=False)])
    res = evaluate(lambda addr: addr == "0xaaa", ds, verbose=False)
    assert (res.tp, res.fp, res.fn, res.tn) == (1, 0, 0, 1)
    assert res.f1 == 1.0


def test_evaluate_probability_threshold() -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True), rec("b", address="0xbbb", positive=False)])
    res = evaluate(lambda addr: 0.8 if addr == "0xaaa" else 0.3, ds, prob_threshold=0.5, verbose=False)
    assert (res.tp, res.fp, res.fn, res.tn) == (1, 0, 0, 1)


def test_evaluate_custom_positive_labels() -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True), rec("b", address="0xbbb", positive=False)])
    res = evaluate(
        lambda addr: "flagged" if addr == "0xaaa" else "benign", ds, positive_labels={"flagged"}, verbose=False
    )
    assert (res.tp, res.fp, res.fn, res.tn) == (1, 0, 0, 1)


def test_evaluate_predict_receives_id_when_address_is_none() -> None:
    ds = make_dataset([rec("alpha", positive=True), rec("beta", positive=False)])
    seen: list[str] = []
    res = evaluate(lambda ident: seen.append(ident) or ("high" if ident == "alpha" else "low"), ds, verbose=False)
    assert seen == ["alpha", "beta"]
    assert (res.tp, res.fp, res.fn, res.tn) == (1, 0, 0, 1)


def test_evaluate_distributions() -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True), rec("b", address="0xbbb", positive=False)])
    res = evaluate(lambda addr: "high" if addr == "0xaaa" else "low", ds, verbose=False)
    assert res.truth_distribution == {"True": 1, "False": 1}
    assert res.predicted_distribution == {"high": 1, "low": 1}


def test_evaluate_all_unlabeled_zero_metrics() -> None:
    ds = make_dataset([rec("u1", positive=None), rec("u2", positive=None)])
    res = evaluate(lambda addr: "high", ds, verbose=False)
    assert res.n_unlabeled == 2
    assert res.n_labeled == 0
    assert res.tp == 0
    assert res.fp == 0
    assert res.fn == 0
    assert res.tn == 0
    assert res.precision == 0.0
    assert res.recall == 0.0
    assert res.f1 == 0.0
    assert res.accuracy == 0.0
    assert res.precision_defined is False
    assert res.recall_defined is False


def test_evaluate_verbose_prints_summary(capsys) -> None:
    ds = make_dataset([rec("a", address="0xaaa", positive=True)])
    evaluate(lambda addr: "high", ds)  # verbose defaults to True
    out = capsys.readouterr().out
    assert "unit-test" in out
    assert "F1=" in out
    assert "P=" in out
    assert "R=" in out


# ── EvaluationResult contract ────────────────────────────────────────────────


def test_evaluation_result_as_dict_rounds_metrics() -> None:
    res = EvaluationResult(
        name="x",
        tp=1,
        fp=0,
        fn=0,
        tn=1,
        n_labeled=2,
        precision=1.0,
        recall=1.0,
        f1=0.123456,
        accuracy=0.987654,
    )
    d = res.as_dict()
    assert d["name"] == "x"
    assert d["tp"] == 1
    assert d["tn"] == 1
    assert d["precision"] == 1.0
    assert d["precision_defined"] is True
    assert "truth_distribution" in d
    assert "predicted_distribution" in d
    # rounding is applied on serialization
    assert d["f1"] == 0.1235
    assert d["accuracy"] == 0.9877


def test_evaluation_result_summary_contains_metrics() -> None:
    res = EvaluationResult(name="x", tp=1, fp=0, fn=0, tn=1, precision=1.0, recall=1.0, f1=1.0, accuracy=1.0)
    s = res.summary()
    assert "P=1.000" in s
    assert "R=1.000" in s
    assert "F1=1.000" in s
    assert "acc=1.000" in s
    assert "tp=1" in s
    assert "tn=1" in s


# ── Loaders (offline fixtures) ───────────────────────────────────────────────


def test_parse_realcats_tsv(tmp_path) -> None:
    p = tmp_path / "CE.tsv"
    p.write_text("address\tlabel\tfeat_balance\n0xABC123\tBlackmail Scam\t1.5\n0xdef456\t\t2.0\n", encoding="utf-8")
    records = _parse_realcats_tsv(p, chain="ethereum", positive=True, limit=None, with_features=True)
    assert len(records) == 2
    assert records[0].address == "0xabc123"  # ethereum addresses lowercased
    assert records[0].id == "0xabc123"
    assert records[0].positive is True
    assert records[0].label == "Blackmail Scam"
    assert records[0].extra["chain"] == "ethereum"
    assert records[0].extra["features"] == {"feat_balance": "1.5"}
    assert records[1].label == "criminal"  # default label when row label empty


def test_parse_realcats_tsv_limit(tmp_path) -> None:
    p = tmp_path / "CE.tsv"
    p.write_text("address\tlabel\n0x1\tA\n0x2\tB\n0x3\tC\n", encoding="utf-8")
    records = _parse_realcats_tsv(p, chain="ethereum", positive=True, limit=2, with_features=False)
    assert len(records) == 2


def test_parse_defihacklabs_readme(tmp_path) -> None:
    p = tmp_path / "README.md"
    p.write_text(
        "# DeFiHackLabs\n"
        "### 20260809 USM - defund() price split-invariance rounding exploit\n"
        "[USM_exp.sol](src/test/2026-08/USM_exp.sol)\n"
        "[20220512 Wormhole](#20220512-wormhole-bridge-exploit)\n",
        encoding="utf-8",
    )
    records = _parse_defihacklabs_readme(p, limit=None)
    by_id = {r.id: r for r in records}
    assert "20260809:USM" in by_id
    usm = by_id["20260809:USM"]
    assert usm.positive is True
    assert usm.address is None
    assert usm.extra["poc_path"] == "src/test/2026-08/USM_exp.sol"
    assert usm.extra["date"] == "20260809"
    # TOC-anchor-only incident also captured
    assert "20220512:Wormhole" in by_id
    assert by_id["20220512:Wormhole"].extra["description"] == "wormhole bridge exploit"


def test_load_realcats_offline_from_seeded_cache(tmp_path) -> None:
    (tmp_path / "CE.tsv").write_text("address\tlabel\n0xAAA\tCriminal Scam\n0xBBB\t\n", encoding="utf-8")
    (tmp_path / "BE.tsv").write_text("address\tlabel\n0xCCC\tBenign\n0xDDD\t\n", encoding="utf-8")
    ds = load_realcats(cache_dir=tmp_path, chains=("ethereum",), limit=3)
    assert ds.from_cache is True  # served from the seeded cache, nothing fetched
    assert len(ds) == 3
    assert ds.records[0].positive is True
    assert ds.records[0].address == "0xaaa"
    assert ds.records[2].positive is False  # third record comes from BE.tsv


def test_cached_download_serves_seeded_file(tmp_path) -> None:
    dest = tmp_path / "artifact.bin"
    dest.write_bytes(b"\x00\x01\x02")
    path, was_cached = _cached_download("https://example.invalid/artifact.bin", tmp_path, "artifact.bin", timeout=1.0)
    assert was_cached is True
    assert path == dest


def test_cached_download_missing_artifact_fails_offline(monkeypatch, tmp_path) -> None:
    def offline_fetch(*args, **kwargs):
        raise DatasetDownloadError("offline: no network in tests")

    monkeypatch.setattr("app.ml_datasets.loaders._http_get_bytes", offline_fetch)
    with pytest.raises(DatasetDownloadError):
        _cached_download("https://example.invalid/missing.bin", tmp_path, "missing.bin", timeout=1.0)
    # no partial download left behind
    assert not (tmp_path / "missing.bin.part").exists()


def test_load_dataset_unknown_name_raises_key_error(tmp_path) -> None:
    with pytest.raises(KeyError):
        load_dataset("does-not-exist", cache_dir=tmp_path)
