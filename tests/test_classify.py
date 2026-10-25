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
    "is_new_in_window": "boolean", "duplicate_of": "varchar", "latest_accession": "varchar",
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


LLC, LP = "Limited Liability Company", "Limited Partnership"


@pytest.mark.parametrize("name, entity, code, family", [
    ("AL-D Ribbit Opportunity V, LLC", LLC, "AL-D", "ribbit opportunity v llc"),
    ("FundersClub 6XV LLC", LLC, "6XV", "fundersclub llc"),
    ("Center15 Capital E LLC", LLC, "E", "center15 capital llc"),
    ("HCM S9Aii LP", LP, None, None),                      # trailing token: LLCs only
    ("Ravikant Capital, LP - G2", LP, None, None),          # rolling-fund class
    ("LINK Fund II LLC", LLC, None, None),                  # fund number
    ("AV Opportunity Fund 2024, LLC", LLC, None, None),     # vintage year
    ("Purple Arch Ventures Fund 8Q, LLC", LLC, None, None),  # quarter class
    ("ALF-III Capital Partners, LLC", LLC, None, None),     # generic remainder
])
def test_vehicle_code(name, entity, code, family):
    got = macros().execute("select vehicle_code(?, ?)", [name, entity]).fetchone()[0]
    assert got == (None if code is None else {"code": code, "family": family})


def vehicle_calls(rows):
    con = classify(rows)
    return {oid: (vt, why) for oid, vt, why in
            con.execute("select offering_id, vehicle_type, vehicle_reason from offering_class").fetchall()}


def test_vehicles_that_differ_only_by_a_code_are_spvs():
    rows = [vc(f"rib{c}", f"{c} Ribbit Opportunity V, LLC", cik=f"R{c}", entity_type=LLC)
            for c in ["AL-D", "CO-B", "GW-E"]]
    rows += [vc(f"fc{c}", f"FundersClub {c} LLC", cik=f"F{c}", entity_type=LLC)
             for c in ["6XV", "2YK", "D6H"]]
    rows += [vc(f"hcm{c}", f"HCM {c} LP", cik=f"H{c}", entity_type=LP)
             for c in ["S9Ai", "S9Aii", "S10A"]]
    rows += [vc("cal", "Calibrate Ventures NH, LLC", entity_type=LLC),
             vc("ribx", "Ribbit Capital X, L.P.", entity_type=LP)]
    got = vehicle_calls(rows)
    for oid in ["ribAL-D", "ribCO-B", "ribGW-E", "fc6XV", "fc2YK", "fcD6H"]:
        assert got[oid] == ("spv", "manager: 3+ vehicles that differ only by a code (one per deal)")
    assert {got[f"hcm{c}"][0] for c in ["S9Ai", "S9Aii", "S10A"]} == {"unclear"}
    assert got["cal"][0] == "unclear"      # one coded vehicle is not a family
    assert got["ribx"][0] == "blind_pool"  # the manager's main fund


def test_rolling_funds_and_manager_named_lps_are_blind_pools():
    got = vehicle_calls([
        vc("roll", "Ravikant Capital, LP - G2", entity_type=LP),
        vc("rollqp", "MCJ Collective QP, LP - B1", entity_type=LP),
        vc("indus", "Indus Valley Capital, LP", entity_type=LP),
        vc("llc", "Indus Valley Capital, LLC", entity_type=LLC),
        vc("deal", "MAV Neuralink, LP", entity_type=LP),
        vc("paren", "Castle Peak Partners (Lark), LP", entity_type=LP),
    ])
    assert got["roll"] == got["rollqp"] == ("blind_pool", "name: rolling-fund quarterly class")
    assert got["indus"] == ("blind_pool", "structure: LP named like a manager")
    # Only a name that ends in the manager words counts.
    assert got["llc"][0] == got["deal"][0] == got["paren"][0] == "unclear"


def test_reviewer_settled_vehicle_patterns():
    # Hand-labelled on 2026-10-20: SPV words win even on large raises; "Series" inside a
    # fund's own name and parallel vehicles of numbered funds stay blind pools.
    got = vehicle_calls([
        vc("b1", "224 Ventures SPV Series LP, Series B1", entity_type=LP),
        vc("port", "Portfolia 2026 SPV 6 LLC", entity_type=LLC),
        vc("annual", "Wilshire Private Equity Annual Fund Series, L.P.", entity_type=LP),
        vc("del", "WA Crown City CLO Fund I (Delaware), LP", entity_type=LP),
        vc("feeder", "Alpine Space Ventures Fund II Feeder, L.P.", entity_type=LP),
    ])
    assert {k: v[0] for k, v in got.items()} == {
        "b1": "spv", "port": "spv", "annual": "blind_pool", "del": "blind_pool",
        "feeder": "blind_pool"}


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
