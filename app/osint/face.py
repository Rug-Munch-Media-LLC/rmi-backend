"""Facial-recognition OSINT — face detection, embeddings, reverse-image search.

Contract (see :mod:`app.osint`): ``lookup_face(image_path, *, timeout=120) ->
OsintReport``.

Runs four face-detection/recognition engines in strict fallback order — the
first engine that finds at least one face wins, and each engine failure
(missing package, model-download error, zero faces detected) falls through to
the next:

1. ``insightface``      — ArcFace ``buffalo_l`` ONNX models (auto-downloaded on
   first use; requires ``onnxruntime``), 512-d embeddings.
2. ``deepface``         — Facenet512 embeddings (VGG-Face selectable), 512-d.
3. ``face_recognition`` — dlib-based HOG detector, 128-d encodings.
4. ``compreface``       — self-hosted CompreFace (remote REST API, see
   ``app.osint.compreface_client`` + ``infra/compreface/``). Detection +
   subject matching against a collection; matched faces carry ``subject`` /
   ``similarity`` in the finding data. Only enabled when ``COMPREFACE_URL``
   and ``COMPREFACE_API_KEY`` are set.

The first three are heavy optional dependencies; ``compreface`` needs only the
(already-shipped) httpx client plus a reachable CompreFace server. The module
imports cleanly without any of them and raises :class:`OsintNotAvailable` only
when a lookup is attempted with none installed. Models auto-download on first
use and can take minutes on a cold start; ``timeout`` is accepted for
interface parity with the other OSINT modules but engine calls are synchronous
CPU/network work and may exceed it on first use.

Reverse-image search: the report always carries a ``reverse_search`` finding
with ready-to-open URLs for Google Lens, Google search-by-image, Bing Visual
Search, and Yandex. Those engines require the image to be reachable at a
public URL — pass one (e.g. ``https://example.com/pic.jpg``) to get clickable
links; for a local file the module reports that the image must be hosted
first. Detection itself requires a local file path; URL inputs skip the face
engines (documented limitation).

Error handling:
  * No engine installed          -> raises :class:`OsintNotAvailable`.
  * Missing/undecodable image    -> raises :class:`OsintError`.
  * Engines installed, no faces  -> report with a single low-confidence
    "no face detected" finding plus per-engine notes in ``report.errors``.
  * Engine exceptions            -> logged, appended to ``report.errors``, and
    the next engine is tried — a lookup never raises for an engine failure.

Usage::

    from app.osint.face import lookup_face

    report = lookup_face("/tmp/face.jpg", timeout=120)
    for finding in report.findings:
        print(finding.source, finding.category, finding.summary, finding.confidence)
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import urllib.parse
from pathlib import Path
from typing import Any, Final

import numpy as np
from PIL import Image

from app.osint import OsintError, OsintFinding, OsintNotAvailable, OsintReport

logger = logging.getLogger(__name__)

__all__ = ["lookup_face"]

# ─── Tunables ────────────────────────────────────────────────────────

#: Engine fallback order — the first engine that detects >= 1 face wins.
#: ``compreface`` is the remote REST tier: it needs a configured, reachable
#: CompreFace server (``COMPREFACE_URL`` + ``COMPREFACE_API_KEY``) and is the
#: only engine that can also *identify* subjects, not just detect faces.
#: ``opencv`` is the cheap cv2 fallback (committed models, no package install /
#: model download): it runs only when the heavier engines are missing, found
#: zero faces, or CompreFace is unconfigured/unreachable.
_ENGINE_ORDER: Final[tuple[str, ...]] = (
    "insightface",
    "deepface",
    "face_recognition",
    "compreface",
    "opencv",
)

#: Committed Haar cascade for the cheap cv2 fallback. opencv-python 5.x ships
#: no cascade XMLs in ``cv2.data.haarcascades`` AND drops the classic
#: ``cv2.CascadeClassifier`` API, so the module tracks both the Haar XML
#: (used by ``cv2.CascadeClassifier`` on classic OpenCV 4.x installs) and the
#: YuNet ONNX model (used by ``cv2.FaceDetectorYN`` on OpenCV 5.x). Both live
#: in-repo at ``app/osint/models/`` and are shared with the Telegram bot
#: handler (``handlers/osint.py``).
HAARCASCADE_MODEL_PATH: Final[Path] = Path(__file__).resolve().parent / "models" / "haarcascade_frontalface_default.xml"
YUNET_MODEL_PATH: Final[Path] = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"

#: deepface recognition model (Facenet512 = 512-d; VGG-Face = 2622-d).
_DEEPFACE_MODEL: Final[str] = "Facenet512"

#: How many leading embedding dimensions are stored in the report (full vector
#: is never dumped — reports stay small; a sha256 fingerprint enables dedup).
_EMBEDDING_PREVIEW_DIMS: Final[int] = 32
_EMBEDDING_ROUND: Final[int] = 6

#: Face-recognition landmark features kept in the report (bounded size).
_FR_LANDMARK_FEATURES: Final[tuple[str, ...]] = (
    "left_eye",
    "right_eye",
    "nose_tip",
    "top_lip",
    "bottom_lip",
)

#: Reverse-image-search endpoints. Each takes the public image URL in the
#: ``{url}`` slot (percent-encoded when substituted).
_REVERSE_SEARCH_TEMPLATES: Final[dict[str, str]] = {
    "google_lens": "https://lens.google.com/uploadbyurl?url={url}",
    "google_search": "https://www.google.com/searchbyimage?image_url={url}",
    "bing_visual_search": "https://www.bing.com/images/searchbyimage?cbir=sbi&imgurl={url}",
    "yandex": "https://yandex.com/images/search?rpt=imageview&url={url}",
}


# ─── Availability helpers ────────────────────────────────────────────


def _engine_available(name: str) -> bool:
    """Return True if the (optional) engine is usable.

    ``opencv`` is special: it needs only ``cv2`` plus at least one committed
    model file (YuNet ONNX or Haar XML) — no pip install, no model download.
    ``compreface`` is a remote service: availability is a *configuration*
    check (``COMPREFACE_URL`` + ``COMPREFACE_API_KEY`` set), not an import —
    reachability is only known at call time and is handled gracefully there
    (see :mod:`app.osint.compreface_client`).
    """
    if name == "opencv":
        return importlib.util.find_spec("cv2") is not None and (
            YUNET_MODEL_PATH.is_file() or HAARCASCADE_MODEL_PATH.is_file()
        )
    if name == "compreface":
        try:
            from app.osint.compreface_client import is_configured
        except ImportError as exc:  # pragma: no cover — module ships in-app
            logger.debug("compreface client unavailable: %s", exc)
            return False
        return is_configured()
    return importlib.util.find_spec(name) is not None


def _load_rgb(image_path: str) -> Any | None:
    """Load an image file as an RGB numpy array, or None if it cannot be decoded."""
    try:
        with Image.open(image_path) as im:
            return np.asarray(im.convert("RGB"))
    except Exception as exc:  # corrupt / unsupported file — engine falls through
        logger.warning("could not decode image %s: %s", image_path, exc)
        return None


# ─── Payload builders ────────────────────────────────────────────────


def _bbox_payload(x1: float, y1: float, x2: float, y2: float) -> dict[str, int]:
    """Normalize a bounding box into a bounded ``{x1, y1, x2, y2, w, h}`` payload."""
    left, top = round(x1), round(y1)
    right, bottom = round(x2), round(y2)
    return {
        "x1": left,
        "y1": top,
        "x2": right,
        "y2": bottom,
        "w": max(0, right - left),
        "h": max(0, bottom - top),
    }


def _embedding_payload(embedding: Any) -> dict[str, Any]:
    """Shrink a raw embedding ndarray into a bounded, hashable report payload.

    Stores a short numeric preview plus a deterministic sha256 of the full
    float32 vector (for dedup/comparison) — never the full embedding.
    """
    arr = np.asarray(embedding, dtype="<f4")
    if arr.size == 0:
        return {"preview": [], "dim": 0, "sha256": "", "truncated": False, "preview_dims": _EMBEDDING_PREVIEW_DIMS}
    digest = hashlib.sha256(arr.tobytes()).hexdigest()
    preview = [round(float(v), _EMBEDDING_ROUND) for v in arr.ravel()[:_EMBEDDING_PREVIEW_DIMS].tolist()]
    return {
        "preview": preview,
        "dim": int(arr.shape[0]),
        "sha256": digest,
        "truncated": bool(arr.shape[0] > _EMBEDDING_PREVIEW_DIMS),
        "preview_dims": _EMBEDDING_PREVIEW_DIMS,
    }


def _landmarks_payload(points: Any, max_points: int = 5) -> list[dict[str, Any]]:
    """Convert an Nx2 landmark array (insightface ``kps``) into ``[{x, y}]``."""
    arr = np.asarray(points, dtype="<f8")
    if arr.ndim != 2 or arr.shape[1] != 2:
        return []
    return [{"x": round(float(row[0]), 2), "y": round(float(row[1]), 2)} for row in arr[:max_points]]


def _fr_landmarks_payload(landmarks: Any, max_points: int = 4) -> list[dict[str, Any]]:
    """Convert face_recognition landmark dicts into a bounded feature list."""
    if not landmarks:
        return []
    out: list[dict[str, Any]] = []
    for feature in _FR_LANDMARK_FEATURES:
        pts = landmarks.get(feature) or []
        out.append(
            {
                "name": feature,
                "points": [{"x": round(float(p[0]), 2), "y": round(float(p[1]), 2)} for p in pts[:max_points]],
            }
        )
    return out


def _face_confidence(face: dict[str, Any]) -> str:
    """Map a detector score onto the report confidence scale."""
    score = face.get("det_score")
    if score is not None and float(score) >= 0.8:
        return "high"
    return "medium"


# ─── Engine 1: insightface (ArcFace / Buffalo) ───────────────────────


def _detect_insightface(image_path: str) -> list[dict[str, Any]]:
    """Detect + embed faces with insightface ``buffalo_l`` (512-d embeddings)."""
    try:
        from insightface.app import FaceAnalysis
    except ImportError as exc:  # pragma: no cover — optional dep
        logger.debug("insightface not installed: %s", exc)
        return []
    try:
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
    except Exception as exc:  # model download / onnxruntime init failure
        logger.warning("insightface init failed: %s", exc)
        return []
    img = _load_rgb(image_path)
    if img is None:
        return []
    try:
        # insightface expects BGR (OpenCV convention); copy to get contiguous memory.
        faces = app.get(np.asarray(img)[:, :, ::-1].copy())
    except Exception as exc:
        logger.warning("insightface detection failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for face in faces:
        bbox = np.asarray(getattr(face, "bbox", []), dtype="<f8").ravel()
        if bbox.size < 4:
            continue
        results.append(
            {
                "bbox": _bbox_payload(bbox[0], bbox[1], bbox[2], bbox[3]),
                "embedding": _embedding_payload(getattr(face, "embedding", [])),
                "landmarks": _landmarks_payload(getattr(face, "kps", None)),
                "det_score": float(getattr(face, "det_score", 0.0) or 0.0),
            }
        )
    return results


# ─── Engine 2: deepface (Facenet512) ─────────────────────────────────


def _deepface_embedding(face_crop: Any) -> dict[str, Any]:
    """Embed a single face crop with deepface; ``{}`` on any failure."""
    try:
        from deepface import DeepFace
    except ImportError as exc:  # pragma: no cover — optional dep
        logger.debug("deepface not installed: %s", exc)
        return {}
    try:
        # "skip" avoids re-detection on the already-cropped face (deepface >= 0.0.80).
        reps = DeepFace.represent(
            img_path=face_crop,
            model_name=_DEEPFACE_MODEL,
            detector_backend="skip",
            enforce_detection=False,
        )
    except Exception:
        try:  # older deepface has no "skip" backend — fall back to default detection
            reps = DeepFace.represent(img_path=face_crop, model_name=_DEEPFACE_MODEL, enforce_detection=False)
        except Exception as exc:
            logger.warning("deepface represent failed: %s", exc)
            return {}
    if not reps:
        return {}
    return _embedding_payload(reps[0].get("embedding"))


def _detect_deepface(image_path: str) -> list[dict[str, Any]]:
    """Detect + embed faces with deepface (``extract_faces`` + ``represent``)."""
    try:
        from deepface import DeepFace
    except ImportError as exc:  # pragma: no cover — optional dep
        logger.debug("deepface not installed: %s", exc)
        return []
    try:
        extracted = DeepFace.extract_faces(
            img_path=image_path,
            detector_backend="opencv",  # lightweight, no extra model download
            enforce_detection=False,
        )
    except Exception as exc:
        logger.warning("deepface extract_faces failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for item in extracted:
        area = item.get("facial_area") or {}
        x, y, w, h = area.get("x", 0), area.get("y", 0), area.get("w", 0), area.get("h", 0)
        if not w or not h:
            continue
        landmarks: list[dict[str, Any]] = []
        for key in ("left_eye", "right_eye"):
            eye = area.get(key)
            if eye:
                landmarks.append(
                    {"name": key, "points": [{"x": round(float(eye[0]), 2), "y": round(float(eye[1]), 2)}]}
                )
        results.append(
            {
                "bbox": _bbox_payload(x, y, x + w, y + h),
                "embedding": _deepface_embedding(item.get("face")),
                "landmarks": landmarks,
                "det_score": float(item.get("confidence") or 0.0),
            }
        )
    return results


# ─── Engine 3: face_recognition (dlib) ───────────────────────────────


def _detect_face_recognition(image_path: str) -> list[dict[str, Any]]:
    """Detect + encode faces with face_recognition (128-d dlib encodings)."""
    try:
        import face_recognition
    except ImportError as exc:  # pragma: no cover — optional dep
        logger.debug("face_recognition not installed: %s", exc)
        return []
    try:
        img = face_recognition.load_image_file(image_path)
    except Exception as exc:
        logger.warning("face_recognition could not load %s: %s", image_path, exc)
        return []
    try:
        locations = face_recognition.face_locations(img)  # (top, right, bottom, left)
        encodings = face_recognition.face_encodings(img, known_face_locations=locations)
        landmark_sets = face_recognition.face_landmarks(img, face_locations=locations)
    except Exception as exc:
        logger.warning("face_recognition detection failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for idx, (top, right, bottom, left) in enumerate(locations):
        emb = encodings[idx] if idx < len(encodings) else None
        landmarks = _fr_landmarks_payload(landmark_sets[idx] if idx < len(landmark_sets) else None)
        results.append(
            {
                "bbox": _bbox_payload(left, top, right, bottom),
                "embedding": _embedding_payload(emb) if emb is not None else {},
                "landmarks": landmarks,
                "det_score": None,
            }
        )
    return results


# ─── Engine 4: compreface (self-hosted remote REST) ──────────────────


def _detect_compreface(image_path: str) -> list[dict[str, Any]]:
    """Detect + match faces via self-hosted CompreFace (``infra/compreface/``).

    This is the only engine that can *identify* a face against a subject
    collection, not just detect it. It runs the recognition endpoint, which
    internally runs the detector and returns every detected face — matched
    faces carry the top ``subject`` + ``similarity`` plus the full
    ``all_subjects`` prediction list; unmatched faces (or an empty collection)
    fall back to bounding-box-only results.

    Raises:
        CompreFaceNotAvailable: when the service is unconfigured or
            unreachable. Deliberately NOT swallowed here — the caller records
            it in ``report.errors`` and falls through to the next engine
            (mirrors how a broken local engine is reported).
    """
    try:
        from app.osint.compreface_client import recognize
    except ImportError as exc:  # pragma: no cover — client module ships in-app
        logger.debug("compreface client unavailable: %s", exc)
        return []

    matches = recognize(image_path, prediction_count=1, det_prob_threshold=0.5)
    results: list[dict[str, Any]] = []
    for match in matches:
        face: dict[str, Any] = {
            "bbox": match.get("bbox") or {},
            "embedding": {},
            "landmarks": [],
            "det_score": match.get("probability"),
        }
        subject = match.get("subject")
        if subject:
            face["subject"] = subject
            face["similarity"] = match.get("similarity")
            face["subjects"] = match.get("all_subjects") or []
        results.append(face)
    return results


# ─── Engine 5: opencv (cv2, cheap fallback) ──────────────────────────


def _detect_opencv(image_path: str) -> list[dict[str, Any]]:
    """Cheap OpenCV face detection using the committed models (no downloads).

    Prefers ``cv2.FaceDetectorYN`` (YuNet ONNX — the only face detector in
    opencv-python 5.x, which dropped ``CascadeClassifier``) and falls back to
    ``cv2.CascadeClassifier`` (Haar XML — classic OpenCV 4.x). Runs only when
    the heavy engines are unavailable or found zero faces. Returns
    bounding-box-only results (no embeddings) so a face count / locations are
    still produced on any machine with ``cv2`` + the committed models.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover — availability checked by caller
        logger.debug("cv2 not installed: %s", exc)
        return []
    img = _load_rgb(image_path)
    if img is None:
        return []

    # Preferred: YuNet (OpenCV 5.x). BGR conversion + contiguous copy for DNN.
    if YUNET_MODEL_PATH.is_file() and hasattr(cv2, "FaceDetectorYN"):
        try:
            detector = cv2.FaceDetectorYN.create(
                str(YUNET_MODEL_PATH),
                "",
                (img.shape[1], img.shape[0]),
                score_threshold=0.9,
                nms_threshold=0.3,
                top_k=5000,
            )
            _ok, faces = detector.detect(np.ascontiguousarray(img[:, :, ::-1]))
            rects: list[tuple[float, float, float, float]] = []
            if faces is not None and len(faces):
                # YuNet rows are [x, y, w, h, landmarks..., score] — convert to
                # (x1, y1, x2, y2) for _bbox_payload.
                rects = [
                    (float(f[0]), float(f[1]), float(f[0]) + float(f[2]), float(f[1]) + float(f[3])) for f in faces
                ]
            if rects:
                return [
                    {
                        "bbox": _bbox_payload(x, y, x2, y2),
                        "embedding": {},
                        "landmarks": [],
                        "det_score": None,
                    }
                    for (x, y, x2, y2) in rects
                ]
        except Exception as exc:
            logger.warning("yunet detection failed, trying haar: %s", exc)

    # Fallback: Haar cascade (classic OpenCV 4.x).
    if HAARCASCADE_MODEL_PATH.is_file() and hasattr(cv2, "CascadeClassifier"):
        try:
            cascade = cv2.CascadeClassifier(str(HAARCASCADE_MODEL_PATH))
            if cascade.empty():
                logger.warning("haarcascade model failed to load: %s", HAARCASCADE_MODEL_PATH)
                return []
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            rects_haar = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
            return [
                {
                    "bbox": _bbox_payload(float(x), float(y), float(x + w), float(y + h)),
                    "embedding": {},
                    "landmarks": [],
                    "det_score": None,
                }
                for (x, y, w, h) in rects_haar
            ]
        except Exception as exc:
            logger.warning("haarcascade detection failed: %s", exc)
            return []

    if not YUNET_MODEL_PATH.is_file() and not HAARCASCADE_MODEL_PATH.is_file():
        logger.warning("no face model committed under app/osint/models/")
    return []


