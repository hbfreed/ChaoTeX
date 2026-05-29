# ChaoTeX

**A bizarre vision benchmark.** The hypothesis: frontier models aren't actually
that good at *seeing* — they lean on text. ChaoTeX tests that by taking real
math from arXiv papers, rendering it as a deliberately **chaotic image**
(colors, rotation, weird typefaces, noise, …), and asking a model to transcribe
the underlying LaTeX. It's fully software-verifiable: we score the text.

```
arXiv .tex  ──►  extract math  ──►  expand macros  ──►  anti-memorize  ──►  CHAOS render  ──►  PNG
                                                              │                                  │
                                                       (ground truth)                      (model sees this)
                                                              └────────────  score text  ◄───────┘
```

## The core idea

* **We never compile the original paper.** Whole-paper LaTeX rarely builds in
  isolation (custom macros, `\input` chains, missing `.sty`). Instead we pull
  individual display-math blocks and render *our own* clean `standalone`
  document — which always compiles.
* **Ground truth = the clean equation. Chaos is a render-time skin.** You can't
  ask a model to transcribe an exact `23.7°` rotation or a hex color, so the
  visual transforms never change *what the equation says* — only how it looks.
  The model's job is to see through the mess.
* **Macro expansion keeps it fair.** Papers define `\bz → \mathbf{z}` etc. in
  the preamble. A model can't recover an invisible macro name from a picture, so
  we expand macros to standard LaTeX. Any snippet still referencing a
  paper-defined macro after expansion is *discarded*.
* **Anti-memorization.** Variables and numbers are renamed up front
  (`Attention(Q,K,V) → Attention(I,b,a)`) so a model can't recite a famous
  equation from training — it has to actually read the image. This is a content
  change, so the ground-truth reference reflects it.

## Difficulty tiers

| Tier | Transforms stacked | Feel |
|------|--------------------|------|
| `easy`   | 1, gentle | a tint or font swap; clearly legible |
| `medium` | 2–3, moderate | rotation, spacing jitter, per-token color |
| `hard`   | 4–6, aggressive | + background noise, size mixing, colored boxes |

Transforms live in `chaos.py`: `color_all`, `per_token_color`, `font`,
`rotate`, `scale`, `jitter_spacing`, `size_mix`, `frame`, `colorbox_bg`,
`tikz_noise`.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and
[Tectonic](https://tectonic-typesetting.github.io/) (a self-contained LaTeX
engine — no system TeX needed) plus `pdftoppm` (poppler) for PDF→PNG.

```bash
uv sync
# tectonic: grab the prebuilt binary into ~/.local/bin, e.g.
#   the musl release tarball from github.com/tectonic-typesetting/tectonic/releases
```

## Usage

```bash
# Run one equation through the whole pipeline and render it
uv run chaotex demo --difficulty hard

# Build a dataset (defaults to a built-in seed set of math-heavy papers)
uv run chaotex build --per-paper 9            # mixes easy/medium/hard
uv run chaotex build 1706.03762 --difficulty hard

# Score a transcription against the ground truth
uv run chaotex score '\[ E = mc^2 \]' '\[ E=mc^2 \]'
```

A build writes `data/dataset/items.jsonl` (one item per line: `id`, `reference`,
`image`, `difficulty`, `transforms`, …) plus PNGs under `data/dataset/images/`.

## Scoring

Transcriptions are compared as text after normalization that folds **spellings
which render identically**, so the score measures *seeing*, not LaTeX dialect:
strip fences / math delimiters, drop spacing-only commands and manual delimiter
sizing (`\left`, `\bigl`, …), unify upright wrappers (`\text`↔`\mathrm`↔
`\operatorname`) and angle brackets (`\langle`↔`<`), alias equivalent macros
(`\leq`↔`\le`, `\rightarrow`↔`\to`, …), drop redundant braces (`^{x}`→`^x`,
`{m_i}^3`→`m_i^3`), and ignore insignificant math-mode whitespace. Genuine
misreads (a wrong variable, a scrambled fraction) are still penalized. Metrics:
exact match, normalized edit-distance similarity (0–1), and raw edit distance.

## Evaluating a model

The benchmark runs through [verifiers](https://github.com/PrimeIntellect-ai/verifiers),
driving any vision model on [OpenRouter](https://openrouter.ai). Each item becomes
a single multimodal prompt (instruction + the chaotic PNG); the reward is the
`similarity` score against the ground-truth LaTeX, with `exact` reported alongside.

```bash
# 1. Put your OpenRouter key in a .env (gitignored)
cp .env.example .env && $EDITOR .env        # set OPENROUTER_API_KEY=sk-or-...

# 2. Build a dataset if you haven't, then evaluate
uv run chaotex build --per-paper 3
uv run chaotex eval --num 10                # default model: google/gemini-3.1-flash-lite

# Options: --model <openrouter-slug>  --difficulty easy|medium|hard
#          --num -1 (all)  --rollouts N  --concurrency N
```

It prints mean similarity + exact-match rate **overall and broken down by
difficulty and by transform** — so you can see exactly which kinds of chaos the
model trips on. Requests use the **`flex`** service tier (≈50% cheaper); a quick
probe call up front confirms the provider actually served `flex`.

## Status / next steps

PoC focused on display equations. The eval harness is built (`chaotex eval`).
Natural extensions: a leaderboard across models, a larger/curated paper set, and
per-token geometric distortion.
