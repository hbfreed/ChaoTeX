"""Seeded, anti-memorization perturbations of a math block.

The goal is *not* to change meaning sensibly but to break verbatim recall: a
model that memorized the paper must instead actually read the image. Every
perturbation keeps the LaTeX valid and renderable.

The walker is structure-aware: it renames variable letters and Greek macros and
perturbs numbers, but never touches environment names (\\begin{align*}) or the
word arguments of text commands (\\text{...}, \\operatorname{KL}), which would
break compilation or corrupt visible words.
"""

from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass, field

# Lowercase Greek macros we are willing to remap (visually swappable letters).
_GREEK = [
    r"\alpha",
    r"\beta",
    r"\gamma",
    r"\delta",
    r"\epsilon",
    r"\zeta",
    r"\eta",
    r"\theta",
    r"\kappa",
    r"\lambda",
    r"\mu",
    r"\nu",
    r"\xi",
    r"\rho",
    r"\sigma",
    r"\tau",
    r"\phi",
    r"\psi",
    r"\omega",
]
_GREEK_SET = set(_GREEK)

# Single letters that carry conventional meaning; left alone to keep blocks sane.
_PROTECTED_LETTERS = set("dei")

# Commands whose following group is human-readable text, not variables.
_TEXT_CMDS = {
    "text",
    "textrm",
    "textbf",
    "textit",
    "textsf",
    "texttt",
    "textnormal",
    "textsc",
    "emph",
    "mathrm",
    "operatorname",
    "mbox",
    "hbox",
    "mathop",
    "label",
    "tag",
    "ref",
    "eqref",
    "cite",
}

_MACRO_RE = re.compile(r"\\[a-zA-Z]+")


@dataclass
class Manipulation:
    latex: str
    letter_map: dict[str, str] = field(default_factory=dict)
    greek_map: dict[str, str] = field(default_factory=dict)
    numbers_changed: int = 0


def _read_group(s: str, i: int) -> tuple[str | None, int]:
    """If s[i]=='{', return (raw group incl. braces, index_after); else (None, i)."""
    if i >= len(s) or s[i] != "{":
        return None, i
    depth, j = 0, i
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i : j + 1], j + 1
        j += 1
    return None, i


def _segment(latex: str) -> list[tuple[str, str]]:
    """Split into tagged tokens: macro | protected | letter | number | other."""
    toks: list[tuple[str, str]] = []
    i, n = 0, len(latex)
    while i < n:
        c = latex[i]
        if c == "\\":
            m = _MACRO_RE.match(latex, i)
            if m:
                name = m.group()[1:]
                j = m.end()
                if name in ("begin", "end"):
                    grp, j2 = _read_group(latex, j)
                    toks.append(("protected", latex[i:j2] if grp is not None else m.group()))
                    i = j2 if grp is not None else j
                    continue
                if name in _TEXT_CMDS:
                    star = ""
                    if j < n and latex[j] == "*":
                        star, j = "*", j + 1
                    grp, j2 = _read_group(latex, j)
                    toks.append(("protected", latex[i:j2] if grp is not None else m.group() + star))
                    i = j2 if grp is not None else j
                    continue
                toks.append(("macro", m.group()))
                i = j
                continue
            toks.append(("other", latex[i : i + 2]))  # escaped char like \, \{ \\
            i += 2
            continue
        if c.isalpha():
            toks.append(("letter", c))
            i += 1
            continue
        if c.isdigit():
            m = re.match(r"\d+\.?\d*", latex[i:])
            toks.append(("number", m.group()))
            i += m.end()
            continue
        toks.append(("other", c))
        i += 1
    return toks


def _build_bijection(used: list[str], pool: list[str], rng: random.Random) -> dict[str, str]:
    mapping: dict[str, str] = {}
    targets = [p for p in pool if p not in used]
    rng.shuffle(targets)
    for sym in used:
        choice = next((t for t in targets if t != sym), None)
        if choice is None:
            choice = next((p for p in pool if p != sym), sym)
        else:
            targets.remove(choice)
        mapping[sym] = choice
    return mapping


def _perturb_number(tok: str, rng: random.Random) -> str:
    if "." in tok:
        try:
            val = float(tok)
        except ValueError:
            return tok
        delta = rng.choice([-0.5, -0.2, -0.1, 0.1, 0.2, 0.5])
        return f"{max(0.0, val + delta):.{len(tok.split('.')[1])}f}"
    width = len(tok)
    lo, hi = (1, 9) if width == 1 else (10 ** (width - 1), 10**width - 1)
    for _ in range(8):
        cand = rng.randint(lo, hi)
        if str(cand) != tok:
            return str(cand)
    return tok


def manipulate(
    latex: str,
    seed: int,
    rename_vars: bool = True,
    perturb_numbers: bool = True,
    number_prob: float = 0.7,
) -> Manipulation:
    """Apply seeded perturbations to a normalized math block."""
    rng = random.Random(seed)
    toks = _segment(latex)

    letters_used = sorted({v for k, v in toks if k == "letter" and v not in _PROTECTED_LETTERS})
    greek_used = sorted({v for k, v in toks if k == "macro" and v in _GREEK_SET})

    letter_map: dict[str, str] = {}
    greek_map: dict[str, str] = {}
    if rename_vars:
        letter_map = _build_bijection(letters_used, list(string.ascii_letters), rng)
        greek_map = _build_bijection(greek_used, _GREEK, rng)

    numbers_changed = 0
    out: list[str] = []
    for kind, val in toks:
        if kind == "letter" and val in letter_map:
            out.append(letter_map[val])
        elif kind == "macro" and val in greek_map:
            out.append(greek_map[val])
        elif kind == "number" and perturb_numbers and rng.random() < number_prob:
            new = _perturb_number(val, rng)
            numbers_changed += new != val
            out.append(new)
        else:
            out.append(val)

    return Manipulation(
        latex="".join(out),
        letter_map=letter_map,
        greek_map=greek_map,
        numbers_changed=numbers_changed,
    )
