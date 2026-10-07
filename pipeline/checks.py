"""Data tests run on every build. FAIL stops the build; WARN is printed for review."""

import duckdb

from pipeline import DB_PATH


def _one(con, sql):
    return con.execute(sql).fetchone()[0]


def run_checks(con: duckdb.DuckDBPyConnection) -> bool:
    results: list[tuple[str, str, str]] = []

    def check(name: str, ok: bool, detail: str, severity: str = "FAIL") -> None:
        results.append(("PASS" if ok else severity, name, detail))

    # Nothing lost between raw and typed layers.
    raw = _one(con, "select count(*) from raw_submission")
    typed = _one(con, "select count(*) from filings")
    check("every raw submission becomes a filing", raw == typed, f"raw={raw:,} filings={typed:,}")

    dupes = _one(con, "select count(*) - count(distinct accession) from filings")
    check("accession is unique in filings", dupes == 0, f"{dupes} duplicates")

    bad_dates = _one(con, "select count(*) from filings where filing_date is null")
    check("every filing date parses", bad_dates == 0, f"{bad_dates} unparsed")

    # Every D/A resolves to an offering.
    unlinked = _one(con, "select count(*) from filings where offering_id is null")
    check("every filing has an offering_id", unlinked == 0, f"{unlinked} without offering")

    n_amend, n_orphan = con.execute("""
        select count(*) filter (where is_amendment),
               count(*) filter (where link_method = 'orphan')
        from filings
    """).fetchone()
    orphan_pct = n_orphan / n_amend if n_amend else 0
    check("orphan amendments under 1% of D/As", orphan_pct < 0.01,
          f"{n_orphan:,} of {n_amend:,} D/As ({orphan_pct:.2%})", "WARN")

    cross_cik = _one(con, """
        select count(*) from (
            select offering_id from filings group by 1 having count(distinct cik) > 1)
    """)
    check("offerings do not span CIKs", cross_cik == 0,
          f"{cross_cik} offerings link filings from different CIKs", "WARN")

    # Dollar totals reconcile between the filing and offering layers.
    from_filings = _one(con, """
        select coalesce(sum(sold), 0) from (
            select sold from filings
            qualify row_number() over (partition by offering_id
                                       order by filing_date desc, accession desc) = 1)
    """)
    from_offerings = _one(con, "select coalesce(sum(sold_latest), 0) from offerings")
    check("sold totals reconcile (filings vs offerings)",
          abs(from_filings - from_offerings) < 1, f"${from_filings:,.0f} vs ${from_offerings:,.0f}")

    n_offerings = _one(con, "select count(*) from offerings")
    n_ids = _one(con, "select count(distinct offering_id) from filings")
    check("one offering row per offering_id", n_offerings == n_ids, f"{n_offerings:,} vs {n_ids:,}")

    oversold = _one(con, "select count(*) from offerings where flag_oversold")
    check("oversold offerings are flagged (sold > 2x target)", True, f"{oversold:,} flagged", "WARN")

    implausible, implausible_local = con.execute("""
        select count(*), count(*) filter (where metro is not null)
        from offerings where flag_implausible
    """).fetchone()
    check("implausible amounts (>= $100B) flagged for review", True,
          f"{implausible:,} flagged, {implausible_local} in tracked metros", "WARN")

    # Quarter-over-quarter volume stays within 20%.
    jumps = con.execute("""
        select source_quarter, n, prev from (
            select source_quarter, count(*) n, lag(count(*)) over (order by source_quarter) prev
            from filings
            where source_quarter in (select source_quarter from load_manifest where complete)
            group by 1)
        where prev is not null and abs(n - prev) / prev > 0.20
        order by 1
    """).fetchall()
    check("quarterly filing counts within 20% of prior quarter", not jumps,
          ", ".join(f"{q}: {p:,}->{n:,}" for q, n, p in jumps) or "all within 20%", "WARN")

    unplaced = _one(con, "select count(*) from issuers where state = 'CA' and county_fips is null")
    check("California issuers placed in a county", unplaced == 0,
          f"{unplaced} CA issuers without a county", "WARN")

    # Classification (30_classify.sql).
    no_kind = _one(con, "select count(*) from offerings where kind is null")
    check("every offering has a kind", no_kind == 0, f"{no_kind} without kind")

    incomplete = _one(con, """
        select count(*) from offerings
        where kind = 'fund' and (vehicle_type is null or manager_key is null)
    """)
    check("every fund has vehicle_type and manager_key", incomplete == 0, f"{incomplete} incomplete")

    unclear, total = con.execute("""
        select count(*) filter (where vehicle_type = 'unclear'), count(*)
        from offerings
        where fund_bucket = 'venture' and is_new_in_window and metro is not null
    """).fetchone()
    share = unclear / total if total else 0
    check("unclear SF/LA VC vehicles under 20%", share < 0.20,
          f"{unclear:,} of {total:,} ({share:.0%}) need a rule or override", "WARN")

    stale = _one(con, """
        select count(*) from manual_overrides
        where cik not in (select cik from issuers)
           or (offering_id is not null and offering_id not in (select offering_id from offerings))
    """)
    check("manual overrides point at known CIKs/offerings", stale == 0,
          f"{stale} override rows match nothing", "WARN")

    # Enrichment (data/enrich/*.parquet).
    outside = _one(con, """
        select count(*) from issuers i join metro_bbox b using (metro)
        where i.lat is not null
          and not (i.lat between b.lat_min and b.lat_max and i.lon between b.lon_min and b.lon_max)
    """)
    dropped = _one(con, "select count(*) from issuers where geo_method = 'zip_conflicts_with_address'")
    check("mapped issuers fall inside their metro", outside == 0,
          f"{outside} outside the bounding box; {dropped} dropped from a metro (address beat ZIP)",
          "WARN")

    sources = dict(con.execute("select source, count(distinct cik) from ai_signals group by 1").fetchall())
    check("AI evidence from name, YC and homepages present",
          all(sources.get(s, 0) > 0 for s in ("name", "yc", "website")),
          ", ".join(f"{k} {v:,}" for k, v in sorted(sources.items())) or "none", "WARN")

    width = max(len(name) for _, name, _ in results)
    for status, name, detail in results:
        print(f"  [{status:4}] {name:<{width}}  {detail}")
    return not any(status == "FAIL" for status, _, _ in results)


def main() -> None:
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        raise SystemExit(0 if run_checks(con) else 1)


if __name__ == "__main__":
    main()
