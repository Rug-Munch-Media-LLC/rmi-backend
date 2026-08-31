"""Tests for the public media router (isolated, no app startup).

The router is mounted on a bare FastAPI app. Image resolution is exercised
against a temp whitelisted dir (env ``RMI_MEDIA_IMAGE_DIRS``) so the tests
never touch real fleet data dirs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.media import router

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

app = FastAPI()
app.include_router(router)


@pytest.fixture
def image_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "shots"
    d.mkdir()
    (d / "abc.png").write_bytes(b"\x89PNG fake")
    (d / "pic.jpg").write_bytes(b"\xff\xd8 fake")
    (d / "notes.txt").write_text("not an image")
    from app.routers import media as media_mod

    media_mod._IMAGE_DIRS = (d,)
    return d


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def test_serves_image_from_whitelisted_dir(client: TestClient, image_dir: Path) -> None:
    resp = client.get("/api/v1/media/image/abc.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"].startswith("public")


def test_missing_image_404(client: TestClient, image_dir: Path) -> None:
    assert client.get("/api/v1/media/image/nope.png").status_code == 404


def test_traversal_rejected(client: TestClient, image_dir: Path) -> None:
    # Path separators / traversal never resolve (basename-only contract).
    assert client.get("/api/v1/media/image/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/api/v1/media/image/..").status_code == 404


def test_non_image_extension_404(client: TestClient, image_dir: Path) -> None:
    assert client.get("/api/v1/media/image/notes.txt").status_code == 404


def test_public_image_url_derivation() -> None:
    from app.utils.media_urls import public_image_url

    assert public_image_url("/path/to/shill_shots/abc.png") == "/api/v1/media/image/abc.png"
    assert public_image_url("abc.webp") == "/api/v1/media/image/abc.webp"
    assert public_image_url("../../etc/passwd") == ""
    assert public_image_url("x.txt") == ""
    assert public_image_url("") == ""
    assert public_image_url(None) == ""
