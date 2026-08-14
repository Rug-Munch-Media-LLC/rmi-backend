"""Runnable benchmark: evaluate the fraud model against labeled datasets.

Usage (from the repo root)::

    .venv/bin/python -m app.ml_datasets.benchmark [--dataset auto] [--offline]

Pipeline
--------
1. Load a labeled dataset.  Default ``auto`` tries DeFiHackLabs, then
   Real-CATS, then an inline synthetic fixture when nothing can be parsed
   offline.  ``--offline`` skips the network entirely (cache or fixture).
2. Enrich each record to a ``FraudGNNDetector`` fingerprint via
   :func:`app.ml_datasets.adapters.address_to_fingerprint`.
3. Run :func:`app.ml_datasets.eval.evaluate` against the model-backed
   ``predict`` callback and print precision/recall/F1 + a small report.

Offline safety
--------------
* ``HF_HUB_OFFLINE=1`` makes the GNN skip the HuggingFace download and use
  the bundled fallback Random Forest (deterministic, no network).
* ``ENVIRONMENT=dev`` declares the app's non-prod mode so the config import
  chain (via ``app.fraud_gnn``) does not trip the production JWT guard.
* The synthetic fixture is inline and self-contained; the whole run takes
  well under 30 seconds with it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Offline-first benchmark: force the GNN's HuggingFace lookup to fail fast so
# the deterministic fallback Random Forest is used, and keep the GNN model
# cache out of /app (not writable on dev boxes).  Declare the app's own
# non-prod mode so importing app.fraud_gnn -> app.config.Settings() does not
# trip the production JWT_SECRET guard.  Set BEFORE importing app.fraud_gnn
# (adapters imports it for FRAUD_FEATURES).
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("GNN_MODEL_DIR", str(Path(tempfile.gettempdir()) / "rmi-benchmark-gnn"))

from app.fraud_gnn import FraudGNNDetector
from app.ml_datasets.adapters import address_to_fingerprint
from app.ml_datasets.eval import evaluate
from app.ml_datasets.loaders import (
    DatasetDownloadError,
    DatasetInfo,
    LabeledDataset,
    LabeledRecord,
    default_cache_dir,
    load_dataset,
    load_defihacklabs,
    load_realcats,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.ml_datasets.eval import EvaluationResult

# ── Synthetic fixture ─────────────────────────────────────────────────────────
# Inline, deterministic ground truth used when no real dataset is cached.
# Addresses carry explicit fingerprints in ``extra["fingerprint"]``; the
# adapter passes them through, so the benchmark exercises the real score path
# (enrich -> FraudGNNDetector.predict) without any network.

_FIXTURE_INFO = DatasetInfo(
    name="synthetic-fixture",
    description=(
        "Inline deterministic fixture: addresses whose fingerprints match the "
        "fallback Random Forest's fraud heuristics (young age, mixer use, high "
        "failed_tx_ratio) vs benign activity.  Used when no real dataset is cached."
    ),
    source_url="",
    license="",
    positive_semantics="synthetic fraud heuristics vs benign activity",
)

# (lo, hi) uniform ranges per feature, mirroring the fallback RF training data.
_BENIGN_RANGES: dict[str, tuple[float, float]] = {
    "tx_count": (150.0, 900.0),
    "avg_tx_value": (0.2, 6.0),
    "tx_value_std": (5.0, 40.0),
    "unique_counterparties": (20.0, 150.0),
    "received_vs_sent_ratio": (0.7, 1.3),
    "avg_time_between_txs": (4.0, 36.0),
    "contract_interactions": (50.0, 400.0),
    "mixer_interactions": (0.0, 0.0),
    "dex_interactions": (5.0, 50.0),
    "cex_deposits": (1.0, 30.0),
    "age_days": (150.0, 800.0),
    "active_days": (100.0, 650.0),
    "eth_balance": (1.0, 60.0),
    "token_diversity": (5.0, 35.0),
    "gas_price_avg": (15.0, 80.0),
    "gas_price_std": (4.0, 20.0),
    "failed_tx_ratio": (0.005, 0.06),
}

_FRAUD_RANGES: dict[str, tuple[float, float]] = {
    "tx_count": (3.0, 40.0),
    "avg_tx_value": (15.0, 250.0),
    "tx_value_std": (30.0, 250.0),
    "unique_counterparties": (80.0, 1000.0),
    "received_vs_sent_ratio": (1.2, 3.0),
    "avg_time_between_txs": (0.1, 3.0),
    "contract_interactions": (0.0, 15.0),
    "mixer_interactions": (1.0, 1.0),
    "dex_interactions": (0.0, 8.0),
    "cex_deposits": (0.0, 3.0),
    "age_days": (1.0, 45.0),
    "active_days": (1.0, 20.0),
    "eth_balance": (0.01, 2.0),
    "token_diversity": (1.0, 6.0),
    "gas_price_avg": (80.0, 500.0),
    "gas_price_std": (30.0, 250.0),
    "failed_tx_ratio": (0.15, 0.85),
}


def _uniform(seed: str, lo: float, hi: float) -> float:
    """Deterministic uniform value in ``[lo, hi)`` from a string seed."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()[:8]
    scaled = int.from_bytes(digest, "big") / float(2**64)
    return lo + scaled * (hi - lo)


