"""Amendment-chain rules in sql/20_offerings.sql, on small synthetic filings."""

from datetime import date

import duckdb
import pytest

from pipeline import SQL_DIR

COLUMNS = {
    "accession": "varchar", "cik": "varchar", "issuer_name": "varchar", "filing_date": "date",
    "form": "varchar", "is_amendment": "boolean", "prev_accession": "varchar",
    "first_sale_date": "date", "offering_amt": "double", "offering_indefinite": "boolean",
    "sold": "double", "remaining": "double", "investors": "integer", "min_investment": "double",
    "industry_group": "varchar", "fund_type": "varchar", "exemptions": "varchar[]",
    "is_506b": "boolean", "is_506c": "boolean", "has_3c": "boolean", "is_equity": "boolean",
    "is_debt": "boolean", "is_option": "boolean", "is_pooled_fund": "boolean",
    "is_other_type": "boolean", "other_type_desc": "varchar", "entity_type": "varchar",
    "sales_commission": "double", "finders_fee": "double",
    "offering_id": "varchar", "link_method": "varchar",
}


def filing(accession, filed, form="D", prev=None, cik="0000000001", sale=date(2020, 1, 1),
           amt=1_000_000.0, sold=0.0, investors=1):
    return {
        "accession": accession, "cik": cik, "issuer_name": "Acme Inc", "filing_date": filed,
        "form": form, "is_amendment": form == "D/A", "prev_accession": prev,
        "first_sale_date": sale, "offering_amt": amt, "offering_indefinite": False,
        "sold": sold, "investors": investors, "exemptions": ["06b"],
    }


