"""Fetch and unpack a paper's LaTeX source from arXiv.

We only need the .tex *content* — we never compile the paper. arXiv serves the
author's source tarball at the e-print endpoint.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import time
from pathlib import Path

import httpx

EPRINT_URL = "https://export.arxiv.org/e-print/{arxiv_id}"
# arXiv requires a descriptive User-Agent and polite rate limiting (>=3s).
_HEADERS = {"User-Agent": "ChaoTeX/0.1 (benchmark dataset builder; contact via arxiv id list)"}

# A small math-heavy seed set so a dataset can be built without crawling.
DEFAULT_SEED_IDS = [
    "1706.03762",  # Attention Is All You Need
    "1412.6980",  # Adam
    "1512.03385",  # ResNet
    "1406.2661",  # GANs
    "1312.6114",  # VAE
]


def fetch_source(arxiv_id: str, timeout: float = 60.0) -> bytes:
    """Download the raw e-print payload for ``arxiv_id``."""
    url = EPRINT_URL.format(arxiv_id=arxiv_id)
    resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def tex_from_payload(payload: bytes) -> str:
    """Concatenate every .tex file in an arXiv e-print payload.

    Handles the three shapes arXiv serves: a .tar.gz, a single gzipped file, or
    raw bytes.
    """
    # Most submissions: gzipped tarball.
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as tar:
            parts = []
            for member in tar.getmembers():
                if member.isfile() and member.name.lower().endswith(".tex"):
                    f = tar.extractfile(member)
                    if f is not None:
                        parts.append(f.read().decode("utf-8", errors="replace"))
            if parts:
                return "\n".join(parts)
    except tarfile.TarError:
        pass

    # Single gzipped .tex (older single-file submissions).
    try:
        return gzip.decompress(payload).decode("utf-8", errors="replace")
    except (OSError, gzip.BadGzipFile):
        pass

    # Last resort: treat as raw text.
    return payload.decode("utf-8", errors="replace")


def fetch_tex(arxiv_id: str, cache_dir: Path | None = None, delay: float = 3.0) -> str:
    """Fetch (and optionally cache) the concatenated .tex source for a paper."""
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{arxiv_id.replace('/', '_')}.tex"
        if cached.exists():
            return cached.read_text(encoding="utf-8")

    payload = fetch_source(arxiv_id)
    tex = tex_from_payload(payload)
    time.sleep(delay)  # be polite to arXiv

    if cache_dir is not None:
        cached.write_text(tex, encoding="utf-8")
    return tex
