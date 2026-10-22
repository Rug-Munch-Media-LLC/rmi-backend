"""Field extraction for the Telegram scan card.

Everything here is a pure read of a scanner result dict: no I/O, no layout
decision, and **no invented values** — an absent metric returns ``None`` or the
string ``unknown``, never a zero.

The scanner payload is wide and partly populated: a quick scan leaves
``liquidity`` / ``top_holders`` / ``deployer_profile`` as ``None``, while the
evidence block under ``evidence.security.checks`` is usually populated and
carries both a status and a human ``details`` string. Extraction tries the
structured field first and the evidence string second, so one card works on a
rich deep scan and on a core-only one.
"""

from __future__ import annotations

from app.domains.telegram.scan_card_vocab import ACTION, REASONS, SEVERITY


def field(data: object, *path: str, default: object = None) -> object:
    """Walk a nested mapping, returning ``default`` at the first dead end."""
    node = data
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None or node == "":
            return default
    return default if node == "" else node


def money(value: object) -> str:
    """Compact USD: $1.24M / $12.4K / $842. ``unknown`` when the value is absent."""
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown"
    if not num:
        return "unknown"
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(num) >= size:
            return f"${num / size:.2f}{suffix}"
    return f"${num:,.2f}"


def num(value: object, unit: str = "", scale: float = 1.0) -> str:
    """Scalar with unit, or ``unknown`` — the guard against fake zeros."""
    try:
        return f"{float(value) * scale:.1f}{unit}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown"


def ratio(top: object, bottom: object) -> str:
    """Volume-to-liquidity multiple, used as a churn proxy."""
    try:
        top_f, bottom_f = float(top), float(bottom)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "unknown"
    return f"{top_f / bottom_f:.1f}x" if bottom_f else "unknown"


def short(address: object) -> str:
    """Address truncated the way editorial standards require (0x1234...abcd)."""
    text = str(address or "")
    return f"{text[:6]}...{text[-4:]}" if len(text) > 12 else text or "unknown"


def checks(result: dict) -> dict:
    """Map of check code -> ``{status, details}`` from the scan evidence block."""
    raw = field(result, "evidence", "security", "checks", default={})
    return raw if isinstance(raw, dict) else {}


def detail(result: dict, code: str, key: str) -> object:
    """Read one ``key=value`` token from a check detail string (``Holders=102086``)."""
    for part in str((checks(result).get(code) or {}).get("details", "")).split():
        name, _, value = part.partition("=")
        if name.lower() == key.lower() and value:
            return value
    return None


def severity(result: dict) -> str:
    """Header severity word, taken from the scanner's own risk level."""
    return SEVERITY.get(str(result.get("risk_level", "")).lower(), "UNKNOWN")


def symbol(result: dict, address: str) -> str:
    """Ticker, falling back to the address tail when metadata never arrived."""
    return str(field(result, "symbol", default="") or "").upper() or short(address)


def verdict(result: dict) -> list[tuple[str, str]]:
    """Three lines: what it is, what drives the risk, what to do about it."""
    determination = str(
        field(result, "risk_report", "determination", default="") or "no determination"
    )
    grade = severity(result)
    flags = list(result.get("risk_flags") or [])
    driver = (
        REASONS.get(str(flags[0]), "No graded risk driver surfaced")
        if flags
        else "No graded risk driver surfaced"
    )
    return [
        (
            "Verdict",
            f"{num(result.get('safety_score'))}/100 safety · {determination} · {num(result.get('confidence'))}% data confidence",
        ),
        ("Driver", driver),
        ("Action", ACTION.get(grade, "Insufficient data to grade.")),
    ]


def metrics(result: dict) -> list[tuple[str, str]]:
    """The fixed metric block, in reading order. Absent data reads ``unknown``."""
    liq = field(result, "liquidity_usd", default=field(result, "liquidity", "liquidity_usd"))
    locked = field(
        result, "liquidity", "lp_locked", default=field(result, "liquidity", "lp_burned")
    )
    lp = "unknown" if locked is None else ("locked/burned" if locked else "not locked")
    top10 = field(
        result,
        "holder_concentration",
        "top10_pct",
        default=field(result, "top_holders", "top10_pct"),
    )
    # A live pair cannot have zero holders, so a literal "0" here is a failed
    # holder lookup, not a fact. Rendering it would read as a rug signal.
    holders = str(
        field(result, "holder_count", default=detail(result, "HD03", "Holders")) or "unknown"
    )
    holders = "unknown" if holders in ("0", "0.0") else holders
    mint = field(
        result,
        "security",
        "mint_authority",
        default=field(result, "contract_authority", "mint_authority"),
    )
    freeze = field(
        result,
        "security",
        "freeze_authority",
        default=field(result, "contract_authority", "freeze_authority"),
    )
    profile = field(result, "deployer_profile", default={}) or {}
    return [
        ("Liquidity", f"{money(liq)} · LP {lp}"),
        ("Holders", f"top-10 {num(top10, '%')} · {holders} total"),
        (
            "Mint/freeze",
            f"mint {mint if mint is not None else 'unknown'} · freeze {freeze if freeze is not None else 'unknown'}",
        ),
        (
            "Transfer tax",
            f"buy {num(field(result, 'security', 'buy_tax'), '%')} · sell {num(field(result, 'security', 'sell_tax'), '%')}",
        ),
        (
            "Deployer",
            f"{num(profile.get('wallet_age_days'), 'd old')} · {num(profile.get('rug_rate'), '% prior rugs')} "
            f"({short(field(result, 'deployer_address'))})",
        ),
        ("Vol/liquidity", ratio(result.get("volume_24h_usd"), liq)),
    ]


def flags(result: dict) -> list[tuple[str, str]]:
    """``(reason, status)`` pairs — actionable wording, never a bare code."""
    graded = checks(result)
    order = [str(c) for c in result.get("risk_flags") or []]
    order += [c for c in graded if str(graded[c].get("status")) in ("fail", "warning")]
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for code in order:
        if code in seen:
            continue
        seen.add(code)
        check = graded.get(code) or {}
        text = str(check.get("details") or "").strip()
        reason = REASONS.get(code) or text or f"Unmapped check {code}"
        if text and code in REASONS:
            reason = f"{reason} ({text})"
        rows.append((reason, str(check.get("status") or "flag")))
    return rows


def link(result: dict, address: str) -> tuple[str, str]:
    """Exactly one link: DexScreener when the scan carries it, else the explorer."""
    url = str(field(result, "dex_screener_url", default="") or "")
    if "dexscreener" in url:
        return "DexScreener", url
    base = {"solana": "https://solscan.io/token/", "bsc": "https://bscscan.com/token/"}.get(
        str(result.get("chain", "")).lower(), "https://etherscan.io/token/"
    )
    return "Explorer", url or f"{base}{address}"
