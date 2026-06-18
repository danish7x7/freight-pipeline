"""Phase 9 — real-model latency + token-usage/cost sample (the HF-dominated number).

DISTINCT from the pipeline load test (which excludes model time): this times live HF
calls end to end so the README can state the two numbers separately and never conflate
them. It also captures token usage per email — the input to the per-email LLM cost — so
the future pre-LLM sender-filter decision (the 8.3b carry-forward) can be made against
measured numbers. Measured here, NOT acted on.

Run: ``uv run python scripts/eval_llm_latency.py [--samples N] [--json eval/llm.json]``.
Needs HF_TOKEN + HF_MODEL (provider-pinned). Cost uses ``HF_USD_PER_MTOK`` (the
provider's published $/MTok); unset => cost is reported as n/a, never fabricated.
"""

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass

import httpx

from freight.config import get_settings
from freight.extraction.prompts import build_extraction_prompt
from freight.synthetic import generate_dataset

_CHAT_PATH = "/v1/chat/completions"


@dataclass
class CallSample:
    """One timed live HF call with its token usage."""

    id: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def usd_per_mtok() -> float | None:
    raw = os.environ.get("HF_USD_PER_MTOK", "").strip()
    return float(raw) if raw else None


def cost_usd(total_tokens: int, rate_per_mtok: float | None) -> float | None:
    """Cost for ``total_tokens`` at the provider rate, or None if no rate is given."""
    if rate_per_mtok is None:
        return None
    return total_tokens / 1_000_000 * rate_per_mtok


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


async def time_calls(samples: int) -> list[CallSample]:
    """Time ``samples`` live HF extraction calls over body-vector corpus emails."""
    settings = get_settings()
    if not settings.hf_token or not settings.hf_model:
        raise SystemExit("HF_TOKEN and HF_MODEL required for the live latency sample.")
    url = f"{settings.hf_base_url.rstrip('/')}{_CHAT_PATH}"
    corpus = [s for s in generate_dataset() if s.attachment_text is None][:samples]
    out: list[CallSample] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for s in corpus:
            prompt = build_extraction_prompt(s.message.subject, s.message.body)
            payload = {
                "model": settings.hf_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
            }
            start = time.perf_counter()
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.hf_token}"},
                json=payload,
            )
            latency_ms = (time.perf_counter() - start) * 1000
            usage = resp.json().get("usage", {}) if resp.status_code == 200 else {}
            out.append(
                CallSample(
                    id=s.message.gmail_message_id,
                    latency_ms=latency_ms,
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                    total_tokens=int(usage.get("total_tokens", 0)),
                )
            )
    return out


def render(samples: list[CallSample], rate: float | None) -> str:
    lat = [s.latency_ms for s in samples]
    mean_prompt = round(statistics.mean(s.prompt_tokens for s in samples))
    mean_completion = round(statistics.mean(s.completion_tokens for s in samples))
    mean_total = round(statistics.mean(s.total_tokens for s in samples))
    per_email = cost_usd(mean_total, rate)

    out: list[str] = []
    out.append("# Phase 9 — real-model latency + per-email LLM cost (HF-dominated)")
    out.append("")
    out.append(f"- samples: {len(samples)} live calls")
    out.append(
        "- THIS IS NOT the pipeline number: it includes HF inference time end to end."
    )
    out.append("")
    out.append("## Latency (live HF call round-trip — the dominant term in extract())")
    out.append(f"- median: {percentile(lat, 50):.0f} ms")
    out.append(f"- p95: {percentile(lat, 95):.0f} ms")
    out.append(f"- min / max: {min(lat):.0f} / {max(lat):.0f} ms")
    out.append("")
    out.append("## Token usage per email (measured)")
    out.append(
        f"- prompt {mean_prompt} + completion {mean_completion} = "
        f"**{mean_total} tokens/email** (mean)"
    )
    out.append("")
    out.append("## Per-email LLM cost")
    if per_email is None:
        out.append("- n/a — set HF_USD_PER_MTOK to the provider's published $/MTok")
    else:
        out.append(
            f"- ${per_email:.6f}/email = {mean_total} tokens x ${rate:.4f}/MTok "
            "(provider published rate; confirm on the pricing page)"
        )
    return "\n".join(out)


async def _run(samples: int, json_path: str | None) -> None:
    rows = await time_calls(samples)
    rate = usd_per_mtok()
    print(render(rows, rate))
    if json_path is not None:
        payload = {
            "rate_usd_per_mtok": rate,
            "samples": [asdict(r) for r in rows],
        }
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\n(wrote {json_path})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 9 real-model latency/cost.")
    parser.add_argument("--samples", type=int, default=8, help="number of live calls")
    parser.add_argument("--json", help="optional path to write the raw samples JSON")
    args = parser.parse_args()
    asyncio.run(_run(args.samples, args.json))


if __name__ == "__main__":
    main()
