"""Harvest custom macro definitions from a paper and expand them in snippets.

ML papers lean heavily on preamble macros (\\bz -> \\mathbf{z}, \\KL[2] -> ...).
A snippet using them won't render in isolation, and its source can't be
recovered from the image (the macro name is invisible). So we expand macros to
standard LaTeX. Any snippet that still references a paper-defined macro after
expansion is unsafe to score and is dropped by the caller via ``remaining``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Macro:
    name: str  # without leading backslash
    nargs: int
    body: str
    default: str | None = None  # default for an optional first argument


def _read_group(s: str, i: int, open_ch: str = "{", close_ch: str = "}") -> tuple[str | None, int]:
    """If s[i] opens a group, return (contents, index_after). Else (None, i)."""
    if i >= len(s) or s[i] != open_ch:
        return None, i
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":  # skip escaped char
            j += 2
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    return None, i  # unbalanced


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t":
        i += 1
    return i


_DEF_CMD = re.compile(r"\\(?:re|provide)?newcommand\*?\s*")
_MATHOP = re.compile(r"\\DeclareMathOperator(?P<star>\*?)\s*")
_DEF = re.compile(r"\\def\s*\\(?P<name>[a-zA-Z]+)")


def harvest(tex: str) -> dict[str, Macro]:
    """Collect macro definitions from the full source."""
    macros: dict[str, Macro] = {}

    # \newcommand / \renewcommand / \providecommand
    for m in _DEF_CMD.finditer(tex):
        i = m.end()
        # name: either {\foo} or \foo
        grp, j = _read_group(tex, i)
        if grp is not None:
            name = grp.strip()
        else:
            nm = re.match(r"\\([a-zA-Z]+)", tex[i:])
            if not nm:
                continue
            name, j = nm.group(1), i + nm.end()
        name = name.lstrip("\\")
        if not name:
            continue
        j = _skip_ws(tex, j)
        nargs, default = 0, None
        opt, j2 = _read_group(tex, j, "[", "]")
        if opt is not None:
            nargs = int(opt) if opt.strip().isdigit() else 0
            j = _skip_ws(tex, j2)
            dft, j3 = _read_group(tex, j, "[", "]")
            if dft is not None:
                default, j = dft, _skip_ws(tex, j3)
        body, j = _read_group(tex, j)
        if body is not None:
            macros[name] = Macro(name=name, nargs=nargs, body=body, default=default)

    # \DeclareMathOperator{\foo}{text}
    for m in _MATHOP.finditer(tex):
        star = m.group("star")
        name, j = _read_group(tex, m.end())
        if name is None:
            continue
        text, j = _read_group(tex, _skip_ws(tex, j))
        if text is not None:
            op = "\\operatorname*" if star else "\\operatorname"
            macros[name.lstrip("\\").strip()] = Macro(name.lstrip("\\").strip(), 0, f"{op}{{{text}}}")

    # \def\foo{...} and \def\foo#1#2{...}
    for m in _DEF.finditer(tex):
        name = m.group("name")
        i = m.end()
        nargs = 0
        while i < len(tex) and tex[i] == "#":
            nargs += 1
            i += 2  # skip #N
        body, j = _read_group(tex, i)
        if body is not None:
            macros[name] = Macro(name=name, nargs=nargs, body=body)

    return macros


def _substitute(body: str, args: list[str]) -> str:
    def repl(m: re.Match) -> str:
        k = int(m.group(1))
        return args[k - 1] if 1 <= k <= len(args) else m.group(0)

    return re.sub(r"#(\d)", repl, body)


def _expand_once(latex: str, macros: dict[str, Macro]) -> str:
    # longest names first so \alphabar is tried before \alpha
    names = sorted(macros, key=len, reverse=True)
    out, i = [], 0
    while i < len(latex):
        if latex[i] == "\\":
            mname = re.match(r"\\([a-zA-Z]+)", latex[i:])
            if mname and mname.group(1) in macros and mname.group(1) == _longest_at(latex, i, names):
                mac = macros[mname.group(1)]
                j = i + mname.end()
                args: list[str] = []
                ok = True
                for a in range(mac.nargs):
                    if a == 0 and mac.default is not None:
                        opt, j2 = _read_group(latex, _skip_ws(latex, j), "[", "]")
                        args.append(opt if opt is not None else mac.default)
                        j = j2 if opt is not None else j
                        continue
                    grp, j2 = _read_group(latex, _skip_ws(latex, j))
                    if grp is None:
                        ok = False
                        break
                    args.append(grp)
                    j = j2
                if ok:
                    out.append(_substitute(mac.body, args))
                    i = j
                    continue
        out.append(latex[i])
        i += 1
    return "".join(out)


def _longest_at(latex: str, i: int, names: list[str]) -> str | None:
    m = re.match(r"\\([a-zA-Z]+)", latex[i:])
    return m.group(1) if m else None


def expand(latex: str, macros: dict[str, Macro], max_iter: int = 12) -> str:
    """Recursively expand harvested macros to a fixpoint."""
    for _ in range(max_iter):
        nxt = _expand_once(latex, macros)
        if nxt == latex:
            break
        latex = nxt
    return latex


def remaining(latex: str, macros: dict[str, Macro]) -> set[str]:
    """Paper-defined macro names still present after expansion (=> unsafe)."""
    found = set(re.findall(r"\\([a-zA-Z]+)", latex))
    return found & set(macros)
