"""Second OCR engine ("second reading") - RapidOCR on ONNX Runtime, fully offline.

Why two engines: Tesseract keeps page layout and spacing well and is excellent on
clean forms; RapidOCR (a deep-learning reader) is much stronger on phone photos,
ID cards with security backgrounds and stylised digits, but sometimes drops the
spaces between words. The tool keeps Tesseract's text as the main reading and
appends RapidOCR's text as a second reading; field readers fall back to it when
the main reading does not contain a value.

Both engines run on this computer. RapidOCR's model files ship inside the package.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image

SECOND_MARKER = "--- second reading ---"


@lru_cache(maxsize=1)
def _engine():
    try:
        import logging

        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        # Several worker processes run in parallel: keep each one's thread use modest.
        return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    except Exception:  # noqa: BLE001 - optional component
        return None


def available() -> bool:
    return _engine() is not None


def read_image(img: Image.Image) -> tuple[str, float] | None:
    """Return (text, quality 0-100) or None when the engine is unavailable or finds nothing.

    Boxes are regrouped into lines (same vertical position) and ordered left to right.
    """
    eng = _engine()
    if eng is None:
        return None
    arr = np.asarray(img.convert("RGB"))[:, :, ::-1].copy()  # RGB -> BGR
    try:
        result, _ = eng(arr)
    except Exception:  # noqa: BLE001
        return None
    if not result:
        return None
    boxes = []
    for pts, text, score in result:
        ys = [p[1] for p in pts]
        xs = [p[0] for p in pts]
        boxes.append((min(ys), max(ys), min(xs), str(text), float(score)))
    boxes.sort(key=lambda b: (b[0] + b[1]) / 2)
    lines: list[list[tuple]] = []
    for b in boxes:
        cy, h = (b[0] + b[1]) / 2, b[1] - b[0]
        if lines:
            last = lines[-1]
            lcy = sum((x[0] + x[1]) / 2 for x in last) / len(last)
            lh = sum(x[1] - x[0] for x in last) / len(last)
            if abs(cy - lcy) < 0.5 * max(h, lh):
                last.append(b)
                continue
        lines.append([b])
    text = "\n".join(" ".join(x[3] for x in sorted(line, key=lambda x: x[2])) for line in lines)
    quality = 100 * sum(b[4] for b in boxes) / len(boxes)
    return text, round(quality, 1)
