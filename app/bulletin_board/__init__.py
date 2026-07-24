"""
RMI Bulletin Board Management System.
Split into: models, sanitizer, manager, badges.
"""

from .models import Comment, Post, PostCategory, PostStatus, Priority, TargetAudience
from .sanitizer import ContentSanitizer
from .manager import BulletinBoardManager
from .badges import (
    BADGES,
    BADGE_TIERS,
    X402_BB_POST_PRICE,
    award_badge,
    get_user_badges,
    get_user_reputation,
    ingest_post_to_rag,
    verify_x402_bot,
)

__all__ = [
    "Post",
    "Comment",
    "PostStatus",
    "PostCategory",
    "TargetAudience",
    "Priority",
    "ContentSanitizer",
    "BulletinBoardManager",
    "BADGES",
    "BADGE_TIERS",
    "X402_BB_POST_PRICE",
    "award_badge",
    "get_user_badges",
    "get_user_reputation",
    "ingest_post_to_rag",
    "verify_x402_bot",
]
