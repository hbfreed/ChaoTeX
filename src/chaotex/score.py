"""Score a model's transcription against the ground-truth math block (text only).

LaTeX has many spellings of the same render, so `normalize()` folds equivalent
forms (upright wrappers, angle brackets, delimiter sizing, redundant braces,
macro aliases, insignificant math-mode spacing) before comparing — the score
measures *seeing*, not dialect. We report a graded similarity, not just exact match.
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

# Manual delimiter sizing (\left \right \bigl \Bigg \big …) — the model can't
# (and shouldn't) reproduce exact sizing, and it never changes which glyph shows.
_SIZED_DELIM = re.compile(r"\\(?:left|right|[bB]igg?[lrm]?)(?![a-zA-Z])")
# Upright text/operator wrappers all render the same upright glyphs.
_TEXT_WRAP = re.compile(r"\\(?:text|textrm|textnormal|mathrm|operatorname)\*?\s*\{")
# A single space right after a macro word, only when a letter follows, is the
# macro's delimiter and must survive (\sum L != \sumL); every other math-mode
# space is insignificant.
_MACRO_GAP = re.compile(r"(\\[A-Za-z]+) (?=[A-Za-z])")
# A redundant brace group around a single token under a script: ^{x} -> ^x.
_SCRIPT_BRACE = re.compile(r"([\^_])\{(\\[a-zA-Z]+|[A-Za-z0-9])\}")
# A redundant brace group around a lone atom (+ at most one script): {m_i} -> m_i.
# TeX absorbs a single-token argument the same way, so this is render-faithful
# and, crucially, applied identically to both sides.
_BASE_BRACE = re.compile(r"\{([A-Za-z0-9](?:[_^](?:\\[a-zA-Z]+|[A-Za-z0-9]))?)\}")
# Aliased macros that render identically (variant -> canonical form).
_MACRO_ALIASES = (("rightarrow", "to"), ("leftarrow", "gets"),
                  ("leq", "le"), ("geq", "ge"), ("neq", "ne"))


def _canonicalize(s: str) -> str:
    """Fold LaTeX spellings that render identically so we score sight, not dialect."""
    s = _SIZED_DELIM.sub("", s)
    s = _TEXT_WRAP.sub(r"\\mathrm{", s)
    s = s.replace("\\langle", "<").replace("\\rangle", ">")
    for variant, canon in _MACRO_ALIASES:
        s = re.sub(rf"\\{variant}(?![a-zA-Z])", rf"\\{canon}", s)
    s = s.replace("{}", "")  # empty groups (e.g. m_i{}^3)
    s = _SCRIPT_BRACE.sub(r"\1\2", s)
    s = _BASE_BRACE.sub(r"\1", s)
    return s


def _drop_insignificant_spaces(s: str) -> str:
    """Remove math-mode spaces, keeping only the ones that delimit a macro name."""
    s = _MACRO_GAP.sub(lambda m: m.group(1) + "\x00", s)  # protect macro-delimiting spaces
    s = s.replace(" ", "")
    return s.replace("\x00", " ")


def normalize(latex: str) -> str:
    """Canonicalize for comparison: drop fences/delimiters, fold equivalent spellings, ignore math spacing."""
    s = latex.strip()
    s = _FENCE_RE.sub("", s).strip()
    # peel leading/trailing math delimiters and environment wrappers
    prev = None
    while prev != s:
        prev = s
        s = _DELIM_RE.sub("", s)
        s = re.sub(r"\s*(?:\\\]|\$\$?|\\end\{[a-zA-Z*]+\})\s*$", "", s)
    s = _SPACE_CMDS.sub(" ", s)
    s = _canonicalize(s)
    s = _WS_RE.sub(" ", s).strip()
    s = _drop_insignificant_spaces(s)
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
