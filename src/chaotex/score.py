"""Score a model's transcription against the ground-truth math block (text only).

LaTeX has many spellings of the same render, so we normalize lightly before
comparing and report a graded similarity rather than only exact match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein
from rapidfuzz.fuzz import ratio

# Strip wrappers a model may add around its answer.
_FENCE_RE = re.compile(r"^```(?:latex|tex)?\s*|\s*```$", re.IGNORECASE)
_DELIM_RE = re.compile(r"^\s*(?:\\\[|\\\]|\$\$?|\\begin\{[a-zA-Z*]+\}|\\end\{[a-zA-Z*]+\})\s*")
# Spacing-only commands that never change the visible glyphs meaningfully.
_SPACE_CMDS = re.compile(r"\\(?:,|;|:|!|quad|qquad| \,|thinspace|;)")
_WS_RE = re.compile(r"\s+")


def normalize(latex: str) -> str:
    """Canonicalize for comparison: drop fences, delimiters, and collapse space."""
    s = latex.strip()
    s = _FENCE_RE.sub("", s).strip()
    # peel leading/trailing math delimiters and environment wrappers
    prev = None
    while prev != s:
        prev = s
        s = _DELIM_RE.sub("", s)
        s = re.sub(r"\s*(?:\\\]|\$\$?|\\end\{[a-zA-Z*]+\})\s*$", "", s)
    s = _SPACE_CMDS.sub(" ", s)
    s = s.replace("\\left", "").replace("\\right", "")
    s = _WS_RE.sub(" ", s).strip()
    return s


@dataclass
class Score:
    exact: bool  # exact match after normalization
    similarity: float  # 0..1, normalized edit-distance similarity
    edit_distance: int  # raw Levenshtein on normalized strings
    ref_len: int


def score(reference: str, hypothesis: str) -> Score:
    ref = normalize(reference)
    hyp = normalize(hypothesis)
    return Score(
        exact=ref == hyp,
        similarity=round(ratio(ref, hyp) / 100.0, 4),
        edit_distance=Levenshtein.distance(ref, hyp),
        ref_len=len(ref),
    )
