"""
RMI Bulletin Board API Router
===============================
Full REST API for bulletin board management.

Public endpoints (no auth):
  GET /api/v1/bulletin-board/posts          - List published posts
  GET /api/v1/bulletin-board/posts/{slug}  - Get single post by slug
  GET /api/v1/bulletin-board/categories    - List categories

Admin endpoints (require admin session):
  POST /admin/bulletin/posts           - Create post
  PUT /admin/bulletin/posts/{id}       - Update post
  DELETE /admin/bulletin/posts/{id}    - Delete (archive) post
  GET /admin/bulletin/posts            - List all posts (admin view)
  POST /admin/bulletin/posts/{id}/publish   - Publish post
  POST /admin/bulletin/posts/{id}/unpublish - Unpublish post
  POST /admin/bulletin/posts/{id}/pin       - Pin/unpin post
  GET /admin/bulletin/stats           - Board statistics
  GET /admin/bulletin/scheduled       - List scheduled posts
  POST /admin/bulletin/process-scheduled - Process scheduled posts
  POST /admin/bulletin/process-expired - Archive expired posts

Architecture (split 2026-08-07 — was a single 785-line module):
  - router.py    — APIRouter definition (prefix /api/v1/bulletin-board)
  - models.py    — PublicPostRequest / CreatePostRequest / UpdatePostRequest / CommentRequest
  - public.py    — public post listing, categories, pinned posts (no auth)
  - admin.py     — admin CRUD, publish/pin/process endpoints + comment moderation
  - social.py    — badges, leaderboard, x402 bot posting endpoints

Backward-compatible re-export layer. Mounted path unchanged
(/api/v1/bulletin-board). Existing imports keep working:
    from app.routers.bulletin_board import router
"""

# Import submodules to register routes on the shared router.
# Order matters for path resolution: public → admin → social (matches the
# original single-module registration order).
from app.routers.bulletin_board import admin, public, social  # noqa: F401

# Re-export request models for backward compatibility
from app.routers.bulletin_board.models import (
    CommentRequest,
    CreatePostRequest,
    PublicPostRequest,
    UpdatePostRequest,
)

# Re-export the combined router for app.mount()
from app.routers.bulletin_board.router import router

__all__ = [
    "CommentRequest",
    "CreatePostRequest",
    "PublicPostRequest",
    "UpdatePostRequest",
    "router",
]
