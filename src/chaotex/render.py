"""Render LaTeX to PNG via tectonic + pdftoppm.

We never compile a whole paper. A math box (optionally wrapped in chaos
transforms) is dropped into a minimal ``standalone`` document with whatever
extra preamble the transforms require.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .config import DOC_TEMPLATE, find_tectonic


class RenderError(RuntimeError):
    """Raised when tectonic or pdftoppm fails to produce output."""


def assemble(body: str, preamble: Iterable[str] = ()) -> str:
    """Build a full standalone document from a body and extra preamble lines."""
    pre = "\n".join(dict.fromkeys(preamble))  # dedupe, keep order
    return DOC_TEMPLATE % {"preamble": pre, "body": body}


def render_document(
    body: str, out_png: str | Path, preamble: Iterable[str] = (), dpi: int = 200, timeout: int = 60
) -> Path:
    """Render an assembled document to ``out_png``. Raises RenderError on failure."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tex = tmp / "doc.tex"
        tex.write_text(assemble(body, preamble), encoding="utf-8")

        try:
            proc = subprocess.run(
                [find_tectonic(), "-X", "compile", str(tex), "--outdir", str(tmp)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderError(f"tectonic timed out after {timeout}s") from exc

        pdf = tmp / "doc.pdf"
        if proc.returncode != 0 or not pdf.exists():
            raise RenderError(_tail(proc.stderr) or "tectonic produced no PDF")

        base = out_png.with_suffix("")  # pdftoppm -singlefile appends .png
        try:
            pp = subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi), "-singlefile", str(pdf), str(base)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RenderError("pdftoppm timed out") from exc

        if pp.returncode != 0 or not out_png.exists():
            raise RenderError(_tail(pp.stderr) or "pdftoppm produced no PNG")

    return out_png


def math_box(inner: str, multiline: bool) -> str:
    """Wrap raw math content in a single box-able inline display unit."""
    core = rf"\begin{{aligned}}{inner}\end{{aligned}}" if multiline else inner
    return rf"$\displaystyle {core}$"


def render_block(
    inner: str, out_png: str | Path, multiline: bool = False, dpi: int = 200, timeout: int = 60
) -> Path:
    """Render clean math (no chaos) into a box. Convenience over render_document."""
    return render_document(math_box(inner, multiline), out_png, dpi=dpi, timeout=timeout)


def _tail(text: str, n: int = 6) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])
