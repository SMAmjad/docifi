"""Find ID cards on a page (e.g. two driver's licences photocopied on one sheet).

The background colour is estimated from the page border; regions that differ from
it and have the proportions of an ID card (about 1.59 : 1) are returned. Two cards
photocopied edge to edge appear as one region about twice as wide; such regions
are split in half.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

CARD_RATIO = (1.3, 1.9)
PAIR_RATIO = (2.6, 3.8)


def find_cards(img: Image.Image) -> list[tuple[float, float, float, float]]:
    """Card boxes as fractions of the page (x0, y0, x1, y1), top-to-bottom, left-to-right."""
    rgb = np.asarray(img.convert("RGB"))
    h, w = rgb.shape[:2]
    scale = 600 / max(h, w)
    small = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.int16)
    border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
    dist = np.sqrt(((lab - np.median(border, axis=0)) ** 2).sum(axis=2))
    d8 = np.clip(dist * 2, 0, 255).astype(np.uint8)
    thr, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = (d8 > max(thr, 50)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    sh, sw = mask.shape
    boxes: list[tuple[int, int, int, int]] = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        ratio = max(cw, ch) / max(1, min(cw, ch))
        area = cw * ch / (sw * sh)
        if CARD_RATIO[0] <= ratio <= CARD_RATIO[1] and 0.02 <= area <= 0.85:
            boxes.append((x, y, cw, ch))
        elif PAIR_RATIO[0] <= ratio <= PAIR_RATIO[1] and 0.04 <= area <= 0.85:
            if cw > ch:
                boxes += [(x, y, cw // 2, ch), (x + cw // 2, y, cw - cw // 2, ch)]
            else:
                boxes += [(x, y, cw, ch // 2), (x, y + ch // 2, cw, ch - ch // 2)]
    boxes.sort(key=lambda b: (round(b[1] / max(1, sh) * 4), b[0]))
    pad = 0.01
    return [(max(0.0, x / sw - pad), max(0.0, y / sh - pad), min(1.0, (x + cw) / sw + pad),
             min(1.0, (y + ch) / sh + pad)) for x, y, cw, ch in boxes]


def crop(img: Image.Image, box: tuple[float, float, float, float], target: int = 1600) -> Image.Image:
    w, h = img.size
    region = img.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
    s = target / max(region.size)
    if s > 1:
        region = region.resize((int(region.width * s), int(region.height * s)), Image.Resampling.LANCZOS)
    return region
