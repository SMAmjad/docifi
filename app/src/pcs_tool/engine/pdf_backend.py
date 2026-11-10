"""All PDF operations go through this module (render, text, assemble, image-to-PDF).

Licensing note: the current implementation uses PyMuPDF (AGPL). Keeping every
PDF call behind this small interface means the library can be swapped for a
permissively licensed one (pypdfium2 + pypdf) before commercial sale by
rewriting only this file. See docs/decisions.md (DR-004).
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

pymupdf.TOOLS.mupdf_display_errors(False)


def page_count(pdf: Path) -> int:
    with pymupdf.open(pdf) as doc:
        return doc.page_count


def is_encrypted(pdf: Path) -> bool:
    with pymupdf.open(pdf) as doc:
        return bool(doc.needs_pass)


def page_text(pdf: Path, index: int) -> str:
    with pymupdf.open(pdf) as doc:
        return doc[index].get_text("text")


def render_page(pdf: Path, index: int, dpi: int = 300) -> Image.Image:
    with pymupdf.open(pdf) as doc:
        pix = doc[index].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        return Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")


def render_page_rgb(pdf: Path, index: int, dpi: int = 200) -> Image.Image:
    with pymupdf.open(pdf) as doc:
        pix = doc[index].get_pixmap(dpi=dpi)
        return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def image_to_pdf(image_path: Path, out_pdf: Path) -> int:
    """Convert an image (multi-frame TIFF supported) to a PDF. Returns page count."""
    frames: list[Image.Image] = []
    with Image.open(image_path) as im:
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            im.seek(i)
            fr = ImageOps.exif_transpose(im.copy())
            frames.append(fr.convert("RGB"))
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out_pdf, "PDF", resolution=200.0, save_all=True, append_images=frames[1:])
    return len(frames)


def text_to_pdf(text: str, out_pdf: Path) -> int:
    doc = pymupdf.open()
    lines = text.splitlines() or [""]
    per_page = 60
    for start in range(0, len(lines), per_page):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(pymupdf.Rect(54, 54, 558, 738), "\n".join(lines[start:start + per_page]), fontsize=9)
    doc.save(out_pdf)
    n = doc.page_count
    doc.close()
    return n


def assemble(pages: list[tuple], out_pdf: Path) -> None:
    """Build a new PDF from (source_pdf, page_index, extra_rotation[, region]) tuples, in order.

    ``region`` = (x0, y0, x1, y1) as fractions of the page: only that part is kept
    (used when one sheet carries several ID cards).
    """
    out = pymupdf.open()
    cache: dict[Path, pymupdf.Document] = {}
    try:
        for item in pages:
            src, idx, rot = item[0], item[1], item[2]
            region = item[3] if len(item) > 3 else None
            if src not in cache:
                cache[src] = pymupdf.open(src)
            if region:
                r = cache[src][idx].rect
                clip = pymupdf.Rect(r.x0 + region[0] * r.width, r.y0 + region[1] * r.height,
                                    r.x0 + region[2] * r.width, r.y0 + region[3] * r.height)
                pg = out.new_page(width=clip.width, height=clip.height)
                pg.show_pdf_page(pg.rect, cache[src], idx, clip=clip)
                continue
            out.insert_pdf(cache[src], from_page=idx, to_page=idx)
            if rot:
                pg = out[-1]
                pg.set_rotation((pg.rotation + rot) % 360)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        out.save(out_pdf, garbage=3, deflate=True)
    finally:
        out.close()
        for d in cache.values():
            d.close()
