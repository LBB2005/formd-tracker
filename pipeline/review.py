"""Hand review of the classification rules (Week 2) and the AI tag (Week 3).

    uv run python -m pipeline.review sample      # review/classification_sample.csv (100 rows)
    uv run python -m pipeline.review score       # accuracy of fund/startup and SPV calls
    uv run python -m pipeline.review ai-sample   # review/ai_label_sample.csv (200 + 200)
    uv run python -m pipeline.review ai-score    # precision / recall of the AI tag
    uv run python -m pipeline.review import form-d-labels.csv   # labels from the Label Desk app
    uv run python -m pipeline.review focus IN OUT   # route Desk items: re-checks + ~200/50 sample

The Label Desk app (review/app/label-desk.html, published as an artifact) is the easy way
to label: it shows one company at a time, hides the tool's call until you answer, and
its "Download all labels" button produces the CSV that `import` reads.

AI labelling: for each company, read its homepage (and EDGAR if needed) and set
human_label to `ai` or `not_ai` using the definition: core product is an AI/ML model or
built on one; mentioning AI features does not qualify. Leave blank to skip.

How to review: open the CSV, check each row on EDGAR (edgar_url), set reviewed=y, and
fill true_kind / true_vehicle_type ONLY where the rule got it wrong. Blank means the
rule was right. Values:
    kind:         fund | operating_company | real_estate | other_investing
    vehicle_type: blind_pool | spv | unclear   (funds only)
"""

import argparse
import csv
from collections import Counter
from pathlib import Path

import duckdb

from pipeline import DB_PATH, ROOT

REVIEW_DIR = ROOT / "review"
SAMPLE = REVIEW_DIR / "classification_sample.csv"
SEED = "v1"  # change to draw a fresh sample

# (label, filter, rows). SF/LA offerings that started in the window.
STRATA = [
    ("operating company", "c.kind = 'operating_company'", 25),
    ("real estate", "c.kind = 'real_estate'", 5),
    ("other investing", "c.kind = 'other_investing'", 5),
    ("VC blind pool", "c.fund_bucket = 'venture' and c.vehicle_type = 'blind_pool'", 20),
    ("VC SPV", "c.fund_bucket = 'venture' and c.vehicle_type = 'spv'", 15),
    ("VC unclear", "c.fund_bucket = 'venture' and c.vehicle_type = 'unclear'", 15),
    ("other funds", "c.kind = 'fund' and c.fund_bucket <> 'venture'", 15),
]

COLUMNS = [
    "stratum", "offering_id", "cik", "issuer_name", "metro", "industry_group", "fund_type",
    "entity_type", "exemptions", "security_class", "other_type_desc", "sold_latest",
    "kind", "kind_reason", "vehicle_type", "vehicle_reason", "manager_key", "fund_number",
    "edgar_url",
]
LABELS = ["reviewed", "true_kind", "true_vehicle_type", "notes"]


def sample(force: bool) -> None:
    if SAMPLE.exists() and not force:
        raise SystemExit(f"{SAMPLE} exists and may hold your labels. Use --force to overwrite.")
    REVIEW_DIR.mkdir(exist_ok=True)
    rows = []
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        for label, where, n in STRATA:
            rows += con.execute(f"""
                select ? as stratum, o.offering_id, o.cik, o.issuer_name, o.metro,
                       o.industry_group, o.fund_type, o.entity_type,
                       array_to_string(o.exemptions, ' '), o.security_class, o.other_type_desc,
                       o.sold_latest, c.kind, c.kind_reason, c.vehicle_type, c.vehicle_reason,
                       c.manager_key, c.fund_number, i.edgar_url
                from offerings o
                join offering_class c using (offering_id)
                join issuers i on i.cik = o.cik
                where o.metro is not null and o.is_new_in_window and ({where})
                qualify row_number() over (partition by o.cik order by hash(o.offering_id || ?)) = 1
                order by hash(o.offering_id || ?)
                limit ?
            """, [label, SEED, SEED, n]).fetchall()
    with open(SAMPLE, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS + LABELS)
        for row in rows:
            writer.writerow(list(row) + ["", "", "", ""])
    print(f"Wrote {len(rows)} rows to {SAMPLE.relative_to(ROOT)}")
    print("Mark reviewed=y per row; fill true_* only where the rule is wrong.")


