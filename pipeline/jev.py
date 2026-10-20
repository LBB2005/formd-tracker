"""Jev (TypeSafe AI's decision model) as an independent second labeller for the review samples.

The tagger's own tie-break judge is Claude, so Claude can't grade it. Jev is a separate
model that never sees the tagger's call: it gets the filing details and homepage text
and answers the same question the reviewer does, with a confidence.

    uv run python -m pipeline.jev run              # ask Jev about every sample item (cached)
    uv run python -m pipeline.jev run --labelled   # only the items you have labelled
    uv run python -m pipeline.jev calibrate        # Jev vs your labels so far: can we trust it?
    uv run python -m pipeline.jev sweep            # review/sweep.csv: what still needs a human
    uv run python -m pipeline.jev desk IN OUT      # add Jev's picks and routes to Label Desk items
    uv run python -m pipeline.jev score            # tagger accuracy from human + auto labels

Routes for items you haven't labelled:
    disagree    Jev and the tagger give different answers: you decide (both calls shown)
    low_conf    they agree but Jev isn't sure: you decide blind
    blind       a random draw of confident agreements: you decide blind, which measures
                how often both are wrong together
    auto        confident agreement: the shared answer is taken as the label

Needs TYPESAFE_API_KEY in .env (scripts/set-key.sh writes it).
"""

import argparse
import csv
import json
import os
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline import ROOT
from pipeline.ai_tag import env_key
from pipeline.review import AI_SAMPLE, SAMPLE

OUT = ROOT / "review" / "jev_labels.csv"
SWEEP = ROOT / "review" / "sweep.csv"
MODEL = "jev-latest"
PROMPT_VERSION = "v1"
CONFIDENT = 0.8           # below this, an agreement still goes to a human
BLIND = {"ai": 40, "cls": 10}
SEED = "v1"
OUT_COLUMNS = ["item_id", "queue", "name", "jev", "jev_vehicle", "confidence", "probabilities",
               "model", "prompt_version", "error"]

AI_DEF = ("An AI company is one whose core product is an AI or machine-learning model, or a "
          "product built on one. Using AI as one feature among many, mentioning AI in "
          "marketing, or using AI internally does not count.")
AI_CRITERIA = {
    "ai": "The core product is an AI or machine-learning model, or a product built on one.",
    "not_ai": "The core product is something else. AI features, AI marketing or internal AI "
              "use do not make it an AI company.",
}
KIND_CRITERIA = {
    "operating_company": "A startup or operating business raising money for its own business.",
    "fund": "A fund or investment vehicle that pools investors' money to invest in other "
            "companies or assets: venture, private equity, hedge or credit funds, SPVs.",
    "real_estate": "A real estate property, project, developer or real estate investment entity.",
    "other_investing": "Another investing entity, such as a holding company or lending vehicle, "
                       "that is neither a pooled fund nor an operating business.",
}
VEHICLE_CRITERIA = {
    "blind_pool": "A fund raising a pool of capital to invest in many companies chosen later "
                  "(e.g. 'Example Ventures Fund II').",
    "spv": "A single-deal vehicle or series created to invest in one named company "
           "(e.g. 'XYZ SPV - Series 12', 'Fund (Investment in Acme)').",
    "unclear": "Not enough information to tell, or this is not a fund.",
}


def homepage(domain: str) -> dict:
    """Title, description, headings and a text excerpt from the cached homepage."""
    from pipeline import web
    if not domain:
        return {"available": False}
    got = web.fetch(domain)
    if not got.get("html"):
        return {"available": False, "reason": got.get("error") or f"status {got.get('status')}"}
    page = web.parse_page(got["html"])
    return {"available": True, "title": page.title, "description": page.description,
            "headings": page.headings[:6], "text": page.text[:2500]}


