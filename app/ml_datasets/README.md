# ml_datasets — Labeled Ground-Truth Dataset Ingestion + Evaluation

Self-contained loaders and a small evaluation harness for benchmarking the
risk/detection models in `app/ml_anomaly.py` and `app/fraud_gnn.py` against
open labeled datasets. **Only loader code is committed — dataset artifacts are
never checked into git** (they are downloaded into a local cache dir).

## Datasets

| Name | Source | Artifact fetched | Labels | License |
|------|--------|------------------|--------|---------|
| `smartbugs-wild` | [smartbugs/smartbugs-wild](https://github.com/smartbugs/smartbugs-wild) | `contracts.csv.tar.gz` (metadata of 47,398 real Ethereum contracts) | **Unlabeled** real-world corpus (use as background / FPR baseline) | Apache-2.0 repo LICENSE; contracts retain original (Etherscan) licenses |
| `real-cats` | [sjdseu/Real-CATS](https://github.com/sjdseu/Real-CATS) | `CE.tsv` / `BE.tsv` / `CB.tsv` / `BB.tsv` (criminal/benign addresses w/ transaction profiles) | `criminal` vs `benign` | ⚠️ **No LICENSE file found in repo at fetch time** — [REVIEW] before commercial redistribution |
| `defihacklabs` | [SunWeb3Sec/DeFiHackLabs](https://github.com/SunWeb3Sec/DeFiHackLabs) | `README.md` incident index (850+ exploited protocols) | `exploited` (known-bad) | Apache-2.0 repo LICENSE |

Loader names accept aliases (`smartbugs`, `smartbugs-wild`, `realcats`,
`real-cats`, `defihacklabs`, `defi-hack-labs`).

## Usage

```python
from app.ml_datasets import evaluate, load_dataset

# 1. Load ground truth (cached locally after first download)
gt = load_dataset("realcats", chains=("ethereum",), limit=2000)

# 2. Wrap an existing detector into a predict(identifier) -> risk_label callback
from app.fraud_gnn import get_fraud_gnn


def predict(addr: str) -> str:
    fp = your_pipeline_enrich(addr)  # build a FRAUD_FEATURES fingerprint
    _, is_fraud, _ = get_fraud_gnn().predict(fp)
    return "high" if is_fraud else "low"


# 3. Score precision / recall / F1
result = evaluate(predict, gt)
print(result.as_dict())
```

`predict` may return a `str` risk label (`"high"`, `"scam"`, ...), a `float`
probability (`>= 0.5` counts as positive by default, override
`prob_threshold=`), a `bool`, or `None` to abstain.  String labels are mapped
to positive via `DEFAULT_POSITIVE_LABELS` (override with `positive_labels=`).
Ground-truth records with `positive is None` (SmartBugs-Wild background) are
excluded from metrics but counted.

### Adapting the existing models

* `app.fraud_gnn.FraudGNNDetector.predict(fp)` — needs a 17-feature wallet
  fingerprint (`FRAUD_FEATURES`), not a raw address; enrich first.
* `app.fraud_gnn.FraudGraphDetector.analyze(transfers)` — graph-based, scores
  a token launch; needs transfer edges, not addresses.  Useful for
  DeFiHackLabs-style incident triage only with on-chain transfer data.
* `app.ml_anomaly.WalletAnomalyDetector` / `TokenMetricAnomalyDetector` — are
  stubs (always return `anomaly_score=0.0`); any benchmark against them will
  show zero recall until implemented.

## Offline behavior & caching

* Cache dir: `$ML_DATASETS_DIR` env var, else `<repo>/data/ml_datasets/`.
* If the artifact file already exists in the cache, it is used as-is — no
  network access.  This is also the fixture path: pre-seed the cache dir with
  a small crafted file and the loader will parse it offline.
* If a download fails (no network), `DatasetDownloadError` is raised with a
  hint; cached copies are always preferred.
* Real-CATS TSVs are 15–90 MB each — use `limit=` for quick smoke runs.

## Dependencies

None new.  Uses stdlib (`urllib.request`, `csv`, `tarfile`, `re`) with an
optional `requests` fast-path (already present in the repo venv).  No change
to `requirements.txt` / `pyproject.toml` / `uv.lock`.

## [REVIEW] flags

1. **Real-CATS license**: repo has no LICENSE file at fetch time — addresses
   come from public reports / exchange disclosures; confirm terms before
   redistributing or using commercially.
2. **SmartBugs-Wild is unlabeled**: it gives a real-world background set, not
   vulnerability ground truth; do not report it as labeled precision/recall.
3. **DeFiHackLabs keys are protocol names, not addresses**: `record.address`
   is `None`; the harness falls back to `record.id` (e.g. `20260809:USM`).
   Matching a detector to these requires an address/protocol mapping step.
4. Artifact URLs are pinned to default branches (`master`/`main`) at write
   time; if upstream renames files, loaders will raise `DatasetDownloadError`
   rather than silently mis-parse.
