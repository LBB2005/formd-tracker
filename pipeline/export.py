"""Export the database for the site and for download.

    uv run python -m pipeline.export

site/src/data/   tables the dashboard reads in the browser (DuckDB-WASM): every in-scope
                 company nationally (US startups with a raise since 2019, venture funds),
                 plus smaller page-specific slices of them (PAGE_TABLES)
site/src/downloads/  national offerings and issuers as Parquet and gzipped CSV for reuse
"""

import csv
import itertools
import json
import re
import shutil
from datetime import datetime, timezone

import duckdb

import yaml

from pipeline import CONFIG_DIR, DB_PATH, ROOT

SITE_DATA = ROOT / "site" / "src" / "data"
DOWNLOADS = ROOT / "site" / "src" / "downloads"
REVIEW = ROOT / "review"

SCOPE = """(select distinct cik from offerings
             where is_new_in_window and (is_startup_raise or (kind = 'fund' and fund_bucket = 'venture')))"""

TABLES = {
    # Every raise by an in-scope company nationally: US startups with a raise since 2019
    # and venture fund vehicles. One row per raise.
    "offerings": """
        select o.offering_id, o.cik, o.issuer_name, o.metro, o.county_name, o.kind,
               o.vehicle_type, o.fund_bucket, o.fund_number, o.manager_key, o.security_class,
               o.is_startup_raise, o.is_new_in_window, o.duplicate_of, o.began_before_window,
               o.first_sale_date, o.first_filed, o.last_filed, o.days_open,
               o.days_sale_to_first_filing, o.n_filings, o.n_amendments,
               o.offering_amt, o.offering_indefinite, o.sold_latest, o.raise_size, o.fill_pct,
               o.investors_latest, o.min_investment, o.industry_group, o.fund_type,
               array_to_string(o.exemptions, ' ') as exemptions, o.is_506c,
               o.ai_tier, o.is_ai, o.ai_name_at_filing, o.ai_theme, o.inferred_stage, o.flag_implausible,
               o.latest_accession,
               'https://www.sec.gov/Archives/edgar/data/' || o.cik::bigint || '/'
                   || replace(o.latest_accession, '-', '') || '/' as filing_url
        from offerings o
        where o.cik in SCOPE
    """,
    "issuers": """
        select i.cik, i.name, i.former_names, i.city, i.state, i.zip5, i.county_name,
               i.metro, i.lat, i.lon, i.geocode_method, i.entity_type, i.year_inc,
               i.is_venture_stage, i.n_offerings, i.first_filing, i.last_filing,
               i.ai_tier, array_to_string(i.ai_evidence, ' | ') as ai_evidence, i.edgar_url,
               d.domain, y.batch as yc_batch, y.one_liner as yc_one_liner,
               k.kind, m.capital_score, m.data_confidence, m.recency_days, m.step_up,
               m.pace_months, m.fill_90d, m.peer_group_size, m.size_band,
               i.cik in (select cik from ai_checks) as ai_checked
        from issuers i
        left join company_domains d using (cik)
        left join yc_matches y using (cik)
        left join issuer_kind k using (cik)
        left join capital_momentum m using (cik)
        where i.cik in SCOPE
    """,
    "filings": """
        select f.accession, f.cik, f.offering_id, f.filing_date, f.form, f.link_method,
               f.sold, f.offering_amt, f.investors, fi.sold_increment, fi.is_correction
        from filings f
        join filing_increments fi using (accession)
        where f.cik in SCOPE
    """,
    "fund_families": """
        select fund_family_id, manager_key, fund_bucket, metro, fund_number, first_filed,
               n_vehicles, target_amt, is_new_in_window, is_emerging, emerging_basis,
               prior_families, array_to_string(offering_ids, ' ') as offering_ids
        from fund_families
        where fund_bucket = 'venture'
    """,
    "managers": """
        select manager_key, n_vehicles, n_blind_pool, n_spv, n_ciks, first_vehicle_filed,
               fund_number_max, has_vc_vehicle, metro,
               array_to_string(sample_names, ' | ') as sample_names,
               adv_crd, adv_name, adv_registration, adv_vc_exemption, adv_private_funds,
               adv_private_fund_assets, adv_since
        from managers
        where has_vc_vehicle
    """,
    # Names and roles only (no personal addresses), for in-scope issuers.
    "people": """
        select ip.cik, ip.display_name, ip.is_entity, array_to_string(ip.roles, ', ') as roles,
               ip.first_seen, ip.last_seen, p.is_repeat_founder, p.exec_turned_gp,
               p.is_platform_staff, p.n_startups_as_exec, p.n_vc_funds
        from issuer_people ip
        join people p using (person_key)
        where ip.cik in SCOPE
    """,
}

