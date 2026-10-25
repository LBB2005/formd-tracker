"""AI name rules and tiering in sql/40_ai_tag.sql."""

import duckdb
import pytest

from pipeline import SQL_DIR


def tag(names: dict[str, tuple[str, list[str]]], overrides=(), external=()):
    con = duckdb.connect()
    con.execute("create table issuers (cik varchar, name varchar, former_names varchar[], metro varchar)")
    con.execute("create table offerings (offering_id varchar, cik varchar, kind varchar)")
    con.execute("create table filings (offering_id varchar, issuer_name varchar)")
    con.execute("create table manual_overrides "
                "(cik varchar, offering_id varchar, field varchar, value varchar, note varchar)")
    for cik, (name, former) in names.items():
        con.execute("insert into issuers values (?, ?, ?, 'sf_bay')", [cik, name, former])
        con.execute("insert into offerings values (?, ?, 'operating_company')", [f"o{cik}", cik])
        con.execute("insert into filings values (?, ?)", [f"o{cik}", name])
    con.execute("create table ai_signals_external "
                "(cik varchar, source varchar, strength varchar, evidence varchar)")
    for row in external:
        con.execute("insert into ai_signals_external values (?, ?, ?, ?)", row)
    for cik, value in overrides:
        con.execute("insert into manual_overrides values (?, null, 'ai_tier', ?, '')", [cik, value])
    con.execute((SQL_DIR / "40_ai_tag.sql").read_text())
    return dict(con.execute("select cik, ai_tier from issuers").fetchall())


@pytest.mark.parametrize("name, tier", [
    ("Harvey AI Corp", "likely"),
    ("X.AI Holdings Corp.", "likely"),
    ("Nowcasting.ai Inc.", "likely"),
    ("RelationalAI, Inc.", "likely"),           # CamelCase suffix
    ("Flo Artificial Intelligence, Inc.", "likely"),
    ("Foundation LLM Technologies, Inc.", "likely"),
    ("Jupiter Intelligence, Inc.", "possible"),  # weak term only
    ("Neuralink Corp.", "possible"),
    ("Ikigai Labs, Inc.", None),                 # lowercase "ai" suffix is not a signal
    ("Thai Kitchen LLC", None),
    ("Haier US Holdings", None),
    ("Kai Robotics, Inc.", None),
])
def test_name_rules(name, tier):
    assert tag({"1": (name, [])})["1"] == tier


def test_former_name_counts():
    assert tag({"1": ("Armadin, Inc.", ["Ballistic AI, Inc."])})["1"] == "likely"


def test_two_independent_sources_make_strong_and_overrides_win():
    tiers = tag(
        {"1": ("Harvey AI Corp", []), "2": ("Quiet Labs, Inc.", []), "3": ("Lumen AI, Inc.", [])},
        external=[("1", "website", "strong", "homepage: legal AI platform")],
        overrides=[("2", "likely"), ("3", "none")],
    )
    assert tiers == {"1": "strong", "2": "likely", "3": "none"}


def test_name_at_filing_is_time_correct():
    con = duckdb.connect()
    con.execute("create table issuers (cik varchar, name varchar, former_names varchar[], metro varchar)")
    con.execute("create table offerings (offering_id varchar, cik varchar, kind varchar)")
    con.execute("create table filings (offering_id varchar, issuer_name varchar)")
    con.execute("create table manual_overrides (cik varchar, offering_id varchar, field varchar, value varchar, note varchar)")
    con.execute("create table ai_signals_external (cik varchar, source varchar, strength varchar, evidence varchar)")
    # Renamed to an AI name in 2024: the 2019 raise was filed under the old name.
    con.execute("insert into issuers values ('1', 'Armadin AI, Inc.', ['Armadin, Inc.'], 'sf_bay')")
    con.execute("insert into offerings values ('r2019', '1', 'operating_company'), ('r2024', '1', 'operating_company')")
    con.execute("insert into filings values ('r2019', 'Armadin, Inc.'), ('r2024', 'Armadin AI, Inc.')")
    con.execute((SQL_DIR / "40_ai_tag.sql").read_text())
    got = dict(con.execute("select offering_id, ai_name_at_filing from offerings").fetchall())
    assert got == {"r2019": False, "r2024": True}


@pytest.mark.parametrize("model_out, answer", [
    ({"answer": "yes", "quote": "AI agents for sales teams", "reason": ""}, "yes"),
    ({"answer": "yes", "quote": "AI platform", "reason": ""}, "unclear"),  # only inside "OpenAI platform"
    (["yes"], None),  # valid JSON that isn't an object
])
def test_local_judge_quote_must_be_whole_words(tmp_path, monkeypatch, model_out, answer):
    import json
    from types import SimpleNamespace
    from pipeline import ai_tag

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"response": json.dumps(model_out)}

    monkeypatch.setattr(ai_tag, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ai_tag.requests, "post", lambda *a, **k: Resp())
    page = SimpleNamespace(hero="Acme builds AI agents for sales teams.",
                           text="Runs on the OpenAI platform.")
    result = ai_tag.ask_model("1", "Acme", "acme.com", page, "test-model")
    assert (result["answer"] if result else None) == answer