def score() -> None:
    if not SAMPLE.exists():
        raise SystemExit("No sample yet. Run: uv run python -m pipeline.review sample")
    rows = [r for r in csv.DictReader(open(SAMPLE)) if r["reviewed"].strip().lower() in ("y", "yes")]
    if not rows:
        raise SystemExit("No rows marked reviewed=y yet.")
    kind_right = sum(not r["true_kind"].strip() or r["true_kind"].strip() == r["kind"] for r in rows)
    funds = [r for r in rows if (r["true_kind"].strip() or r["kind"]) == "fund"]
    veh_right = sum(
        not r["true_vehicle_type"].strip() or r["true_vehicle_type"].strip() == r["vehicle_type"]
        for r in funds
    )
    print(f"Reviewed rows: {len(rows)}")
    print(f"  kind accuracy:         {kind_right}/{len(rows)} ({kind_right / len(rows):.0%})")
    if funds:
        print(f"  vehicle_type accuracy: {veh_right}/{len(funds)} funds ({veh_right / len(funds):.0%})")
    confusion = Counter(
        (r["vehicle_type"], r["true_vehicle_type"].strip() or r["vehicle_type"]) for r in funds
    )
    print("  vehicle_type (rule -> truth):")
    for (rule, truth), n in sorted(confusion.items()):
        print(f"    {rule:>10} -> {truth:<10} {n}")
    by_stratum = Counter(r["stratum"] for r in rows)
    wrong = [r for r in rows if (r["true_kind"].strip() and r["true_kind"].strip() != r["kind"]) or (
        r["true_vehicle_type"].strip() and r["true_vehicle_type"].strip() != r["vehicle_type"])]
    print(f"  per stratum reviewed: {dict(by_stratum)}")
    if wrong:
        print("\nMisclassified rows (fix a rule if several share a pattern; else add to "
              "config/manual_overrides.csv):")
        for r in wrong:
            print(f"  {r['issuer_name'][:60]:<60} rule={r['kind']}/{r['vehicle_type'] or '-'} "
                  f"truth={r['true_kind'] or r['kind']}/{r['true_vehicle_type'] or r['vehicle_type'] or '-'}"
                  f"  [{r['kind_reason']}; {r['vehicle_reason']}]")


AI_SAMPLE = REVIEW_DIR / "ai_label_sample.csv"
AI_COLUMNS = ["cik", "name", "city", "domain", "yc_batch", "predicted", "ai_tier", "ai_evidence",
              "edgar_url", "human_label", "notes"]