# Page-specific slices of the tables above, so a page downloads only the rows and columns
# it reads (DuckDB-WASM loads each attached file whole). Each reads the shared file just
# written, so values and definitions match it exactly. The shared files stay for reuse.
PAGE_TABLES = {
    # Overview: startup raises, the columns the AI-share charts read.
    "startup_raises": """
        select cik, issuer_name, metro, first_filed, sold_latest, is_ai, ai_name_at_filing,
               flag_implausible, is_startup_raise, is_new_in_window
        from DATA/offerings.parquet
        where is_startup_raise and is_new_in_window
    """,
    # Findings: the same rows for SF Bay Area only, which is all its AI-share card reads.
    "findings_sf_raises": """
        select cik, issuer_name, metro, first_filed, sold_latest, is_ai, flag_implausible,
               is_startup_raise, is_new_in_window
        from DATA/startup_raises.parquet
        where metro = 'sf_bay'
    """,
    # Raise velocity: timing and fill columns, no names.
    "velocity": """
        select metro, first_filed, is_ai, flag_implausible, is_startup_raise, is_new_in_window,
               days_sale_to_first_filing, days_open, n_amendments, fill_pct, offering_amt,
               sold_latest
        from DATA/offerings.parquet
        where is_startup_raise and is_new_in_window
    """,
    # New funds and Findings: new venture vehicles, enough to count SPVs vs blind pools.
    "fund_vehicles": """
        select metro, first_filed, kind, fund_bucket, vehicle_type, is_new_in_window
        from DATA/offerings.parquet
        where kind = 'fund' and fund_bucket = 'venture' and is_new_in_window
    """,
    # New funds: the Fund I table and fill chart.
    "fund_i": """
        select offering_id, cik, issuer_name, manager_key, metro, kind, fund_bucket,
               vehicle_type, fund_number, is_new_in_window, first_sale_date, first_filed,
               last_filed, offering_amt, sold_latest, fill_pct, investors_latest, n_filings,
               filing_url
        from DATA/offerings.parquet
        where kind = 'fund' and fund_bucket = 'venture' and vehicle_type = 'blind_pool'
          and fund_number = 1 and is_new_in_window
    """,
    # Fund managers (one manager's page): every vehicle with a manager key, re-filed
    # duplicates counted once.
    "manager_vehicles": """
        select manager_key, cik, issuer_name, vehicle_type, fund_number, first_filed,
               offering_amt, offering_indefinite, sold_latest, investors_latest, filing_url
        from DATA/offerings.parquet
        where manager_key is not null and duplicate_of is null
    """,
    # Fund managers: people on those vehicles, without platform administrators.
    "manager_people": """
        select cik, display_name, roles, is_entity, exec_turned_gp, is_platform_staff
        from DATA/people.parquet
        where not is_platform_staff
          and cik in (select cik from DATA/offerings.parquet where manager_key is not null)
    """,
    # Map: raises that can be drawn (tracked metro, geocoded), joined to coordinates.
    "map_points": """
        select o.cik, o.issuer_name, o.metro, o.first_filed, o.sold_latest, o.inferred_stage,
               o.is_startup_raise, o.is_ai, o.kind, o.fund_bucket, o.vehicle_type,
               o.is_new_in_window, o.flag_implausible, i.lat, i.lon, i.city, i.geocode_method
        from DATA/offerings.parquet o
        join DATA/issuers.parquet i using (cik)
        where o.metro is not null and o.is_new_in_window and not o.flag_implausible
          and i.lat is not null
          and (o.is_startup_raise
               or (o.kind = 'fund' and o.fund_bucket = 'venture' and o.vehicle_type = 'blind_pool'))
    """,
    # AI raises: AI-tagged startup raises with the issuer's evidence.
    "ai_raises": """
        select o.cik, o.issuer_name, o.metro, o.first_filed, o.sold_latest, o.offering_amt,
               o.inferred_stage, o.security_class, o.ai_tier, o.n_filings, o.filing_url,
               o.is_startup_raise, o.is_new_in_window,
               i.ai_evidence, i.domain, i.yc_batch, i.city
        from DATA/offerings.parquet o
        join DATA/issuers.parquet i using (cik)
        where o.is_startup_raise and o.is_new_in_window
          and o.ai_tier is not null and o.ai_tier <> 'none'
    """,
    # Company lookup: one row per company for the search cards, with money raised per
    # year of first filing since the window opens. Re-filed duplicates and implausible
    # amounts are left out of the totals.
    "directory": """
        with raised as (
            select cik, year(first_filed) as yr, sum(sold_latest) as sold
            from DATA/offerings.parquet
            where duplicate_of is null and not flag_implausible
            group by all
        ),
        years as (
            select unnest(range(min(year(first_filed)), max(year(first_filed)) + 1)) as yr
            from DATA/offerings.parquet
        )
        select i.cik, i.name, i.city, i.state, i.metro, i.kind, i.ai_tier, i.n_offerings,
               i.last_filing, coalesce(sum(r.sold), 0) as sold_total,
               min(y.yr) as first_year,
               list(coalesce(r.sold, 0) order by y.yr) as sold_by_year
        from DATA/issuers.parquet i
        cross join years y
        left join raised r on r.cik = i.cik and r.yr = y.yr
        group by all
    """,
}

