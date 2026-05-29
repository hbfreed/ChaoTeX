# CLAUDE.md

Guidance for working in this repo. Read this before editing.

## What ChaoTeX is

A vision benchmark testing the thesis "models aren't actually good at *seeing*."
We take real math from arXiv papers, render it as a deliberately **chaotic
image** (color, rotation, weird fonts, noise, occlusion), and ask a model to
transcribe the underlying LaTeX. It's software-verifiable: we score the text.

Pipeline (per item):

```
fetch .tex → extract math block → expand macros → [anti-memorize] → set REFERENCE
                                                          ↓
                                          apply visual CHAOS → render PNG → degrade pixels
model sees the PNG, outputs LaTeX text → score(text, REFERENCE)
```

## Invariants — do not break these

These are the load-bearing design rules. Most past bugs came from violating one.

1. **Never compile the original paper.** Whole-paper LaTeX rarely builds in
   isolation. We extract individual display-math blocks and render our own
   minimal `standalone` doc (`render.py`). Anything that fails to compile is
   *discarded*, never patched ("render-or-discard").
2. **Ground truth = the clean equation; chaos is a render-time skin.** Every
   transform in `chaos.py` is *ground-truth invariant* — it changes how the math
   looks, never what it says (you can't ask a model to transcribe an exact
   rotation angle or hex color).
3. **Content changes happen *before* the reference is fixed.** Variable/number
   renaming (`manipulate.py`, anti-memorization) alters meaning, so it runs up
   front and the stored `reference` reflects it. Never put a content change in
   `chaos.py`.
