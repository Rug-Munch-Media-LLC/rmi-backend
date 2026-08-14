"""
PyGOD-based graph anomaly detection over wallet/transfer graphs.

Complement to ``app.pyod_anomaly.py`` (tabular feature vectors) and
``app.fraud_gnn.py`` (graph heuristics for token-launch fraud): this module
finds *structurally* anomalous nodes and subgraphs — wash-trading rings,
mixer-like fan-in/fan-out hubs, and sybil clusters — in a transfer/entity
graph.

Design
------
* Input is a ``networkx.Graph`` / ``DiGraph`` (or an edge list), optionally
  with a per-node feature dict (e.g. wallet fingerprints built for
  ``app.fraud_gnn.FRAUD_FEATURES``).
* ``fit(graph)`` stores the graph and, when ``pygod`` + ``torch`` are
  installed, trains an ensemble of PyGOD detectors (DOMINANT + AnomalyDAE,
  deep autoencoders, plus SCAN, a structural clustering detector) on the
  adjacency structure (+ node features when provided). SCAN runs with pure
  torch — the deep models additionally need the ``pyg-lib``/``torch-sparse``
  C++ extensions, so at least one model fits on stock CPU torch installs.
* ``score(graph_or_node)`` returns an :class:`AnomalyResult` carrying per-node
  anomaly scores, a graph-level score and the top anomalous nodes.
* When ``pygod``/``torch`` are NOT installed (the common CPU-only case) the
  detector uses a pure-networkx heuristic backend — degree/neighbourhood
  local-outlier ratios, clustering-coefficient deviation, cycle/SCC
  involvement (wash trading) and dense-component analysis (sybil clusters) —
  so it stays useful without a heavy ML dependency. PyGOD is an optional
  upgrade path, not a requirement.
* Scoring is lazy: ``score()`` before ``fit()`` fits on the supplied graph on
  first use. Degenerate inputs (empty / too-small graphs) produce a
  zero-score result with a logged warning instead of raising.

The :class:`AnomalyResult` field names mirror ``app.pyod_anomaly.AnomalyResult``
(``anomaly_score`` / ``is_anomaly`` / ``contributing_features`` /
``model_type`` + ``to_dict()``) so callers of either module can handle the
result uniformly. Here ``contributing_features`` maps the top anomalous node
ids to their per-node scores, and ``node_scores`` / ``top_nodes`` carry the
full per-node map and ordered node list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeGuard, cast

import networkx as nx
import numpy as np

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Hashable, Iterable, Sequence

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Default anomaly threshold (mirrors app.pyod_anomaly.DEFAULT_THRESHOLD).
DEFAULT_THRESHOLD = 0.8
# Fraction of the graph assumed anomalous (seeds PyGOD contamination).
DEFAULT_CONTAMINATION = 0.1
# Graphs smaller than this cannot be meaningfully scored; score() degrades.
MIN_GRAPH_NODES = 3
# Cap on how many top anomalous nodes are returned.
MAX_TOP_NODES = 10
# PyGOD detectors used by fit() when torch is available. DOMINANT + AnomalyDAE
# are deep detectors that need the ``pyg-lib``/``torch-sparse`` C++ extensions
# (prebuilt wheels can lag new torch releases); SCAN is a structural detector
# that runs with pure torch, so at least one model works even without them.
PYGOD_MODELS: tuple[str, ...] = ("dominant", "anomalydae", "scan")
# Small dense components (>= density) up to this size are treated as
# sybil-cluster candidates on undirected graphs.
SYBIL_MIN_DENSITY = 0.5
SYBIL_MAX_COMPONENT = 30
# Sub-score weights for the networkx heuristic backend. Directed graphs
# weight cycle/SCC membership (wash trading) most heavily.
DIRECTED_WEIGHTS: tuple[float, float, float, float] = (0.20, 0.20, 0.15, 0.45)
UNDIRECTED_WEIGHTS: tuple[float, float, float, float] = (0.25, 0.25, 0.25, 0.25)


# ── Errors ───────────────────────────────────────────────────────────────────


class PyGODNotAvailable(RuntimeError):
    """Raised when ``pygod``/``torch`` are not installed and a fit is attempted."""


# ── Result model ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnomalyResult:
    """Outcome of scoring a graph (or a single node against a fitted graph).

    Field names mirror :class:`app.pyod_anomaly.AnomalyResult` so callers can
    treat tabular and graph results uniformly.

    Attributes:
        anomaly_score: Graph-level anomaly score in ``[0, 1]`` — higher is
            more anomalous. When a single node id is passed to
            :meth:`PyGODAnomalyDetector.score`, this is that node's score.
        is_anomaly: ``anomaly_score >= threshold``.
        contributing_features: Top anomalous nodes mapped to their per-node
            scores (see ``top_nodes``).
        model_type: Human-readable description of the scoring backend.
        node_scores: Full per-node anomaly score map.
        top_nodes: Node ids sorted by anomaly score, descending.
        warnings: Non-fatal diagnostics (e.g. backend fallback reasons).
    """

    anomaly_score: float
    is_anomaly: bool
    contributing_features: dict[Hashable, float]
    model_type: str
    node_scores: dict[Hashable, float] = field(default_factory=dict)
    top_nodes: list[Hashable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "anomaly_score": self.anomaly_score,
            "is_anomaly": self.is_anomaly,
            "contributing_features": {str(node): score for node, score in self.contributing_features.items()},
            "model_type": self.model_type,
            "node_scores": {str(node): score for node, score in self.node_scores.items()},
            "top_nodes": [str(node) for node in self.top_nodes],
            "warnings": self.warnings,
        }


# ── Optional dependency loading ──────────────────────────────────────────────


def _load_pygod_models() -> tuple[type[Any], type[Any], type[Any]]:
    """Import the PyGOD detector classes, raising :class:`PyGODNotAvailable` if absent."""
    try:
        import torch  # noqa: F401  (pygod requires torch at import time)
        import torch_geometric  # noqa: F401  (pygod requires torch_geometric)
        from pygod.detector import DOMINANT, SCAN, AnomalyDAE
    except ImportError as exc:
        raise PyGODNotAvailable(
            "pygod/torch is not installed; run `pip install pygod torch torch_geometric` "
            "to enable deep graph anomaly detection"
        ) from exc
    return DOMINANT, AnomalyDAE, SCAN


def pygod_available() -> bool:
    """Return True if the PyGOD dependency can be imported in this environment."""
    try:
        _load_pygod_models()
    except PyGODNotAvailable:
        return False
    return True


# ── Detector ─────────────────────────────────────────────────────────────────


class PyGODAnomalyDetector:
    """Graph anomaly detector using a PyGOD deep ensemble with a heuristic fallback.

    The detector is lazily fitted: calling :meth:`score` before :meth:`fit`
    fits on the supplied graph on first use. When ``pygod``/``torch`` are not
    installed (or ``use_pygod=False``), a pure-networkx heuristic backend is
    used instead — degree / neighbourhood local-outlier ratios,
    clustering-coefficient deviation, cycle/SCC membership (wash trading) and
    dense-component analysis (sybil clusters) — so the module is useful even
    in CPU-only environments with no heavy ML stack.
    """

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        contamination: float = DEFAULT_CONTAMINATION,
        random_state: int = 42,
        use_pygod: bool = True,
        model_names: Sequence[str] | None = None,
        max_top_nodes: int = MAX_TOP_NODES,
        min_graph_nodes: int = MIN_GRAPH_NODES,
        pygod_epoch: int = 20,
        pygod_hid_dim: int = 16,
        pygod_num_layers: int = 2,
    ) -> None:
        self.threshold = threshold
        self._contamination = contamination
        self._random_state = random_state
        self.use_pygod = use_pygod
        names = tuple(model_names) if model_names else PYGOD_MODELS
        unknown = [name for name in names if name not in PYGOD_MODELS]
        if unknown:
            raise ValueError(f"unknown PyGOD model names {unknown!r}; choose from {PYGOD_MODELS}")
        self.pygod_models: tuple[str, ...] = names
        self._max_top_nodes = max_top_nodes
        self._min_graph_nodes = min_graph_nodes
        self._pygod_epoch = pygod_epoch
        self._pygod_hid_dim = pygod_hid_dim
        self._pygod_num_layers = pygod_num_layers
        self._graph: nx.Graph | None = None
        self._node_features: dict[Hashable, dict[str, float]] | None = None
        self._node_scores: dict[Hashable, float] = {}
        self._backend = "unavailable"
        self._fitted = False
        self._warnings: list[str] = []

    # ── Public API ──────────────────────────────────────────────────────────

    def fit(
        self,
        graph: nx.Graph | Iterable[tuple[Hashable, Hashable]],
        node_features: dict[Hashable, dict[str, float]] | None = None,
    ) -> None:
        """Fit the detector on a graph (deep PyGOD ensemble when available).

        Args:
            graph: ``networkx.Graph`` / ``DiGraph`` or an iterable of
                ``(source, target)`` edges (a directed graph is built).
            node_features: Optional per-node feature dict, e.g. wallet
                fingerprints built with ``app.fraud_gnn.FRAUD_FEATURES``.
                When omitted, structural features are derived from the graph.

        Raises:
            ValueError: If the graph is empty or has fewer than
                :data:`MIN_GRAPH_NODES` nodes.
        """
        g = self._coerce_graph(graph)
        if g.number_of_nodes() < self._min_graph_nodes:
            raise ValueError(f"fit() requires at least {self._min_graph_nodes} nodes, got {g.number_of_nodes()}")
        self._graph = g
        self._node_features = node_features
        self._warnings = []

        if self.use_pygod and pygod_available():
            fitted = self._fit_pygod(g, node_features)
            if fitted is not None:
                scores, fitted_models = fitted
                self._node_scores = scores
                self._backend = f"pygod-ensemble({','.join(fitted_models)})"
                self._fitted = True
                logger.info(
                    "pygod_anomaly_fitted",
                    backend=self._backend,
                    nodes=int(g.number_of_nodes()),
                    edges=int(g.number_of_edges()),
                )
                return
            self._warnings.append("pygod fit failed; using networkx heuristics")
            logger.warning("pygod_anomaly_fallback", reason="pygod fit failed")

        self._fit_heuristic(g)
        logger.info(
            "pygod_anomaly_fitted",
            backend=self._backend,
            nodes=int(g.number_of_nodes()),
            edges=int(g.number_of_edges()),
        )

    def score(
        self,
        graph_or_node: nx.Graph | Iterable[tuple[Hashable, Hashable]] | Hashable | None = None,
    ) -> AnomalyResult:
        """Score a graph (or one node) and return an :class:`AnomalyResult`.

        Args:
            graph_or_node: A graph to score (fitted lazily on first use), an
                edge list (also fitted lazily), a single node id scored
                against the previously fitted graph, or ``None`` to re-score
                the fitted graph.

        Raises:
            ValueError: If a node id is passed that is not in the fitted graph.

        Degrades gracefully: empty/too-small graphs and missing backends
        return a zero-score result with a logged warning instead of raising.
        """
        if graph_or_node is None:
            if self._graph is None or not self._fitted:
                return self._degenerate("no graph fitted; call score(graph) or fit(graph) first")
            return self._score_fitted_graph(self._graph)

        if isinstance(graph_or_node, nx.Graph):
            graph = graph_or_node
        elif self._looks_like_edge_list(graph_or_node):
            graph = self._coerce_graph(graph_or_node)
        else:
            # networkx's Graph type args widen the union; the value is a node id here.
            return self._score_node(cast("Hashable", graph_or_node))

        if graph.number_of_nodes() == 0:
            return self._degenerate("cannot score an empty graph")
        if graph.number_of_nodes() < self._min_graph_nodes:
            return self._degenerate(f"graph too small ({graph.number_of_nodes()} nodes < {self._min_graph_nodes})")
        if not self._fitted or graph is not self._graph:
            self.fit(graph)
        return self._score_fitted_graph(graph)

    def status(self) -> dict[str, Any]:
        """Return a snapshot of detector state (useful for /health endpoints)."""
        return {
            "fitted": self._fitted,
            "pygod_available": pygod_available(),
            "backend": self._backend,
            "node_count": int(self._graph.number_of_nodes()) if self._graph is not None else 0,
            "edge_count": int(self._graph.number_of_edges()) if self._graph is not None else 0,
            "threshold": self.threshold,
            "warnings": list(self._warnings),
        }

    # ── Scoring internals ───────────────────────────────────────────────────

    def _score_fitted_graph(self, graph: nx.Graph) -> AnomalyResult:
        scores = self._node_scores
        if not scores:
            return self._degenerate("fitted graph produced no node scores")
        nodes = sorted(scores, key=lambda node: scores[node], reverse=True)
        top_nodes = nodes[: self._max_top_nodes]
        top_scores = [scores[node] for node in top_nodes]

        graph_score = round(
            float(0.5 * top_scores[0] + 0.5 * float(np.mean(top_scores))),
            4,
        )
        contributing = {node: round(float(scores[node]), 4) for node in top_nodes}
        return AnomalyResult(
            anomaly_score=graph_score,
            is_anomaly=graph_score >= self.threshold,
            contributing_features=contributing,
            model_type=self._backend,
            node_scores={node: round(float(score), 4) for node, score in scores.items()},
            top_nodes=top_nodes,
            warnings=list(self._warnings),
        )

    def _score_node(self, node: Hashable) -> AnomalyResult:
        if self._graph is None or not self._fitted:
            return self._degenerate("node scoring requires a fitted graph; call score(graph) or fit(graph) first")
        if not self._graph.has_node(node):
            raise ValueError(f"node {node!r} is not in the fitted graph")
        score = float(self._node_scores.get(node, 0.0))
        return AnomalyResult(
            anomaly_score=round(score, 4),
            is_anomaly=score >= self.threshold,
            contributing_features={node: round(score, 4)},
            model_type=self._backend,
            node_scores={node: round(score, 4)},
            top_nodes=[node],
            warnings=list(self._warnings),
        )

    def _degenerate(self, warning: str) -> AnomalyResult:
        logger.warning("pygod_anomaly_unavailable", detail=warning)
        return AnomalyResult(
            anomaly_score=0.0,
            is_anomaly=False,
            contributing_features={},
            model_type="unavailable",
            warnings=[warning],
        )

    # ── Networkx heuristic backend (default, no torch) ──────────────────────

    def _fit_heuristic(self, graph: nx.Graph) -> None:
        self._node_scores = self._heuristic_node_scores(graph)
        self._backend = "networkx-heuristic(degree,local-outlier,clustering,cycles)"
        self._fitted = True
        if not pygod_available():
            self._warnings.append("pygod/torch not installed; using networkx heuristics instead of deep models")
            logger.warning("pygod_anomaly_unavailable", detail="using networkx heuristic backend")

    def _heuristic_node_scores(self, graph: nx.Graph) -> dict[Hashable, float]:
        """Composite per-node anomaly scores from pure networkx statistics.

        Four sub-scores, each in ``[0, 1]``:
          * hub: raw degree relative to the max degree (fan-out/sink hubs).
          * local: degree vs mean neighbour degree — a node that is a hub
            among leaves scores high (mixer-like local outlier).
          * clustering_dev: deviation of the clustering coefficient from the
            graph mean (dense rings and hub-spokes both deviate).
          * directed: SCC membership of size >= 2 (wash-trading cycles);
            undirected: density of the node's small connected component
            (sybil clusters).
        """
        nodes = list(graph.nodes())
        if not nodes:
            return {}

        deg = dict(graph.degree())
        max_deg = max(deg.values()) if deg else 0
        hub = {node: (deg[node] / max_deg if max_deg else 0.0) for node in nodes}

        local_raw: dict[Hashable, float] = {}
        for node in nodes:
            neighbours = list(graph.neighbors(node))
            if not neighbours:
                local_raw[node] = 0.0
            else:
                mean_neighbour = sum(deg[m] for m in neighbours) / len(neighbours)
                local_raw[node] = deg[node] / (mean_neighbour + 1.0)
        max_local = max(local_raw.values()) if local_raw else 0.0
        local = {node: (local_raw[node] / max_local if max_local > 0 else 0.0) for node in nodes}

        clust = nx.clustering(graph)
        clust_vals = list(clust.values())
        mean_clust = float(np.mean(clust_vals)) if clust_vals else 0.0
        deviation = {node: abs(clust[node] - mean_clust) for node in nodes}
        max_deviation = max(deviation.values()) if deviation else 0.0
        clustering_dev = {node: (deviation[node] / max_deviation if max_deviation > 0 else 0.0) for node in nodes}

        if isinstance(graph, nx.DiGraph):
            cycle = self._directed_cycle_scores(graph)
            weights = DIRECTED_WEIGHTS
            extra = cycle
        else:
            extra = self._sybil_component_scores(graph)
            weights = UNDIRECTED_WEIGHTS

        scored: dict[Hashable, float] = {}
        for node in nodes:
            combined = (
                weights[0] * hub[node]
                + weights[1] * local[node]
                + weights[2] * clustering_dev[node]
                + weights[3] * extra.get(node, 0.0)
            )
            scored[node] = round(min(1.0, max(0.0, float(combined))), 4)
        return scored

    @staticmethod
    def _directed_cycle_scores(graph: nx.DiGraph) -> dict[Hashable, float]:
        """Score 1.0 for nodes inside a directed cycle (SCC of size >= 2)."""
        scores: dict[Hashable, float] = dict.fromkeys(graph.nodes(), 0.0)
        for component in nx.strongly_connected_components(graph):
            if len(component) >= 2:
                for node in component:
                    scores[node] = 1.0
        return scores

    @staticmethod
    def _sybil_component_scores(graph: nx.Graph) -> dict[Hashable, float]:
        """Score small dense components as sybil-cluster candidates.

        A connected component with ``3 <= size <= SYBIL_MAX_COMPONENT`` and
        internal density >= :data:`SYBIL_MIN_DENSITY` is treated as a
        sybil-like clique; members score the component density.
        """
        scores: dict[Hashable, float] = dict.fromkeys(graph.nodes(), 0.0)
        for component in nx.connected_components(graph):
            size = len(component)
            if size < 3 or size > SYBIL_MAX_COMPONENT:
                continue
            subgraph = graph.subgraph(component)
            max_edges = size * (size - 1) / 2.0
            density = subgraph.number_of_edges() / max_edges if max_edges else 0.0
            if density >= SYBIL_MIN_DENSITY:
                for node in component:
                    scores[node] = float(density)
        return scores

    # ── PyGOD deep backend (optional, requires torch) ───────────────────────

    def _fit_pygod(
        self,
        graph: nx.Graph,
        node_features: dict[Hashable, dict[str, float]] | None,
    ) -> tuple[dict[Hashable, float], tuple[str, ...]] | None:
        """Train the PyGOD ensemble and return scores + fitted model names.

        Returns ``None`` when no model could be fitted, so the caller falls
        back to the networkx heuristic backend instead of raising.
        """
        try:
            import torch
            from torch_geometric.data import Data

            dominant_cls, anomalydae_cls, scan_cls = _load_pygod_models()
        except (ImportError, PyGODNotAvailable) as exc:
            logger.warning("pygod_anomaly_unavailable", detail=str(exc))
            return None

        nodes = list(graph.nodes())
        if not nodes:
            return None
        feature_matrix = self._build_feature_matrix(graph, node_features)
        node_index = {node: i for i, node in enumerate(nodes)}

        edge_pairs: list[tuple[int, int]] = []
        for u, v in graph.edges():
            edge_pairs.append((node_index[u], node_index[v]))
            if isinstance(graph, nx.DiGraph):
                edge_pairs.append((node_index[v], node_index[u]))
        if not edge_pairs:
            return None

        data = Data(
            x=torch.tensor(feature_matrix, dtype=torch.float32),
            edge_index=torch.tensor(edge_pairs, dtype=torch.long).t().contiguous(),
        )

        normalized: list[np.ndarray] = []
        fitted_models: list[str] = []
        failed: list[str] = []
        for name in self.pygod_models:
            try:
                model = self._build_pygod_model(name, dominant_cls, anomalydae_cls, scan_cls)
                model.fit(data)
                raw = np.asarray(model.decision_function(data), dtype=float)
                low, high = float(raw.min()), float(raw.max())
                if high > low:
                    normalized.append((raw - low) / (high - low))
                else:
                    normalized.append(np.full_like(raw, 0.5))
                fitted_models.append(name)
            except Exception as exc:
                failed.append(name)
                logger.warning("pygod_model_failed", model=name, detail=str(exc))

        if not normalized:
            return None
        if failed:
            self._warnings.append(f"pygod models skipped (missing native extensions): {','.join(failed)}")

        averaged = np.mean(np.stack(normalized), axis=0)
        scores = {node: round(float(averaged[i]), 4) for i, node in enumerate(nodes)}
        return scores, tuple(fitted_models)

    def _build_pygod_model(
        self,
        name: str,
        dominant_cls: type[Any],
        anomalydae_cls: type[Any],
        scan_cls: type[Any],
    ) -> Any:
        """Construct one PyGOD detector (CPU-only, small dims for fast fits)."""
        if name == "anomalydae":
            return anomalydae_cls(
                emb_dim=self._pygod_hid_dim,
                hid_dim=self._pygod_hid_dim,
                num_layers=self._pygod_num_layers,
                contamination=self._contamination,
                epoch=self._pygod_epoch,
                gpu=-1,
                verbose=0,
            )
        if name == "scan":
            # Structural clustering detector — runs with pure torch, no
            # pyg-lib/torch-sparse C++ extensions required.
            return scan_cls(contamination=self._contamination, verbose=0)
        return dominant_cls(
            hid_dim=self._pygod_hid_dim,
            num_layers=self._pygod_num_layers,
            contamination=self._contamination,
            epoch=self._pygod_epoch,
            gpu=-1,
            verbose=0,
        )

    def _build_feature_matrix(
        self,
        graph: nx.Graph,
        node_features: dict[Hashable, dict[str, float]] | None,
    ) -> np.ndarray:
        """Build the node feature matrix for PyGOD.

        Uses the user-supplied per-node features when available; otherwise
        derives structural features (degree, in/out degree, clustering,
        neighbourhood local-outlier ratio) and min-max normalises each column
        so no single feature dominates the GCN input.
        """
        nodes = list(graph.nodes())
        if node_features:
            keys = sorted({key for features in node_features.values() if features for key in features})
            matrix = np.zeros((len(nodes), len(keys)), dtype=float)
            for i, node in enumerate(nodes):
                features = node_features.get(node) or {}
                for j, key in enumerate(keys):
                    raw = features.get(key)
                    try:
                        value = float(raw) if raw is not None else 0.0
                    except (TypeError, ValueError):
                        value = 0.0
                    matrix[i, j] = value if np.isfinite(value) else 0.0
            return self._normalize_columns(matrix)

        deg = dict(graph.degree())
        in_deg = dict(graph.in_degree()) if isinstance(graph, nx.DiGraph) else {}
        out_deg = dict(graph.out_degree()) if isinstance(graph, nx.DiGraph) else {}
        clust = nx.clustering(graph)

        rows: list[list[float]] = []
        for node in nodes:
            neighbours = list(graph.neighbors(node))
            mean_neighbour = sum(deg[m] for m in neighbours) / len(neighbours) if neighbours else 0.0
            if isinstance(graph, nx.DiGraph):
                in_d = in_deg[node]
                out_d = out_deg[node]
            else:
                in_d = deg[node]
                out_d = deg[node]
            rows.append(
                [
                    float(deg[node]),
                    float(in_d),
                    float(out_d),
                    float(clust[node]),
                    deg[node] / (mean_neighbour + 1.0),
                ]
            )
        return self._normalize_columns(np.asarray(rows, dtype=float))

    @staticmethod
    def _normalize_columns(matrix: np.ndarray) -> np.ndarray:
        """Min-max normalise each column of ``matrix`` into ``[0, 1]``."""
        if matrix.size == 0:
            return matrix
        col_min = matrix.min(axis=0)
        col_max = matrix.max(axis=0)
        span = col_max - col_min
        span = np.where(span == 0.0, 1.0, span)
        return (matrix - col_min) / span

    @staticmethod
    def _coerce_graph(graph: nx.Graph | Iterable[tuple[Hashable, Hashable]]) -> nx.Graph:
        """Accept either a networkx graph or a raw edge list."""
        if isinstance(graph, nx.Graph):
            return graph
        digraph = nx.DiGraph()
        for u, v in graph:
            digraph.add_edge(u, v)
        return digraph

    @staticmethod
    def _looks_like_edge_list(value: object) -> TypeGuard[Iterable[tuple[Hashable, Hashable]]]:
        """Return True when ``value`` is a list/tuple of ``(u, v)`` pairs.

        Used by :meth:`score` to distinguish an edge list from a single node
        id (which is any hashable, including a tuple — a tuple of two 2-tuples
        is therefore treated as an edge list, matching the fit() input type).
        """
        return isinstance(value, (list, tuple)) and all(
            isinstance(pair, (list, tuple)) and len(pair) == 2 for pair in value
        )


# ── Singleton ────────────────────────────────────────────────────────────────

_detector: PyGODAnomalyDetector | None = None


def get_pygod_anomaly() -> PyGODAnomalyDetector:
    """Return the shared :class:`PyGODAnomalyDetector` singleton."""
    global _detector
    if _detector is None:
        _detector = PyGODAnomalyDetector()
    return _detector


__all__ = [
    "AnomalyResult",
    "PyGODAnomalyDetector",
    "PyGODNotAvailable",
    "get_pygod_anomaly",
    "pygod_available",
]
