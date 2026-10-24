"""Minimal solid-colour PNG encoder (zlib + struct, no image dependency)."""

from __future__ import annotations

import struct
import zlib

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """Encode a ``size``x``size`` RGB square as a valid PNG."""
    r, g, b = (max(0, min(255, int(channel))) for channel in rgb)
    pixel_row = b"\x00" + bytes((r, g, b)) * size
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        _PNG_MAGIC
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(pixel_row * size, 9))
        + _chunk(b"IEND", b"")
    )
