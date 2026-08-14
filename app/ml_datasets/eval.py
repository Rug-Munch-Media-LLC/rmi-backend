"""Evaluation harness: precision/recall/F1 against loaded ground-truth sets.

The harness is deliberately small and model-agnostic.  It consumes a
``predict`` callback::

    predict(identifier: str) -> str | float | bool | None

    * ``str``   — risk label, e.g. ``"high"`` / ``"low"`` / ``"scam"`` / ``"benign"``
    * ``float`` — probability in [0, 1]; ``>= prob_threshold`` counts as positive
    * ``bool``  — ``True`` = positive
    * ``None``  — abstain (counted as negative, recorded in ``n_abstained``)

Ground truth comes from :class:`app.ml_datasets.loaders.LabeledRecord` where
``record.positive`` is ``True``/``False``.  Records with ``positive is None``
(e.g. the SmartBugs-Wild background corpus) are unlabeled: they are excluded
from metric computation but counted (``n_unlabeled``).

To benchmark an existing detector, wrap it in a callback.  For example, the
:class:`~app.fraud_gnn.FraudGNNDetector` consumes *wallet fingerprints*, not
raw addresses, so the adapter must enrich the identifier first::

    def predict(addr: str) -> str:
        fp = enrich_address_to_fingerprint(addr)      # your pipeline
        _, is_fraud, _ = get_fraud_gnn().predict(fp)
        return "high" if is_fraud else "low"
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ml_datasets.loaders import LabeledDataset

# Default set of risk labels that map to "positive".  Anything else (e.g.
# "low", "medium", "benign", "0", "no") maps to negative.
DEFAULT_POSITIVE_LABELS = frozenset(
    {
        "high",
        "risky",
        "risk",
        "scam",
        "scammy",
        "fraud",
        "fraudulent",
        "criminal",
        "malicious",
        "exploited",
        "vulnerable",
        "positive",
        "bad",
        "flagged",
        "1",
        "true",
        "yes",
    }
)

#: ``predict`` callback signature: identifier -> risk label / prob / abstain.
PredictFn = Callable[[str], str | float | bool | None]


@dataclass
class EvaluationResult:
    """Outcome of evaluating a detector against a ground-truth set."""

    name: str
    n_total: int = 0
    n_labeled: int = 0
    n_unlabeled: int = 0
    n_errors: int = 0
    n_abstained: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    accuracy: float = 0.0
    precision_defined: bool = True
    recall_defined: bool = True
    truth_distribution: dict[str, int] = field(default_factory=dict)
    predicted_distribution: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "n_total": self.n_total,
            "n_labeled": self.n_labeled,
            "n_unlabeled": self.n_unlabeled,
            "n_errors": self.n_errors,
            "n_abstained": self.n_abstained,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "accuracy": round(self.accuracy, 4),
            "precision_defined": self.precision_defined,
            "recall_defined": self.recall_defined,
            "truth_distribution": self.truth_distribution,
            "predicted_distribution": self.predicted_distribution,
        }

    def summary(self) -> str:
        return (
            f"{self.name}: P={self.precision:.3f} R={self.recall:.3f} "
            f"F1={self.f1:.3f} acc={self.accuracy:.3f} "
            f"(tp={self.tp} fp={self.fp} fn={self.fn} tn={self.tn} "
            f"labeled={self.n_labeled} unlabeled={self.n_unlabeled} "
            f"errors={self.n_errors} abstained={self.n_abstained})"
        )


def precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float, bool, bool]:
    """Core metrics with zero-denominator safety.

    Returns ``(precision, recall, f1, precision_defined, recall_defined)``;
    undefined metrics (no predicted positives / no ground-truth positives)
    are returned as 0.0 with the corresponding ``*_defined`` flag False.
    """
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1, (tp + fp) > 0, (tp + fn) > 0


def _classify_prediction(
    pred: str | float | bool | None,
    positive_labels: frozenset[str],
    prob_threshold: float,
) -> bool | None:
    """Map a raw prediction to positive / negative / abstain (None)."""
    if pred is None:
        return None
    if isinstance(pred, bool):
        return pred
    if isinstance(pred, (int, float)):
        return float(pred) >= prob_threshold
    return pred.strip().lower() in positive_labels


def evaluate(
    predict: PredictFn,
    dataset: LabeledDataset,
    *,
    positive_labels: frozenset[str] | set[str] | None = None,
    prob_threshold: float = 0.5,
    verbose: bool = True,
) -> EvaluationResult:
    """Evaluate ``predict`` against a loaded :class:`LabeledDataset`.

    ``predict`` is called with ``record.address`` when present, otherwise with
    ``record.id`` (e.g. DeFiHackLabs protocol names).  Exceptions raised by
    ``predict`` are swallowed and counted in ``n_errors``; the record is
    conservatively treated as predicted-negative.
    """
    labels = frozenset(positive_labels) if positive_labels is not None else DEFAULT_POSITIVE_LABELS
    res = EvaluationResult(name=dataset.info.name)
    truth_counter: Counter[str] = Counter()
    pred_counter: Counter[str] = Counter()

    for rec in dataset.records:
        res.n_total += 1
        truth = rec.positive
        truth_counter[str(truth)] += 1
        if truth is None:
            res.n_unlabeled += 1
            continue
        res.n_labeled += 1

        identifier = rec.address if rec.address is not None else rec.id
        try:
            raw = predict(identifier)
        except Exception:
            res.n_errors += 1
            pred_pos = False
        else:
            if raw is None:
                res.n_abstained += 1
                pred_pos = False
            else:
                pred_pos = _classify_prediction(raw, labels, prob_threshold) or False
                pred_counter[str(raw)] += 1

        if pred_pos:
            if truth:
                res.tp += 1
            else:
                res.fp += 1
        else:
            if truth:
                res.fn += 1
            else:
                res.tn += 1

    res.precision, res.recall, res.f1, res.precision_defined, res.recall_defined = precision_recall_f1(
        res.tp, res.fp, res.fn
    )
    denom = res.tp + res.tn + res.fp + res.fn
    res.accuracy = (res.tp + res.tn) / denom if denom else 0.0
    res.truth_distribution = dict(truth_counter)
    res.predicted_distribution = dict(pred_counter)

    if verbose:
        print(res.summary())
    return res
