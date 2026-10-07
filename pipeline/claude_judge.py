"""Claude as the tie-break judge for ambiguous homepages (Message Batches API, 50% off).

A homepage is ambiguous when it mentions AI only in body text. Claude Opus 5.5 answers
whether the company's core product is AI, as JSON with a verbatim quote. A "yes" counts
only if that quote appears in the text we sent and contains an AI term, the same check
used for the local model. Requests go out as batches; batch IDs are kept in
data/enrich/claude_batches.json so a later run (or CI job) collects the results.

Server-side refusal fallbacks aren't available on the Batches API, so a declined request
is recorded as "unclear", which leaves the company untagged rather than guessing.

    uv run python -m pipeline.claude_judge                # pending batches and spend so far
    uv run python -m pipeline.ai_tag --pilot 50           # synchronous pilot: measures cost
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from hashlib import sha1

import anthropic

from pipeline import DATA_DIR

MODEL = "claude-opus-5-5"
PROMPT_VERSION = "v3-claude-quote"
STATE = DATA_DIR / "enrich" / "claude_batches.json"
PRICE_IN, PRICE_OUT = 4.00, 20.00  # $ per million tokens, standard; batches are half
AI_TERMS = re.compile(
    r"(?i)\b(a\.?i\.?|artificial intelligence|machine learning|deep learning|neural networks?|"
    r"large language models?|llms?|generative ai|gen ?ai|computer vision|natural language "
    r"processing|nlp|ai[- ](?:powered|native|first|driven)|ai agents?|agentic|foundation models?)\b")

SYSTEM = (
    "You classify companies for an investment research dataset built from SEC Form D "
    "filings. Definition: an AI company is one whose core product is an AI or "
    "machine-learning model, or a product built on one. A company that uses AI as one "
    "feature among many, mentions AI in marketing, or sells a non-AI product does not "
    "qualify. Judge only from the homepage text provided."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "quote": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["answer", "quote", "reason"],
    "additionalProperties": False,
}


def excerpt(page) -> str:
    return f"{page.hero[:600]}\n{page.text[:1800]}"


def params(name: str, domain: str, text: str) -> dict:
    return {
        "model": MODEL,
        "max_tokens": 4000,
        "system": SYSTEM,
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user", "content": (
            f"Company: {name}\nWebsite: {domain}\n\nHomepage text:\n{text}\n\n"
            "Is this company's core product an AI or machine-learning model, or built on one? "
            "Set quote to the exact phrase copied from the homepage text that describes the "
            "core product (empty string if there is none), and reason to one short sentence."
        )}],
    }


def interpret(raw_text: str | None, sent_text: str, stop_reason: str | None) -> dict:
    """Model output -> answer record, applying the verbatim-quote rule."""
    if stop_reason == "refusal" or not raw_text:
        return {"answer": "unclear", "quote": "", "quote_verified": False, "reason": "no answer"}
    try:
        out = json.loads(raw_text)
    except ValueError:
        return {"answer": "unclear", "quote": "", "quote_verified": False, "reason": "bad json"}
    answer = out.get("answer", "unclear")
    quote = " ".join(str(out.get("quote", "")).split())
    verified = bool(quote) and quote.lower() in " ".join(sent_text.split()).lower() \
        and bool(AI_TERMS.search(quote))
    if answer == "yes" and not verified:
        answer = "unclear"
    return {"answer": answer, "quote": quote[:300], "quote_verified": verified,
            "reason": str(out.get("reason", ""))[:300]}


def _text(message) -> str | None:
    return next((b.text for b in message.content if b.type == "text"), None)


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"batches": [], "usage": {"input_tokens": 0, "output_tokens": 0}}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def submit(items: list[tuple[str, str, str, str]], max_per_batch: int = 5000) -> list[str]:
    """items: (cik, name, domain, excerpt). Returns new batch IDs and records them."""
    client = anthropic.Anthropic()
    state = load_state()
    ids = []
    for i in range(0, len(items), max_per_batch):
        chunk = items[i:i + max_per_batch]
        batch = client.messages.batches.create(requests=[
            {"custom_id": cik, "params": params(name, domain, text)}
            for cik, name, domain, text in chunk
        ])
        state["batches"].append({
            "id": batch.id, "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prompt_version": PROMPT_VERSION, "collected": False,
            # Keep what was sent so the quote check can run when results come back.
            "sent": {cik: text for cik, _, _, text in chunk},
        })
        ids.append(batch.id)
    save_state(state)
    return ids


def collect() -> dict[str, dict]:
    """Collect every finished, uncollected batch. Returns {cik: answer record}."""
    state = load_state()
    if not any(not b["collected"] for b in state["batches"]):
        return {}
    client = anthropic.Anthropic()
    answers = {}
    for b in state["batches"]:
        if b["collected"]:
            continue
        status = client.messages.batches.retrieve(b["id"])
        if status.processing_status != "ended":
            print(f"  batch {b['id']}: {status.processing_status}", flush=True)
            continue
        for result in client.messages.batches.results(b["id"]):
            sent = b["sent"].get(result.custom_id, "")
            if result.result.type == "succeeded":
                msg = result.result.message
                state["usage"]["input_tokens"] += msg.usage.input_tokens
                state["usage"]["output_tokens"] += msg.usage.output_tokens
                rec = interpret(_text(msg), sent, msg.stop_reason)
            else:
                rec = {"answer": "unclear", "quote": "", "quote_verified": False,
                       "reason": f"batch {result.result.type}"}
            rec.update(model=MODEL, prompt_version=b["prompt_version"],
                       prompt_sha=sha1(sent.encode()).hexdigest()[:12])
            answers[result.custom_id] = rec
        b["collected"] = True
        b.pop("sent", None)
        print(f"  batch {b['id']}: collected", flush=True)
    save_state(state)
    return answers


def pending_ciks() -> set[str]:
    return {cik for b in load_state()["batches"] if not b["collected"] for cik in b.get("sent", {})}


def spend(state: dict) -> float:
    u = state["usage"]
    return 0.5 * (u["input_tokens"] * PRICE_IN + u["output_tokens"] * PRICE_OUT) / 1e6


def pilot(items: list[tuple[str, str, str, str]]) -> float:
    """Synchronous run on a few companies to measure tokens and project the batch cost."""
    client = anthropic.Anthropic()
    tin = tout = 0
    for cik, name, domain, text in items:
        msg = client.messages.create(**params(name, domain, text))
        tin += msg.usage.input_tokens
        tout += msg.usage.output_tokens
        rec = interpret(_text(msg), text, msg.stop_reason)
        print(f"  {rec['answer']:8} {name[:40]:40} {rec['quote'][:70]!r}")
    n = len(items)
    per = (tin * PRICE_IN + tout * PRICE_OUT) / 1e6 / n
    print(f"\nPilot: {n} companies, {tin / n:.0f} input + {tout / n:.0f} output tokens each, "
          f"${per:.4f} each at standard price (${per / 2:.4f} via batches).")
    return per


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    state = load_state()
    pending = [b for b in state["batches"] if not b["collected"]]
    print(f"Batches: {len(state['batches'])} submitted, {len(pending)} pending; "
          f"spend so far ~${spend(state):.2f} (batch pricing)")


if __name__ == "__main__":
    main()
