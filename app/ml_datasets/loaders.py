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
* **solrpds** — Solana rug-pull dataset (DeFiLabX/SolRPDS, CC BY 4.0): liquidity
  pools on Raydium/Jupiter annotated with ``INACTIVITY_STATUS`` (the paper's
  rug-pull indicator) plus liquidity add/remove profiles; CSV/JSON artifacts
  covering 2021-11 .. 2024-11.
* **openaml** — FINOS OpenAML (finos-labs/dtcch-2025-OpenAML, Apache-2.0):
  ~500k vetted illicit wallet addresses grouped into three AML risk categories
  (sanctioned, fraud, flagged); gzipped CSV ``blockchain,wallet_address,flag``.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import re
import tarfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

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


def _clean_str(value: Any) -> str | None:
    """Strip a raw cell; map empty/``null``/``None``/``nan`` strings to None.

    Both SolRPDS (CSV/JSON) and OpenAML (gzipped CSV) encode missing values as
    the literal string ``"null"`` — normalize them here so downstream code only
    sees real values.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "nan"):
        return None
    return text


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


# ── SolRPDS ──────────────────────────────────────────────────────────────────


SOLRPDS_INFO = DatasetInfo(
    name="solrpds",
    description=(
        "Solana rug-pull dataset built from 3.69B blockchain transactions "
        "(2021-11 .. 2024-11). Liquidity pools on Raydium/Jupiter annotated with "
        "INACTIVITY_STATUS — the paper's key rug-pull indicator — plus liquidity "
        "add/remove profiles. One CSV/JSON row per (pool x mint)."
    ),
    source_url="https://github.com/DeFiLabX/SolRPDS",
    license="CC BY 4.0 (Creative Commons Attribution 4.0 International; repo README)",
    citation=(
        "Alhaidari, A., Kalal, B., Palanisamy, B., Sural, S. (2025). SolRPDS: A Dataset "
        "for Analyzing Rug Pulls in Solana Decentralized Finance. CODASPY 2025, pp. 293-298. "
        "arXiv:2504.07132"
    ),
    positive_semantics=(
        "INACTIVITY_STATUS == Inactive (abandoned pool, the paper's rug-pull indicator) "
        "vs Active (still operating). [REVIEW] confirm label mapping before production use."
    ),
)
SOLRPDS_BASE = "https://raw.githubusercontent.com/DeFiLabX/SolRPDS/main/dataset"
# year -> (remote CSV path, remote JSON path)
SOLRPDS_FILES: dict[str, tuple[str, str]] = {
    "2021": ("CSV/2021.csv", "json/2021.json"),
    "2022": ("CSV/2022.csv", "json/2022.json"),
    "2023": ("CSV/2023.csv", "json/2023.json"),
    "2024": ("CSV/Jan_2024-Nov_2024.csv", "json/Jan_2024-Nov_2024.json"),
}
_SOLRPDS_ADDR_COL = "LIQUIDITY_POOL_ADDRESS"
_SOLRPDS_MINT_COL = "MINT"
_SOLRPDS_LABEL_COL = "INACTIVITY_STATUS"


def load_solrpds(
    cache_dir: str | Path | None = None,
    years: tuple[str, ...] = ("2021", "2022", "2023", "2024"),
    fmt: str = "csv",
    limit: int | None = None,
    with_features: bool = False,
) -> LabeledDataset:
    """Load SolRPDS rug-pull labeled pools.

    ``years`` selects which annual slices to fetch (default all four);
    ``fmt`` selects the artifact format: ``"csv"`` (smaller, default) or
    ``"json"`` — both carry the same columns. Files are a few MB each and are
    cached under ``<cache>/solrpds_<year>.<ext>``. Set ``with_features=True``
    to keep the liquidity-profile columns in ``record.extra``.
    """
    if fmt not in ("csv", "json"):
        raise ValueError(f"SolRPDS: fmt must be 'csv' or 'json', got {fmt!r}")
    unknown = [y for y in years if y not in SOLRPDS_FILES]
    if unknown:
        raise ValueError(f"SolRPDS: unknown year(s) {unknown}; available: {sorted(SOLRPDS_FILES)}")
    cache = _resolve_cache_dir(cache_dir)
    records: list[LabeledRecord] = []
    from_cache = True
    for year in years:
        remote = SOLRPDS_FILES[year][0 if fmt == "csv" else 1]
        cache_name = f"solrpds_{Path(remote).name}"
        path, was_cached = _cached_download(f"{SOLRPDS_BASE}/{remote}", cache, cache_name, timeout=300.0)
        from_cache = from_cache and was_cached
        if fmt == "csv":
            records.extend(_parse_solrpds_csv(path, year=year, limit=limit, with_features=with_features))
        else:
            records.extend(_parse_solrpds_json(path, year=year, limit=limit, with_features=with_features))
        if limit is not None and len(records) >= limit:
            break
    if not records:
        raise DatasetDownloadError("SolRPDS: no records loaded (check 'years'/'fmt' arguments and network access)")
    if limit is not None:
        records = records[:limit]
    return LabeledDataset(info=SOLRPDS_INFO, records=records, from_cache=from_cache)


def _parse_solrpds_csv(path: Path, *, year: str, limit: int | None, with_features: bool) -> list[LabeledRecord]:
    records: list[LabeledRecord] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise DatasetDownloadError(f"SolRPDS: {path.name} has no header row")
        for row in reader:
            rec = _solrpds_row_to_record(row, year=year, with_features=with_features)
            if rec is None:
                continue
            records.append(rec)
            if limit is not None and len(records) >= limit:
                break
    return records


def _parse_solrpds_json(path: Path, *, year: str, limit: int | None, with_features: bool) -> list[LabeledRecord]:
    raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(raw, list):
        raise DatasetDownloadError(f"SolRPDS: {path.name} is not a JSON array")
    items = cast("list[dict[str, Any]]", raw)
    records: list[LabeledRecord] = []
    for item in items:
        rec = _solrpds_row_to_record(item, year=year, with_features=with_features)
        if rec is None:
            continue
        records.append(rec)
        if limit is not None and len(records) >= limit:
            break
    return records


def _solrpds_row_to_record(row: dict[str, Any], *, year: str, with_features: bool) -> LabeledRecord | None:
    """Normalize one SolRPDS row (shared by the CSV and JSON artifacts)."""
    pool = _clean_str(row.get(_SOLRPDS_ADDR_COL))
    status = _clean_str(row.get(_SOLRPDS_LABEL_COL))
    if not pool or status is None:
        return None  # no pool address or no inactivity label -> no ground truth
    positive = status.lower() == "inactive"
    extra: dict[str, Any] = {
        "mint": _clean_str(row.get(_SOLRPDS_MINT_COL)),
        "year": year,
        "inactivity_status": status,
    }
    if with_features:
        extra.update(
            {
                k: v
                for k, v in row.items()
                if k not in (_SOLRPDS_ADDR_COL, _SOLRPDS_MINT_COL, _SOLRPDS_LABEL_COL) and _clean_str(v) is not None
            }
        )
    return LabeledRecord(
        id=f"{pool}:{_clean_str(row.get(_SOLRPDS_MINT_COL)) or '-'}",
        address=pool,
        label="rug-pull" if positive else "benign",
        positive=positive,
        source=SOLRPDS_INFO.name,
        extra=extra,
    )


# ── OpenAML ──────────────────────────────────────────────────────────────────


OPENAML_INFO = DatasetInfo(
    name="openaml",
    description=(
        "FINOS OpenAML — ~500k vetted wallet addresses grouped by AML risk "
        "category: sanctioned (OFAC/SEC/stablecoin blocklists), fraud "
        "(exploit-linked & security incidents), flagged (reported scams & "
        "phishing, e.g. HashDit). All listed addresses are illicit."
    ),
    source_url="https://github.com/finos-labs/dtcch-2025-OpenAML",
    license="Apache-2.0 (Copyright 2025 FINOS; repo LICENSE)",
    citation=(
        "OpenAML: Open and Intelligent Compliance for On-Chain Anti-Money Laundering "
        "(FINOS / DTCC hackathon; finos-labs/dtcch-2025-OpenAML)"
    ),
    positive_semantics="all listed addresses are illicit: sanctioned / fraud / flagged",
)
OPENAML_BASE = "https://raw.githubusercontent.com/finos-labs/dtcch-2025-OpenAML/main/Data"
# category -> (gzipped CSV filename, default label)
OPENAML_FILES: dict[str, tuple[str, str]] = {
    "flagged": ("address_flagged.csv.gz", "flagged"),
    "fraud": ("address_fraud.csv.gz", "fraud"),
    "sanctioned": ("address_sanctioned.csv.gz", "sanctioned"),
}


def load_openaml(
    cache_dir: str | Path | None = None,
    categories: tuple[str, ...] = ("flagged", "fraud", "sanctioned"),
    limit: int | None = None,
) -> LabeledDataset:
    """Load FINOS OpenAML labeled addresses (every record is illicit/positive).

    Each category is a gzipped CSV (``blockchain,wallet_address,flag``) fetched
    from the repo's ``Data/`` folder and cached under its original filename.
    EVM (``0x...``) addresses are lowercased for consistent lookups.
    """
    unknown = [c for c in categories if c not in OPENAML_FILES]
    if unknown:
        raise ValueError(f"OpenAML: unknown category(ies) {unknown}; available: {sorted(OPENAML_FILES)}")
    cache = _resolve_cache_dir(cache_dir)
    records: list[LabeledRecord] = []
    from_cache = True
    for category in categories:
        fname, default_label = OPENAML_FILES[category]
        path, was_cached = _cached_download(f"{OPENAML_BASE}/{fname}", cache, fname, timeout=300.0)
        from_cache = from_cache and was_cached
        records.extend(_parse_openaml_csv(path, category=category, default_label=default_label, limit=limit))
        if limit is not None and len(records) >= limit:
            break
    if not records:
        raise DatasetDownloadError("OpenAML: no records loaded (check 'categories' argument and network access)")
    if limit is not None:
        records = records[:limit]
    return LabeledDataset(info=OPENAML_INFO, records=records, from_cache=from_cache)


def _parse_openaml_csv(path: Path, *, category: str, default_label: str, limit: int | None) -> list[LabeledRecord]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    lines = text.splitlines()
    # The shipped artifacts are headerless (blockchain,wallet_address,flag rows);
    # the Data/README documents a header. Sniff: if the first line looks like a
    # header we honor it, otherwise fall back to positional columns.
    header, rows = _sniff_csv_header(lines)
    iterable: Iterable[dict[str, Any]]
    addr_col: str
    chain_col: str | None
    label_col: str | None
    if header:
        reader = csv.DictReader(io.StringIO("\n".join(lines)))
        if reader.fieldnames is None:
            raise DatasetDownloadError(f"OpenAML: {path.name} has no header row")
        addr_col = next(
            (c for c in reader.fieldnames if c.strip().lower() in ("wallet_address", "address")),
            reader.fieldnames[0],
        )
        chain_col = next((c for c in reader.fieldnames if c.strip().lower() in ("blockchain", "chain")), None)
        label_col = next((c for c in reader.fieldnames if c.strip().lower() in ("annotation", "flag", "label")), None)
        iterable = reader
    else:
        # Positional: blockchain,wallet_address,flag (the shipped shape).
        addr_col, chain_col, label_col = "wallet_address", "blockchain", "flag"
        iterable = (dict(zip(("blockchain", "wallet_address", "flag"), r, strict=False)) for r in rows)
    records: list[LabeledRecord] = []
    for row in iterable:
        if limit is not None and len(records) >= limit:
            break
        rec = _openaml_row_to_record(
            row,
            addr_col=addr_col,
            chain_col=chain_col,
            label_col=label_col,
            category=category,
            default_label=default_label,
        )
        if rec is None:
            continue
        records.append(rec)
    return records


def _sniff_csv_header(lines: list[str]) -> tuple[bool, list[list[str]]]:
    """Return ``(has_header, parsed_rows)`` for the first non-empty line."""
    if not lines:
        return False, []
    first = next((ln for ln in lines if ln.strip()), "")
    lowered = first.lower()
    has_header = any(k in lowered for k in ("wallet_address", "blockchain", "annotation", "address"))
    return has_header, [r for r in csv.reader(lines) if r]


def _openaml_row_to_record(
    row: dict[str, Any],
    *,
    addr_col: str,
    chain_col: str | None,
    label_col: str | None,
    category: str,
    default_label: str,
) -> LabeledRecord | None:
    addr = _clean_str(row.get(addr_col))
    if addr is None:
        return None
    if addr.lower().startswith("0x"):
        addr = addr.lower()
    chain = _clean_str(row.get(chain_col)) if chain_col else None
    annotation = _clean_str(row.get(label_col)) if label_col else None
    return LabeledRecord(
        id=addr,
        address=addr,
        label=annotation or default_label,
        positive=True,  # every OpenAML list is an illicit wallet
        source=OPENAML_INFO.name,
        extra={"chain": (chain or "unknown").lower(), "category": category},
    )


# ── Registry ─────────────────────────────────────────────────────────────────


DATASET_REGISTRY: dict[str, Callable[..., LabeledDataset]] = {
    "smartbugs": load_smartbugs,
    "smartbugs-wild": load_smartbugs,
    "realcats": load_realcats,
    "real-cats": load_realcats,
    "defihacklabs": load_defihacklabs,
    "defi-hack-labs": load_defihacklabs,
    "solrpds": load_solrpds,
    "openaml": load_openaml,
}


def load_dataset(name: str, cache_dir: str | Path | None = None, **kwargs: Any) -> LabeledDataset:
    """Load a dataset by name (aliases accepted).  Extra kwargs pass to the loader."""
    key = name.strip().lower()
    loader = DATASET_REGISTRY.get(key)
    if loader is None:
        available = sorted({k.split("-")[0] for k in DATASET_REGISTRY})
        raise KeyError(f"Unknown dataset {name!r}; available: {available}")
    return loader(cache_dir=cache_dir, **kwargs)
