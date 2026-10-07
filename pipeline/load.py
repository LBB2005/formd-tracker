"""Load cached quarterly ZIPs into raw_* tables in data/formd.duckdb.

Every column is loaded as text exactly as the SEC ships it, plus source_quarter.
Typing and cleaning happen in sql/, so the raw layer stays auditable.
"""

import zipfile
from datetime import date
from pathlib import Path

import duckdb
import yaml

from pipeline import CACHE_DIR, CONFIG_DIR, DATA_DIR, DB_PATH
from pipeline.daily import DAILY_DIR
from pipeline.download import CENSUS_DIR, QUARTERLY_DIR

EXTRACT_DIR = CACHE_DIR / "extracted"

RAW_TABLES = {
    "FORMDSUBMISSION": "raw_submission",
    "ISSUERS": "raw_issuers",
    "OFFERING": "raw_offering",
    "RELATEDPERSONS": "raw_relatedpersons",
    "RECIPIENTS": "raw_recipients",
    "SIGNATURES": "raw_signatures",
}


def extract(zip_path: Path) -> Path:
    quarter = zip_path.name.split("_")[0].lower()
    out = EXTRACT_DIR / quarter
    marker = out / ".complete"
    if not marker.exists() or marker.stat().st_mtime < zip_path.stat().st_mtime:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                if member.upper().endswith(".TSV"):
                    (out / Path(member).name.upper()).parent.mkdir(parents=True, exist_ok=True)
                    (out / Path(member).name.upper()).write_bytes(zf.read(member))
        marker.touch()
    return out


def read_tsv(path: Path) -> str:
    # quote='' because the SEC files are unquoted; names can contain stray '"'.
    return (
        f"read_csv('{path}', delim='\t', header=true, all_varchar=true, quote='', "
        f"escape='', strict_mode=false, null_padding=true, encoding='utf-8')"
    )


def sources() -> list[tuple[str, Path, str, bool]]:
    """(quarter, folder, source, complete) for every quarter to load.

    Quarterly ZIPs win. Quarters the SEC hasn't published yet come from the
    parsed EDGAR filings that pipeline.daily writes under data/cache/daily/.
    """
    zips = sorted(QUARTERLY_DIR.glob("*_d.zip"))
    if not zips:
        raise SystemExit("No quarterly ZIPs cached. Run: uv run python -m pipeline.download")
    out = [(z.name.split("_")[0].lower(), extract(z), "quarterly", True) for z in zips]
    have = {q for q, *_ in out}
    today = date.today()
    current = f"{today.year}q{(today.month - 1) // 3 + 1}"
    for folder in sorted(DAILY_DIR.glob("[0-9][0-9][0-9][0-9]q[1-4]")):
        if folder.name not in have and (folder / "FORMDSUBMISSION.TSV").exists():
            out.append((folder.name, folder, "edgar_daily", folder.name < current))
    return out


def load_raw(con: duckdb.DuckDBPyConnection) -> None:
    for table in RAW_TABLES.values():
        con.execute(f"drop table if exists {table}")
    manifest = []
    for quarter, folder, source, complete in sources():
        for stem, table in RAW_TABLES.items():
            src = folder / f"{stem}.TSV"
            if not src.exists():
                raise SystemExit(f"{folder} is missing {stem}.tsv")
            select = f"select *, '{quarter}' as source_quarter from {read_tsv(src)}"
            exists = con.execute(
                "select count(*) from information_schema.tables where table_name = ?", [table]
            ).fetchone()[0]
            if exists:
                con.execute(f"insert into {table} by name {select}")
            else:
                con.execute(f"create table {table} as {select}")
        manifest.append((quarter, source, complete))
        print(f"  loaded {quarter} ({source}{'' if complete else ', quarter in progress'})")
    con.execute("create or replace table load_manifest "
                "(source_quarter varchar, source varchar, complete boolean)")
    con.executemany("insert into load_manifest values (?, ?, ?)", manifest)


def load_geo(con: duckdb.DuckDBPyConnection) -> None:
    """ZIP -> county lookup from the Census 2020 ZCTA relationship file."""
    src = CENSUS_DIR / "zcta_county_2020.txt"
    if not src.exists():
        raise SystemExit("Census ZCTA file missing. Run: uv run python -m pipeline.download")
    # A ZCTA can straddle counties; keep the county holding most of its land area.
    con.execute(f"""
        create or replace table zip_county as
        select zcta as zip5, arg_max(county_fips, land_part) as county_fips,
               arg_max(county_name, land_part) as county_name
        from (
            select GEOID_ZCTA5_20 as zcta, GEOID_COUNTY_20 as county_fips,
                   NAMELSAD_COUNTY_20 as county_name, AREALAND_PART::bigint as land_part
            from read_csv('{src}', delim='|', header=true, all_varchar=true)
            where GEOID_ZCTA5_20 is not null and GEOID_ZCTA5_20 <> ''
        )
        group by zcta
    """)
    # Fallback for PO-box and other non-ZCTA ZIPs: the dominant county of the ZIP3.
    con.execute("""
        create or replace table zip3_county as
        select zip3, arg_max(county_fips, {'n': n, 'fips': county_fips}) as county_fips
        from (select left(zip5, 3) as zip3, county_fips, count(*) as n
              from zip_county group by 1, 2)
        group by zip3
    """)


