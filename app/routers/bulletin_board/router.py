"""Bulletin board router definition.

Single ``APIRouter`` that endpoint submodules (``public`` / ``admin`` /
``social``) register handlers on. Re-exported by the package ``__init__`` so
``app.mount`` keeps mounting the same router at ``/api/v1/bulletin-board``.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/bulletin-board", tags=["bulletin-board"])
