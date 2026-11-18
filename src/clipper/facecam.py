"""Automatisk detektering av streamerns facecam.

Idé: streamerns kamera ligger STILLA på samma plats i hela klippet, medan ansikten i spelet
rör sig. Vi letar ansikten (OpenCV Haar) i flera bildrutor och väljer det ansikte som syns på
samma ställe i de flesta bildrutorna och som ligger ute mot en kant/hörn. Runt ansiktet tas
sedan en kamerarektangel med samma bildformat som facecam-ytan i layouten.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import FacecamRect
from .log import get_logger

log = get_logger("facecam")

Box = tuple[float, float, float, float]   # x, y, w, h i andelar 0–1


@dataclass
class Detection:
    box: Box
    support: int       # i hur många bildrutor ansiktet syntes
    frames: int


def iou(a: Box, b: Box) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _near_edge(b: Box) -> bool:
    cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
    return cx < 0.34 or cx > 0.66 or cy < 0.30 or cy > 0.70


def _close(a: Box, b: Box, tol: float = 0.07) -> bool:
    """Samma ansikte om mittpunkterna ligger nära varandra och storleken liknar."""
    ca = (a[0] + a[2] / 2, a[1] + a[3] / 2)
    cb = (b[0] + b[2] / 2, b[1] + b[3] / 2)
    size_ok = 0.6 < (a[2] / b[2] if b[2] else 0) < 1.67
    return abs(ca[0] - cb[0]) < tol and abs(ca[1] - cb[1]) < tol * 1.5 and size_ok


def choose_face(frames_boxes: list[list[Box]], min_ratio: float = 0.3) -> Detection | None:
    """Ren funktion: välj det ansikte som står kvar på samma plats i flest bildrutor
    (och ligger mot en kant). Spelets ansikten rör sig och får lågt stöd."""
    n = len(frames_boxes)
    if n == 0:
        return None
    best: Detection | None = None
    for i, boxes in enumerate(frames_boxes):
        for b in boxes:
            if not _near_edge(b) or b[2] > 0.25:      # facecam-ansikten är små och ligger i kanten
                continue
            cluster = [b]
            for j, other in enumerate(frames_boxes):
                if j == i:
                    continue
                match = next((o for o in other if _close(b, o)), None)
                if match is not None:
                    cluster.append(match)
            if best is None or len(cluster) > best.support:
                avg = tuple(sum(c[k] for c in cluster) / len(cluster) for k in range(4))
                best = Detection(avg, len(cluster), n)   # type: ignore[arg-type]
    if best and best.support >= max(3, round(min_ratio * n)):
        return best
    return None


def cam_rect_from_face(face: Box, src_aspect: float, target_aspect: float, expand: float = 2.6) -> FacecamRect:
    """Rektangel runt ansiktet med bildformatet target_aspect (bredd/höjd i pixlar).
    src_aspect = källans bredd/höjd (t.ex. 16/9) behövs för att räkna om andelar till pixlar."""
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2 + 0.15 * fh          # ansiktet sitter i övre halvan av kameran
    # arbeta i "pixelenheter" där höjd=1, bredd=src_aspect
    w_px = fw * src_aspect * expand
    h_px = w_px / target_aspect
    w, h = w_px / src_aspect, h_px
    if h > 1:                                               # för högt -> krymp
        h = 1.0
        w = (h * target_aspect) / src_aspect
    w = min(w, 1.0)
    x = min(max(cx - w / 2, 0.0), 1.0 - w)
    y = min(max(cy - h / 2, 0.0), 1.0 - h)
    return FacecamRect(x=round(x, 4), y=round(y, 4), w=round(w, 4), h=round(h, 4))


def _sample_frames(path: str | Path, n: int, duration: float, width: int = 640):
    import cv2
    import numpy as np
    frames = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            img = cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                frames.append(img)
    return frames


def detect_faces(gray) -> list[Box]:
    import cv2
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    h, w = gray.shape[:2]
    faces = cascade.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.08, minNeighbors=5,
                                     minSize=(max(16, w // 40), max(16, w // 40)))
    return [(x / w, y / h, fw / w, fh / h) for (x, y, fw, fh) in faces]


def detect_facecam(path: str | Path, src_w: int, src_h: int, duration: float,
                   target_aspect: float, n_frames: int = 12) -> FacecamRect | None:
    try:
        frames = _sample_frames(path, n_frames, duration)
        det = choose_face([detect_faces(f) for f in frames])
    except Exception as e:  # noqa: BLE001 – detektering får aldrig stoppa renderingen
        log.warning("facecam-detektering misslyckades", extra={"error": str(e)})
        return None
    if not det:
        log.info("ingen facecam hittad – använder gameplay-layout", extra={"file": str(path)})
        return None
    rect = cam_rect_from_face(det.box, src_w / src_h, target_aspect)
    log.info("facecam hittad", extra={"face": det.box, "support": f"{det.support}/{det.frames}", "rect": rect.model_dump()})
    return rect
