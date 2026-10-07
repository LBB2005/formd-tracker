"""Spot-check our filings against EDGAR's live records (Week 1 gate).

For each company in review/spot_check.csv:
  - with a CIK: compare every D and D/A EDGAR lists for that CIK (data.sec.gov
    submissions API) with the filings in our database, inside the loaded date range;
  - without a CIK: search EDGAR's full registrant list (cik-lookup-data.txt) for names
    starting with the company name, split SPV/fund vehicles from possible operating
    entities, and check each candidate's full EDGAR history for a Form D. This
    separates "the company never filed" from "our pipeline dropped it".

Usage:
    uv run python -m pipeline.verify_edgar
"""

import csv
import json
import re
from datetime import date

import duckdb

from pipeline import CACHE_DIR, DB_PATH, ROOT
from pipeline.sec_client import fetch_to_cache

SPOT_CHECK = ROOT / "review" / "spot_check.csv"
RESULTS = ROOT / "review" / "coverage_results.csv"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"


def edgar_form_d(cik: str) -> set[tuple[str, str, str]]:
    """(accession, form, filing_date) for every D and D/A on EDGAR for this CIK."""
    dest = CACHE_DIR / "submissions" / f"CIK{cik}.json"
    path, _ = fetch_to_cache(SUBMISSIONS.format(cik=cik), dest, refresh=True)
    data = json.loads(path.read_text())
    blocks = [data["filings"]["recent"]]
    for extra in data["filings"].get("files", []):
        page, _ = fetch_to_cache(f"https://data.sec.gov/submissions/{extra['name']}",
                                 CACHE_DIR / "submissions" / extra["name"], refresh=True)
        blocks.append(json.loads(page.read_text()))
    out = set()
    for b in blocks:
        for acc, form, filed in zip(b["accessionNumber"], b["form"], b["filingDate"]):
            if form in ("D", "D/A"):
                out.add((acc, form, filed))
    return out


VEHICLE = re.compile(r"(?i)(a series of|\bspv\b|\bfunds?\b|co-?invest|\bseries\b|syndicat|"
                     r"\bholdings? (lp|llc)|investors|\bl\.?p\.?$|alternate|capital|ventures)")
_entities: list[tuple[str, str]] | None = None


def entities() -> list[tuple[str, str]]:
    """Every EDGAR registrant as (NAME, cik), from the SEC's cik-lookup-data.txt."""
    global _entities
    if _entities is None:
        path, _ = fetch_to_cache("https://www.sec.gov/Archives/edgar/cik-lookup-data.txt",
                                 CACHE_DIR / "edgar" / "cik-lookup-data.txt")
        _entities = []
        for line in path.read_text(encoding="latin-1").splitlines():
            name, _, rest = line.rpartition(":")[0].rpartition(":")
            if name:
                _entities.append((name, rest.zfill(10)))
    return _entities


def name_search(name: str) -> tuple[list[tuple[str, str, int]], int]:
    """EDGAR registrants whose name starts with `name`.

    Returns ([(cik, name, n_form_d)] for possible operating entities, n_vehicles), where
    vehicles are SPVs/funds named after the company.
    """
    prefix = re.compile(rf"^{re.escape(name.upper())}\b")
    hits = sorted({(n, c) for n, c in entities() if prefix.match(n)})
    vehicles = [h for h in hits if VEHICLE.search(h[0])]
    candidates = [h for h in hits if not VEHICLE.search(h[0])][:15]
    out = [(cik, n, len(edgar_form_d(cik))) for n, cik in candidates]
    return out, len(vehicles)


def main() -> None:
    rows = list(csv.DictReader(open(SPOT_CHECK)))
    results = []
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        lo, hi = con.execute("select min(filing_date), max(filing_date) from filings").fetchone()
        print(f"Our data covers filings {lo} to {hi}.\n")
        all_ok = True
        for row in rows:
            name, cik = row["company"], row["cik"].strip()
            if not cik:
                candidates, n_vehicles = name_search(row.get("search_name") or name)
                filers = [c for c in candidates if c[2]]
                ours = con.execute(
                    "select count(*) from issuers where cik in (select unnest(?::varchar[]))",
                    [[c for c, _, _ in filers]]).fetchone()[0] if filers else 0
                print(f"{name:<22} name search: {len(candidates)} possible operating entities, "
                      f"{len(filers)} with a Form D ({ours} in our data); "
                      f"{n_vehicles} SPV/fund vehicles named after it")
                for c, n, d in candidates[:6]:
                    print(f"{'':<24}{c}  {n}  ({d} Form D filings)")
                if len(filers) > ours:
                    all_ok = False
                results.append({
                    "company": name, "method": "name search",
                    "status": "files Form D" if filers else "no Form D as an operating company",
                    "edgar_form_d": sum(d for _, _, d in filers), "ours": "",
                    "registrants": "; ".join(f"{n} ({c})" for c, n, _ in filers),
                    "vehicles_named_after": n_vehicles})
                continue
            cik = cik.zfill(10)
            edgar = {e for e in edgar_form_d(cik) if lo <= date.fromisoformat(e[2]) <= hi}
            ours = {(a, f, str(d)) for a, f, d in con.execute(
                "select accession, form, filing_date from filings where cik = ?", [cik]).fetchall()}
            missing = sorted(edgar - ours)
            extra = sorted(ours - edgar)
            ok = not missing and not extra
            all_ok &= ok
            n_off = con.execute("select count(*) from offerings where cik = ?", [cik]).fetchone()[0]
            print(f"{name:<22} {'OK  ' if ok else 'DIFF'} EDGAR {len(edgar)} filings, ours {len(ours)} "
                  f"-> {n_off} offerings")
            vehicles = name_search(row.get("search_name") or name)[1]
            results.append({
                "company": name, "method": "cik",
                "status": "matches EDGAR" if ok else "differs from EDGAR",
                "edgar_form_d": len(edgar), "ours": len(ours),
                "registrants": cik, "vehicles_named_after": vehicles})
            for acc, form, filed in missing:
                print(f"{'':<24}missing from ours: {filed} {form} {acc}")
            for acc, form, filed in extra:
                print(f"{'':<24}only in ours:      {filed} {form} {acc}")
    with open(RESULTS, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nWrote {RESULTS.relative_to(ROOT)}")
    print("All checks match." if all_ok else "Differences found; see above.")


if __name__ == "__main__":
    main()