# A company's page: its issuer row plus these lists of its rows (columns, order), read from
# the files just written. Companies are split into one JSON file per last two digits of the
# CIK, so a page downloads about 1% of the data instead of four national files.
COMPANY_LISTS = {
    "offerings": ("offering_id, first_filed, last_filed, first_sale_date, kind, inferred_stage, "
                  "security_class, offering_amt, offering_indefinite, sold_latest, investors_latest, "
                  "n_filings, exemptions, filing_url", "first_filed, offering_id"),
    "filings": ("accession, offering_id, filing_date, form, link_method, sold, offering_amt, "
                "investors, sold_increment, is_correction", "filing_date, accession"),
    "people": ("display_name, roles, first_seen, last_seen, is_repeat_founder, exec_turned_gp, "
               "is_platform_staff, is_entity", "is_entity, display_name"),
}


def company_shard(cik: str) -> str:
    """Which company file holds this CIK (the page computes the same from the URL)."""
    return cik[-2:]


def write_company_files(con, data_dir=SITE_DATA) -> int:
    """data/company/NN.json, {cik: {issuer, offerings, filings, people}}, and shards.js,
    which attaches them so the page gets content-hashed URLs. Returns the company count."""
    out = data_dir / "company"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    lists = ",\n".join(
        f"{t} as (select cik, list(struct_pack({cols}) order by {order}) as {t} "
        f"from '{data_dir / t}.parquet' group by cik)" for t, (cols, order) in COMPANY_LISTS.items())
    rows = con.execute(f"""
        with {lists}
        select i.cik, to_json(struct_pack(issuer := i, {", ".join(f"{t} := {t}.{t}" for t in COMPANY_LISTS)}))::varchar
        from '{data_dir / "issuers"}.parquet' i
        {" ".join(f"left join {t} using (cik)" for t in COMPANY_LISTS)}
        order by right(i.cik, 2), i.cik
    """).fetchall()
    shards = []
    for shard, group in itertools.groupby(rows, key=lambda r: company_shard(r[0])):
        (out / f"{shard}.json").write_text("{" + ",".join(f'"{cik}":{j}' for cik, j in group) + "}")
        shards.append(shard)
    (out / "shards.js").write_text(
        "// Written by pipeline/export.py: the company files, keyed by the CIK's last two digits.\n"
        'import {FileAttachment} from "observablehq:stdlib";\n\n'
        "export const SHARDS = {\n"
        + "".join(f'  "{s}": FileAttachment("./{s}.json"),\n' for s in shards) + "};\n")
    return len(rows)