def build(rows):
    con = duckdb.connect()
    con.execute(f"create table filings ({', '.join(f'{k} {v}' for k, v in COLUMNS.items())})")
    for row in rows:
        cols = list(row)
        con.execute(
            f"insert into filings ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )
    con.execute((SQL_DIR / "20_offerings.sql").read_text())
    return con


def links(con):
    return {a: (o, m) for a, o, m in
            con.execute("select accession, offering_id, link_method from filings").fetchall()}


def test_chain_of_amendments_collapses_to_original():
    con = build([
        filing("A-1", date(2020, 1, 10)),
        filing("A-2", date(2020, 6, 1), "D/A", prev="A-1", sold=400_000),
        filing("A-3", date(2021, 1, 5), "D/A", prev="A-2", sold=900_000),  # points at prior D/A
    ])
    assert links(con) == {
        "A-1": ("A-1", "original"),
        "A-2": ("A-1", "prev_pointer"),
        "A-3": ("A-1", "prev_pointer"),
    }
    n, sold, n_amend, new = con.execute(
        "select count(*), any_value(sold_latest), any_value(n_amendments), "
        "any_value(is_new_in_window) from offerings").fetchone()
    assert (n, sold, n_amend, new) == (1, 900_000, 2, True)


def test_raised_is_latest_snapshot_not_sum_and_corrections_flagged():
    con = build([
        filing("A-1", date(2020, 1, 10), sold=500_000),
        filing("A-2", date(2020, 6, 1), "D/A", prev="A-1", sold=800_000),
        filing("A-3", date(2021, 1, 5), "D/A", prev="A-2", sold=700_000),
    ])
    sold, corrections, decreased = con.execute(
        "select sold_latest, n_corrections, flag_sold_decreased from offerings").fetchone()
    assert sold == 700_000
    assert corrections == 1 and decreased
    increments = con.execute(
        "select sold_increment from filing_increments order by filing_date").fetchall()
    assert [r[0] for r in increments] == [None, 300_000, -100_000]


def test_new_original_from_same_cik_is_a_new_offering():
    con = build([
        filing("A-1", date(2020, 1, 10)),
        filing("B-1", date(2022, 3, 1), sale=date(2022, 2, 20), amt=5_000_000),
    ])
    assert con.execute("select count(*) from offerings").fetchone()[0] == 2


def test_chain_leaving_window_is_marked_external():
    con = build([
        filing("A-2", date(2019, 4, 1), "D/A", prev="OLD-1"),
        filing("A-3", date(2020, 4, 1), "D/A", prev="A-2"),
    ])
    assert links(con) == {
        "A-2": ("OLD-1", "prev_pointer_external"),
        "A-3": ("OLD-1", "prev_pointer_external"),
    }
    new, before = con.execute(
        "select is_new_in_window, began_before_window from offerings").fetchone()
    assert (new, before) == (False, True)


def test_external_roots_for_same_offering_are_merged():
    # One D/A points at the 2017 original, a later one at a 2018 amendment we don't hold.
    con = build([
        filing("A-5", date(2019, 4, 1), "D/A", prev="OLD-1"),
        filing("A-6", date(2020, 4, 1), "D/A", prev="OLD-2"),
    ])
    assert {o for o, _ in links(con).values()} == {"OLD-1"}


def test_broken_pointer_falls_back_to_sale_date_then_amount():
    con = build([
        filing("A-1", date(2020, 1, 10), sale=date(2020, 1, 2)),
        filing("A-2", date(2020, 9, 1), "D/A", sale=date(2020, 1, 2)),  # no pointer
        filing("B-1", date(2021, 1, 10), sale=date(2021, 1, 1), amt=7_000_000),
        filing("B-2", date(2021, 6, 1), "D/A", sale=date(2021, 1, 9), amt=7_000_000),
    ])
    got = links(con)
    assert got["A-2"] == ("A-1", "fallback_sale_date")
    assert got["B-2"] == ("B-1", "fallback_amount")


def test_unmatched_amendment_is_orphan():
    con = build([
        filing("A-1", date(2019, 1, 10), sale=date(2019, 1, 2), amt=1_000_000),
        # Same amount but more than 18 months later, different sale date.
        filing("A-2", date(2021, 1, 5), "D/A", sale=date(2020, 12, 1), amt=1_000_000),
    ])
    assert links(con)["A-2"] == ("A-2", "orphan")


def test_refiled_original_is_a_duplicate_not_a_new_raise():
    con = build([
        # Filed twice on one day (Island Green AD-I LP), then copies 6 and 12 days later,
        # each within 7 days of the one before: all point at the earliest accession.
        filing("D-2", date(2026, 10, 2), sale=date(2026, 8, 24), amt=17e6, sold=17e6),
        filing("D-1", date(2026, 10, 2), sale=date(2026, 8, 24), amt=17e6, sold=17e6),
        filing("D-3", date(2026, 10, 8), sale=date(2026, 8, 24), amt=17e6, sold=17e6),
        filing("D-5", date(2026, 10, 14), sale=date(2026, 8, 24), amt=17e6, sold=17e6),
        # Same figures, but over a month later: a new raise.
        filing("D-4", date(2026, 11, 20), sale=date(2026, 8, 24), amt=17e6, sold=17e6),
        # Different amount sold: a different raise.
        filing("E-1", date(2026, 10, 2), sale=date(2026, 8, 24), amt=17e6, sold=5e6),
        # Empty filings (no amount, nothing sold) are never collapsed.
        filing("F-1", date(2026, 10, 2), sale=None, amt=None, sold=0.0),
        filing("F-2", date(2026, 10, 3), sale=None, amt=None, sold=0.0),
        # Another CIK with identical figures.
        filing("G-1", date(2026, 10, 2), cik="0000000002", sale=date(2026, 8, 24), amt=17e6, sold=17e6),
    ])
    got = dict(con.execute("select offering_id, duplicate_of from offerings").fetchall())
    assert got == {"D-1": None, "D-2": "D-1", "D-3": "D-1", "D-5": "D-1", "D-4": None,
                   "E-1": None, "F-1": None, "F-2": None, "G-1": None}
    new = dict(con.execute("select offering_id, is_new_in_window from offerings").fetchall())
    assert [o for o, is_new in sorted(new.items()) if not is_new] == ["D-2", "D-3", "D-5"]
    # Rows are kept, so filing-level and offering-level totals still match.
    assert con.execute("select count(*) from offerings").fetchone()[0] == 9


@pytest.mark.parametrize("order", [["X-1", "X-2"], ["X-2", "X-1"]])
def test_same_day_filings_resolve_deterministically(order):
    rows = {
        "X-1": filing("X-1", date(2019, 4, 1), "D/A", prev="OLD-1", sale=date(2018, 1, 1)),
        "X-2": filing("X-2", date(2019, 4, 1), "D/A", prev="OLD-1", sale=date(2018, 2, 1)),
    }
    con = build([rows[a] for a in order])
    assert con.execute("select first_sale_date from offerings").fetchone()[0] == date(2018, 2, 1)