def load_items() -> list[dict]:
    """Both review samples as Label Desk-style items, without the tagger's call."""
    items = []
    if AI_SAMPLE.exists():
        for r in csv.DictReader(open(AI_SAMPLE)):
            items.append({"id": f"ai-{r['cik']}", "queue": "ai", "name": r["name"],
                          "predicted": r["predicted"], "human": r["human_label"].strip(),
                          "state": {"company": r["name"], "city": r["city"],
                                    "website": r["domain"] or "not found",
                                    "y_combinator_batch": r["yc_batch"] or "none",
                                    "domain": r["domain"]}})
    if SAMPLE.exists():
        for r in csv.DictReader(open(SAMPLE)):
            reviewed = r["reviewed"].strip().lower() in ("y", "yes")
            kind = r["true_kind"].strip() or r["kind"]
            items.append({"id": f"cls-{r['offering_id']}", "queue": "cls", "name": r["issuer_name"],
                          "predicted": r["kind"], "predicted_vehicle": r["vehicle_type"],
                          "human": kind if reviewed else "",
                          "human_vehicle": (r["true_vehicle_type"].strip() or r["vehicle_type"])
                          if reviewed and kind == "fund" else "",
                          "state": {"issuer_name": r["issuer_name"],
                                    "industry_group_as_filed": r["industry_group"],
                                    "fund_type_as_filed": r["fund_type"] or "none",
                                    "entity_type": r["entity_type"],
                                    "exemptions_claimed": r["exemptions"],
                                    "securities_offered": r["security_class"],
                                    "other_securities_description": r["other_type_desc"] or "none",
                                    "amount_sold_usd": r["sold_latest"]}})
    return items


def ask(client, item: dict) -> dict:
    from typesafe_sdk import Choice
    if item["queue"] == "ai":
        state = {k: v for k, v in item["state"].items() if k != "domain"}
        state["homepage"] = homepage(item["state"]["domain"])
        questions = {"ai": Choice(instructions=f"Is this an AI company? {AI_DEF}",
                                  criteria=AI_CRITERIA)}
    else:
        state = item["state"]
        questions = {
            "kind": Choice(instructions="What is this SEC Form D filer?", criteria=KIND_CRITERIA),
            "vehicle": Choice(instructions="If this filer is a fund, what kind of vehicle is it?",
                              criteria=VEHICLE_CRITERIA),
        }
    res = client.system_one(state={"form_d_filer": state}, questions=questions, model=MODEL)
    main = res.choices["ai" if item["queue"] == "ai" else "kind"]
    vehicle = res.choices.get("vehicle")
    return {"jev": main.choice, "confidence": round(main.confidence, 4),
            "jev_vehicle": vehicle.choice if vehicle and main.choice == "fund" else "",
            "probabilities": json.dumps({k: round(v, 4) for k, v in main.probabilities.items()}),
            "model": res.model}


def load_answers() -> dict[str, dict]:
    if not OUT.exists():
        return {}
    return {r["item_id"]: r for r in csv.DictReader(open(OUT))
            if r["prompt_version"] == PROMPT_VERSION and not r["error"]}


def run(queue: str | None, limit: int | None, workers: int, labelled: bool = False) -> None:
    from typesafe_sdk import TypeSafeClient, TypeSafeError
    os.environ.setdefault("TYPESAFE_API_KEY", env_key("TYPESAFE_API_KEY") or "")
    done = load_answers()
    todo = [it for it in load_items() if it["id"] not in done and (not queue or it["queue"] == queue)
            and (not labelled or it["human"])]
    todo = todo[:limit] if limit else todo
    print(f"Jev: {len(done)} answers cached, asking about {len(todo)} more")

    def one(item):
        row = {"item_id": item["id"], "queue": item["queue"], "name": item["name"],
               "prompt_version": PROMPT_VERSION, "error": ""}
        try:
            row |= ask(client, item)
        except TypeSafeError as exc:
            row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return row

    with TypeSafeClient() as client, ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(one, todo))
    failed = [r for r in rows if r["error"]]
    if failed and all("Authentication" in r["error"] for r in failed) and len(failed) == len(rows):
        raise SystemExit("TypeSafe rejected the API key. Re-save it with scripts/set-key.sh.")
    keep = list(done.values()) + [r for r in rows if not r["error"]]
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(keep, key=lambda r: r["item_id"]))
    print(f"Wrote {len(keep)} answers to {OUT.name}; {len(failed)} failed")
    for r in failed[:5]:
        print(f"  {r['item_id']}: {r['error']}")


