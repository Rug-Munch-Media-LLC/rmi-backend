#!/usr/bin/env python3
"""Generate typed Pydantic v2 vendor data models with datamodel-code-generator.

Replaces hand-written vendor data models (Helius, Alchemy, DexScreener,
CoinGecko, ...) with generated ones. The wire format of a vendor API is the
source of truth; we capture a representative JSON response per vendor under
``app/databus/vendor_models/samples/`` and generate the Pydantic models from
it, so model drift and hand-typing errors are eliminated.

Usage
-----
    # Regenerate ALL checked-in vendor models from their samples
    python scripts/gen_vendor_models.py

    # Generate a single vendor model set
    python scripts/gen_vendor_models.py --input samples/dexscreener_pairs.json --output dexscreener
    python scripts/gen_vendor_models.py --input samples/coingecko_markets.json --output coingecko

    # Generate from a live vendor URL (fetched once, sample not persisted)
    python scripts/gen_vendor_models.py --input https://api.dexscreener.com/latest/dex/tokens/SOL --output dexscreener

    # Generate from an OpenAPI / JSON Schema definition
    python scripts/gen_vendor_models.py --input vendor_openapi.json --input-file-type openapi --output vendor

Requires (dev/test tool only — NOT a runtime dependency):
    pip install datamodel-code-generator

How it works
------------
* Invokes ``datamodel-codegen`` with the repo's conventions:
  ``--output-model-type pydantic_v2.BaseModel``, ``--target-python-version 3.11``
  (repo ``requires-python = ">=3.11"``), ``--snake-case-field`` (Python names
  are snake_case; the original vendor field names are kept as Pydantic v2
  aliases, so ``model_validate(vendor_payload)`` works with zero config),
  and the ``ruff-format``/``ruff-check`` formatters so output matches repo
  style.
* Post-processes the generated file:
  - strips the non-deterministic ``timestamp`` header line,
  - applies per-module class renames (see ``MODULE_RENAMES``) so the generic
    names datamodel-code-generator infers (``Model``, ``M5``, ``H1`` ...)
    become meaningful names (``DexScreenerPairs``, ``TxnsM5`` ...),
  - runs ``ruff check`` on the result and fails loudly if it is not clean.
* Generated files land in ``app/databus/vendor_models/<name>.py``.

Sample policy
-------------
Samples under ``app/databus/vendor_models/samples/`` are real vendor
responses, lightly normalized to capture the types the API actually returns:
* nullable fields that happened to be ``null`` in every captured response are
  given a representative value (e.g. CoinGecko ``roi``) so the generated type
  is ``X | None`` instead of a bare null,
* monetary fields that the API returns as decimals are stored as JSON floats
  (e.g. DexScreener ``fdv``/``marketCap``, CoinGecko ``market_cap``), because
  Pydantic ``float`` accepts both int and decimal input while ``int`` rejects
  decimals.

After generating a new module, export it from
``app/databus/vendor_models/__init__.py`` (the script warns if it is missing).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "app" / "databus" / "vendor_models"
SAMPLES_DIR = MODELS_DIR / "samples"
INIT_FILE = MODELS_DIR / "__init__.py"

# Repo requires Python >= 3.11 (see pyproject.toml).
TARGET_PYTHON_VERSION = "3.11"

# Class-name cleanups applied after generation, keyed by output module name.
# datamodel-code-generator names root schemas "Model" and infers cryptic names
# for repeated time-window shapes (M5/H1/H6/H24); these renames give the
# generated API meaningful, stable names.
MODULE_RENAMES: dict[str, dict[str, str]] = {
    "dexscreener_pairs": {
        "M5": "TxnsM5",
        "H1": "TxnsH1",
        "H6": "TxnsH6",
        "H24": "TxnsH24",
        "Model": "DexScreenerPairs",
    },
    "coingecko_markets": {
        "ModelItem": "CoinGeckoMarket",
        "Model": "CoinGeckoMarketList",
    },
}

# Generator flags shared by every invocation.
GENERATOR_FLAGS: list[str] = [
    "--target-python-version",
    TARGET_PYTHON_VERSION,
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--snake-case-field",
    "--formatters",
    "ruff-format",
    "ruff-check",
]

# Generated code is a mechanical artifact of a vendor's wire format. When the
# generator emits names that trip ruff's pep8-naming rules (rare with
# --snake-case-field, but possible for enums/root names), we add a module-level
# suppression scoped to exactly those codes rather than disabling checks
# repo-wide.
ALLOWED_NOQA_CODES = {"N801", "N802", "N803", "N815"}


def _find_codegen() -> str:
    """Locate the datamodel-codegen executable (venv bin preferred)."""
    venv_bin = REPO_ROOT / ".venv" / "bin" / "datamodel-codegen"
    if venv_bin.is_file():
        return str(venv_bin)
    found = shutil.which("datamodel-codegen")
    if found:
        return found
    sys.exit("datamodel-codegen not found. Install it with: .venv/bin/pip install datamodel-code-generator")


def _run_codegen(codegen: str, input_path: str, input_type: str, output: Path) -> None:
    """Run datamodel-codegen for one input, writing to ``output``."""
    cmd = [
        codegen,
        "--input",
        input_path,
        "--input-file-type",
        input_type,
        "--output",
        str(output),
        *GENERATOR_FLAGS,
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)  # noqa: S603 - dev tool, trusted argv, no shell
    if proc.returncode != 0:
        sys.exit(f"datamodel-codegen failed for {input_path}:\n{proc.stderr[-2000:]}")
    # Suppress the dependency-free-formatter deprecation warning on stderr only.
    for line in proc.stderr.splitlines():
        if "FutureWarning" in line or "warn_deprecated" in line:
            continue
        if line.strip():
            print(f"  [codegen] {line}")


def _strip_timestamp(text: str) -> str:
    """Remove the timestamp header line so regeneration is deterministic."""
    lines = [ln for ln in text.splitlines() if not ln.startswith("#   timestamp:")]
    # Collapse the blank line left behind if the timestamp was the last header line.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)) + "\n"


def _apply_renames(text: str, renames: dict[str, str]) -> str:
    """Word-boundary class renames (class defs and annotation references)."""
    for old, new in renames.items():
        text = re.sub(rf"\b{re.escape(old)}\b", new, text)
    return text


def _add_noqa_header(text: str, rules: str) -> str:
    """Insert a module-level ruff noqa directive under the generated header."""
    if f"# ruff: noqa: {rules}" in text:
        return text
    # Insert after the last header comment line (before the first blank line).
    lines = text.splitlines()
    idx = 0
    while idx < len(lines) and lines[idx].startswith("#"):
        idx += 1
    if idx < len(lines) and lines[idx] == "":
        lines.insert(idx, f"# ruff: noqa: {rules}")
        lines.insert(idx + 1, "")
    return "\n".join(lines) + "\n"


def _ruff_path() -> str:
    return shutil.which("ruff") or str(REPO_ROOT / ".venv" / "bin" / "ruff")


def _ruff_codes(path: Path) -> list[str]:
    """Return sorted ruff violation codes for ``path`` (empty when clean)."""
    proc = subprocess.run(  # noqa: S603 - dev tool, trusted argv, no shell
        [_ruff_path(), "check", "--output-format", "json", str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    try:
        violations = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    return sorted({v.get("code", "") for v in violations if v.get("code")})


def _postprocess(generated: Path, module: str) -> None:
    """Deterministic cleanup: header, renames, then a ruff lint gate."""
    text = generated.read_text()
    text = _strip_timestamp(text)
    text = _apply_renames(text, MODULE_RENAMES.get(module, {}))
    generated.write_text(text)

    # Normalize formatting/imports after text edits, then see what remains.
    subprocess.run(  # noqa: S603 - dev tool, trusted argv, no shell
        [_ruff_path(), "format", str(generated)], cwd=REPO_ROOT, capture_output=True
    )
    subprocess.run(  # noqa: S603 - dev tool, trusted argv, no shell
        [_ruff_path(), "check", "--fix", str(generated)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    codes = _ruff_codes(generated)
    leftover = [c for c in codes if c not in ALLOWED_NOQA_CODES]
    if leftover:
        sys.exit(
            f"generated {generated.name} is not ruff-clean (unhandled: "
            f"{', '.join(leftover)}). Run .venv/bin/ruff check "
            f"{generated} to inspect."
        )
    if codes:
        text = generated.read_text()
        generated.write_text(_add_noqa_header(text, ",".join(codes)))
        # Re-lint to confirm the scoped noqa made it clean.
        after = _ruff_codes(generated)
        if after:
            sys.exit(f"generated {generated.name} still not ruff-clean: {after}")
    print(f"  [ruff] {generated.name} clean")


def _exported_in_init(module: str) -> bool:
    """True if ``module`` is imported/exported by vendor_models/__init__.py."""
    if not INIT_FILE.exists():
        return False
    return module in INIT_FILE.read_text()


def _generate(module: str, input_path: str, input_type: str) -> Path:
    """Generate one module and return its path."""
    print(f"Generating vendor models for '{module}' from {input_path}")
    output = MODELS_DIR / f"{module}.py"
    _run_codegen(_find_codegen(), input_path, input_type, output)
    _postprocess(output, module)
    if not _exported_in_init(module):
        print(f"  [warn] {module} is not exported from {INIT_FILE.relative_to(REPO_ROOT)} — add an import there.")
    return output


def _gen_all() -> list[Path]:
    """Regenerate every vendor module from its checked-in sample."""
    generated: list[Path] = []
    for sample in sorted(SAMPLES_DIR.glob("*.json")):
        module = sample.stem
        generated.append(_generate(module, str(sample), "json"))
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        help="JSON/OpenAPI schema or JSON sample file/URL to generate from. "
        "Omit to regenerate all samples in app/databus/vendor_models/samples/.",
    )
    parser.add_argument(
        "--output",
        help="Output module name (e.g. 'dexscreener'); writes "
        "app/databus/vendor_models/<output>.py. Required with --input.",
    )
    parser.add_argument(
        "--input-file-type",
        default="auto",
        choices=["auto", "openapi", "jsonschema", "json", "yaml"],
        help="Input format for datamodel-codegen (default: auto-detect).",
    )
    parser.add_argument(
        "--rename",
        metavar="JSON",
        help='Extra class renames as inline JSON: \'{"Old": "New"}\'. Merged over the per-module defaults.',
    )
    args = parser.parse_args()

    if args.input:
        if not args.output:
            parser.error("--output is required when --input is given")
        if args.rename:
            try:
                extra = json.loads(args.rename)
            except json.JSONDecodeError as exc:
                parser.error(f"--rename must be valid JSON: {exc}")
            MODULE_RENAMES.setdefault(args.output, {}).update(extra)
        _generate(args.output, args.input, args.input_file_type)
    else:
        if args.output or args.input_file_type != "auto" or args.rename:
            parser.error("--input, --output, --rename are for single-vendor use only")
        generated = _gen_all()
        print(f"\nGenerated {len(generated)} vendor model module(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
