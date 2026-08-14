"""Unit tests for ``app.osint.phone`` — pure libphonenumber, no network.

The module raises :class:`OsintNotAvailable` at import when the optional
``phonenumbers`` dependency is missing, so the whole file self-skips via
``pytest.importorskip``. No network I/O is ever performed by this module.

Covers valid/possible/invalid numbers, unparseable input, region/timezone
lookup, carrier findings, and the VOIP / toll-free scam-risk flag (with
``number_type`` monkeypatched so the risk path is deterministic).
"""

from __future__ import annotations

import pytest

pytest.importorskip("phonenumbers")

import phonenumbers
from phonenumbers import PhoneNumberType

from app.osint.phone import lookup_phone


def test_lookup_phone_valid_us_number() -> None:
    report = lookup_phone("+14155552671", timeout=10)

    assert report.target == "+14155552671"
    assert report.target_type == "phone"
    assert report.errors == []

    validation = next(f for f in report.findings if f.category == "validation")
    assert validation.confidence == "high"
    assert "Valid phone number" in validation.summary
    assert validation.data["e164"] == "+14155552671"
    assert validation.data["country_code"] == 1
    assert validation.data["valid"] is True

    region = next(f for f in report.findings if f.category == "region")
    assert region.data["region_code"] == "US"
    assert region.data["timezones"]


def test_lookup_phone_france_number_with_carrier() -> None:
    report = lookup_phone("+33612345678", timeout=10)
    assert report.errors == []
    carrier = [f for f in report.findings if f.category == "carrier"]
    assert carrier, "expected a carrier finding for a number with carrier data"
    assert carrier[0].data["carrier"]  # e.g. "SFR"


def test_lookup_phone_unparseable_input() -> None:
    report = lookup_phone("not a phone", timeout=10)
    assert report.errors, "unparseable input must produce an error entry"
    invalid = [f for f in report.findings if f.category == "validation"]
    assert invalid
    assert invalid[0].confidence == "low"
    assert "could not be parsed" in invalid[0].summary


def test_lookup_phone_parsed_but_invalid() -> None:
    report = lookup_phone("+123456", timeout=10)
    validation = next(f for f in report.findings if f.category == "validation")
    assert validation.data["valid"] is False
    assert report.errors  # validity failure surfaced as an error


def test_lookup_phone_voip_risk_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phonenumbers, "number_type", lambda number: PhoneNumberType.VOIP)
    report = lookup_phone("+14155552671", timeout=10)
    risk = next(f for f in report.findings if f.category == "risk")
    assert risk.data["scam_risk"] == "voip_or_toll_free"
    assert "common scam-calling vector" in risk.summary
    assert risk.confidence == "low"


def test_lookup_phone_toll_free_risk_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phonenumbers, "number_type", lambda number: PhoneNumberType.TOLL_FREE)
    report = lookup_phone("+14155552671", timeout=10)
    assert any(f.category == "risk" for f in report.findings)


def test_lookup_phone_carrier_monkeypatched(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.osint.phone import carrier as phone_carrier

    monkeypatch.setattr(phone_carrier, "name_for_number", lambda number, lang: "Test Carrier")
    report = lookup_phone("+14155552671", timeout=10)
    carrier = next(f for f in report.findings if f.category == "carrier")
    assert carrier.data["carrier"] == "Test Carrier"
    assert carrier.confidence == "high"


def test_lookup_phone_no_risk_for_mobile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(phonenumbers, "number_type", lambda number: PhoneNumberType.MOBILE)
    report = lookup_phone("+14155552671", timeout=10)
    assert not any(f.category == "risk" for f in report.findings)
