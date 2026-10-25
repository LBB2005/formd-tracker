"""AI-tag evidence beyond company names: YC directory, homepages, model tie-break.

    uv run python -m pipeline.ai_tag                         # whole US, Claude judge
    uv run python -m pipeline.ai_tag --metros sf_bay,la      # just some metros
    uv run python -m pipeline.ai_tag --max-minutes 240       # stop after 4 hours (CI chunks)
    uv run python -m pipeline.ai_tag --pilot 50              # paid Claude pilot: measures cost
    uv run python -m pipeline.ai_tag --judge ollama          # free local model instead
    uv run python -m pipeline.ai_tag --judge none            # no model step

Runs on a built database and is resumable: every company checked is recorded in
data/enrich/ai_checks.parquet (re-checked after 90 days), so each run only checks new
companies, tracked metros first. Ambiguous homepages go to Claude Opus 5.5 through the
Message Batches API; batches submitted in one run are collected by the next
(state in data/enrich/claude_batches.json). Outputs in data/enrich/*.parquet are loaded
into ai_signals_external by the next build for sql/40_ai_tag.sql.

Universe: venture-stage operating companies with a raise since 2019, excluding industries
where AI products are vanishingly rare (restaurants, energy, travel and similar).
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha1

import duckdb
import requests

from pipeline import CACHE_DIR, DATA_DIR, DB_PATH, ROOT
from pipeline.web import PARKED, REFRESH_DAYS, fetch, final_domain, parse_page, valid_domain

ENRICH_DIR = DATA_DIR / "enrich"
YC_URL = "https://yc-oss.github.io/api/companies/all.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3.2:3b"

EXCLUDED_INDUSTRIES = (
    "Restaurants", "Agriculture", "Oil and Gas", "Other Energy", "Coal Mining",
    "Electric Utilities", "Lodging and Conventions", "Tourism and Travel Services",
    "Other Travel", "Airlines and Airports", "Hospitals and Physicians",
)

LEGAL = {"inc", "incorporated", "corp", "corporation", "co", "company", "llc", "l", "c",
         "ltd", "limited", "pbc", "plc", "lp", "holdings", "holding", "the"}
GENERIC = LEGAL | {"technologies", "technology", "tech", "labs", "lab", "ai", "software",
                   "systems", "health", "bio", "therapeutics", "robotics", "app", "hq",
                   "group", "studios", "studio", "solutions", "platform", "global", "usa",
                   "us", "international", "data", "digital", "network", "networks", "and"}

# AI vocabulary for homepage text. "ML" alone is skipped (millilitres, Maximum Likelihood).
AI_TERMS = re.compile(
    r"(?i)\b(a\.?i\.?|artificial intelligence|machine learning|deep learning|"
    r"neural networks?|large language models?|llms?|generative ai|gen ?ai|"
    r"computer vision|natural language processing|nlp|ai[- ](?:powered|native|first|driven)|"
    r"ai agents?|agentic|foundation models?|reinforcement learning|transformer models?)\b")
YC_AI_TAGS = re.compile(
    r"(?i)\b(ai|artificial intelligence|machine learning|deep learning|computer vision|nlp|"
    r"generative|llm|conversational ai|aiops|ai assistant)\b")

SYSTEM_PROMPT = (
    "You classify companies for an investment research dataset. Definition: an AI company "
    "is one whose core product is an AI or machine-learning model, or is built on one. "
    "Companies that only mention AI features, use AI internally, or sell non-AI products "
    "do not qualify. Reply with JSON only."
)


# ---------- names ----------

def tokens(name: str) -> list[str]:
    name = name.lower().replace("&", " and ")
    name = re.sub(r"\.ai\b", " ai", name)
    return [t for t in re.split(r"[^a-z0-9]+", name) if t]


def norm(name: str) -> str:
    """Legal name -> comparison key: "Harvey AI Corp" -> "harveyai"."""
    toks = tokens(name)
    while toks and toks[-1] in LEGAL:
        toks.pop()
    return "".join(toks)


def core(name: str) -> str:
    """Name without generic words: "Acme Robotics Technologies, Inc." -> "acme"."""
    return "".join(t for t in tokens(name) if t not in GENERIC)


def distinctive(name: str) -> str:
    """Longest non-generic token, used to verify a homepage belongs to the company."""
    cands = [t for t in tokens(name) if t not in GENERIC and len(t) >= 3]
    return max(cands, key=len) if cands else norm(name)


# ---------- step 1: Y Combinator directory ----------

def load_yc() -> list[dict]:
    path = CACHE_DIR / "yc" / "companies_all.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 7 * 86400:
        path.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(YC_URL, timeout=60)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return json.loads(path.read_text())


def match_yc(companies: list[tuple], yc: list[dict]) -> dict[str, dict]:
    """cik -> YC record, on a unique normalized-name match (then unique core-name match)."""
    by_norm: dict[str, list[dict]] = {}
    by_core: dict[str, list[dict]] = {}
    for rec in yc:
        for n in [rec["name"], *(rec.get("former_names") or [])]:
            if len(norm(n)) >= 4:
                by_norm.setdefault(norm(n), []).append(rec)
            if len(core(n)) >= 5:
                by_core.setdefault(core(n), []).append(rec)
    out = {}
    for cik, name, former, _metro, _ind in companies:
        names = [name, *(former or [])]
        for key_fn, index, method in ((norm, by_norm, "name"), (core, by_core, "core name")):
            hits = {r["id"]: r for n in names for r in index.get(key_fn(n), [])}
            if len(hits) == 1:
                rec = next(iter(hits.values()))
                out[cik] = {**rec, "_method": method}
                break
    return out


# ---------- step 2: domains ----------

def candidate_domains(name: str, yc_site: str | None) -> list[tuple[str, str]]:
    cands = []
    if yc_site:
        host = (re.sub(r"^https?://", "", yc_site).split("/")[0]).lower().removeprefix("www.")
        if host:
            cands.append((host, "yc"))
    full, short = norm(name), core(name)
    for slug in dict.fromkeys([full, short]):
        if len(slug) < 3:
            continue
        for d in (f"{slug}.com", f"{slug}.ai", f"{slug}.io", f"{slug}.co",
                  f"get{slug}.com", f"try{slug}.com", f"{slug}hq.com"):
            cands.append((d, "guess"))
    seen, out = set(), []
    for d, src in cands:
        if d not in seen and valid_domain(d):
            seen.add(d)
            out.append((d, src))
    return out[:12]


def verify(name: str, page) -> bool:
    """Does this homepage plausibly belong to the company?

    The company's distinctive name token must appear in the title, description or top
    headings; a mention buried in body text is too weak (shops, directories, SEO pages).
    """
    hero = f"{page.title} {page.description} {' '.join(page.headings[:5])}"
    if not hero.strip() or PARKED.search(f"{page.title} {page.text[:2000]}"):
        return False
    key = distinctive(name)
    squashed = re.sub(r"[^a-z0-9]", "", hero.lower())
    return bool(re.search(rf"(?i)\b{re.escape(key)}", hero)) or norm(name) in squashed


def find_domain(cik: str, name: str, yc_site: str | None) -> dict:
    try:
        return _find_domain(cik, name, yc_site)
    except Exception as exc:  # noqa: BLE001 - one company's oddity must not stop the run
        print(f"  skipped {cik} ({type(exc).__name__}: {exc})", flush=True)
        return {"cik": cik, "domain": None, "source": None, "status": None, "page": None}


def _find_domain(cik: str, name: str, yc_site: str | None) -> dict:
    for domain, source in candidate_domains(name, yc_site):
        res = fetch(domain)
        if not res.get("html"):
            continue
        page = parse_page(res["html"])
        if source == "yc" or verify(name, page):
            return {"cik": cik, "domain": final_domain(res) or domain, "source": source,
                    "status": res.get("status"), "page": page}
    return {"cik": cik, "domain": None, "source": None, "status": None, "page": None}


# ---------- step 3: homepage scoring ----------

def score_page(domain: str, page, name: str = "", source: str = "guess") -> tuple[str, list[str], int, int]:
    hero_terms = sorted({m.group(0).lower() for m in AI_TERMS.finditer(page.hero)})
    body_hits = [m.group(0).lower() for m in AI_TERMS.finditer(page.text[:50_000])]
    # A .ai domain is strong evidence only when we know it is the company's own: it came
    # from YC, or the company's name itself says AI. A guessed humans.ai for "Humans,
    # Inc." can belong to someone else entirely.
    own_ai_domain = domain.endswith(".ai") and (source == "yc" or "ai" in tokens(name))
    if hero_terms:
        strength = "strong"
    elif own_ai_domain:
        strength = "strong"
    elif domain.endswith(".ai"):
        strength = "weak"
    elif len(body_hits) >= 6:
        strength = "strong"
    elif body_hits:
        strength = "weak"
    else:
        strength = "none"
    return strength, hero_terms, len(body_hits), len(set(body_hits))


# ---------- step 4: model tie-break ----------

PROMPT_VERSION = "v2-quote"


def ask_model(cik: str, name: str, domain: str, page, model: str) -> dict | None:
    """Ask a model whether the company's core product is AI, with a verbatim quote.

    Small models agree too easily, so a "yes" only counts when the model quotes a phrase
    that (a) appears verbatim in the homepage text we sent and (b) contains an AI term.
    Anything else is recorded as "unclear".
    """
    path = CACHE_DIR / "llm" / re.sub(r"[^a-z0-9.]", "_", model) / PROMPT_VERSION / f"{cik}.json"
    if path.exists():
        return json.loads(path.read_text())
    excerpt = f"{page.hero[:600]}\n{page.text[:1800]}"
    prompt = (
        f"Company: {name}\nWebsite: {domain}\n\nHomepage text:\n{excerpt}\n\n"
        "Question: is this company's CORE PRODUCT an AI or machine-learning model, or a "
        "product built on one? Using AI as one feature among many, or mentioning AI, is NOT "
        "enough. Answer as JSON with keys: "
        '"answer" ("yes", "no" or "unclear"), '
        '"quote" (copy the exact phrase from the homepage text that describes the core '
        'product; empty string if none), "reason" (one short sentence).'
    )
    try:
        resp = requests.post(OLLAMA_URL, timeout=120, json={
            "model": model, "system": SYSTEM_PROMPT, "prompt": prompt, "format": "json",
            "stream": False, "options": {"temperature": 0, "seed": 7},
        })
        resp.raise_for_status()
        out = json.loads(resp.json()["response"])
    except (requests.RequestException, ValueError, KeyError):
        return None
    if not isinstance(out, dict):
        return None
    from pipeline.claude_judge import verbatim  # same whole-word quote rule as the Claude judge
    answer = str(out.get("answer", "")).strip().lower()
    quote = " ".join(str(out.get("quote", "")).split())
    verified = bool(quote) and verbatim(quote, excerpt) and bool(AI_TERMS.search(quote))
    if answer == "yes" and not verified:
        answer = "unclear"  # an unsupported yes is not evidence
    result = {"answer": answer if answer in ("yes", "no", "unclear") else "unclear",
              "quote": quote[:300], "quote_verified": verified,
              "reason": str(out.get("reason", ""))[:300], "model": model,
              "prompt_version": PROMPT_VERSION, "prompt_sha": sha1(prompt.encode()).hexdigest()[:12]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result))
    return result


def model_available(model: str) -> bool:
    try:
        tags = requests.get("http://localhost:11434/api/tags", timeout=5).json()
        return any(m["name"] == model for m in tags.get("models", []))
    except (requests.RequestException, ValueError):
        return False


# ---------- output ----------

def write_parquet(con, name: str, columns: dict[str, str], rows: list[tuple]) -> None:
    ENRICH_DIR.mkdir(parents=True, exist_ok=True)
    con.execute(f"create or replace temp table _out ({', '.join(f'{k} {v}' for k, v in columns.items())})")
    if rows:
        con.executemany(f"insert into _out values ({', '.join('?' * len(columns))})", rows)
    con.execute(f"copy (select * from _out order by all) to '{ENRICH_DIR / name}' (format parquet)")


CHECK_COLUMNS = {
    "cik": "varchar", "checked_at": "date", "domain": "varchar", "source": "varchar",
    "title": "varchar", "description": "varchar", "strength": "varchar",
    "hero_terms": "varchar[]", "body_hits": "integer", "distinct_terms": "integer",
}
LLM_COLUMNS = {"cik": "varchar", "model": "varchar", "prompt_version": "varchar",
               "answer": "varchar", "quote": "varchar", "quote_verified": "boolean",
               "reason": "varchar", "prompt_sha": "varchar"}


def read_parquet_dicts(name: str) -> list[dict]:
    path = ENRICH_DIR / name
    if not path.exists():
        return []
    with duckdb.connect() as con:
        cur = con.execute(f"select * from read_parquet('{path}')")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def universe(scope: str, metros: list[str] | None) -> list[tuple]:
    """Venture-stage operating companies with a raise in the window, tracked metros first."""
    where = "i.metro is not null" if scope == "metros" else "i.state in (select unnest(?))"
    if metros:
        where = "i.metro in (select unnest(?))"
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        return con.execute(f"""
            select i.cik, i.name, i.former_names, i.metro,
                   arg_max(o.industry_group, o.last_filed) as industry
            from issuers i join offerings o on o.cik = i.cik
            where o.kind = 'operating_company' and o.is_new_in_window and i.is_venture_stage
              and {where}
            group by all
            having industry not in {EXCLUDED_INDUSTRIES}
            order by i.metro nulls last, i.cik
        """, [metros] if metros else ([] if scope == "metros" else [US_STATES])).fetchall()


US_STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
             "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
             "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
             "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "PR"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scope", choices=["us", "metros"], default="us")
    parser.add_argument("--metros", help="comma-separated metro keys (overrides --scope)")
    parser.add_argument("--judge", choices=["claude", "ollama", "none"], default="claude")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model for --judge ollama")
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--max-minutes", type=float, default=None,
                        help="stop checking new companies after this long (CI chunks)")
    parser.add_argument("--pilot", type=int, default=0,
                        help="judge N ambiguous companies synchronously with Claude to measure cost")
    parser.add_argument("--no-submit", action="store_true", help="collect batches but don't submit")
    args = parser.parse_args(argv)
    start = time.time()

    companies = universe(args.scope, args.metros.split(",") if args.metros else None)
    names = {c[0]: c[1] for c in companies}
    previous = {r["cik"]: r for r in read_parquet_dicts("ai_checks.parquet")}
    stale_before = datetime.now(timezone.utc).date() - timedelta(days=REFRESH_DAYS)
    todo = [c for c in companies if c[0] not in previous or previous[c[0]]["checked_at"] < stale_before]
    print(f"Universe: {len(companies):,} companies; {len(companies) - len(todo):,} already "
          f"checked; {len(todo):,} to check", flush=True)

    yc = load_yc()
    yc_hits = match_yc(companies, yc)
    print(f"Y Combinator: {len(yc_hits):,} matched", flush=True)

    # Homepages for companies not yet checked, in chunks so a time budget can stop cleanly.
    results: dict[str, dict] = {}
    chunk = 400
    for i in range(0, len(todo), chunk):
        if args.max_minutes is not None and (time.time() - start) / 60 > args.max_minutes:
            print(f"  time budget reached; {len(todo) - i:,} companies left for the next run")
            break
        part = todo[i:i + chunk]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(find_domain, cik, name,
                                   (yc_hits.get(cik) or {}).get("website")): cik
                       for cik, name, *_ in part}
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()
        print(f"  checked {min(i + chunk, len(todo)):,}/{len(todo):,} "
              f"({(time.time() - start) / 60:.0f} min)", flush=True)

    today = datetime.now(timezone.utc).date()
    checks = dict(previous)
    pages = {}
    for cik, r in results.items():
        if r["page"] is None:
            checks[cik] = {"cik": cik, "checked_at": today, "domain": None, "source": None,
                           "title": None, "description": None, "strength": "no_domain",
                           "hero_terms": [], "body_hits": 0, "distinct_terms": 0}
            continue
        strength, hero, nb, nd = score_page(r["domain"], r["page"], names[cik], r["source"])
        pages[cik] = r
        checks[cik] = {"cik": cik, "checked_at": today, "domain": r["domain"], "source": r["source"],
                       "title": r["page"].title[:300], "description": r["page"].description[:500],
                       "strength": strength, "hero_terms": hero, "body_hits": nb, "distinct_terms": nd}

    # Ambiguous = weak website evidence (or a weak name term) with no strong name or YC signal.
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        name_strong = {c for (c,) in con.execute(
            "select distinct cik from ai_signals where source = 'name' and strength = 'strong'").fetchall()}
        name_weak = {c for (c,) in con.execute(
            "select distinct cik from ai_signals where source = 'name' and strength = 'weak'").fetchall()}
    ambiguous = [cik for cik, c in checks.items()
                 if c["domain"] and cik not in name_strong
                 and (c["strength"] == "weak" or cik in name_weak)
                 and not YC_AI_TAGS.search(" ".join((yc_hits.get(cik) or {}).get("tags") or []))]

    llm = {r["cik"]: r for r in read_parquet_dicts("llm_labels.parquet")}
    if args.judge == "claude":
        from pipeline import claude_judge
        if os.environ.get("ANTHROPIC_API_KEY") or env_key("ANTHROPIC_API_KEY"):
            os.environ.setdefault("ANTHROPIC_API_KEY", env_key("ANTHROPIC_API_KEY") or "")
            got = claude_judge.collect()
            llm.update(got)
            if got:
                print(f"Claude: collected {len(got):,} answers", flush=True)
            pending = claude_judge.pending_ciks()
            unjudged = [cik for cik in ambiguous
                        if cik not in pending
                        and llm.get(cik, {}).get("prompt_version") != claude_judge.PROMPT_VERSION]
            # Only companies whose page is in hand this run (or in the web cache) can be sent.
            items = []
            for cik in unjudged:
                r = pages.get(cik) or find_domain(cik, names[cik], (yc_hits.get(cik) or {}).get("website"))
                if r.get("page") is not None:
                    items.append((cik, names[cik], r["domain"], claude_judge.excerpt(r["page"])))
            if args.pilot:
                claude_judge.pilot(items[:args.pilot])
                return
            if items and not args.no_submit:
                # Spending cap: what doesn't fit isn't recorded, so the next run sends it.
                cap = claude_judge.max_usd()
                send, est = claude_judge.within_cap(items, cap)
                print(f"Claude: submitting {len(send):,} of {len(items):,} (est ${est:.2f}, "
                      f"cap ${cap:.2f}); {len(items) - len(send):,} left for later runs", flush=True)
                if send:
                    ids = claude_judge.submit(send)
                    print(f"Claude: submitted {len(send):,} ambiguous companies in batch(es) "
                          f"{', '.join(ids)}; results are collected on the next run", flush=True)
            print(f"Claude: {len(pending):,} pending from earlier runs; "
                  f"spend so far ~${claude_judge.spend(claude_judge.load_state()):.2f}")
        else:
            print("Claude judge: no ANTHROPIC_API_KEY; ambiguous companies stay unjudged.")
    elif args.judge == "ollama" and model_available(args.model):
        for cik in ambiguous:
            if llm.get(cik, {}).get("prompt_version") == PROMPT_VERSION:
                continue
            r = pages.get(cik) or find_domain(cik, names[cik], (yc_hits.get(cik) or {}).get("website"))
            if r.get("page") is not None:
                ans = ask_model(cik, names[cik], r["domain"], r["page"], args.model)
                if ans:
                    llm[cik] = ans

    # ---- write outputs ----
    with duckdb.connect() as con:
        write_parquet(con, "ai_checks.parquet", CHECK_COLUMNS,
                      [tuple(c.get(k) for k in CHECK_COLUMNS) for c in checks.values()])
        write_parquet(con, "yc_matches.parquet",
                      {"cik": "varchar", "yc_id": "integer", "yc_name": "varchar",
                       "batch": "varchar", "tags": "varchar[]", "website": "varchar",
                       "yc_locations": "varchar", "one_liner": "varchar", "match_method": "varchar"},
                      [(cik, r["id"], r["name"], r.get("batch"), r.get("tags") or [],
                        r.get("website"), r.get("all_locations"), r.get("one_liner"), r["_method"])
                       for cik, r in yc_hits.items()])
        write_parquet(con, "company_domains.parquet",
                      {"cik": "varchar", "domain": "varchar", "source": "varchar",
                       "title": "varchar", "description": "varchar"},
                      [(c["cik"], c["domain"], c["source"], c["title"], c["description"])
                       for c in checks.values() if c["domain"]])
        write_parquet(con, "web_signals.parquet",
                      {"cik": "varchar", "domain": "varchar", "strength": "varchar",
                       "hero_terms": "varchar[]", "body_hits": "integer", "distinct_terms": "integer"},
                      [(c["cik"], c["domain"], c["strength"], c["hero_terms"], c["body_hits"],
                        c["distinct_terms"]) for c in checks.values() if c["domain"]])
        write_parquet(con, "llm_labels.parquet", LLM_COLUMNS,
                      [tuple(a.get(k) for k in LLM_COLUMNS) for a in llm.values()])

        signals = []
        for cik, r in yc_hits.items():
            tags = r.get("tags") or []
            if YC_AI_TAGS.search(" ".join(tags)):
                signals.append((cik, "yc", "strong",
                                f"yc: {r['name']} ({r.get('batch')}) tags {', '.join(tags[:5])}"))
        for c in checks.values():
            if c["strength"] in ("strong", "weak"):
                hero, nb = c["hero_terms"] or [], c["body_hits"] or 0
                why = (f"hero mentions {', '.join(hero)}" if hero else
                       ".ai domain" if c["domain"].endswith(".ai") and nb < 6 else
                       f"{nb} AI-term mentions in homepage text")
                signals.append((c["cik"], "website", c["strength"], f"website {c['domain']}: {why}"))
        for cik, a in llm.items():
            if a["answer"] == "yes":
                signals.append((cik, "llm", "strong",
                                f"model {a['model']} quotes homepage: \"{(a['quote'] or '')[:120]}\""))
        write_parquet(con, "ai_signals.parquet",
                      {"cik": "varchar", "source": "varchar", "strength": "varchar",
                       "evidence": "varchar"}, signals)
    done = sum(1 for c in companies if c[0] in checks)
    print(f"Wrote data/enrich/: {done:,} of {len(companies):,} companies checked; "
          f"{len(signals):,} AI signals ({sum(s[1] == 'yc' for s in signals)} yc, "
          f"{sum(s[1] == 'website' for s in signals)} website, {sum(s[1] == 'llm' for s in signals)} model)")


def env_key(name: str) -> str | None:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return None


if __name__ == "__main__":
    main()