#: Engine dispatch — first entry in _ENGINE_ORDER with a non-empty result wins.
_ENGINES: Final[dict[str, Any]] = {
    "insightface": _detect_insightface,
    "deepface": _detect_deepface,
    "face_recognition": _detect_face_recognition,
    "compreface": _detect_compreface,
    "opencv": _detect_opencv,
}


# ─── Reverse-image search ────────────────────────────────────────────


def _reverse_search_urls(public_url: str) -> dict[str, str]:
    """Build reverse-image-search URLs for a publicly reachable image URL."""
    encoded = urllib.parse.quote(public_url, safe="")
    return {name: template.format(url=encoded) for name, template in _REVERSE_SEARCH_TEMPLATES.items()}


# ─── Public API ──────────────────────────────────────────────────────


def lookup_face(image_path: str, *, timeout: int = 120) -> OsintReport:
    """Detect faces in an image and produce a facial-recognition OSINT report.

    Args:
        image_path: Local path to an image file (JPEG/PNG/WebP). A public
            ``http(s)://`` URL is accepted for reverse-image-search links only;
            face detection requires a local file (documented limitation).
        timeout: Accepted for interface parity with the other OSINT modules.
            Engine calls are synchronous and first-use model downloads can
            exceed this; the value is not currently applied per engine.

    Returns:
        An :class:`OsintReport` with one ``face`` finding per detected face
        (source = winning engine, data includes bbox, truncated/hashed
        embedding, landmarks) plus a ``reverse_search`` finding with
        ready-to-open URLs (or a note that the image must be hosted first).

    Raises:
        OsintNotAvailable: if no face engine is available (none of the
            optional packages installed, no committed opencv model, and no
            CompreFace server configured).
        OsintError: if the local image file does not exist or is undecodable.
    """
    report = OsintReport(target=image_path, target_type="face")

    is_public_url = image_path.startswith(("http://", "https://"))
    if not is_public_url and not Path(image_path).is_file():
        raise OsintError(f"image file not found: {image_path}")

    available = [name for name in _ENGINE_ORDER if _engine_available(name)]
    if not available:
        raise OsintNotAvailable(
            "no face-recognition engine installed; run: pip install insightface deepface face_recognition "
            "(or install opencv-python with the committed Haar cascade model, "
            "or set COMPREFACE_URL + COMPREFACE_API_KEY for a CompreFace server — "
            "see infra/compreface/README.md)"
        )

    engines_tried: list[str] = []
    failures: list[str] = []
    detected: list[tuple[str, dict[str, Any]]] = []

    if is_public_url:
        failures.append("detection requires a local file path; skipping face engines for URL input")
    else:
        for name in _ENGINE_ORDER:
            if not _engine_available(name):
                if name == "opencv":
                    failures.append("opencv: cv2 not installed or no committed face model under app/osint/models/")
                elif name == "compreface":
                    failures.append(
                        "compreface: COMPREFACE_URL / COMPREFACE_API_KEY not set (see infra/compreface/README.md)"
                    )
                else:
                    failures.append(f"{name}: package not installed")
                continue
            engines_tried.append(name)
            try:
                faces = _ENGINES[name](image_path)
            except Exception as exc:  # engine bug — never abort the lookup
                logger.warning("face engine %s raised: %s", name, exc)
                failures.append(f"{name}: {exc}")
                continue
            if faces:
                detected.extend((name, face) for face in faces)
                break
            logger.info("face engine %s found 0 faces; falling through", name)

    # -- face findings ------------------------------------------------------
    # Engine failures are surfaced in report.errors even when a later engine
    # succeeds, matching the per-source degradation pattern of the other OSINT
    # modules (see app/osint/domain.py).
    report.errors.extend(failures)
    if detected:
        for name, face in detected:
            report.findings.append(
                OsintFinding(
                    source=name,
                    category="face",
                    summary="1 face detected",
                    confidence=_face_confidence(face),
                    data=face,
                )
            )
    else:
        report.findings.append(
            OsintFinding(
                source="face_detection",
                category="face",
                summary="No face detected",
                confidence="low",
                data={"engines_tried": engines_tried, "failures": failures},
            )
        )

    # -- reverse-image search ------------------------------------------------
    if is_public_url:
        report.findings.append(
            OsintFinding(
                source="reverse_image_search",
                category="reverse_search",
                summary="Reverse-image search links generated for the public image URL",
                confidence="high",
                data={"urls": _reverse_search_urls(image_path)},
            )
        )
    else:
        report.findings.append(
            OsintFinding(
                source="reverse_image_search",
                category="reverse_search",
                summary="Reverse-image search unavailable: image must be hosted at a public URL first",
                confidence="low",
                data={
                    "urls": {},
                    "requires_public_url": True,
                    "note": (
                        "Google Lens / Bing / Yandex need the image reachable at a public URL. "
                        "Host the file (e.g. S3, imgur) and re-run with the https:// URL."
                    ),
                },
            )
        )

    return report
