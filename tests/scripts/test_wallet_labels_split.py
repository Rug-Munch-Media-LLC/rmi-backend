"""Focused tests for the split wallet-label ingestion package."""

from scripts.wallet_labels_ingest.row_builders import LabelFields, ScamFields, label_row, scam_row


def test_label_row_preserves_shape() -> None:
    row = label_row(LabelFields("0xABC", "ethereum", "Known", "entity", "tag", "test"))
    assert row == {
        "address": "0xabc",
        "chain_id": "ethereum",
        "label_name": "Known",
        "label_category": "entity",
        "label_subtype": "tag",
        "source": "test",
        "is_sanctioned": False,
    }


def test_scam_row_preserves_shape() -> None:
    row = scam_row(ScamFields("0xABC", "ethereum", "test", "phish", 0.9, "evidence"))
    assert row["address"] == "0xabc"
    assert row["threat_type"] == "phish"
    assert row["confidence"] == 0.9


def test_public_surface_is_importable() -> None:
    from scripts.wallet_labels_ingest import SOURCE_REGISTRY, main, make_label_id

    assert "builtin_contracts" in SOURCE_REGISTRY
    assert callable(main)
    assert len(make_label_id("0xABC", "source", "ethereum")) == 16