4. **Ground truth must equal what is *visible*.** `extract.py` strips invisible
   tokens (`\label`, comments, `\notag`) and converts numbered environments to
   unnumbered (no stray "(1)" a model can't infer).
5. **Macro fairness guard.** A model can't recover an invisible `\newcommand`
   name from a picture. `macros.py` expands paper macros; `build` *discards* any
   snippet where `macros.remaining()` is non-empty.
6. **Token effects apply in ONE pass over the original tokens**
   (`chaos._apply_token_effects`). Never chain per-atom transforms — a later
   pass would re-tokenize LaTeX an earlier pass injected (e.g. the word "red" in
   `\color{red}`) and corrupt it.
7. **Inter-atom insertion rules** (spacing/kerns in `chaos.py`): only after a
   letter/number or a real delimiter `)`/`]`; **never after a macro** (`\left`,
   `\right`, `\sqrt` need their next token), **never after `}`** (it's an
   argument boundary — splits `\frac{}{}`), and **never before `^`/`_`**. A box
   macro used as a script must be braced (`^{\scalebox..}`, not `^\scalebox..`).

## Modules (`src/chaotex/`)

| File | Role |
|------|------|
| `config.py` | `DOC_TEMPLATE`, base packages, `find_tectonic()` |
| `arxiv.py` | fetch + unpack a paper's `.tex` (`fetch_tex`, `DEFAULT_SEED_IDS`) |
| `extract.py` | `extract_snippets() -> [Snippet(inner, multiline, kind)]`; `Snippet.block` is the clean reference |
| `macros.py` | `harvest` / `expand` / `remaining` for `\newcommand`/`\def`/`\DeclareMathOperator` |
| `manipulate.py` | structure-aware variable/number renaming; `_segment()` tokenizer (shared with chaos) |
| `chaos.py` | visual transform registry + `TIERS` (easy/medium/hard) + `apply_chaos()` |
| `degrade.py` | post-render pixel effects (blur, downscale, noise, contrast, jpeg) |
| `render.py` | `render_document()`, `math_box()`, `assemble()`, `RenderError` |
| `score.py` | `normalize()` + `score() -> Score(exact, similarity, edit_distance, ref_len)` |
| `cli.py` | typer app: `demo`, `build`, `score` |

`chaos.apply_chaos()` returns the rendered body + preamble **and** the chosen
pixel `degrade` effects (applied afterward by `degrade.degrade_image`, since
they're post-render).

## Difficulty

`easy` (1 gentle transform, no degradation) → `medium` (2–3, mild degradation) →
`hard` (4–6 + 2–3 pixel degradations at full intensity). The difficulty lives
almost entirely in the **destructive** transforms — `noise`, `blur`,
`downscale`, `glyph_jitter`, `occlude`. Global transforms (whole-block rotate,
tint) are weak — models invert them easily.

Known wart: `hard` samples transforms uniformly, so it has high variance — a draw
can come up all-decorative and land easy. Intended fix (not yet done): split
decorative vs destructive pools with a guaranteed destructive floor per tier.

## Dev workflow

- **uv + Python ≥3.12.** `uv sync`; run via `uv run chaotex …`; add deps with
  `uv add` (don't use pip/requirements.txt).
- **LaTeX engine: Tectonic** (user-local musl binary in `~/.local/bin`,
  self-contained, no system TeX). `pdftoppm` (poppler) + ghostscript do PDF→PNG.
  First tectonic run downloads support files (needs network).
- `data/` is gitignored — regenerate with `chaotex build`. `examples/` is
  tracked (showcase images).

```bash
uv run chaotex demo --difficulty hard       # one equation through the pipeline
uv run chaotex build --per-paper 9          # mixed-difficulty dataset → data/dataset/
uv run chaotex score '\[ E=mc^2 \]' '...'   # score a transcription
```

## Gotchas

- Some arXiv submissions are just a PDF wrapped in a tiny `.tex` (e.g. Adam,
  `1412.6980`) → 0 extractable math. `build` handles this gracefully (skips).
- `eulervm` font breaks `\hat` — removed from the font pool. Safe fonts:
  `\boldmath`, `euler`, `mathptmx`.
- When adding a chaos transform, render-test it across many seeds *and*
  equations with `\frac`, `\sqrt`, sub/superscripts, and `\left…\right` — those
  expose the brace/insertion edge cases above.

## Eval harness (planned — see TODO below)

The benchmark is scored with [**verifiers**](https://github.com/PrimeIntellect-ai/verifiers)
(PrimeIntellect), driving models through **OpenRouter** (OpenAI-compatible API).

- **Environment**: a `load_environment()` returning a `vf.SingleTurnEnv(dataset, rubric, parser)`.
  - **dataset**: a HuggingFace `datasets.Dataset` built from `data/dataset/items.jsonl`. One row per item:
    - `prompt`: a single user message whose `content` is `[{"type":"text","text": INSTRUCTION}, {"type":"image_url","image_url":{"url": "data:image/png;base64,…"}}]` (verifiers supports multimodal input — cf. its `mmmu` env).
    - `answer`: the clean `reference` LaTeX (ground truth).
    - `info`: `{id, difficulty, transforms, degrade, arxiv_id}` for per-slice reporting.
  - **parser**: strip ``` fences / surrounding prose to isolate the LaTeX.
  - **rubric**: async reward funcs wrapping `chaotex.score.score` — `similarity` (0–1) as the primary reward, plus a binary `exact`. Scoring already normalizes delimiters/spacing, so the parser can be light.
- **Provider**: OpenRouter. base_url `https://openrouter.ai/api/v1`, key from env `OPENROUTER_API_KEY`.
- **Service tier**: request **`flex`** (≈50% cheaper, higher latency, lower availability — fine for batch eval). Pass as a top-level sampling arg: `vf-eval … -S '{"service_tier":"flex"}'`. Flex is only honored by OpenAI / Google Vertex / Google AI Studio; the response echoes the served tier (`flex`/`default`/`null`) — log it to confirm we actually got the discount.
- **Default model**: `google/gemini-3.1-flash-lite` — exact OpenRouter slug (cheap; Google → flex-eligible).

## TODO — make the eval runnable

Ordered so the eval works once all are checked. Keep this list current.

- [ ] `uv add verifiers && prime lab setup --skip-install` (verifiers pulls the OpenAI client + `datasets`; `prime lab setup` wires up the verifiers/prime CLI without reinstalling envs).
- [ ] `src/chaotex/eval.py`: `image_data_url(path)` (base64 PNG → data URL); `build_dataset(items_path, difficulty=None, max_items=None)` → `datasets.Dataset` with `prompt`/`answer`/`info`; a transcription `INSTRUCTION` constant ("Transcribe the math in this image as LaTeX; output only the LaTeX").
- [ ] In `eval.py`: a `vf.Parser` (or plain fn) that strips fences, and a `vf.Rubric` with `similarity`/`exact` reward funcs wrapping `chaotex.score.score`; `load_environment(**kwargs)` returning the `vf.SingleTurnEnv`.
- [ ] Lay out the env so the `prime` CLI discovers it; pin eval defaults in `[tool.verifiers.eval]` (num_examples, rollouts_per_example).
- [ ] OpenRouter wiring: base_url + `OPENROUTER_API_KEY`, and pass `-S '{"service_tier":"flex"}'` (if the OpenAI SDK rejects it top-level, move to `extra_body`). Log the served tier from the response.
- [ ] Set `google/gemini-3.1-flash-lite` as the default model.
- [ ] `chaotex eval` CLI wrapper (typer) that runs the eval and prints mean similarity + exact-match rate **broken down by difficulty and by transform**.
- [ ] Smoke test end-to-end: `chaotex build --per-paper 3` → run eval on ~10 items via flex → verify scores produced and `service_tier == "flex"` in responses.
- [ ] Document the run command in `README.md`.

## Also not yet built

The decorative/destructive tier floor (fixes `hard`-tier variance — see Difficulty);
a larger curated paper set (avoid PDF-wrapped submissions).
