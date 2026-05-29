"""verifiers eval harness for ChaoTeX.

Turns a built dataset (`data/dataset/items.jsonl` + PNGs) into a
`vf.SingleTurnEnv`: each row is a single multimodal user message (instruction +
base64 image), the `answer` is the clean reference LaTeX, and the rubric wraps
`chaotex.score.score` into `similarity` (primary reward) and `exact` metrics.

`load_environment()` is what the `prime`/`verifiers` CLI discovers; the
`chaotex eval` CLI wrapper (see cli.py) drives it through OpenRouter.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import verifiers as vf
from datasets import Dataset

from .config import DATA_DIR, DEFAULT_MODEL
from .score import score as score_fn

# Provider wiring (OpenRouter, OpenAI-compatible). The key lives in the env var
# named here; verifiers reads it when we hand it a ClientConfig.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_VAR = "OPENROUTER_API_KEY"

INSTRUCTION = (
    "Transcribe the math in this image as LaTeX. "
    "Output only the LaTeX, with no explanation or surrounding text."
)

# A model may wrap its answer in a fenced block; grab the fence contents if so.
_FENCE_BLOCK = re.compile(r"```(?:latex|tex|math)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def image_data_url(path: str | Path) -> str:
    """Read a PNG and return it as an inline ``data:`` URL for the chat API."""
    data = Path(path).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def parse_latex(text: str) -> str:
    """Isolate the LaTeX from a model reply: prefer a fenced block, else strip stray fences/prose."""
    text = text.strip()
    m = _FENCE_BLOCK.search(text)
    if m:
        return m.group(1).strip()
    return text.strip("`").strip()


def build_dataset(
    items_path: str | Path,
    difficulty: str | None = None,
    max_items: int | None = None,
) -> Dataset:
    """Build a verifiers eval dataset from an `items.jsonl` produced by `chaotex build`.

    Each row: `prompt` (one multimodal user message), `answer` (reference LaTeX),
    and a flat `info` dict for per-slice reporting. `transforms`/`degrade` are
    comma-joined strings (keeps the Arrow schema flat and avoids empty-list typing).
    """
    items_path = Path(items_path)
    base = items_path.parent
    rows: list[dict] = []
    with items_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if difficulty and item["difficulty"] != difficulty:
                continue
            img = base / item["image"]
            prompt = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": INSTRUCTION},
                        {"type": "image_url", "image_url": {"url": image_data_url(img)}},
                    ],
                }
            ]
            rows.append(
                {
                    "prompt": prompt,
                    "answer": item["reference"],
                    "info": {
                        "id": item["id"],
                        "difficulty": item["difficulty"],
                        "transforms": ",".join(item.get("transforms", [])),
                        "degrade": ",".join(item.get("degrade", [])),
                        "arxiv_id": item.get("arxiv_id", ""),
                    },
                }
            )
            if max_items and len(rows) >= max_items:
                break
    if not rows:
        raise ValueError(f"no items in {items_path} (difficulty={difficulty}); run `chaotex build` first")
    return Dataset.from_list(rows)


def similarity_reward(completion, answer, parser, **kwargs) -> float:
    """Primary reward: normalized edit-distance similarity (0..1) of transcription vs reference."""
    hyp = parser.parse_answer(completion) or ""
    return score_fn(answer, hyp).similarity


def exact_reward(completion, answer, parser, **kwargs) -> float:
    """Binary: 1.0 iff the transcription matches the reference exactly after normalization."""
    hyp = parser.parse_answer(completion) or ""
    return 1.0 if score_fn(answer, hyp).exact else 0.0


def load_environment(
    items_path: str | Path | None = None,
    difficulty: str | None = None,
    max_items: int | None = None,
    **kwargs,
) -> vf.SingleTurnEnv:
    """Return the ChaoTeX `SingleTurnEnv`. Discovered by the verifiers/prime CLI."""
    if items_path is None:
        items_path = DATA_DIR / "dataset" / "items.jsonl"
    dataset = build_dataset(items_path, difficulty=difficulty, max_items=max_items)
    parser = vf.Parser(extract_fn=parse_latex)
    # `exact` carries weight 0 — it rides along as a reported metric, not reward.
    rubric = vf.Rubric(funcs=[similarity_reward, exact_reward], weights=[1.0, 0.0], parser=parser)
    return vf.SingleTurnEnv(eval_dataset=dataset, parser=parser, rubric=rubric)


def openrouter_client_config() -> vf.ClientConfig:
    """ClientConfig pointing verifiers at OpenRouter (key from $OPENROUTER_API_KEY)."""
    return vf.ClientConfig(
        client_type="openai_chat_completions",
        api_base_url=OPENROUTER_BASE_URL,
        api_key_var=OPENROUTER_KEY_VAR,
    )


def probe_service_tier(model: str = DEFAULT_MODEL) -> str | None:
    """One tiny direct call to confirm the provider honored `service_tier: flex`.

    verifiers normalizes away the raw response, so we read the served tier from a
    standalone OpenAI-SDK call. Returns the served tier string ("flex"/"default"),
    or None if the field wasn't echoed.
    """
    import os

    from openai import OpenAI

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=os.environ[OPENROUTER_KEY_VAR])
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=1,
        service_tier="flex",
    )
    return getattr(resp, "service_tier", None)
