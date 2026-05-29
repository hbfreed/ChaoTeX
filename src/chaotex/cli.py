"""ChaoTeX command line: build the dataset, demo the pipeline, score a guess."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from . import arxiv, chaos, degrade, macros
from .config import DATA_DIR, DEFAULT_MODEL
from .extract import Snippet, extract_snippets
from .manipulate import manipulate
from .render import RenderError, render_document
from .score import score as score_fn

app = typer.Typer(add_completion=False, help="ChaoTeX: a vision benchmark from chaotically rendered LaTeX.")
console = Console()

# Load secrets (e.g. OPENROUTER_API_KEY) from a project-local .env if present.
load_dotenv()

_SAMPLE = r"\hat{f}(\xi)=\int_{-\infty}^{\infty} f(x)\, e^{-2\pi i x \xi}\,dx"


@app.command()
def demo(
    latex: str = typer.Option(_SAMPLE, help="Math content (no delimiters) to run through the pipeline."),
    difficulty: str = typer.Option("medium", help="easy | medium | hard"),
    seed: int = typer.Option(0, help="Seed for manipulation + chaos."),
    anti_memorize: bool = typer.Option(True, help="Rename variables/numbers first (anti-recall)."),
    out: Path = typer.Option(DATA_DIR / "demo.png", help="Output PNG path."),
) -> None:
    """Run one equation through anti-memorize -> chaos -> render and report it."""
    inner = manipulate(latex, seed=seed).latex if anti_memorize else latex
    reference = Snippet(inner=inner, multiline=False, kind="bracket").block
    ch = chaos.apply_chaos(inner, multiline=False, seed=seed, difficulty=difficulty)
    console.print(f"[bold]reference (ground truth)[/bold]\n{reference}")
    console.print(f"[dim]difficulty={difficulty}  transforms={ch.applied}  degrade={ch.degrade}[/dim]")
    try:
        path = render_document(ch.body, out, preamble=ch.preamble)
        degrade.degrade_image(path, ch.degrade, seed=seed, intensity=ch.degrade_intensity)
        console.print(f"[green]rendered[/green] -> {path}")
    except RenderError as exc:
        console.print(f"[red]render failed:[/red] {exc}")
        raise typer.Exit(1) from None


@app.command()
def build(
    ids: list[str] | None = typer.Argument(None, help="arXiv IDs (default: built-in seed set)."),
    out_dir: Path = typer.Option(DATA_DIR / "dataset", help="Dataset output directory."),
    per_paper: int = typer.Option(5, help="Max rendered items per paper."),
    difficulty: str = typer.Option("mix", help="easy | medium | hard | mix (cycles all three)."),
    anti_memorize: bool = typer.Option(True, help="Rename variables/numbers before rendering."),
    seed: int = typer.Option(0, help="Base seed."),
    dpi: int = typer.Option(200, help="Render resolution."),
) -> None:
    """Build a dataset: fetch -> extract -> expand macros -> anti-memorize -> chaos -> render."""
    ids = ids or arxiv.DEFAULT_SEED_IDS
    diffs = ["easy", "medium", "hard"] if difficulty == "mix" else [difficulty]
    cache = DATA_DIR / "cache"
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    items_path = out_dir / "items.jsonl"

    n = 0
    with items_path.open("w", encoding="utf-8") as fh:
        for arxiv_id in ids:
            try:
                tex = arxiv.fetch_tex(arxiv_id, cache_dir=cache)
            except Exception as exc:  # network/parse issues shouldn't kill the run
                console.print(f"[red]fetch failed[/red] {arxiv_id}: {exc}")
                continue
            paper_macros = macros.harvest(tex)
            snippets = extract_snippets(tex)
            console.print(
                f"[cyan]{arxiv_id}[/cyan]: {len(snippets)} candidate blocks, {len(paper_macros)} macros"
            )

            kept = 0
            for i, snip in enumerate(snippets):
                if kept >= per_paper:
                    break
                # expand the paper's macros; drop anything still referencing one,
                # since the model could never recover an invisible macro name.
                inner = macros.expand(snip.inner, paper_macros)
                if macros.remaining(inner, paper_macros):
                    continue

                item_seed = seed + n
                diff = diffs[n % len(diffs)]
                # content change (ground-truth altering) happens up front
                if anti_memorize:
                    inner = manipulate(inner, seed=item_seed).latex
                reference = Snippet(inner=inner, multiline=snip.multiline, kind=snip.kind).block
                ch = chaos.apply_chaos(inner, snip.multiline, seed=item_seed, difficulty=diff)

                item_id = f"{arxiv_id.replace('/', '_')}_{i:03d}"
                png = img_dir / f"{item_id}.png"
                try:
                    render_document(ch.body, png, preamble=ch.preamble, dpi=dpi)
                except RenderError:
                    continue  # discard anything we can't render
                degrade.degrade_image(png, ch.degrade, seed=item_seed, intensity=ch.degrade_intensity)
                fh.write(
                    json.dumps(
                        {
                            "id": item_id,
                            "arxiv_id": arxiv_id,
                            "kind": snip.kind,
                            "multiline": snip.multiline,
                            "difficulty": diff,
                            "transforms": ch.applied,
                            "degrade": ch.degrade,
                            "seed": item_seed,
                            "reference": reference,
                            "image": str(png.relative_to(out_dir)),
                        }
                    )
                    + "\n"
                )
                kept += 1
                n += 1
            console.print(f"  rendered {kept}")

    console.print(f"[green]built {n} items[/green] -> {items_path}")


@app.command()
def eval(  # noqa: A001 (shadowing builtin is fine for a CLI verb)
    items: Path = typer.Option(DATA_DIR / "dataset" / "items.jsonl", help="Dataset items.jsonl."),
    model: str = typer.Option(DEFAULT_MODEL, help="OpenRouter model slug."),
    num: int = typer.Option(10, help="Number of examples to evaluate (-1 = all)."),
    difficulty: str | None = typer.Option(None, help="Filter to one tier: easy | medium | hard."),
    rollouts: int = typer.Option(1, help="Rollouts per example."),
    max_tokens: int = typer.Option(512, help="Max completion tokens (equations are short)."),
    service_tier: str = typer.Option("flex", help="OpenAI service tier ('flex' is ~50% cheaper)."),
    concurrency: int = typer.Option(8, help="Max concurrent rollouts."),
    probe_tier: bool = typer.Option(True, help="One tiny call first to confirm the served tier."),
) -> None:
    """Run the verifiers eval over rendered items via OpenRouter; report scores by slice."""
    import os

    from . import eval as ev

    if not os.environ.get(ev.OPENROUTER_KEY_VAR):
        console.print(f"[red]${ev.OPENROUTER_KEY_VAR} is not set.[/red] Export your OpenRouter key first.")
        raise typer.Exit(1)

    if probe_tier:
        try:
            served = ev.probe_service_tier(model)
            tag = "[green]" if served == service_tier else "[yellow]"
            console.print(f"{tag}service_tier requested={service_tier} served={served}[/]")
        except Exception as exc:  # noqa: BLE001 — the probe is best-effort
            console.print(f"[yellow]tier probe failed (continuing): {exc}[/yellow]")

    env = ev.load_environment(items_path=items, difficulty=difficulty, max_items=None)
    client_cfg = ev.openrouter_client_config()
    sampling_args = {"max_tokens": max_tokens, "service_tier": service_tier}

    console.print(f"[cyan]evaluating[/cyan] {model} on {num if num > 0 else 'all'} examples …")
    out = env.evaluate_sync(
        client=client_cfg,
        model=model,
        sampling_args=sampling_args,
        num_examples=num,
        rollouts_per_example=rollouts,
        max_concurrent=concurrency,
    )
    _report(out)


def _report(out) -> None:
    """Print mean similarity + exact rate overall and broken down by difficulty and transform.

    `out` is a GenerateOutputs TypedDict; each rollout is a RolloutOutput dict.
    """
    outputs = out["outputs"]
    rollouts = [r for r in outputs if r.get("error") is None]
    errored = len(outputs) - len(rollouts)

    def metric(r, name: str) -> float:
        return r["metrics"].get(name, r["reward"])

    def summarize(rs: list) -> tuple[float, float, int]:
        if not rs:
            return (0.0, 0.0, 0)
        sim = sum(metric(r, "similarity_reward") for r in rs) / len(rs)
        ex = sum(metric(r, "exact_reward") for r in rs) / len(rs)
        return (sim, ex, len(rs))

    def slice_table(title: str, label_col: str, groups: list[tuple[str, tuple]]) -> None:
        """Render one breakdown table from (label, summary) pairs (summaries precomputed)."""
        table = Table(title=title)
        for col in (label_col, "n", "mean_similarity", "exact_rate"):
            table.add_column(col)
        for name, (s, e, c) in groups:
            table.add_row(name, str(c), f"{s:.3f}", f"{e:.3f}")
        console.print(table)

    sim, ex, n = summarize(rollouts)
    console.print(
        f"\n[bold]overall[/bold]  n={n}"
        + (f" [dim](+{errored} errored)[/dim]" if errored else "")
        + f"  mean_similarity={sim:.3f}  exact_rate={ex:.3f}"
    )

    by_diff = [(d, summarize([r for r in rollouts if r["info"].get("difficulty") == d]))
               for d in ("easy", "medium", "hard")]
    slice_table("by difficulty", "difficulty", [(d, s) for d, s in by_diff if s[2]])

    # one rollout can carry several transforms; credit it to each.
    transforms: dict[str, list] = {}
    for r in rollouts:
        for t in (r["info"].get("transforms") or "").split(","):
            if t:
                transforms.setdefault(t, []).append(r)
    if transforms:
        summaries = {t: summarize(rs) for t, rs in transforms.items()}
        ordered = sorted(summaries.items(), key=lambda kv: kv[1][0])
        slice_table("by transform", "transform", ordered)


@app.command()
def score(
    reference: str = typer.Argument(..., help="Ground-truth LaTeX (or @file)."),
    hypothesis: str = typer.Argument(..., help="Model transcription (or @file)."),
) -> None:
    """Score a single transcription against the reference."""
    ref = _read_arg(reference)
    hyp = _read_arg(hypothesis)
    s = score_fn(ref, hyp)
    table = Table(show_header=False)
    table.add_row("exact", str(s.exact))
    table.add_row("similarity", f"{s.similarity:.3f}")
    table.add_row("edit_distance", str(s.edit_distance))
    table.add_row("ref_len", str(s.ref_len))
    console.print(table)


def _read_arg(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


if __name__ == "__main__":
    app()
