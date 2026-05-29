"""Random visual transforms ("the chaos"): a render-time skin over clean math.

Every transform here is *ground-truth invariant* — it changes how the equation
looks, never what it says. The model must see through the mess and transcribe
the underlying math. (Content changes like variable renaming live in
``manipulate`` and are applied up front so the reference reflects them.)

Transforms come in two flavours:
  * token transforms  -> rewrite the inner math string (per-atom color, etc.)
  * box transforms    -> wrap the boxed math (\\rotatebox, \\colorbox, ...)
Difficulty tiers control how many transforms stack and how hard they hit.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .manipulate import _segment
from .render import math_box

# Dark, legible-on-white colors for foreground use.
_COLORS = [
    "red",
    "blue",
    "teal",
    "violet",
    "purple",
    "brown",
    "olive",
    "magenta",
    "orange!80!black",
    "cyan!70!black",
    "green!50!black",
    "red!70!black",
    "blue!60!black",
]
# Light tints for backgrounds (keep contrast readable).
_TINTS = ["red!10", "blue!10", "yellow!15", "green!10", "gray!15", "orange!12", "purple!10"]

# Global math-font swaps that keep letters identifiable. (kind, value)
_FONTS = [
    ("cmd", r"\boldmath"),
    ("pre", r"\usepackage{euler}"),
    ("pre", r"\usepackage{mathptmx}"),
]

# Token kinds safe to wrap individually. Macros are excluded: many take a
# following argument (\hat, \sqrt, \frac), so wrapping one in a group would
# steal its argument and break compilation.
_ATOM = {"letter", "number"}


@dataclass
class Chaos:
    body: str
    preamble: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    degrade: list[str] = field(default_factory=list)  # pixel effects, post-render
    degrade_intensity: float = 1.0


@dataclass
class _Ctx:
    inner: str
    multiline: bool
    body: str = ""
    preamble: list[str] = field(default_factory=list)

    def need(self, *lines: str) -> None:
        for ln in lines:
            if ln not in self.preamble:
                self.preamble.append(ln)


# --------------------------------------------------------------------------- #
# token effects (all applied in ONE pass over the original tokens, before
# boxing). Single-pass is essential: chaining would let a later effect
# re-tokenize and mangle text a prior effect injected (e.g. the word "red" in
# \color{red}). Names: per_token_color, size_mix, glyph_jitter, jitter_spacing.
# --------------------------------------------------------------------------- #
_TOKEN_EFFECTS = {"per_token_color", "size_mix", "glyph_jitter", "jitter_spacing"}


def _apply_token_effects(ctx: _Ctx, rng: random.Random, intensity: float, effects: set[str]) -> None:
    color = "per_token_color" in effects
    size = "size_mix" in effects
    jitter = "glyph_jitter" in effects
    space = "jitter_spacing" in effects
    spaces = [r"\,", r"\;", r"\:", r"\!", r"\quad"]
    max_ang, max_rise = 30 * intensity, 6 * intensity

    toks = _segment(ctx.inner)
    out: list[str] = []
    for i, (kind, val) in enumerate(toks):
        if kind in _ATOM:
            core = val  # math fragment
            if color and rng.random() < min(1.0, 0.4 + intensity):
                core = rf"{{\color{{{rng.choice(_COLORS)}}}{core}}}"
            boxed = False  # have we wrapped into a text-mode box yet?
            if size and rng.random() < 0.5 * intensity:
                f = round(rng.uniform(0.55, 1.7), 2)
                core, boxed = rf"\scalebox{{{f}}}{{${core}$}}", True
            if jitter and rng.random() < 0.85:
                ang = round(rng.uniform(-max_ang, max_ang), 1)
                body = core if boxed else f"${core}$"
                core, boxed = rf"\rotatebox{{{ang}}}{{{body}}}", True
            if jitter and rng.random() < 0.6:
                v = round(rng.uniform(-max_rise, max_rise), 1)
                body = core if boxed else f"${core}$"
                core, boxed = rf"\raisebox{{{v}pt}}{{{body}}}", True
            # A bare box macro can't sit directly after ^ or _ ("^\scalebox..");
            # brace it so it's always a valid single script argument.
            if boxed:
                core = f"{{{core}}}"
            out.append(core)
        else:
            out.append(val)

        # inter-atom insertions: only after a self-contained atom, never before
        # a script marker, and never after a macro (\left/\right/\sqrt need
        # theirs). A closing "}" is an argument boundary (e.g. between \frac's
        # two groups), NOT a safe spot — only real delimiters ) ] are.
        nxt = toks[i + 1][1] if i + 1 < len(toks) else ""
        safe = (kind in ("letter", "number") or (kind == "other" and val in ")]")) and not nxt.startswith(
            ("^", "_")
        )
        if safe:
            if space and rng.random() < 0.35 * intensity:
                out.append(rng.choice(spaces))
            if jitter and rng.random() < 0.45 * intensity:
                out.append(rf"\mkern{round(rng.uniform(-9, -3), 1)}mu")

    ctx.inner = "".join(out)


# --------------------------------------------------------------------------- #
# box transforms (operate on ctx.body, after boxing). Lower order = inner.
# --------------------------------------------------------------------------- #
def _b_color_all(ctx: _Ctx, rng: random.Random, intensity: float) -> None:
    ctx.body = rf"{{\color{{{rng.choice(_COLORS)}}}{ctx.body}}}"


def _b_font(ctx: _Ctx, rng: random.Random, intensity: float) -> None:
    kind, val = rng.choice(_FONTS)
    if kind == "pre":
        ctx.need(val)
    else:
        ctx.body = rf"{{{val}{ctx.body}}}"


def _b_frame(ctx: _Ctx, rng: random.Random, intensity: float) -> None:
    ctx.body = rf"\fbox{{{ctx.body}}}"


def _b_colorbox_bg(ctx: _Ctx, rng: random.Random, intensity: float) -> None:
    ctx.body = rf"\colorbox{{{rng.choice(_TINTS)}}}{{{ctx.body}}}"


def _b_scale(ctx: _Ctx, rng: random.Random, intensity: float) -> None:
    sx = round(max(0.45, 1 + rng.uniform(-1, 1) * 0.5 * intensity), 2)
    sy = round(max(0.45, 1 + rng.uniform(-1, 1) * 0.5 * intensity), 2)
    ctx.body = rf"\scalebox{{{sx}}}[{sy}]{{{ctx.body}}}"


def _b_rotate(ctx: _Ctx, rng: random.Random, intensity: float) -> None:
    angle = round(rng.uniform(-1, 1) * 30 * intensity, 1)
    ctx.body = rf"\rotatebox{{{angle}}}{{{ctx.body}}}"


def _b_tikz_noise(ctx: _Ctx, rng: random.Random, intensity: float) -> None:
    ctx.need(r"\usepackage{tikz}", r"\usetikzlibrary{backgrounds,calc}")
    seed = rng.randint(1, 99999)
    step = round(rng.uniform(3, 6), 1)
    ctx.body = (
        r"\begin{tikzpicture}\node[inner sep=5pt] (m) {" + ctx.body + r"};"
        r"\begin{scope}[on background layer]"
        rf"\draw[gray!30,step={step}pt] (m.south west) grid (m.north east);"
        r"\draw[gray!40] (m.south west) -- (m.north east) (m.north west) -- (m.south east);"
        rf"\pgfmathsetseed{{{seed}}}"
        r"\foreach \i in {1,...,16}{"
        r"\pgfmathsetmacro{\fx}{rnd}\pgfmathsetmacro{\fy}{rnd}"
        r"\coordinate (px) at ($(m.south west)!\fx!(m.south east)$);"
        r"\coordinate (py) at ($(m.south west)!\fy!(m.north west)$);"
        r"\fill[gray!45] (px |- py) circle (0.6pt);}"
        r"\end{scope}\end{tikzpicture}"
    )


def _b_occlude(ctx: _Ctx, rng: random.Random, intensity: float) -> None:
    """Draw semi-transparent strokes *through* the glyphs (foreground occlusion)."""
    ctx.need(r"\usepackage{tikz}", r"\usetikzlibrary{calc}")
    seed = rng.randint(1, 99999)
    n = int(2 + 4 * intensity)
    width = round(0.6 + 1.4 * intensity, 1)
    op = round(0.35 + 0.3 * intensity, 2)
    ctx.body = (
        r"\begin{tikzpicture}\node[inner sep=4pt] (m) {" + ctx.body + r"};"
        rf"\pgfmathsetseed{{{seed}}}"
        rf"\foreach \i in {{1,...,{n}}}{{"
        r"\pgfmathsetmacro{\ax}{rnd}\pgfmathsetmacro{\ay}{rnd}"
        r"\pgfmathsetmacro{\bx}{rnd}\pgfmathsetmacro{\by}{rnd}"
        r"\coordinate (pa) at ($(m.south west)!\ax!(m.south east)$);"
        r"\coordinate (qa) at ($(m.south west)!\ay!(m.north west)$);"
        r"\coordinate (pb) at ($(m.south west)!\bx!(m.south east)$);"
        r"\coordinate (qb) at ($(m.south west)!\by!(m.north west)$);"
        rf"\draw[gray,opacity={op},line width={width}pt] (pa |- qa) -- (pb |- qb);}}"
        r"\end{tikzpicture}"
    )


# box transforms with apply order (inner -> outer)
_BOX_ORDER = ["color_all", "font", "frame", "colorbox_bg", "scale", "rotate", "tikz_noise", "occlude"]
_BOX = {
    "color_all": _b_color_all,
    "font": _b_font,
    "frame": _b_frame,
    "colorbox_bg": _b_colorbox_bg,
    "scale": _b_scale,
    "rotate": _b_rotate,
    "tikz_noise": _b_tikz_noise,
    "occlude": _b_occlude,
}


@dataclass
class Tier:
    count: tuple[int, int]  # how many LaTeX transforms to stack
    intensity: float
    pool: list[str]
    degrade_count: tuple[int, int]  # how many post-render pixel effects
    degrade_intensity: float
    degrade_pool: list[str]


# Pixel-degradation effects (see degrade.py).
_DEGRADE_ALL = ["blur", "downscale", "noise", "contrast", "jpeg"]

TIERS: dict[str, Tier] = {
    "easy": Tier(
        (1, 1),
        0.30,
        ["color_all", "rotate", "scale", "font", "colorbox_bg"],
        (0, 0),
        0.0,
        [],
    ),
    "medium": Tier(
        (2, 3),
        0.60,
        ["color_all", "rotate", "scale", "font", "colorbox_bg", "frame", "jitter_spacing", "per_token_color"],
        (1, 1),
        0.40,
        ["blur", "downscale", "jpeg"],
    ),
    "hard": Tier(
        (4, 6),
        1.0,
        [
            "color_all",
            "rotate",
            "scale",
            "font",
            "colorbox_bg",
            "frame",
            "jitter_spacing",
            "per_token_color",
            "size_mix",
            "tikz_noise",
            "glyph_jitter",
            "occlude",
        ],
        (2, 3),
        1.0,
        _DEGRADE_ALL,
    ),
}


def apply_chaos(inner: str, multiline: bool, seed: int, difficulty: str = "medium") -> Chaos:
    """Apply a random, difficulty-scaled stack of visual transforms.

    Returns the rendered body + preamble plus the chosen *pixel* degradations
    (applied after rendering by ``degrade.degrade_image``).
    """
    tier = TIERS[difficulty]
    rng = random.Random(seed)
    k = min(rng.randint(*tier.count), len(tier.pool))
    chosen = rng.sample(tier.pool, k)

    ctx = _Ctx(inner=inner, multiline=multiline)
    token_effects = _TOKEN_EFFECTS & set(chosen)
    if token_effects:
        _apply_token_effects(ctx, rng, tier.intensity, token_effects)

    ctx.body = math_box(ctx.inner, ctx.multiline)
    for name in _BOX_ORDER:  # deterministic inner->outer order
        if name in chosen:
            _BOX[name](ctx, rng, tier.intensity)

    dk = rng.randint(*tier.degrade_count)
    degrade = rng.sample(tier.degrade_pool, min(dk, len(tier.degrade_pool)))

    return Chaos(
        body=ctx.body,
        preamble=ctx.preamble,
        applied=chosen,
        degrade=degrade,
        degrade_intensity=tier.degrade_intensity,
    )
