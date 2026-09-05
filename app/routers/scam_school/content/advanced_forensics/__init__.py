"""Scam School — Advanced Course: "Blockchain Forensics & Intelligence".

12 modules covering on-chain fund tracing, entity attribution, mixer detection,
MEV forensics, flash loan analysis, bridge exploits, governance attacks,
contract auditing, DeFi risk assessment, dark web intelligence, and regulatory landscape.
"""

from __future__ import annotations

from app.routers.scam_school.content.advanced_forensics.helpers import COURSE_META
from app.routers.scam_school.content.advanced_forensics.modules import MODULES

advanced_forensics = {**COURSE_META, "modules": MODULES}

__all__ = ["advanced_forensics"]
