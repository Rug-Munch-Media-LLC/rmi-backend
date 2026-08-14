"""Labeled ground-truth dataset ingestion + evaluation for RugMunch detectors.

Submodules
----------
* ``loaders`` — fetch/parse open labeled datasets (SmartBugs-Wild, Real-CATS,
  DeFiHackLabs, SolRPDS, OpenAML) with local caching and graceful offline
  failure.
* ``eval`` — small model-agnostic harness: given a
  ``predict(address_or_contract) -> risk_label`` callback, compute
  precision/recall/F1 against a loaded ground-truth set.

No large data files are committed.  Downloads are cached under
``<repo>/data/ml_datasets`` (override with the ``ML_DATASETS_DIR`` env var).
"""

from __future__ import annotations

from app.ml_datasets.eval import (
    DEFAULT_POSITIVE_LABELS,
    EvaluationResult,
    evaluate,
    precision_recall_f1,
)
from app.ml_datasets.loaders import (
    DatasetDownloadError,
    DatasetInfo,
    LabeledDataset,
    LabeledRecord,
    load_dataset,
    load_defihacklabs,
    load_openaml,
    load_realcats,
    load_smartbugs,
    load_solrpds,
)

__all__ = [
    "DEFAULT_POSITIVE_LABELS",
    "DatasetDownloadError",
    "DatasetInfo",
    "EvaluationResult",
    "LabeledDataset",
    "LabeledRecord",
    "evaluate",
    "load_dataset",
    "load_defihacklabs",
    "load_openaml",
    "load_realcats",
    "load_smartbugs",
    "load_solrpds",
    "precision_recall_f1",
]

__version__ = "0.1.0"