def _fixture_features(address: str, *, positive: bool) -> dict[str, float]:
    template = _FRAUD_RANGES if positive else _BENIGN_RANGES
    return {name: round(_uniform(f"{address}:{name}", lo, hi), 4) for name, (lo, hi) in template.items()}


def build_synthetic_dataset(limit: int = 60) -> LabeledDataset:
    """Build the inline fixture: ``limit`` balanced, labeled records."""
    records: list[LabeledRecord] = []
    for i in range(1, limit + 1):
        positive = i % 2 == 0  # even -> fraud, odd -> benign
        addr = f"fixture-{'fraud' if positive else 'benign'}-{i:04d}"
        records.append(
            LabeledRecord(
                id=addr,
                address=addr,
                label="synthetic_fraud" if positive else "synthetic_benign",
                positive=positive,
                source=_FIXTURE_INFO.name,
                extra={"fingerprint": _fixture_features(addr, positive=positive)},
            )
        )
    return LabeledDataset(info=_FIXTURE_INFO, records=records, from_cache=True)


# ── Dataset loading ───────────────────────────────────────────────────────────


def _default_limit(dataset: str, explicit: int | None) -> int:
    """Per-dataset record cap so default runs stay fast (raise via --limit)."""
    if explicit is not None:
        return explicit
    return {"synthetic": 60, "defihacklabs": 200, "real-cats": 200, "smartbugs": 200}.get(dataset, 200)


def _cache_has(cache_dir: Path | None, *filenames: str) -> bool:
    base = cache_dir if cache_dir is not None else default_cache_dir()
    return all((base / name).is_file() for name in filenames)


def _cache_files_for(dataset: str, chain: str) -> tuple[str, ...]:
    """Artifact filenames a dataset needs cached to load without the network."""
    if dataset == "defihacklabs":
        return ("defihacklabs_README.md",)
    if dataset == "real-cats":
        return ("CE.tsv", "BE.tsv") if chain == "ethereum" else ("CB.tsv", "BB.tsv")
    if dataset == "smartbugs":
        return ("smartbugs-wild_contracts.csv.tar.gz",)
    return ()


def _auto_load(args: argparse.Namespace) -> tuple[LabeledDataset, str]:
    """Default loader: DeFiHackLabs -> Real-CATS -> synthetic fixture."""
    cache = Path(args.cache_dir) if args.cache_dir else None
    limit = _default_limit("defihacklabs", args.limit)

    if args.offline:
        if _cache_has(cache, "defihacklabs_README.md"):
            return load_defihacklabs(cache_dir=cache, limit=limit), "cached"
        if _cache_has(cache, "CE.tsv", "BE.tsv"):
            return load_realcats(cache_dir=cache, chains=(args.chain,), limit=limit), "cached"
        print("[benchmark] --offline and no cached dataset; using the synthetic fixture.", file=sys.stderr)
        return build_synthetic_dataset(limit=_default_limit("synthetic", args.limit)), "synthetic-fixture"

    # Online: prefer the small DeFiHackLabs incident index, then Real-CATS.
    try:
        ds = load_defihacklabs(cache_dir=cache, limit=limit)
        return ds, "cached" if ds.from_cache else "network"
    except DatasetDownloadError:
        pass
    try:
        ds = load_realcats(cache_dir=cache, chains=(args.chain,), limit=limit)
        return ds, "cached" if ds.from_cache else "network"
    except DatasetDownloadError:
        pass
    print("[benchmark] no dataset parsed (offline?); using the synthetic fixture.", file=sys.stderr)
    return build_synthetic_dataset(limit=_default_limit("synthetic", args.limit)), "synthetic-fixture"


def _load_dataset(args: argparse.Namespace) -> tuple[LabeledDataset, str]:
    """Load the requested dataset; always falls back to the synthetic fixture."""
    if args.dataset == "synthetic":
        return build_synthetic_dataset(limit=_default_limit("synthetic", args.limit)), "synthetic-fixture"
    if args.dataset == "auto":
        return _auto_load(args)

    cache = Path(args.cache_dir) if args.cache_dir else None
    if args.offline and not _cache_has(cache, *_cache_files_for(args.dataset, args.chain)):
        print(f"[benchmark] --offline and {args.dataset} not cached; using the synthetic fixture.", file=sys.stderr)
        return build_synthetic_dataset(limit=_default_limit("synthetic", args.limit)), "synthetic-fixture"
    kwargs: dict[str, Any] = {}
    if args.dataset == "real-cats":
        kwargs["chains"] = (args.chain,)
    try:
        ds = load_dataset(args.dataset, cache_dir=cache, limit=_default_limit(args.dataset, args.limit), **kwargs)
    except DatasetDownloadError as exc:
        print(f"[benchmark] WARNING: {exc}", file=sys.stderr)
        print("[benchmark] falling back to the synthetic fixture.", file=sys.stderr)
        return build_synthetic_dataset(limit=_default_limit("synthetic", args.limit)), "synthetic-fixture"
    return ds, "cached" if ds.from_cache else "network"


