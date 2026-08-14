"""OCR text + layout extraction for scam documents, airdrop forms, TOS pages, and screenshots.

Engine strategy
---------------
* Default: **RapidOCR** (``rapidocr-onnxruntime``) — a lightweight ONNX drop-in for
  PaddleOCR's PP-OCRv4 models. CPU-only, ~15 MB wheel, no PaddlePaddle dependency.
  As of ``rapidocr-onnxruntime==1.4.4`` the PP-OCRv4 detection / recognition / cls
  ONNX models ship inside the wheel (``rapidocr_onnxruntime/models/``) — no
  first-use download. Some other builds auto-download models to a cache dir on
  first engine construction.
* Fallback: **PaddleOCR** (``paddleocr`` + ``paddlepaddle``) — heavier (~500 MB),
  used only when RapidOCR is not installed. Same PP-OCRv4-family models.
* If neither engine is installed: ``OcrNotAvailable`` is raised with install
  instructions. Importing this module never fails (engine imports are lazy).

Concurrency
-----------
``extract_text`` is CPU-bound (ONNX inference) and synchronous. Callers in async
contexts should run it via ``asyncio.to_thread``. Engine construction is guarded
by a module-level lock; the cached engine is shared across calls.

Example
-------
>>> result = extract_text("/tmp/scam_form.png")
>>> result.text          # plain concatenated text
>>> result.lines[0]      # OcrLine(text=..., confidence=..., bbox=((x,y)*4))
>>> result.to_dict()     # JSON-friendly {text, lines: [{text, confidence, bbox}], engine}
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

__all__ = [
    "OcrLine",
    "OcrNotAvailable",
    "OcrResult",
    "extract_text",
]

# Input can be a filesystem path, raw image bytes, or a decoded BGR/RGB ndarray.
# (numpy/Path are TYPE_CHECKING-only: annotations defer under `from __future__ import annotations`.)
INSTALL_HINT = (
    "pip install rapidocr-onnxruntime   # light (~15 MB, ONNX, CPU-only, default)\n"
    "pip install paddleocr paddlepaddle  # heavier (~500 MB) fallback"
)


class OcrNotAvailable(RuntimeError):
    """Raised when no OCR engine (RapidOCR or PaddleOCR) is installed."""

    def __init__(self) -> None:
        super().__init__("No OCR engine available. Install one with:\n" + INSTALL_HINT)


@dataclass(frozen=True)
class OcrLine:
    """A single detected text line with confidence and quad bounding box.

    ``bbox`` is an axis-agnostic quad of 4 ``(x, y)`` points (top-left, top-right,
    bottom-right, bottom-left) in the original image's pixel space, normalized to
    plain Python ints for JSON safety.
    """

    text: str
    confidence: float
    bbox: tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "bbox": [[x, y] for x, y in self.bbox],
        }


@dataclass(frozen=True)
class OcrResult:
    """Structured OCR output: full text plus per-line layout."""

    text: str
    lines: tuple[OcrLine, ...]
    engine: str  # "rapidocr" | "paddleocr"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "lines": [line.to_dict() for line in self.lines],
            "engine": self.engine,
        }


# ── Engine cache (module-level singleton) ─────────────────────────────────────
_ENGINE: Any | None = None
_ENGINE_NAME: str | None = None
_ENGINE_LOCK = threading.Lock()


def _normalize_int(value: Any) -> int:
    """Convert numpy ints/floats (and python scalars) to a safe python int."""
    return round(float(value))


def _normalize_bbox(raw_box: Any) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Normalize a 4-point quad (any numpy/python shape) into 4 (x, y) int tuples."""
    points = tuple((_normalize_int(p[0]), _normalize_int(p[1])) for p in raw_box)
    if len(points) != 4:
        raise ValueError(f"OCR bbox must have 4 corner points, got {len(points)}")
    return points


def _lines_from_rapidocr(result: Any) -> tuple[OcrLine, ...]:
    """Normalize RapidOCR output: list of [box, text, score] entries."""
    lines: list[OcrLine] = []
    for entry in result or []:
        if not entry or len(entry) < 3:
            continue
        box, text, score = entry[0], entry[1], entry[2]
        lines.append(
            OcrLine(
                text=str(text),
                confidence=float(score),
                bbox=_normalize_bbox(box),
            )
        )
    return tuple(lines)


def _lines_from_paddleocr(result: Any) -> tuple[OcrLine, ...]:
    """Normalize PaddleOCR output: list of [box, (text, score)] entries."""
    lines: list[OcrLine] = []
    for entry in result or []:
        if not entry or len(entry) < 2:
            continue
        box, rec = entry[0], entry[1]
        try:
            text, score = rec[0], rec[1]
        except (TypeError, IndexError):
            continue
        lines.append(
            OcrLine(
                text=str(text),
                confidence=float(score),
                bbox=_normalize_bbox(box),
            )
        )
    return tuple(lines)


def _load_engine() -> tuple[Any, str]:
    """Return the cached OCR engine, lazily creating it under a lock.

    Tries RapidOCR first (light, default), falls back to PaddleOCR.
    Raises ``OcrNotAvailable`` if neither is installed.
    """
    global _ENGINE, _ENGINE_NAME
    if _ENGINE is not None:
        return _ENGINE, _ENGINE_NAME or "rapidocr"

    with _ENGINE_LOCK:
        if _ENGINE is not None:  # re-check under lock (double-checked locking)
            return _ENGINE, _ENGINE_NAME or "rapidocr"

        try:
            from rapidocr_onnxruntime import RapidOCR

            _ENGINE = RapidOCR()
            _ENGINE_NAME = "rapidocr"
        except ImportError:
            try:
                from paddleocr import PaddleOCR

                _ENGINE = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
                _ENGINE_NAME = "paddleocr"
            except ImportError:
                raise OcrNotAvailable() from None

        return _ENGINE, _ENGINE_NAME or "rapidocr"


def extract_text(image_source: str | Path | bytes | np.ndarray) -> OcrResult:
    """Extract text + layout from an image using the cached OCR engine.

    Args:
        image_source: path to an image file, raw image ``bytes``, or a decoded
            ``np.ndarray`` (BGR or RGB). PIL images can be passed as
            ``np.asarray(pil_image)``.

    Returns:
        ``OcrResult`` with plain concatenated ``text`` and per-line layout.

    Raises:
        OcrNotAvailable: no OCR engine installed (see install hint in the
            exception message).
        ValueError: invalid/unreadable image input.
    """
    engine, engine_name = _load_engine()
    try:
        result, _elapse = engine(image_source)  # (lines, timings) | (None, None)
    except Exception as exc:  # engine-specific decode/inference errors
        raise ValueError(f"OCR failed on image input: {exc}") from exc

    if not result:
        return OcrResult(text="", lines=(), engine=engine_name)

    lines = _lines_from_rapidocr(result) if engine_name == "rapidocr" else _lines_from_paddleocr(result)
    text = "\n".join(line.text for line in lines).strip()
    return OcrResult(text=text, lines=lines, engine=engine_name)
