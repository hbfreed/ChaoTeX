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
| `eval.py` | verifiers harness: `build_dataset`, `load_environment`, `similarity`/`exact` rewards, OpenRouter `ClientConfig`, `probe_service_tier` |
| `cli.py` | typer app: `demo`, `build`, `score`, `eval` |

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

## Eval harness (built — `eval.py` + `chaotex eval`)

The benchmark is scored with [**verifiers**](https://github.com/PrimeIntellect-ai/verifiers)
(PrimeIntellect), driving models through **OpenRouter** (OpenAI-compatible API).
`eval.py` builds the environment; `chaotex eval` (cli.py) drives it and prints
the per-slice report.

- **Environment** (`eval.load_environment()` → `vf.SingleTurnEnv`):
  - **dataset**: `datasets.Dataset` from `data/dataset/items.jsonl` (`build_dataset`).
    One row per item: `prompt` is a single multimodal user message
    (`[{type:text, text:INSTRUCTION}, {type:image_url, image_url:{url:"data:image/png;base64,…"}}]`),
    `answer` is the clean `reference` LaTeX, `info` is
    `{id, difficulty, transforms, degrade, arxiv_id}` for per-slice reporting.
    `transforms`/`degrade` are **comma-joined strings** (flat Arrow schema; avoids
    empty-list typing) — split on `,` when slicing.
  - **parser**: `vf.Parser(extract_fn=parse_latex)` — prefers a fenced block, else
    strips stray backticks. `score()` normalizes again at scoring time, so this is light.
  - **rubric**: `vf.Rubric(funcs=[similarity_reward, exact_reward], weights=[1.0, 0.0])`
    wrapping `chaotex.score.score`. `similarity` (0–1) is the reward; `exact` rides
    along as a weight-0 metric.
- **Provider**: OpenRouter via `vf.ClientConfig(client_type="openai_chat_completions",
  api_base_url="https://openrouter.ai/api/v1", api_key_var="OPENROUTER_API_KEY")`.
  The key is read from a project-local **`.env`** (gitignored; see `.env.example`),
  loaded by `load_dotenv()` in cli.py.
- **Service tier**: `flex` (≈50% cheaper) passed in `sampling_args`. verifiers
  normalizes away the raw response, so `chaotex eval` confirms the discount with a
  one-shot `probe_service_tier()` direct call that reads `response.service_tier`
  before the run (prints `requested=… served=…`).
- **Default model**: `config.DEFAULT_MODEL = "google/gemini-3.1-flash-lite"`
  (cheap; Google → flex-eligible). Shared by `eval.py` and the CLI default.
- **Discovery**: `[tool.verifiers]` `module = "chaotex.eval"` + `[tool.verifiers.eval]`
  defaults in `pyproject.toml`.

## TODO — eval is runnable; remaining polish

Done: deps (`verifiers`/`datasets`/`openai`/`python-dotenv`), `eval.py`
(`image_data_url`/`build_dataset`/`INSTRUCTION`/`parse_latex`/rubric/`load_environment`),
OpenRouter+flex wiring with served-tier probe, default model, `chaotex eval` CLI
(per-difficulty + per-transform breakdown), end-to-end smoke test (served tier
confirmed `flex`), README docs.

- [ ] `prime lab setup --skip-install` / `vf-eval`-driven runs: we drive the env
  through our own `chaotex eval` CLI (not the `prime`/`vf-eval` CLI). The
  `[tool.verifiers]` discovery keys are in place but the `prime` CLI path is unverified.
- [ ] Confirm `service_tier` is forwarded by verifiers' `sampling_args` to the
  provider during the *actual* eval (the probe confirms provider support, not
  that verifiers passes it through). If not, move it to `extra_body`.
- [ ] Larger smoke runs / cost check at scale; wire eval into CI if desired.

## Also not yet built

The decorative/destructive tier floor (fixes `hard`-tier variance — see Difficulty);
a larger curated paper set (avoid PDF-wrapped submissions).
