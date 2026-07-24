"""
RMI Bulletin Board Management System.
Split into: models, sanitizer, manager, badges.
"""

from .badges import (
    BADGE_TIERS,
    BADGES,
    X402_BB_POST_PRICE,
    award_badge,
    get_user_badges,
    get_user_reputation,
    ingest_post_to_rag,
    verify_x402_bot,
)
from .manager import BulletinBoardManager
from .models import Comment, Post, PostCategory, PostStatus, Priority, TargetAudience
from .sanitizer import ContentSanitizer

__all__ = [
    "BADGES",
    "BADGE_TIERS",
    "X402_BB_POST_PRICE",
    "BulletinBoardManager",
    "Comment",
    "ContentSanitizer",
    "Post",
    "PostCategory",
    "PostStatus",
    "Priority",
    "TargetAudience",
    "award_badge",
    "get_user_badges",
    "get_user_reputation",
    "ingest_post_to_rag",
    "verify_x402_bot",
]
