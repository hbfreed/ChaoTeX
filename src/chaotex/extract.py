"""Extract display-math blocks from raw .tex source.

Output blocks are normalized so the ground-truth source matches what is
*visibly* rendered: invisible tokens (\\label, comments, \\notag) are stripped,
and numbered environments become unnumbered (no stray "(1)" the model can't see).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Multiline environments we keep (mapped to their starred, unnumbered form).
_MULTILINE = {
    "align": "align*",
    "align*": "align*",
    "gather": "gather*",
    "gather*": "gather*",
    "multline": "multline*",
    "multline*": "multline*",
    "eqnarray": "align*",
    "eqnarray*": "align*",
    "flalign": "align*",
    "flalign*": "align*",
}
# Single-equation environments collapse to \[ ... \].
_SINGLE = {"equation", "equation*", "displaymath", "math"}

_ENV_RE = re.compile(r"\\begin\{(?P<env>[a-zA-Z*]+)\}(?P<body>.*?)\\end\{(?P=env)\}", re.DOTALL)
_BRACKET_RE = re.compile(r"\\\[(?P<body>.*?)\\\]", re.DOTALL)
_DOLLARS_RE = re.compile(r"\$\$(?P<body>.*?)\$\$", re.DOTALL)

# Tokens that are invisible in the rendered output and must not leak into truth.
_COMMENT_RE = re.compile(r"(?<!\\)%.*?$", re.MULTILINE)
_LABEL_RE = re.compile(r"\\label\s*\{[^}]*\}")
_NOTAG_RE = re.compile(r"\\(?:notag|nonumber)\b")

# Custom macros / external assets we cannot render from a snippet in isolation.
_REJECT = re.compile(
    r"\\(?:newcommand|renewcommand|def|includegraphics|input|include|ref|eqref|cite|caption)\b"
)


@dataclass
class Snippet:
    inner: str  # cleaned math content, no delimiters
    multiline: bool  # render via aligned / reference as align*
    kind: str  # "bracket" | env name

    @property
    def block(self) -> str:
        """Clean, canonical ground-truth form (what the model must transcribe)."""
        if self.multiline:
            return f"\\begin{{align*}}\n{self.inner}\n\\end{{align*}}"
        return f"\\[\n{self.inner}\n\\]"


def _clean_body(body: str) -> str:
    body = _COMMENT_RE.sub("", body)
    body = _LABEL_RE.sub("", body)
    body = _NOTAG_RE.sub("", body)
    return body.strip()


def extract_snippets(tex: str, min_len: int = 12, max_len: int = 600) -> list[Snippet]:
    """Return cleaned, plausibly-renderable display-math blocks from ``tex``."""
    out: list[Snippet] = []
    seen: set[str] = set()

    def add(body: str, multiline: bool, kind: str) -> None:
        if _REJECT.search(body):
            return
        inner = _clean_body(body)
        if not inner or not (min_len <= len(inner) <= max_len):
            return
        if inner in seen:
            return
        seen.add(inner)
        out.append(Snippet(inner=inner, multiline=multiline, kind=kind))

    for m in _ENV_RE.finditer(tex):
        env = m.group("env")
        if env in _MULTILINE:
            add(m.group("body"), True, env)
        elif env in _SINGLE:
            add(m.group("body"), False, env)
    for m in _BRACKET_RE.finditer(tex):
        add(m.group("body"), False, "bracket")
    for m in _DOLLARS_RE.finditer(tex):
        add(m.group("body"), False, "bracket")

    return out
