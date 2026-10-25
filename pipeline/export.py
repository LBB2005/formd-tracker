"""Export the database for the site and for download.

    uv run python -m pipeline.export

site/src/data/   tables the dashboard reads in the browser (DuckDB-WASM): every in-scope
                 company nationally (US startups with a raise since 2019, venture funds)
site/src/downloads/  national offerings and issuers as Parquet and gzipped CSV for reuse
"""

import csv
import json
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

    # Hand-review results, when they exist, for the methodology page.
    reviews = {}
    for path in REVIEW.glob("*.csv"):
        rows = list(csv.DictReader(open(path)))
        reviews[path.stem] = rows
    (SITE_DATA / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    (SITE_DATA / "reviews.json").write_text(json.dumps(reviews, indent=1))
    print(f"  data/meta.json, data/reviews.json ({', '.join(reviews) or 'none'})")


if __name__ == "__main__":
    main()