def matches(item: dict, jev: dict, against: str) -> bool:
    """Does Jev's answer equal the tagger's (against='predicted') or the human's ('human')?"""
    if item["queue"] == "ai":
        return jev["jev"] == item[against]
    vehicle = item["predicted_vehicle" if against == "predicted" else "human_vehicle"]
    same_kind = jev["jev"] == item[against]
    return same_kind and (item[against] != "fund" or not vehicle or jev["jev_vehicle"] == vehicle)


def calibrate() -> None:
    answers = load_answers()
    for queue, title in (("ai", "AI company?"), ("cls", "Fund or startup?")):
        rows = [(it, answers[it["id"]]) for it in load_items()
                if it["queue"] == queue and it["human"] and it["human"] != "unsure" and it["id"] in answers]
        if not rows:
            print(f"{title}: no labelled items with a Jev answer yet")
            continue
        jev_right = sum(matches(it, a, "human") for it, a in rows)
        tag_right = sum(matches(it, {"jev": it["predicted"], "jev_vehicle": it.get("predicted_vehicle", "")},
                                "human") for it, _ in rows)
        print(f"{title} on {len(rows)} items you labelled:")
        print(f"  Jev agrees with you:        {jev_right}/{len(rows)} ({jev_right / len(rows):.0%})")
        print(f"  tagger agrees with you:     {tag_right}/{len(rows)} ({tag_right / len(rows):.0%})")
        both = [(it, a) for it, a in rows if matches(it, a, "predicted")]
        if both:
            wrong = sum(not matches(it, a, "human") for it, a in both)
            print(f"  when Jev and tagger agree:  both wrong on {wrong}/{len(both)} ({wrong / len(both):.0%})")
        for lo, hi in ((0, CONFIDENT), (CONFIDENT, 1.01)):
            band = [(it, a) for it, a in rows if lo <= float(a["confidence"]) < hi]
            if band:
                ok = sum(matches(it, a, "human") for it, a in band)
                print(f"  Jev confidence {lo:.1f}-{min(hi, 1):.1f}:     {ok}/{len(band)} right ({ok / len(band):.0%})")
        misses = [(it, a) for it, a in rows if not matches(it, a, "human")]
        for it, a in misses[:12]:
            you = "/".join(filter(None, [it["human"], it.get("human_vehicle")]))
            jev = "/".join(filter(None, [a["jev"], a["jev_vehicle"]]))
            print(f"    miss: {it['name'][:44]:<44} you={you:<22} jev={jev} ({float(a['confidence']):.2f})")


def routes() -> dict[str, str]:
    """item_id -> route for items without a human label (see module docstring)."""
    answers, out = load_answers(), {}
    for queue in ("ai", "cls"):
        agreed = []
        for it in load_items():
            if it["queue"] != queue or it["human"] or it["id"] not in answers:
                continue
            a = answers[it["id"]]
            if not matches(it, a, "predicted"):
                out[it["id"]] = "disagree"
            elif float(a["confidence"]) < CONFIDENT:
                out[it["id"]] = "low_conf"
            else:
                agreed.append(it["id"])
        rng = random.Random(f"{SEED}-{queue}")
        blind = set(rng.sample(sorted(agreed), min(BLIND[queue], len(agreed))))
        out |= {iid: "blind" if iid in blind else "auto" for iid in agreed}
    return out


