"""Post-render image degradation — the part that actually hurts OCR.

Global LaTeX transforms (rotate the whole equation, one tint) are trivially
invertible by a decent vision model. Pixel-level damage is not: blur, low
resolution, speckle/grain, and low contrast destroy the high-frequency detail
that distinguishes ``\\xi`` from ``\\zeta`` or a subscript from a superscript.

These operate on the rendered PNG, after LaTeX, so they never risk a compile
failure and compose freely.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


def _blur(img: Image.Image, rng: random.Random, intensity: float) -> Image.Image:
    radius = round(rng.uniform(0.6, 2.4) * intensity, 2)
    return img.filter(ImageFilter.GaussianBlur(radius)) if radius > 0.1 else img


def _downscale(img: Image.Image, rng: random.Random, intensity: float) -> Image.Image:
    # lose resolution by shrinking then scaling back up (jagged, soft glyphs)
    factor = 1.0 - rng.uniform(0.4, 0.78) * intensity
    factor = max(0.15, factor)
    small = img.resize((max(1, int(img.width * factor)), max(1, int(img.height * factor))), Image.BILINEAR)
    return small.resize(img.size, Image.BILINEAR)


def _noise(img: Image.Image, rng: random.Random, intensity: float) -> Image.Image:
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    sigma = rng.uniform(10, 45) * intensity
    gen = np.random.default_rng(rng.randint(0, 2**31 - 1))
    arr += gen.normal(0, sigma, arr.shape)
    # occasional salt-and-pepper speckle
    if rng.random() < 0.6:
        mask = gen.random(arr.shape[:2]) < 0.02 * intensity
        arr[mask] = gen.choice([0.0, 255.0], size=mask.sum())[:, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _contrast(img: Image.Image, rng: random.Random, intensity: float) -> Image.Image:
    # crush contrast toward mid-gray; pushes text close to background
    factor = 1.0 - rng.uniform(0.4, 0.72) * intensity
    out = ImageEnhance.Contrast(img).enhance(factor)
    if rng.random() < 0.5:
        out = ImageEnhance.Brightness(out).enhance(rng.uniform(0.85, 1.15))
    return out


def _jpeg(img: Image.Image, rng: random.Random, intensity: float) -> Image.Image:
    quality = int(round(35 - 28 * intensity))  # 35 (mild) .. 7 (brutal)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=max(2, quality))
    buf.seek(0)
    return Image.open(buf).convert("RGB")


_EFFECTS = {
    "blur": _blur,
    "downscale": _downscale,
    "noise": _noise,
    "contrast": _contrast,
    "jpeg": _jpeg,
}
# Order matters: geometry/contrast before lossy compression / final noise.
_ORDER = ["contrast", "downscale", "blur", "noise", "jpeg"]


def degrade_image(path: str | Path, effects: list[str], seed: int, intensity: float = 1.0) -> list[str]:
    """Apply the named degradation effects to the PNG at ``path`` in place."""
    if not effects:
        return []
    rng = random.Random(seed)
    img = Image.open(path).convert("RGB")
    applied = []
    for name in _ORDER:
        if name in effects:
            img = _EFFECTS[name](img, rng, intensity)
            applied.append(name)
    img.save(path)
    return applied


def choose_effects(rng: random.Random, pool: list[str], k: int) -> list[str]:
    return rng.sample(pool, min(k, len(pool)))
