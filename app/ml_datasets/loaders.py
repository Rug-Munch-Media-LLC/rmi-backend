"""Loaders for open labeled datasets used to benchmark risk/detection models.

Design rules
------------
* stdlib-first: ``urllib.request`` fallback keeps the module importable in any
  Python 3.9+ environment; ``requests`` is used when available (it is in this
  repo).  No new dependencies are introduced.
* Downloads are cached under the cache dir — default
  ``<repo>/data/ml_datasets`` (override with the ``ML_DATASETS_DIR`` env var).
  If a cached file already exists it is used and nothing is fetched.
* Every loader fails gracefully offline: a cached copy is served when present,
  otherwise a :class:`DatasetDownloadError` with a recovery hint is raised.
* Only loader *code* is committed; the datasets themselves are never checked
  into git (they live in the cache dir).

Datasets
--------
* **smartbugs-wild** — 47,398 real-world Solidity contracts collected from
  Ethereum (smartbugs/smartbugs-wild).  Metadata artifact: ``contracts.csv.tar.gz``.
  NOTE: the artifact is *unlabeled* — it is a real-world background set useful
  for false-positive measurement, not a positive-labeled corpus.
* **real-cats** — 50,943 criminal + 102,178 benign addresses with transaction
  profiles (sjdseu/Real-CATS).  ``CE.tsv``/``BE.tsv`` (Ethereum),
  ``CB.tsv``/``BB.tsv`` (Bitcoin); rows carry ``address`` and ``label`` columns.
* **defihacklabs** — 850+ real DeFi exploit incidents with Foundry PoCs
  (SunWeb3Sec/DeFiHackLabs) — a "known-bad" protocol/incident list parsed from
  the repo README (``### YYYYMMDD Protocol - description`` headings).
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import tarfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# ── Errors ───────────────────────────────────────────────────────────────────


class DatasetDownloadError(RuntimeError):
    """Raised when a dataset artifact cannot be fetched (e.g. offline)."""


# ── Data model ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DatasetInfo:
    """Static metadata about a dataset (never depends on network)."""

    name: str
    description: str
    source_url: str
    license: str
    citation: str = ""
    positive_semantics: str = ""


@dataclass
class LabeledRecord:
    """One ground-truth row.

    ``positive`` semantics are set by each loader:
    True = criminal / exploited / vulnerable, False = benign,
    None = unlabeled (real-world background, excluded from metrics).
    """

    id: str
    address: str | None  # None for datasets keyed by protocol name, not address
    label: str | None  # human-readable label, e.g. "Blackmail Scam"
    positive: bool | None
    source: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "address": self.address,
            "label": self.label,
            "positive": self.positive,
            "source": self.source,
            "extra": self.extra,
        }


@dataclass
class LabeledDataset:
    """A loaded ground-truth set."""

    info: DatasetInfo
    records: list[LabeledRecord]
    from_cache: bool = False

    def __len__(self) -> int:
        return len(self.records)

    @property
    def labeled_count(self) -> int:
        return sum(1 for r in self.records if r.positive is not None)


# ── Cache / download helpers ─────────────────────────────────────────────────


def default_cache_dir() -> Path:
    """Cache dir: ``$ML_DATASETS_DIR`` or ``<repo>/data/ml_datasets``."""
    env = os.environ.get("ML_DATASETS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "ml_datasets"


def _resolve_cache_dir(cache_dir: str | Path | None) -> Path:
    return Path(cache_dir) if cache_dir is not None else default_cache_dir()


_UA = "rmi-backend/ml_datasets (+https://rugmunch.io)"


def _http_get_bytes(url: str, timeout: float = 120.0) -> bytes:
    """GET a URL, preferring ``requests`` with an ``urllib`` fallback."""
    try:
        import requests

        resp = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
        resp.raise_for_status()
        return resp.content
    except ImportError:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 - dataset URLs are operator-defined constants, never user input
            return resp.read()
    except Exception as exc:
        raise DatasetDownloadError(f"GET {url} failed: {exc}") from exc


def _cached_download(url: str, cache_dir: Path, filename: str, timeout: float = 120.0) -> tuple[Path, bool]:
    """Return ``(path, was_already_cached)``.

    If the file already exists locally it is returned untouched — this is the
    offline path and the fixture/test path (pre-seed the cache dir).
    """
    cache_dir = Path(cache_dir)
    dest = cache_dir / filename
    if dest.is_file() and dest.stat().st_size > 0:
        return dest, True
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = _http_get_bytes(url, timeout=timeout)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest, False


# ── SmartBugs-Wild ───────────────────────────────────────────────────────────


SBW_INFO = DatasetInfo(
    name="smartbugs-wild",
    description=(
        "47,398 real-world Solidity contracts collected from Ethereum (2019). "
        "The bundled metadata artifact is UNLABELED — use as a real-world "
        "background / false-positive baseline, not as a positive-labeled set."
    ),
    source_url="https://github.com/smartbugs/smartbugs-wild",
    license=(
        "Apache-2.0 (repo LICENSE); individual contracts retain their original licenses (obtained via Etherscan APIs)"
    ),
    citation="The SmartBugs Wild Dataset. ICSE 2020 paper: arXiv:1910.10601",
    positive_semantics="unlabeled real-world contracts (no ground-truth vuln labels in artifact)",
)
SBW_TARBALL_URL = "https://raw.githubusercontent.com/smartbugs/smartbugs-wild/master/contracts.csv.tar.gz"


def load_smartbugs(cache_dir: str | Path | None = None, limit: int | None = None) -> LabeledDataset:
    """Load SmartBugs-Wild contract metadata (addresses + row metadata).

    The tarball is a few MB; it is cached under ``<cache>/smartbugs-wild_contracts.csv.tar.gz``.
    """
    cache = _resolve_cache_dir(cache_dir)
    archive, was_cached = _cached_download(SBW_TARBALL_URL, cache, "smartbugs-wild_contracts.csv.tar.gz", timeout=180.0)
    records = _parse_smartbugs_tarball(archive, limit=limit)
    if not records:
        raise DatasetDownloadError("SmartBugs-Wild: no records parsed from metadata CSV")
    return LabeledDataset(info=SBW_INFO, records=records, from_cache=was_cached)


def _parse_smartbugs_tarball(archive: Path, limit: int | None) -> list[LabeledRecord]:
    records: list[LabeledRecord] = []
    with tarfile.open(archive, "r:gz") as tf:
        members = [m for m in tf.getmembers() if m.isfile() and m.name.lower().endswith(".csv")]
        if not members:
            raise DatasetDownloadError(f"SmartBugs-Wild: no CSV member inside {archive.name}")
        raw = tf.extractfile(members[0])
        if raw is None:
            raise DatasetDownloadError(f"SmartBugs-Wild: cannot read {members[0].name}")
        text = raw.read().decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise DatasetDownloadError("SmartBugs-Wild: metadata CSV has no header")
    addr_col = next((c for c in reader.fieldnames if c.strip().lower() == "address"), reader.fieldnames[0])

    for row in reader:
        if limit is not None and len(records) >= limit:
            break
        addr = (row.get(addr_col) or "").strip()
        if not addr:
            continue
        extra = {k: v for k, v in row.items() if k != addr_col and v not in ("", None)}
        records.append(
            LabeledRecord(
                id=addr,
                address=addr,
                label="real-world_contract",
                positive=None,  # unlabeled background corpus
                source=SBW_INFO.name,
                extra=extra,
            )
        )
    return records


# ── Real-CATS ────────────────────────────────────────────────────────────────


RC_INFO = DatasetInfo(
    name="real-cats",
    description=(
        "Real-world crypto-asset addresses with transaction profiles "
        "(50,943 criminal from public reports + 102,178 benign from exchange customers)."
    ),
    source_url="https://github.com/sjdseu/Real-CATS",
    license=(
        "No LICENSE file found in repo at fetch time (2026-08). Addresses derive from "
        "public incident reports / exchange disclosures. [REVIEW] confirm terms before "
        "redistributing or using in a commercial product."
    ),
    citation="Real-CATS: Real-world Crypto-Asset Tracing System (sjdseu/Real-CATS)",
    positive_semantics="criminal (reported scams, blackmail, etc.) vs benign",
)
RC_BASE = "https://raw.githubusercontent.com/sjdseu/Real-CATS/master"
# filename -> (chain, positive)
RC_FILES: dict[str, tuple[str, bool]] = {
    "CE.tsv": ("ethereum", True),
    "BE.tsv": ("ethereum", False),
    "CB.tsv": ("bitcoin", True),
    "BB.tsv": ("bitcoin", False),
}


def load_realcats(
    cache_dir: str | Path | None = None,
    chains: tuple[str, ...] = ("ethereum",),
    limit: int | None = None,
    with_features: bool = False,
) -> LabeledDataset:
    """Load Real-CATS labeled addresses.

    ``chains`` filters to ``"ethereum"`` and/or ``"bitcoin"`` (Ethereum is the
    default because the repo's detectors target EVM).  Files are 15-90 MB; each
    is cached under its original filename.  Set ``with_features=True`` to keep
    the full 32/52 transaction-profile feature row in ``record.extra["features"]``.
    """
    cache = _resolve_cache_dir(cache_dir)
    records: list[LabeledRecord] = []
    from_cache = True
    for fname, (chain, positive) in RC_FILES.items():
        if chain not in chains:
            continue
        path, was_cached = _cached_download(f"{RC_BASE}/{fname}", cache, fname, timeout=300.0)
        from_cache = from_cache and was_cached
        records.extend(
            _parse_realcats_tsv(path, chain=chain, positive=positive, limit=limit, with_features=with_features)
        )
        if limit is not None and len(records) >= limit:
            break
    if not records:
        raise DatasetDownloadError("Real-CATS: no records loaded (check 'chains' argument and network access)")
    if limit is not None:
        records = records[:limit]
    return LabeledDataset(info=RC_INFO, records=records, from_cache=from_cache)


def _parse_realcats_tsv(
    path: Path,
    *,
    chain: str,
    positive: bool,
    limit: int | None,
    with_features: bool,
) -> list[LabeledRecord]:
    records: list[LabeledRecord] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if reader.fieldnames is None:
            raise DatasetDownloadError(f"Real-CATS: {path.name} has no header row")
        for row in reader:
            if limit is not None and len(records) >= limit:
                break
            addr = (row.get("address") or "").strip()
            if not addr:
                continue
            if chain == "ethereum":
                addr = addr.lower()
            label = (row.get("label") or "").strip() or ("criminal" if positive else "benign")
            extra: dict[str, Any] = {"chain": chain}
            if with_features:
                extra["features"] = {
                    k: v for k, v in row.items() if k not in ("address", "label") and v not in ("", None)
                }
            records.append(
                LabeledRecord(
                    id=addr,
                    address=addr,
                    label=label,
                    positive=positive,
                    source=RC_INFO.name,
                    extra=extra,
                )
            )
    return records


# ── DeFiHackLabs ─────────────────────────────────────────────────────────────


DHL_INFO = DatasetInfo(
    name="defihacklabs",
    description=(
        "Real DeFi exploit incidents with Foundry PoCs — a 'known-bad' list of "
        "850+ exploited protocols, parsed from the repo README incident index."
    ),
    source_url="https://github.com/SunWeb3Sec/DeFiHackLabs",
    license="Apache-2.0 (repo LICENSE)",
    citation="DeFiHackLabs: Reproduce DeFi hack incidents using Foundry (SunWeb3Sec)",
    positive_semantics="exploited protocol / incident (positive = known-bad)",
)
DHL_README_URL = "https://raw.githubusercontent.com/SunWeb3Sec/DeFiHackLabs/main/README.md"

# Incident headings look like:  ### 20260809 USM - defund() price split-invariance rounding exploit
_INCIDENT_RE = re.compile(r"^###\s+(\d{8})\s+(.+?)\s*-\s*(.+)$")
# PoC link right below the heading:  [USM_exp.sol](src/test/2026-08/USM_exp.sol)
_POC_RE = re.compile(r"\]\((src/test/[^\s)]+\.sol)\)")
# TOC index anchors (the full incident list, 850+); slugs vary in separator:
#   [20260809 USM](#20260809-usm---defund-price-split-invariance-rounding-exploit)
#   [20230328 SafeMoon Hack](past/2023/README.md#20230328-safemoon-hack)
_ANCHOR_RE = re.compile(r"^\[(\d{8}) (.+?)\]\([^)]*#(\d{8}-[^)]+)\)$")


def load_defihacklabs(cache_dir: str | Path | None = None, limit: int | None = None) -> LabeledDataset:
    """Load the DeFiHackLabs incident index (a small ~120 KB README fetch)."""
    cache = _resolve_cache_dir(cache_dir)
    path, was_cached = _cached_download(DHL_README_URL, cache, "defihacklabs_README.md", timeout=90.0)
    records = _parse_defihacklabs_readme(path, limit=limit)
    if not records:
        raise DatasetDownloadError("DeFiHackLabs: no incident headings parsed from README")
    return LabeledDataset(info=DHL_INFO, records=records, from_cache=was_cached)


def _parse_defihacklabs_readme(path: Path, limit: int | None) -> list[LabeledRecord]:
    """Parse the incident index.

    Two record sources, merged by ``date:protocol`` (detailed write-ups win):
    * ``### YYYYMMDD Protocol - description`` headings (110 rich entries with
      PoC paths);
    * TOC anchor links (the full 850+ incident index).
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    records: dict[str, LabeledRecord] = {}

    def _add(rec: LabeledRecord) -> None:
        records.setdefault(rec.id, rec)

    # Pass 1: rich detailed write-ups.
    for i, line in enumerate(lines):
        m = _INCIDENT_RE.match(line.strip())
        if not m:
            continue
        date, protocol, description = m.group(1), m.group(2).strip(), m.group(3).strip()
        poc: str | None = None
        for probe in lines[i + 1 : i + 8]:
            pm = _POC_RE.search(probe)
            if pm:
                poc = pm.group(1)
                break
        rid = f"{date}:{protocol}"
        extra: dict[str, Any] = {"date": date, "protocol": protocol}
        if poc:
            extra["poc_path"] = poc
        _add(
            LabeledRecord(
                id=rid,
                address=None,  # protocol-name keyed, not an address
                label=f"exploited:{protocol}",
                positive=True,
                source=DHL_INFO.name,
                extra=extra,
            )
        )

    # Pass 2: TOC anchors for incidents without a detailed write-up.
    for line in lines:
        m = _ANCHOR_RE.match(line.strip())
        if not m:
            continue
        date, protocol, slug = m.group(1), m.group(2).strip(), m.group(3).strip()
        rid = f"{date}:{protocol}"
        if rid in records:
            continue  # detailed write-up already captured it
        # Slug is "YYYYMMDD<separator(s)>description" — de-hyphenate the tail.
        description = re.sub(r"\s+", " ", slug[8:].lstrip("-").replace("-", " ")).strip()
        _add(
            LabeledRecord(
                id=rid,
                address=None,
                label=f"exploited:{protocol}",
                positive=True,
                source=DHL_INFO.name,
                extra={"date": date, "protocol": protocol, "description": description},
            )
        )

    out = list(records.values())
    if limit is not None:
        out = out[:limit]
    return out


# ── Registry ─────────────────────────────────────────────────────────────────


DATASET_REGISTRY: dict[str, Callable[..., LabeledDataset]] = {
    "smartbugs": load_smartbugs,
    "smartbugs-wild": load_smartbugs,
    "realcats": load_realcats,
    "real-cats": load_realcats,
    "defihacklabs": load_defihacklabs,
    "defi-hack-labs": load_defihacklabs,
}


def load_dataset(name: str, cache_dir: str | Path | None = None, **kwargs: Any) -> LabeledDataset:
    """Load a dataset by name (aliases accepted).  Extra kwargs pass to the loader."""
    key = name.strip().lower()
    loader = DATASET_REGISTRY.get(key)
    if loader is None:
        available = sorted({k.split("-")[0] for k in DATASET_REGISTRY})
        raise KeyError(f"Unknown dataset {name!r}; available: {available}")
    return loader(cache_dir=cache_dir, **kwargs)