def sweep() -> None:
    answers, route = load_answers(), routes()
    items = {it["id"]: it for it in load_items()}
    with open(SWEEP, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "queue", "name", "route", "tagger", "jev", "jev_vehicle", "confidence"])
        for iid, r in sorted(route.items(), key=lambda kv: (kv[1], kv[0])):
            it, a = items[iid], answers[iid]
            w.writerow([iid, it["queue"], it["name"], r,
                        it["predicted"] + (f"/{it['predicted_vehicle']}" if it.get("predicted_vehicle") else ""),
                        a["jev"], a["jev_vehicle"], a["confidence"]])
    counts = Counter((items[i]["queue"], r) for i, r in route.items())
    print(f"Wrote {SWEEP.name}")
    for queue in ("ai", "cls"):
        c = {r: counts[(queue, r)] for r in ("disagree", "low_conf", "blind", "auto")}
        print(f"  {queue}: {c}  -> {sum(c.values()) - c['auto']} for you to review")


def desk(src: Path, dst: Path) -> None:
    """Add jev, jev_vehicle, jev_conf and route to Label Desk item chunks (items/c*.json)."""
    answers, route = load_answers(), routes()
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in sorted(src.glob("c*.json")):
        doc = json.loads(path.read_text())
        for it in doc["items"]:
            a = answers.get(it["id"])
            if a:
                it |= {"jev": a["jev"], "jev_vehicle": a["jev_vehicle"] or None,
                       "jev_conf": float(a["confidence"])}
                n += 1
            it["route"] = route.get(it["id"], "labelled" if a else None)
        (dst / path.name).write_text(json.dumps({"items": doc["items"]}))
    print(f"Added Jev answers to {n} items in {dst}")


def score() -> None:
    """Tagger accuracy from human labels plus confident agreements (auto)."""
    answers, route = load_answers(), routes()
    for queue, title in (("ai", "AI tag"), ("cls", "fund/startup kind")):
        human = auto = right = total = 0
        blind = [it for it in load_items() if route.get(it["id"]) == "blind"]
        for it in load_items():
            if it["queue"] != queue:
                continue
            if it["human"] and it["human"] != "unsure":
                truth = {"jev": it["human"], "jev_vehicle": it.get("human_vehicle", "")}
                human += 1
            elif route.get(it["id"]) == "auto":
                truth = answers[it["id"]]
                auto += 1
            else:
                continue
            total += 1
            # matches(item, truth, "predicted"): is the tagger's call equal to the truth?
            right += matches(it, truth, "predicted")
        if total:
            print(f"{title}: tagger right on {right}/{total} ({right / total:.0%}); "
                  f"{human} labelled by you, {auto} confident Jev+tagger agreements")
        done_blind = [it for it in blind if it["queue"] == queue and it["human"]]
        if done_blind:
            wrong = sum(not matches(it, answers[it["id"]], "human") for it in done_blind)
            print(f"  blind check: both wrong on {wrong}/{len(done_blind)} confident agreements; "
                  f"about {wrong / len(done_blind) * auto:.0f} of the {auto} auto labels may be wrong")
    if AI_SAMPLE.exists():
        print("For precision/recall by tagged/untagged half, run: uv run python -m pipeline.review ai-score")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "calibrate", "sweep", "desk", "score"])
    parser.add_argument("paths", nargs="*", help="for desk: input and output item folders")
    parser.add_argument("--queue", choices=["ai", "cls"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--labelled", action="store_true", help="run: only items you've labelled")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args.queue, args.limit, args.workers, args.labelled)
    if args.command == "desk":
        if len(args.paths) != 2:
            raise SystemExit("Usage: uv run python -m pipeline.jev desk ITEMS_IN ITEMS_OUT")
        return desk(Path(args.paths[0]), Path(args.paths[1]))
    {"calibrate": calibrate, "sweep": sweep, "score": score}[args.command]()


if __name__ == "__main__":
    main()
