"""Name parsing (sql/00_macros.sql) and classification rules (sql/30_classify.sql)."""

from datetime import date, timedelta

import duckdb
import pytest

from pipeline import SQL_DIR


def macros():
    con = duckdb.connect()
    con.execute((SQL_DIR / "00_macros.sql").read_text())
    return con


@pytest.mark.parametrize("name, number, stem", [
    ("Accel XIV L.P.", 14, "accel"),
    ("Andreessen Horowitz Fund IX - AI Infrastructure, L.P.", 9, "andreessen horowitz"),
    ("01 Advisors 04, L.P.", 4, "01 advisors"),
    ("Kindred Ventures Associates III, L.P.", 3, "kindred ventures"),
    ("DragonX Fund I-A, LP", 1, "dragonx"),
    ("122 West Ventures II, L.P.", 2, "122 west ventures"),
    ("MVP Vintage 2023, LP", None, "mvp vintage"),
    ("X, a series of Rhone VC, LP", None, "rhone vc"),
])
def test_fund_number_and_manager_stem(name, number, stem):
    con = macros()
    assert con.execute("select fund_number(?)", [name]).fetchone()[0] == number
    assert con.execute("select manager_stem(?)", [name]).fetchone()[0] == stem


@pytest.mark.parametrize("name, stem", [
    ("Kindred Ventures Management, LLC", "kindred ventures"),
    ("Accelmed Partners II Management, LLC", "accelmed partners"),
    ("Andreessen Horowitz", "andreessen horowitz"),
    ("Craft Ventures Management LP", "craft ventures"),
])
def test_adviser_stem_matches_fund_manager_stem(name, stem):
    con = macros()
    got = con.execute("select regexp_replace(adviser_stem(?), '\\s+', ' ', 'g')", [name]).fetchone()[0]
    assert got == stem


def test_generic_stems_are_not_merged():
    con = macros()
    assert con.execute("select is_generic_stem('capital partners')").fetchone()[0]
    assert not con.execute("select is_generic_stem('kindred ventures')").fetchone()[0]


def test_unquote():
    con = macros()
    assert con.execute("""select unquote('"Say ""hi"", Ltd."')""").fetchone()[0] == 'Say "hi", Ltd.'
    assert con.execute("select unquote('Plain, LLC')").fetchone()[0] == "Plain, LLC"


OFFERING_COLS = {
    "offering_id": "varchar", "cik": "varchar", "issuer_name": "varchar",
    "industry_group": "varchar", "fund_type": "varchar", "entity_type": "varchar",
    "is_pooled_fund": "boolean", "has_3c": "boolean", "is_equity": "boolean",
    "is_debt": "boolean", "is_option": "boolean", "is_other_type": "boolean",
    "other_type_desc": "varchar", "first_filed": "date", "first_sale_date": "date",
    "offering_amt": "double", "investors_latest": "integer", "metro": "varchar",
    "is_new_in_window": "boolean", "latest_accession": "varchar",
}


def offering(oid, name, industry="Other Technology", fund_type=None, filed=date(2024, 1, 1),
             cik=None, **flags):
    row = {"offering_id": oid, "cik": cik or f"C-{oid}", "issuer_name": name,
           "industry_group": industry, "fund_type": fund_type, "first_filed": filed,
           "first_sale_date": filed, "latest_accession": oid, "is_new_in_window": True,
           "metro": "sf_bay", "is_pooled_fund": False, "has_3c": False, "is_equity": False,
           "is_debt": False, "is_option": False, "is_other_type": False}
    row.update(flags)
    return row


def vc(oid, name, **kw):
    return offering(oid, name, industry="Pooled Investment Fund",
                    fund_type="Venture Capital Fund", has_3c=True, **kw)


