"""Set-of-Mark annotation: detect text regions, number them, draw boxes.

Internal module. Uses macOS Vision framework via pyobjc for OCR — already
available since we depend on pyobjc-framework-Quartz. No extra ML deps,
no remote calls.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Mark:
    id: int
    x: int
    y: int
    w: int
    h: int
    text: str
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def stable_id(self) -> str:
        """A short hash of (text, ~position) that survives observe() reshuffling.

        IDs from `id` change every observe (top-to-bottom ordering); stable_id
        stays the same as long as the same element keeps the same text and
        appears in roughly the same place (rounded to 20px buckets).
        """
        bucket_x = (self.x + self.w // 2) // 20
        bucket_y = (self.y + self.h // 2) // 20
        key = f"{self.text}|{bucket_x},{bucket_y}".encode("utf-8")
        return hashlib.blake2b(key, digest_size=6).hexdigest()

    @property
    def stable_id_loose(self) -> str:
        """Coarser companion to stable_id — 60px bucket (3x tight) tolerates layout drift."""
        bucket_x = (self.x + self.w // 2) // 60
        bucket_y = (self.y + self.h // 2) // 60
        key = f"{self.text}|{bucket_x},{bucket_y}".encode("utf-8")
        return hashlib.blake2b(key, digest_size=6).hexdigest()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "stable_id": self.stable_id,
            "stable_id_loose": self.stable_id_loose,
            "bbox": [self.x, self.y, self.w, self.h],
            "center": list(self.center),
            "text": self.text,
            "confidence": round(self.confidence, 3),
        }


def vision_available() -> bool:
    try:
        import Vision  # noqa: F401
        return True
    except Exception:
        return False


def detect_marks(image_path: Path) -> list[Mark]:
    """Run macOS Vision OCR; return numbered marks ordered top-to-bottom, left-to-right."""
    if not vision_available():
        return []
    try:
        from Foundation import NSURL
        from Vision import (
            VNImageRequestHandler,
            VNRecognizeTextRequest,
            VNRequestTextRecognitionLevelAccurate,
        )
        from PIL import Image

        url = NSURL.fileURLWithPath_(str(image_path))
        handler = VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)

        success, _err = handler.performRequests_error_([request], None)
        if not success:
            return []
        with Image.open(image_path) as im:
            img_w, img_h = im.size

        raw: list[tuple[int, int, int, int, str, float]] = []
        for obs in request.results() or []:
            bbox = obs.boundingBox()
            cands = obs.topCandidates_(1)
            if cands is None or len(cands) == 0:
                continue
            cand = cands[0]
            text = str(cand.string())
            conf = float(cand.confidence())

            # Vision: normalized 0-1, origin BOTTOM-LEFT
            ox, oy = float(bbox.origin.x), float(bbox.origin.y)
            ow, oh = float(bbox.size.width), float(bbox.size.height)
            x = int(ox * img_w)
            w = int(ow * img_w)
            h = int(oh * img_h)
            y = int((1.0 - oy - oh) * img_h)
            raw.append((x, y, w, h, text, conf))

        # Sort top-to-bottom, then left-to-right (rough reading order)
        raw.sort(key=lambda r: (r[1] // 40, r[0]))
        return [
            Mark(id=i + 1, x=x, y=y, w=w, h=h, text=text, confidence=conf)
            for i, (x, y, w, h, text, conf) in enumerate(raw)
        ]
    except Exception:
        return []


def annotate(image_path: Path, marks: list[Mark], out_path: Path) -> Path:
    """Draw numbered red boxes + label badges on the screenshot."""
    from PIL import Image, ImageDraw, ImageFont

    im = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(im)

    # Pick a font size that scales with image size
    font_size = max(18, im.height // 80)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        font = ImageFont.load_default()

    for m in marks:
        # Box outline
        draw.rectangle([m.x, m.y, m.x + m.w, m.y + m.h], outline=(255, 0, 0), width=3)
        # Numbered badge above box
        label = str(m.id)
        try:
            tw = int(draw.textlength(label, font=font))
        except Exception:
            tw = font_size * len(label)
        th = font_size + 8
        bx0 = m.x
        by0 = max(0, m.y - th)
        bx1 = bx0 + tw + 12
        by1 = by0 + th
        draw.rectangle([bx0, by0, bx1, by1], fill=(255, 0, 0))
        draw.text((bx0 + 6, by0 + 2), label, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)
    return out_path


def find_by_text(marks: list[Mark], query: str) -> Optional[Mark]:
    """Best mark matching `query`. Exact > prefix > substring (case-insensitive)."""
    q = query.strip().lower()
    if not q or not marks:
        return None
    exact = [m for m in marks if m.text.strip().lower() == q]
    if exact:
        return max(exact, key=lambda m: m.confidence)
    prefix = [m for m in marks if m.text.strip().lower().startswith(q)]
    if prefix:
        return max(prefix, key=lambda m: m.confidence)
    sub = [m for m in marks if q in m.text.lower()]
    if sub:
        return max(sub, key=lambda m: m.confidence)
    return None


def find_by_mark_id(marks: list[Mark], mark_id: int) -> Optional[Mark]:
    for m in marks:
        if m.id == mark_id:
            return m
    return None


def find_by_stable_id(marks: list[Mark], stable_id: str) -> Optional[Mark]:
    for m in marks:
        if m.stable_id == stable_id:
            return m
    return None


def find_by_stable_id_loose(marks: list[Mark], stable_id: str) -> Optional[Mark]:
    for m in marks:
        if m.stable_id_loose == stable_id:
            return m
    return None
