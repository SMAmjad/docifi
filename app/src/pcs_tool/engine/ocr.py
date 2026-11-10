"""Step 2 - Read the text of every page.

Order of attempts (cheapest and most reliable first):
1. The PDF's own text layer (digital documents): exact and instant.
2. OCR with Tesseract after detecting page orientation.
3. If confidence is low: clean the image (deskew, denoise, contrast) and retry.
4. If still low: try all four orientations and keep the best reading.

Results are cached by file fingerprint so a second run is fast. The cache holds
page text (personal data) and therefore lives inside the output folder, which
the secure-delete command removes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

from . import ocr_second, pdf_backend

CACHE_VERSION = "ocr-v5"
SPARSE_MARKER = "--- additional words (sparse reading) ---"


@dataclass
class PageReading:
    text: str
    method: str
    confidence: float
    rotation: int
    image_hash: str
    error: str | None = None
    ink: float = 1.0            # share of dark pixels; ~0 means a blank page
    second_quality: float = 0.0  # quality of the second reading (RapidOCR), 0 if not run


# --------------------------------------------------------------------------- image helpers

def dhash(img: Image.Image, size: int = 16) -> str:
    """Difference hash: near-identical page images give near-identical hashes."""
    small = np.asarray(img.convert("L").resize((size + 1, size), Image.Resampling.BILINEAR), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return "".join("1" if b else "0" for b in bits)


def hash_distance(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 10**6
    return sum(x != y for x, y in zip(a, b))


def _clean_image(img: Image.Image) -> Image.Image:
    """Denoise, boost contrast, binarise and deskew a scanned page."""
    arr = np.asarray(img.convert("L"))
    h, w = arr.shape
    if max(h, w) < 1800:  # small photos read better when enlarged
        scale = 1800 / max(h, w)
        arr = cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    arr = cv2.fastNlMeansDenoising(arr, h=10)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    arr = clahe.apply(arr)
    binar = cv2.adaptiveThreshold(arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    # deskew using the minimum-area rectangle around dark pixels
    coords = np.column_stack(np.where(binar < 128))
    if len(coords) > 500:
        angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
        angle = angle - 90 if angle > 45 else angle
        if 0.3 < abs(angle) < 10:
            M = cv2.getRotationMatrix2D((binar.shape[1] / 2, binar.shape[0] / 2), angle, 1.0)
            binar = cv2.warpAffine(binar, M, (binar.shape[1], binar.shape[0]),
                                   flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(binar)


def _detect_rotation(img: Image.Image) -> int:
    try:
        osd = pytesseract.image_to_osd(img, config="--psm 0 -c min_characters_to_try=5")
        m = re.search(r"Rotate:\s*(\d+)", osd)
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
        if m and conf and float(conf.group(1)) >= 1.5:
            return int(m.group(1)) % 360
    except pytesseract.TesseractError:
        pass
    return 0


def _ocr(img: Image.Image, lang: str) -> tuple[str, float]:
    """OCR returning (text, quality 0-100). Quality = mean word confidence x share of real words."""
    data = pytesseract.image_to_data(img, lang=lang, config="--psm 3", output_type=pytesseract.Output.DICT)
    lines: dict[tuple[int, int, int], list[str]] = {}
    confs: list[float] = []
    real = 0
    total = 0
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        c = float(data["conf"][i])
        if c >= 0:
            confs.append(c)
        total += 1
        if re.fullmatch(r"[A-Za-z]{3,}[.,:;]?|\d[\d,./-]*", word):
            real += 1
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    if not confs or total == 0:
        return text, 0.0
    quality = (sum(confs) / len(confs)) * (0.5 + 0.5 * real / total)
    return text, round(quality, 1)


def _rotate(img: Image.Image, clockwise_deg: int) -> Image.Image:
    return img.rotate(-clockwise_deg, expand=True) if clockwise_deg else img


# --------------------------------------------------------------------------- main entry

def read_page(pdf: Path, index: int, settings: dict) -> PageReading:
    """Read one page. Never raises: errors are returned in ``PageReading.error``."""
    tess = settings.get("tesseract_path")
    if tess:
        pytesseract.pytesseract.tesseract_cmd = tess
    lang = settings.get("language", "eng")
    try:
        thumb = pdf_backend.render_page(pdf, index, dpi=40)
        ihash = dhash(thumb)
        ink = float((np.asarray(thumb) < 140).mean())
        text = pdf_backend.page_text(pdf, index)
        alnum = len(re.findall(r"[A-Za-z0-9]", text))
        if alnum >= int(settings.get("min_text_layer_chars", 50)):
            return PageReading(text=text, method="text layer", confidence=100.0, rotation=0, image_hash=ihash, ink=ink)
        if not tess:
            return PageReading(text=text, method="none", confidence=0.0, rotation=0, image_hash=ihash, ink=ink,
                               error="This page is a scan and the text reader (Tesseract) is not installed.")

        img = pdf_backend.render_page(pdf, index, dpi=int(settings.get("dpi", 300)))
        rot = _detect_rotation(img)
        best_text, best_q = _ocr(_rotate(img, rot), lang)
        best_rot = rot
        if best_q < float(settings.get("low_confidence", 60)):
            cleaned = _clean_image(_rotate(img, rot))
            t2, q2 = _ocr(cleaned, lang)
            if q2 > best_q:
                best_text, best_q = t2, q2
        if best_q < float(settings.get("try_all_rotations_below", 45)):
            base = _clean_image(img)
            for r in (0, 90, 180, 270):
                if r == best_rot:
                    continue
                t3, q3 = _ocr(_rotate(base, r), lang)
                if q3 > best_q + 3:
                    best_text, best_q, best_rot = t3, q3, r
        if best_q < float(settings.get("low_confidence", 60)):
            # Cards and photos: Tesseract's "sparse text" mode finds scattered words that page mode misses.
            sparse = pytesseract.image_to_string(_rotate(img, best_rot), lang=lang, config="--psm 11")
            sparse_lines = [ln.strip() for ln in sparse.splitlines() if len(ln.strip()) >= 3]
            if sparse_lines:
                best_text = best_text + "\n" + SPARSE_MARKER + "\n" + "\n".join(sparse_lines)
        reading = PageReading(text=best_text, method="ocr", confidence=best_q, rotation=best_rot,
                              image_hash=ihash, ink=ink)
        if best_q < float(settings.get("second_reading_below", 70)):
            add_second_reading(pdf, index, reading, settings)
        return reading
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the run
        return PageReading(text="", method="failed", confidence=0.0, rotation=0, image_hash="",
                           error=f"Page could not be read ({type(exc).__name__}).")


def add_second_reading(pdf: Path, index: int, reading: PageReading, settings: dict) -> PageReading:
    """Append a RapidOCR reading to a scanned page (in place). Safe to call twice."""
    if reading.method != "ocr" or reading.second_quality or not settings.get("second_engine", True) \
            or not ocr_second.available():
        return reading
    try:
        color = pdf_backend.render_page_rgb(pdf, index, dpi=200)
        second = ocr_second.read_image(_rotate(color, reading.rotation))
    except Exception:  # noqa: BLE001
        second = None
    if second and second[0].strip():
        reading.second_quality = second[1]
        reading.text = reading.text + "\n" + ocr_second.SECOND_MARKER + "\n" + second[0]
        # A page counts as well read if either engine read it well (second-engine scores run higher).
        reading.confidence = round(max(reading.confidence, second[1] * 0.8), 1)
    else:
        reading.second_quality = -1.0  # tried, nothing found: do not try again
    return reading


def second_reading_job(pdf: Path, index: int, reading: PageReading, settings: dict) -> PageReading:
    """Process-pool friendly wrapper."""
    tess = settings.get("tesseract_path")
    if tess:
        pytesseract.pytesseract.tesseract_cmd = tess
    return add_second_reading(pdf, index, reading, settings)


# --------------------------------------------------------------------------- cache

class OcrCache:
    """Per-page cache keyed by (file SHA-256, page index, settings fingerprint)."""

    def __init__(self, folder: Path, settings: dict):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        fp = json.dumps({k: settings.get(k) for k in ("language", "dpi", "low_confidence", "second_engine",
                                                      "second_reading_below", "try_all_rotations_below",
                                                      "min_text_layer_chars")},
                        sort_keys=True)
        self.salt = hashlib.sha256((CACHE_VERSION + fp).encode()).hexdigest()[:12]

    def _path(self, sha: str, index: int | str) -> Path:
        key = f"{index:04d}" if isinstance(index, int) else str(index)
        return self.folder / f"{sha[:24]}_{key}_{self.salt}.json"

    def get(self, sha: str, index: int) -> PageReading | None:
        """index may also be a card key such as 12_card1."""
        p = self._path(sha, index)
        if not p.is_file():
            return None
        try:
            return PageReading(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - corrupt cache entry: just re-read the page
            return None

    def put(self, sha: str, index: int, reading: PageReading) -> None:
        if reading.error:
            return
        self._path(sha, index).write_text(json.dumps(asdict(reading)), encoding="utf-8")