def classify(rows, overrides=()):
    con = macros()
    con.execute(f"create table offerings ({', '.join(f'{k} {v}' for k, v in OFFERING_COLS.items())})")
    con.execute("create table filings (accession varchar, is_security_to_be_acquired boolean)")
    con.execute("create table issuers (cik varchar, is_venture_stage boolean)")
    con.execute("create table manual_overrides "
                "(cik varchar, offering_id varchar, field varchar, value varchar, note varchar)")
    for row in rows:
        cols = [c for c in row if c in OFFERING_COLS]
        con.execute(f"insert into offerings ({', '.join(cols)}) values ({', '.join('?' * len(cols))})",
                    [row[c] for c in cols])
        con.execute("insert into filings values (?, false)", [row["latest_accession"]])
        con.execute("insert into issuers values (?, ?)", [row["cik"], row.get("venture", True)])
    for ov in overrides:
        con.execute("insert into manual_overrides values (?, ?, ?, ?, '')", ov)
    con.execute((SQL_DIR / "30_classify.sql").read_text())
    return con


def calls(con):
    return {oid: (kind, vt) for oid, kind, vt in
            con.execute("select offering_id, kind, vehicle_type from offering_class").fetchall()}


def test_fund_vs_company_and_vehicle_types():
    con = classify([
        offering("startup", "Acme Robotics, Inc.", is_equity=True),
        offering("safe", "Tiny AI, Inc.", is_other_type=True, other_type_desc="SAFE"),
        offering("aurum", "Anthropic - a Series of Aurum VP Fund LLC", industry="Other"),
        offering("tower", "Tower Plaza LLC", industry="Commercial"),
        vc("pool", "Goodfinch Fund II, LP"),
        vc("spv", "Lightspeed SPV I-C, LLC"),
        vc("deal", "OurCrowd (Investment in Surgical Theater) L.P."),
        vc("mystery", "Quiet Harbor LP"),
    ])
    assert calls(con) == {
        "startup": ("operating_company", None),
        "safe": ("operating_company", None),
        "aurum": ("fund", "spv"),  # industry "Other", caught by the name rule
        "tower": ("real_estate", None),
        "pool": ("fund", "blind_pool"),
        "spv": ("fund", "spv"),
        "deal": ("fund", "spv"),
        "mystery": ("fund", "unclear"),
    }
    sec = dict(con.execute(
        "select offering_id, security_class from offering_class where kind = 'operating_company'"
    ).fetchall())
    assert sec == {"startup": "equity", "safe": "safe_or_convertible"}


def test_numbered_deal_series_become_spvs():
    rows = [vc(f"hcm{i}", f"HCM G {r}, L.P.", cik=f"H{i}", filed=date(2024, 1, 1) + timedelta(days=30 * i))
            for i, r in enumerate(["I", "II", "III", "IV", "V"])]
    rows.append(vc("accel", "Accel XIV L.P."))
    got = calls(classify(rows))
    assert {got[f"hcm{i}"][1] for i in range(5)} == {"spv"}
    assert got["accel"][1] == "blind_pool"


def test_parallel_vehicles_form_one_family_and_emerging_is_fund_i_to_iii():
    con = classify([
        vc("a1", "Goodfinch Fund I, LP", cik="G1", filed=date(2020, 1, 1)),
        vc("a1b", "Goodfinch Fund I-A, LP", cik="G2", filed=date(2020, 2, 1)),
        vc("a4", "Goodfinch Fund IV, LP", cik="G3", filed=date(2024, 1, 1)),
    ])
    fams = dict(con.execute(
        "select fund_family_id, is_emerging from fund_families order by 1").fetchall())
    assert fams == {"goodfinch|fund1": True, "goodfinch|fund4": False}


def test_overrides_apply_and_are_recorded():
    con = classify(
        [vc("mystery", "Quiet Harbor LP", cik="0000000009")],
        overrides=[("0000000009", None, "vehicle_type", "blind_pool")],
    )
    vt, fields = con.execute(
        "select vehicle_type, overridden_fields from offering_class").fetchone()
    assert vt == "blind_pool" and fields == ["vehicle_type"]


def test_established_companies_are_not_startup_raises():
    con = classify([
        offering("young", "Acme Robotics, Inc.", is_equity=True),
        offering("old", "Intel Corp", is_equity=True, venture=False),
    ])
    got = dict(con.execute("select offering_id, is_startup_raise from offering_class").fetchall())
    assert got == {"young": True, "old": False}
