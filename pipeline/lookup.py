"""Look up an issuer's offerings and filings, with EDGAR links, for spot checks.

Usage:
    uv run python -m pipeline.lookup "Anthropic"
    uv run python -m pipeline.lookup 0001234567
"""

import sys

import duckdb

from pipeline import DB_PATH


def filing_url(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"


def money(x) -> str:
    return "—" if x is None else f"${x:,.0f}"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    term = " ".join(sys.argv[1:])
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        if term.isdigit():
            where, param = "i.cik = lpad(?, 10, '0')", term
        else:
            where, param = ("i.name ilike ? or list_contains("
                            "list_transform(i.former_names, x -> lower(x)), lower(?))"), None
        params = [param] if param else [f"%{term}%", term]
        # Popular names are swamped by SPVs ("Anthropic - a Series of ..."); list
        # operating companies first, then vehicles.
        matches = con.execute(f"""
            select i.cik, i.name, i.city, i.state, i.metro, i.entity_type, i.edgar_url,
                   i.last_filing,
                   coalesce(bool_or(o.is_pooled_fund
                                    or o.industry_group = 'Pooled Investment Fund'
                                    or regexp_matches(i.name, '(?i)(a series of|\\bspv\\b|syndicate)')),
                            false) as is_vehicle
            from issuers i join offerings o on o.cik = i.cik
            where {where}
            group by all
            order by is_vehicle, last_filing desc
        """, params).fetchall()
        if not matches:
            raise SystemExit(f"No issuer matches {term!r}.")
        issuers = [m[:7] for m in matches[:10]]
        hidden = matches[10:]
        if hidden:
            n_vehicles = sum(m[8] for m in hidden)
            print(f"{len(matches)} matches; showing 10 ({len(hidden)} more hidden, "
                  f"{n_vehicles} of them fund/SPV vehicles). Use a CIK to pick one.")
        for cik, name, city, state, metro, etype, url in issuers:
            print(f"\n{name}  (CIK {cik}, {etype}, {city}, {state}, metro={metro or '—'})")
            print(f"  EDGAR: {url}")
            offerings = con.execute("""
                select offering_id, first_sale_date, first_filed, last_filed, n_filings,
                       offering_amt, offering_indefinite, sold_latest, investors_latest,
                       industry_group, fund_type, array_to_string(exemptions, ' '),
                       is_new_in_window
                from offerings where cik = ? order by first_filed
            """, [cik]).fetchall()
            for (oid, sale, first, last, n, amt, indef, sold, inv, ind, ftype, ex, new) in offerings:
                target = "Indefinite" if indef else money(amt)
                print(f"  Offering {oid}  first sale {sale}  filed {first} -> {last} "
                      f"({n} filing{'s' if n > 1 else ''}){'' if new else '  [began before window]'}")
                print(f"    target {target}  sold {money(sold)}  investors {inv}  "
                      f"{ind}{' / ' + ftype if ftype else ''}  exemptions: {ex}")
                for acc, fdate, form, fsold, link in con.execute("""
                    select accession, filing_date, form, sold, link_method
                    from filings where offering_id = ? order by filing_date, accession
                """, [oid]).fetchall():
                    print(f"      {fdate}  {form:<3}  sold {money(fsold):>16}  {link:<22} "
                          f"{filing_url(cik, acc)}")


if __name__ == "__main__":
    main()
