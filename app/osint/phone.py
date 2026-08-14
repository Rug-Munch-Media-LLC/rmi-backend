"""Phone number OSINT — validation, carrier, region, timezone, line-type risk.

Contract (see ``app.osint``): ``lookup_phone(phone, *, timeout=30) -> OsintReport``.

Uses google's libphonenumber Python port (``phonenumbers``) — pure Python,
no network calls, so ``timeout`` is accepted for interface parity only.

Error handling:
  * Missing dependency  -> raises :class:`OsintNotAvailable`.
  * Unparseable input   -> does NOT raise: returns a report with a single
    low-confidence "invalid" finding plus an entry in ``report.errors``.
  * Parsed but invalid  -> returns findings with the validity status.

Scam-risk flag: VOIP and toll-free line types are surfaced as a low-confidence
risk finding (both are common vectors for spoofed/scam calling).
"""

from __future__ import annotations

try:
    import phonenumbers
    from phonenumbers import PhoneNumberType, carrier, geocoder, timezone
except ImportError as exc:  # pragma: no cover
    from app.osint import OsintNotAvailable

    raise OsintNotAvailable("phonenumbers is not installed; run: pip install phonenumbers") from exc

from app.osint import OsintFinding, OsintReport

__all__ = ["lookup_phone"]

# Human-readable labels for libphonenumber's PhoneNumberType enum values.
_LINE_TYPE_NAMES: dict[int, str] = {
    PhoneNumberType.FIXED_LINE: "fixed line",
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed line or mobile",
    PhoneNumberType.TOLL_FREE: "toll-free",
    PhoneNumberType.PREMIUM_RATE: "premium rate",
    PhoneNumberType.SHARED_COST: "shared cost",
    PhoneNumberType.VOIP: "voip",
    PhoneNumberType.PERSONAL_NUMBER: "personal number",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.UAN: "universal access number",
    PhoneNumberType.VOICEMAIL: "voicemail",
    PhoneNumberType.UNKNOWN: "unknown",
}

# Line types that are common scam-calling vectors (spoofing / burner lines).
_SCAM_RISK_TYPES: frozenset[int] = frozenset({PhoneNumberType.VOIP, PhoneNumberType.TOLL_FREE})


def lookup_phone(phone: str, *, timeout: int = 30) -> OsintReport:
    """Look up validation, carrier, region, timezone, and line-type risk for a phone number.

    Args:
        phone: E.164 or national-format phone number (e.g. "+14155552671").
        timeout: Accepted for interface parity with other OSINT modules; this
            module performs no network I/O, so the value is unused.

    Returns:
        An :class:`OsintReport` with findings in the ``validation``, ``region``,
        ``carrier``, and (optionally) ``risk`` categories, all sourced from
        ``libphonenumber``.

    Raises:
        OsintNotAvailable: if the ``phonenumbers`` package is not installed.
    """
    report = OsintReport(target=phone, target_type="phone")

    try:
        number = phonenumbers.parse(phone, None)
    except phonenumbers.NumberParseException as exc:
        report.errors.append(f"unparseable phone number: {exc}")
        report.findings.append(
            OsintFinding(
                source="libphonenumber",
                category="validation",
                summary="Invalid phone number: could not be parsed",
                confidence="low",
                data={"raw": phone, "reason": str(exc)},
            )
        )
        return report

    valid = phonenumbers.is_valid_number(number)
    possible = phonenumbers.is_possible_number(number)
    if not valid:
        report.errors.append("phone number failed libphonenumber validity check")

    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    international = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    line_type = phonenumbers.number_type(number)
    line_type_name = _LINE_TYPE_NAMES.get(line_type, "unknown")

    # -- validation -------------------------------------------------------
    if valid:
        summary = f"Valid phone number: {international}"
        confidence = "high"
    elif possible:
        summary = f"Possible but not fully valid phone number: {international}"
        confidence = "medium"
    else:
        summary = f"Invalid phone number: {international}"
        confidence = "low"
    report.findings.append(
        OsintFinding(
            source="libphonenumber",
            category="validation",
            summary=summary,
            confidence=confidence,
            data={
                "e164": e164,
                "international": international,
                "country_code": number.country_code,
                "national_number": str(number.national_number),
                "valid": valid,
                "possible": possible,
                "line_type": line_type_name,
            },
        )
    )

    # -- region / timezone -------------------------------------------------
    region_code = phonenumbers.region_code_for_number(number) or ""
    region_description = geocoder.description_for_number(number, "en") or ""
    timezones = list(timezone.time_zones_for_number(number))
    if region_code:
        summary = f"Region: {region_description or region_code} ({region_code})"
        confidence = "high"
    else:
        summary = "Region: unknown"
        confidence = "low"
    report.findings.append(
        OsintFinding(
            source="libphonenumber",
            category="region",
            summary=summary,
            confidence=confidence,
            data={
                "region_code": region_code,
                "region_description": region_description,
                "timezones": timezones,
            },
        )
    )

    # -- carrier ------------------------------------------------------------
    carrier_name = carrier.name_for_number(number, "en") or ""
    if carrier_name:
        report.findings.append(
            OsintFinding(
                source="libphonenumber",
                category="carrier",
                summary=f"Carrier: {carrier_name}",
                confidence="high",
                data={"carrier": carrier_name},
            )
        )

    # -- scam-risk flag (VOIP / toll-free) ----------------------------------
    if line_type in _SCAM_RISK_TYPES:
        report.findings.append(
            OsintFinding(
                source="libphonenumber",
                category="risk",
                summary=(
                    f"{line_type_name.title()} number — common scam-calling vector; "
                    "verify the caller's identity independently"
                ),
                confidence="low",
                data={"line_type": line_type_name, "scam_risk": "voip_or_toll_free"},
            )
        )

    return report
