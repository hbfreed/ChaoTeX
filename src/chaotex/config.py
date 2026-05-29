"""Shared configuration: tectonic discovery, paths, and the render template."""

from __future__ import annotations

import shutil
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# Standalone document. `preamble` carries chaos packages/commands; `body` is the
# (possibly transformed) math box.
DOC_TEMPLATE = r"""\documentclass[border=12pt]{standalone}
\usepackage{amsmath,amssymb,amsfonts,mathtools}
\usepackage{graphicx,xcolor}
%(preamble)s
\begin{document}
%(body)s
\end{document}
"""

# Default base packages always present (so transforms can assume them).
BASE_PACKAGES = ("amsmath", "amssymb", "amsfonts", "mathtools", "graphicx", "xcolor")


def find_tectonic() -> str:
    """Locate the tectonic binary, falling back to the user-local install."""
    exe = shutil.which("tectonic")
    if exe:
        return exe
    local = Path.home() / ".local" / "bin" / "tectonic"
    if local.exists():
        return str(local)
    raise RuntimeError(
        "tectonic not found on PATH or in ~/.local/bin. "
        "Install it from https://github.com/tectonic-typesetting/tectonic/releases"
    )
