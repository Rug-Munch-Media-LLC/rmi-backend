"""KOL (Key Opinion Leader) wallet attribution models.

One row per (kol_id, chain, address) link with a confidence tier and
evidence string, per docs/KOL-TRACKING-DESIGN.md §2.5.

Confidence tiers map 1:1 to the label store:
    deterministic > strong_heuristic > probabilistic > crowdsourced_unverified
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — SQLAlchemy Mapped[] needs runtime resolution

from sqlalchemy import BigInteger, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class KOLWallet(Base):
    """A wallet address attributed to a KOL (X account)."""

    __tablename__ = "kol_wallet"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # kol_id = handle.lower(); FK to kol_account (kol_account table not yet
    # migrated — build order item #1 in KOL-TRACKING-DESIGN.md §7).
    kol_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chain: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str] = mapped_column(String(128), nullable=False)
    # self_disclosed | public_tagging | airdrop_poap_ens | onchain_activity | manual
    attribution_method: Mapped[str] = mapped_column(String(32), nullable=False, default="self_disclosed")
    # deterministic | strong_heuristic | probabilistic | crowdsourced_unverified
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="probabilistic")
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    # active | revoked | unverified
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("kol_id", "chain", "address", name="uq_kol_wallet_kol_chain_addr"),
        Index("ix_kol_wallet_kol_id", "kol_id"),
        Index("ix_kol_wallet_address", "address"),
    )