def ai_sample(force: bool) -> None:
    """200 companies the tag calls AI and 200 it doesn't, from the SF/LA startup universe."""
    if AI_SAMPLE.exists() and not force:
        raise SystemExit(f"{AI_SAMPLE} exists and may hold your labels. Use --force to overwrite.")
    REVIEW_DIR.mkdir(exist_ok=True)
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        universe = """
            select i.cik, i.name, i.city, d.domain, y.batch,
                   case when i.ai_tier in ('strong', 'likely') then 'ai' else 'not_ai' end as predicted,
                   coalesce(i.ai_tier, ''), array_to_string(i.ai_evidence, ' | '), i.edgar_url
            from issuers i
            left join company_domains d using (cik)
            left join yc_matches y using (cik)
            where i.metro is not null and i.is_venture_stage
              and i.cik in (select cik from offerings where is_startup_raise and is_new_in_window)
        """
        rows = []
        for predicted in ("ai", "not_ai"):
            rows += con.execute(f"""
                select * from ({universe}) u where predicted = ?
                order by hash(cik || ?) limit 200
            """, [predicted, SEED]).fetchall()
    with open(AI_SAMPLE, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(AI_COLUMNS)
        for row in rows:
            writer.writerow(list(row) + ["", ""])
    print(f"Wrote {len(rows)} rows to {AI_SAMPLE.relative_to(ROOT)} "
          f"({sum(r[5] == 'ai' for r in rows)} tagged AI, {sum(r[5] == 'not_ai' for r in rows)} not)")
    print("Set human_label to ai / not_ai per row (definition in the file docstring).")


def ai_score() -> None:
    if not AI_SAMPLE.exists():
        raise SystemExit("No AI sample yet. Run: uv run python -m pipeline.review ai-sample")
    rows = [r for r in csv.DictReader(open(AI_SAMPLE)) if r["human_label"].strip() in ("ai", "not_ai")]
    if not rows:
        raise SystemExit("No rows labelled yet (human_label = ai / not_ai).")
    tp = sum(r["predicted"] == "ai" and r["human_label"].strip() == "ai" for r in rows)
    fp = sum(r["predicted"] == "ai" and r["human_label"].strip() == "not_ai" for r in rows)
    fn = sum(r["predicted"] == "not_ai" and r["human_label"].strip() == "ai" for r in rows)
    tn = sum(r["predicted"] == "not_ai" and r["human_label"].strip() == "not_ai" for r in rows)
    print(f"Labelled: {len(rows)}  (tagged AI: {tp + fp}, not tagged: {fn + tn})")
    if tp + fp:
        print(f"  precision: {tp}/{tp + fp} = {tp / (tp + fp):.0%}")
    if tp + fn:
        # The untagged half is a random sample of a much larger untagged pool, so recall
        # from this sample needs reweighting; report the raw figure and say so.
        print(f"  recall (sample, unweighted): {tp}/{tp + fn} = {tp / (tp + fn):.0%}")
    print("Wrong calls:")
    for r in rows:
        if (r["predicted"] == "ai") != (r["human_label"].strip() == "ai"):
            print(f"  {r['name'][:50]:<50} tag={r['predicted']:<7} you={r['human_label']:<7} {r['ai_evidence'][:80]}")


def import_labels(path: str, reviewer: str = "me") -> None:
    """Merge a Label Desk CSV export into the review CSVs (one reviewer's labels)."""
    rows = [r for r in csv.DictReader(open(path)) if r["reviewer"] == reviewer]
    ai = {r["item_id"].removeprefix("ai-"): r for r in rows if r["queue"] == "ai"}
    cls = {r["item_id"].removeprefix("cls-"): r for r in rows if r["queue"] == "cls"}
    n_ai = n_cls = 0
    if AI_SAMPLE.exists() and ai:
        sample = list(csv.DictReader(open(AI_SAMPLE)))
        for r in sample:
            lab = ai.get(r["cik"])
            if lab and lab["label_ai"] in ("ai", "not_ai", "unsure"):
                # "unsure" (e.g. on a re-check) clears an earlier answer: it no longer counts.
                r["human_label"] = "" if lab["label_ai"] == "unsure" else lab["label_ai"]
                r["notes"] = lab["note"]
                n_ai += lab["label_ai"] != "unsure"
        with open(AI_SAMPLE, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=AI_COLUMNS)
            w.writeheader()
            w.writerows(sample)
    if SAMPLE.exists() and cls:
        sample = list(csv.DictReader(open(SAMPLE)))
        for r in sample:
            lab = cls.get(r["offering_id"])
            if lab and lab["label_kind"]:
                r["reviewed"] = "y"
                r["true_kind"] = "" if lab["label_kind"] == r["kind"] else lab["label_kind"]
                veh = lab["label_vehicle"] if lab["label_kind"] == "fund" else ""
                r["true_vehicle_type"] = "" if not veh or veh == r["vehicle_type"] else veh
                r["notes"] = lab["note"]
                n_cls += 1
        with open(SAMPLE, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS + LABELS)
            w.writeheader()
            w.writerows(sample)
    print(f"Imported {n_ai} AI labels and {n_cls} fund/startup labels from {path}.")


RECHECK = REVIEW_DIR / "recheck.csv"
TARGET_AI = {"ai": 100, "not_ai": 100}  # labelled items wanted per tagger call (~200 total)
TARGET_CLS = 50


def focus(src: Path, dst: Path) -> None:
    """Route Label Desk items (items/c*.json) so the "Needs you" view shows a smaller sample.

    recheck: an existing label to look at again (review/recheck.csv), shown first and blind
    todo:    a seeded random top-up to reach TARGET_AI / TARGET_CLS labelled items
    later:   unlabelled and not needed for now
    """
    import json
    import random
    from datetime import datetime, timezone

    ai = {f"ai-{r['cik']}": r for r in csv.DictReader(open(AI_SAMPLE))}
    cls = {f"cls-{r['offering_id']}": r for r in csv.DictReader(open(SAMPLE))}
    done = {i for i, r in ai.items() if r["human_label"].strip() in ("ai", "not_ai")}
    done |= {i for i, r in cls.items() if r["reviewed"].strip().lower() in ("y", "yes")}
    recheck = {r["item_id"] for r in csv.DictReader(open(RECHECK))} if RECHECK.exists() else set()
    rng = random.Random(f"focus-{SEED}")
    todo = set()
    for call, want in TARGET_AI.items():
        pool = sorted(i for i, r in ai.items() if r["predicted"] == call and i not in done)
        have = sum(ai[i]["predicted"] == call for i in done if i in ai)
        todo |= set(rng.sample(pool, max(0, min(len(pool), want - have))))
    pool = sorted(i for i in cls if i not in done)
    todo |= set(rng.sample(pool, max(0, min(len(pool), TARGET_CLS - len(done & cls.keys())))))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    dst.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    for path in sorted(src.glob("c*.json")):
        doc = json.loads(path.read_text())
        for it in doc["items"]:
            for key in ("route", "recheck_after", "jev", "jev_vehicle", "jev_conf"):
                it.pop(key, None)
            iid = it["id"]
            route = ("recheck" if iid in recheck else "todo" if iid in todo
                     else None if iid in done else "later")
            if route:
                it["route"] = route
            if route == "recheck":
                it["recheck_after"] = stamp
            counts[(it["queue"], route or "labelled")] += 1
        (dst / path.name).write_text(json.dumps({"items": doc["items"]}))
    for q in ("ai", "cls"):
        print(f"{q}: " + ", ".join(f"{r} {n}" for (qq, r), n in sorted(counts.items()) if qq == q))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["sample", "score", "ai-sample", "ai-score", "import", "focus"])
    parser.add_argument("path", nargs="?", help="Label Desk CSV for `import`; items folder for `focus`")
    parser.add_argument("out", nargs="?", help="output items folder for `focus`")
    parser.add_argument("--reviewer", default="me", help="whose labels to import (default: you)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "import":
        if not args.path:
            raise SystemExit("Usage: uv run python -m pipeline.review import form-d-labels.csv")
        return import_labels(args.path, args.reviewer)
    if args.command == "focus":
        if not (args.path and args.out):
            raise SystemExit("Usage: uv run python -m pipeline.review focus ITEMS_IN ITEMS_OUT")
        return focus(Path(args.path), Path(args.out))
    {"sample": lambda: sample(args.force), "score": score,
     "ai-sample": lambda: ai_sample(args.force), "ai-score": ai_score}[args.command]()


if __name__ == "__main__":
    main()