def main() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        for name, query in TABLES.items():
            query = query.replace("SCOPE", SCOPE)
            con.execute(f"copy ({query} order by all) to '{SITE_DATA / name}.parquet' "
                        "(format parquet, compression zstd)")
            n = con.execute(f"select count(*) from '{SITE_DATA / name}.parquet'").fetchone()[0]
            print(f"  data/{name}.parquet  {n:,} rows")
        for name, query in PAGE_TABLES.items():
            query = re.sub(r"DATA/(\w+\.parquet)", lambda m: f"'{SITE_DATA / m[1]}'", query)
            con.execute(f"copy ({query} order by all) to '{SITE_DATA / name}.parquet' "
                        "(format parquet, compression zstd)")
            n = con.execute(f"select count(*) from '{SITE_DATA / name}.parquet'").fetchone()[0]
            kb = (SITE_DATA / f"{name}.parquet").stat().st_size / 1e3
            print(f"  data/{name}.parquet  {n:,} rows, {kb:,.0f} kB (page slice)")
        n = write_company_files(con)
        files = list((SITE_DATA / "company").glob("*.json"))
        mb = sum(f.stat().st_size for f in files) / 1e6
        print(f"  data/company/*.json  {n:,} companies in {len(files)} files, {mb:,.0f} MB")

        # National downloads.
        for name, query in {
            "formd_offerings": "select * exclude (link_methods) from offerings",
            "formd_issuers": "select * from issuers",
        }.items():
            for ext, opts in (("parquet", "(format parquet, compression zstd)"),
                              ("csv.gz", "(header, compression gzip)")):
                con.execute(f"copy ({query} order by all) to '{DOWNLOADS / name}.{ext}' {opts}")
            print(f"  downloads/{name}.parquet + .csv.gz")

        meta = {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data_through": str(con.execute("select max(filing_date) from filings").fetchone()[0]),
            "data_from": str(con.execute("select min(filing_date) from filings").fetchone()[0]),
            "n_filings": con.execute("select count(*) from filings").fetchone()[0],
            "n_offerings": con.execute(
                "select count(*) from offerings where duplicate_of is null").fetchone()[0],
            "n_issuers": con.execute("select count(*) from issuers").fetchone()[0],
            "sources": [list(r) for r in con.execute(
                "select source_quarter, source, complete from load_manifest order by 1").fetchall()],
            "ai_signals": dict(con.execute(
                "select source, count(distinct cik) from ai_signals group by 1").fetchall()),
            "metros": [{"key": k, "label": m["label"], "view": m["view"]}
                       for k, m in yaml.safe_load((CONFIG_DIR / "geo.yaml").read_text())["metros"].items()],
            "ai_coverage": dict(con.execute("""
                select coalesce(i.metro, 'rest_of_us'),
                       round(avg((i.cik in (select cik from ai_checks))::int), 3)
                from issuers i
                where i.is_venture_stage and i.cik in (select cik from offerings
                                                       where is_startup_raise and is_new_in_window)
                group by 1""").fetchall()),
            # Size of each half the AI review sample was drawn from (SF/LA venture-stage
            # startups, tagged vs untagged), so the site can weight recall correctly.
            "ai_sample_pool": dict(con.execute("""
                select case when ai_tier in ('strong', 'likely') then 'ai' else 'not_ai' end, count(*)
                from issuers
                where metro in ('sf_bay', 'la') and is_venture_stage
                  and cik in (select cik from offerings where is_startup_raise and is_new_in_window)
                group by 1""").fetchall()),
            "geocode": dict(con.execute(
                "select geocode_method, count(*) from issuers where metro is not null "
                "group by 1").fetchall()),
        }

        # The classification sample's kind/vehicle_type columns are frozen at the rules of
        # the day it was drawn; attach what today's rules say (before hand overrides) so the
        # methodology page grades the current rules.
        rules = {oid: {"rule_kind": k, "rule_vehicle_type": v} for oid, k, v in con.execute(
            "select offering_id, rule_kind, rule_vehicle_type from offering_class").fetchall()}

    # Hand-review results, when they exist, for the methodology page.
    reviews = {}
    for path in REVIEW.glob("*.csv"):
        rows = list(csv.DictReader(open(path)))
        if path.stem == "classification_sample":
            for r in rows:
                r.update(rules.get(r["offering_id"], {"rule_kind": None, "rule_vehicle_type": None}))
        reviews[path.stem] = rows
    (SITE_DATA / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    (SITE_DATA / "reviews.json").write_text(json.dumps(reviews, indent=1))
    print(f"  data/meta.json, data/reviews.json ({', '.join(reviews) or 'none'})")


if __name__ == "__main__":
    main()
