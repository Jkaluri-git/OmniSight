"""Image utilities: downscale for the VLM, crop to the anomalous component, pixel-diff for regressions."""
from __future__ import annotations

import io

from PIL import Image, ImageChops

from ..models import BBox


def _open(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGB")


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def downscale(png: bytes, max_side: int = 1568) -> bytes:
    im = _open(png)
    if max(im.size) <= max_side:
        return png
    im.thumbnail((max_side, max_side))
    return _png(im)


def crop_region(size: tuple[int, int], bbox: BBox, pad: int = 48, min_side: int = 220) -> tuple[int, int, int, int]:
    W, H = size
    x0, y0, x1, y1 = bbox.x - pad, bbox.y - pad, bbox.x + bbox.w + pad, bbox.y + bbox.h + pad
    if x1 - x0 < min_side:
        c = (x0 + x1) / 2; x0, x1 = c - min_side / 2, c + min_side / 2
    if y1 - y0 < min_side:
        c = (y0 + y1) / 2; y0, y1 = c - min_side / 2, c + min_side / 2
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def crop(png: bytes, region: tuple[int, int, int, int]) -> bytes:
    im = _open(png)
    x0, y0, x1, y1 = region
    return _png(im.crop((x0, y0, min(x1, im.width), min(y1, im.height))))


def focus_fraction(bbox: BBox, region: tuple[int, int, int, int]) -> BBox:
    x0, y0, x1, y1 = region
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    return BBox(x=(bbox.x - x0) / w, y=(bbox.y - y0) / h, w=bbox.w / w, h=bbox.h / h)


def layout_shift(before: bytes, after: bytes, ignore: list[tuple[int, int, int, int]] | None = None, tol: int = 24) -> float:
    """Fraction of pixels that changed OUTSIDE the regions we intended to modify (0..1)."""
    a, b = _open(before), _open(after)
    w, h = min(a.width, b.width), min(a.height, b.height)
    diff = ImageChops.difference(a.crop((0, 0, w, h)), b.crop((0, 0, w, h))).convert("L").point(lambda v: 255 if v > tol else 0)
    for x0, y0, x1, y1 in ignore or []:
        diff.paste(0, (max(0, x0), max(0, y0), min(w, x1), min(h, y1)))
    hist = diff.histogram()
    changed = hist[255] if len(hist) > 255 else 0
    extra = abs(a.height - b.height) * w
    return min(1.0, (changed + extra) / float(w * max(a.height, b.height)))