OVERRIDE_FIELDS = {"kind", "vehicle_type", "fund_bucket", "manager_key", "metro", "ai_tier"}


def load_config(con: duckdb.DuckDBPyConnection) -> None:
    """Metros from config/geo.yaml and hand corrections from config/manual_overrides.csv.

    Runs on every build (including --sql-only) so config edits apply immediately.
    """
    geo = yaml.safe_load((CONFIG_DIR / "geo.yaml").read_text())
    rows = [
        (key, metro["label"], fips, county)
        for key, metro in geo["metros"].items()
        for fips, county in metro["counties"].items()
    ]
    con.execute(
        "create or replace table metro_counties "
        "(metro varchar, metro_label varchar, county_fips varchar, county_name varchar)"
    )
    con.executemany("insert into metro_counties values (?, ?, ?, ?)", rows)
    con.execute("create or replace table metro_bbox "
                "(metro varchar, lat_min double, lat_max double, lon_min double, lon_max double)")
    con.executemany("insert into metro_bbox values (?, ?, ?, ?, ?)",
                    [(k, *m["bbox"]) for k, m in geo["metros"].items()])

    con.execute(f"""
        create or replace table manual_overrides as
        select lpad(trim(cik), 10, '0') as cik, nullif(trim(offering_id), '') as offering_id,
               trim(field) as field, nullif(trim(value), '') as value, note
        from read_csv('{CONFIG_DIR / "manual_overrides.csv"}', header=true, all_varchar=true)
    """)
    bad = con.execute("select distinct field from manual_overrides").fetchall()
    unknown = {f for (f,) in bad} - OVERRIDE_FIELDS
    if unknown:
        raise SystemExit(f"manual_overrides.csv: unknown field(s) {sorted(unknown)}; "
                         f"allowed: {sorted(OVERRIDE_FIELDS)}")


ENRICH_TABLES = {
    # file in data/enrich/   -> table, empty schema used when the file doesn't exist yet
    "ai_signals.parquet": ("ai_signals_external",
                           "cik varchar, source varchar, strength varchar, evidence varchar"),
    "yc_matches.parquet": ("yc_matches",
                           "cik varchar, yc_id integer, yc_name varchar, batch varchar, "
                           "tags varchar[], website varchar, yc_locations varchar, "
                           "one_liner varchar, match_method varchar"),
    "company_domains.parquet": ("company_domains", "cik varchar, domain varchar, source varchar, "
                                                   "title varchar, description varchar"),
    "web_signals.parquet": ("web_signals", "cik varchar, domain varchar, strength varchar, "
                                           "hero_terms varchar[], body_hits integer, "
                                           "distinct_terms integer"),
    "llm_labels.parquet": ("llm_labels", "cik varchar, model varchar, prompt_version varchar, "
                                         "answer varchar, quote varchar, quote_verified boolean, "
                                         "reason varchar, prompt_sha varchar"),
    "adv_advisers.parquet": ("adv_advisers",
                             "crd varchar, name varchar, legal_name varchar, city varchar, "
                             "state varchar, website varchar, registration varchar, "
                             "vc_exemption boolean, n_private_funds integer, "
                             "private_fund_gross_assets double, regulatory_aum double, "
                             "sec_status_date date, latest_adv_date date"),
    "ai_checks.parquet": ("ai_checks", "cik varchar, checked_at date, domain varchar, "
                                       "source varchar, title varchar, description varchar, "
                                       "strength varchar, hero_terms varchar[], "
                                       "body_hits integer, distinct_terms integer"),
    "geocodes.parquet": ("geocodes", "cik varchar, lat double, lon double, "
                                     "geocode_method varchar, matched_address varchar"),
}


def load_enrichment(con: duckdb.DuckDBPyConnection) -> None:
    """Load data/enrich/*.parquet (written by pipeline.ai_tag and pipeline.geocode).

    These are committed snapshots of slow, polite third-party lookups, so a rebuild
    never has to repeat them.
    """
    enrich = DATA_DIR / "enrich"
    for file, (table, empty_schema) in ENRICH_TABLES.items():
        path = enrich / file
        if path.exists():
            con.execute(f"create or replace table {table} as select * from read_parquet('{path}')")
        elif empty_schema:
            con.execute(f"create or replace table {table} ({empty_schema})")


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_PATH)) as con:
        print("Loading raw Form D tables:")
        load_raw(con)
        load_geo(con)
        for table in RAW_TABLES.values():
            n = con.execute(f"select count(*) from {table}").fetchone()[0]
            print(f"  {table}: {n:,} rows")


if __name__ == "__main__":
    main()