# ── Prediction + report ───────────────────────────────────────────────────────


def _build_predict(
    detector: FraudGNNDetector,
    dataset: LabeledDataset,
    *,
    chain: str,
) -> tuple[Callable[[str], float], int]:
    """Return ``(predict, n_embedded_fingerprints)``.

    ``predict`` maps a record identifier to a fraud probability via the
    fingerprint adapter.  Records that carry real feature values (the
    synthetic fixture, or Real-CATS ``with_features`` rows) feed them through;
    everything else is hash-synthesised by the adapter.
    """
    embedded: dict[str, dict[str, Any]] = {}
    for rec in dataset.records:
        ident = rec.address if rec.address is not None else rec.id
        blob = rec.extra.get("fingerprint")
        if isinstance(blob, dict):
            embedded[ident] = {str(k): v for k, v in blob.items()}

    def predict(identifier: str) -> float:
        fingerprint = address_to_fingerprint(identifier, chain=chain, features=embedded.get(identifier))
        prob, _is_fraud, _model = detector.predict(fingerprint)
        return prob

    return predict, len(embedded)


def _print_report(
    *,
    dataset: LabeledDataset,
    provenance: str,
    result: EvaluationResult,
    model_type: str,
    chain: str,
    prob_threshold: float,
    n_embedded: int,
) -> None:
    print("─" * 64)
    print("Fraud model benchmark")
    print("─" * 64)
    print(f"dataset        : {dataset.info.name}  (loaded: {provenance})")
    print(f"  description  : {dataset.info.description}")
    print(f"  source       : {dataset.info.source_url or '(inline fixture)'}")
    print(f"  license      : {dataset.info.license or '(none)'}")
    print(f"records        : {result.n_total}  labeled={result.n_labeled}  unlabeled={result.n_unlabeled}")
    print(f"fingerprints   : {n_embedded} records carried real feature values (rest hash-synthesised)")
    print(f"model          : {model_type}")
    print(f"eval           : chain={chain}  prob_threshold={prob_threshold:g}")
    print()
    print("Metrics")
    print(f"  precision    : {result.precision:.4f}")
    print(f"  recall       : {result.recall:.4f}")
    print(f"  f1           : {result.f1:.4f}")
    print(f"  accuracy     : {result.accuracy:.4f}")
    print(f"  tp/fp/fn/tn  : {result.tp}/{result.fp}/{result.fn}/{result.tn}")
    print(f"  errors       : {result.n_errors}  abstained={result.n_abstained}")
    print(f"  truth        : {result.truth_distribution}")
    print(f"  predicted    : {result.predicted_distribution}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.ml_datasets.benchmark",
        description="Benchmark the fraud model (FraudGNNDetector) against labeled datasets.",
    )
    parser.add_argument(
        "--dataset",
        choices=("auto", "synthetic", "defihacklabs", "real-cats", "smartbugs"),
        default="auto",
        help=(
            "Dataset to evaluate. 'auto' tries defihacklabs, then real-cats, "
            "then an inline synthetic fixture (default: auto)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max labeled records to evaluate (per file for real-cats).",
    )
    parser.add_argument(
        "--chain",
        default="ethereum",
        help="Chain for real-cats (ethereum|bitcoin).",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Dataset cache dir (default: $ML_DATASETS_DIR or <repo>/data/ml_datasets).",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Never touch the network: only cached datasets or the synthetic fixture.",
    )
    parser.add_argument(
        "--prob-threshold",
        type=float,
        default=0.5,
        help="Fraud probability threshold for positive classification (default: 0.5).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    dataset, provenance = _load_dataset(args)

    detector = FraudGNNDetector()
    detector._ensure_loaded()
    model_type = str(detector.status().get("model_type") or "unknown")

    predict, n_embedded = _build_predict(detector, dataset, chain=args.chain)
    result = evaluate(predict, dataset, prob_threshold=args.prob_threshold, verbose=False)

    _print_report(
        dataset=dataset,
        provenance=provenance,
        result=result,
        model_type=model_type,
        chain=args.chain,
        prob_threshold=args.prob_threshold,
        n_embedded=n_embedded,
    )

    if result.n_errors:
        print()
        print(f"BENCHMARK: FAIL ({result.n_errors} prediction errors - the predictor crashed during evaluation)")
        return 1
    print()
    print("BENCHMARK: PASS (harness ran end-to-end; zero prediction errors)")
    if n_embedded == 0:
        print(
            "NOTE: no records carried real fingerprint features - metrics reflect the hash-stub baseline, not model quality."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
