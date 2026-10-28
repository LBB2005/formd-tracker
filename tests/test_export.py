"""Every Parquet file a site page attaches is one that pipeline/export.py writes, and the
per-company files hold each company where the page looks for it."""

import json
import re

import duckdb

from pipeline import ROOT
from pipeline.export import COMPANY_LISTS, PAGE_TABLES, TABLES, write_company_files

PAGES = sorted((ROOT / "site" / "src").glob("*.md"))


def test_pages_attach_only_exported_parquet():
    exported = set(TABLES) | set(PAGE_TABLES)
    for page in PAGES:
        for name in re.findall(r'FileAttachment\("data/(\w+)\.parquet"\)', page.read_text()):
            assert name in exported, f"{page.name} attaches data/{name}.parquet, which export.py never writes"


def test_page_tables_read_from_exported_files():
    for i, (name, query) in enumerate(PAGE_TABLES.items()):
        written_before = set(TABLES) | set(list(PAGE_TABLES)[:i])
        sources = re.findall(r"DATA/(\w+)\.parquet", query)
        assert sources, f"{name} should read a shared file so its values match it"
        for source in sources:
            assert source in written_before, f"{name} reads {source}, which is not written before it"


def test_each_company_is_in_the_file_for_its_last_two_digits(tmp_path):
    ciks = ["0000000101", "0000000201", "0000000199"]
    with duckdb.connect() as con:
        con.execute(f"copy (select unnest({ciks}) as cik, 'Co' as name) to '{tmp_path}/issuers.parquet'")
        for t, (cols, order) in COMPANY_LISTS.items():
            # Two rows for the first company, inserted out of order; none for the others.
            fields = ", ".join(f"i as {c.strip()}" for c in cols.split(","))
            con.execute(f"copy (select '{ciks[0]}' as cik, {fields} from range(2) t(i) order by i desc) "
                        f"to '{tmp_path}/{t}.parquet'")
        assert write_company_files(con, tmp_path) == 3

    out = tmp_path / "company"
    assert sorted(f.name for f in out.glob("*.json")) == ["01.json", "99.json"]
    first = json.loads((out / "01.json").read_text())
    assert set(first) == {"0000000101", "0000000201"}
    assert first["0000000101"]["issuer"] == {"cik": "0000000101", "name": "Co"}
    for t in COMPANY_LISTS:
        assert [next(iter(r.values())) for r in first["0000000101"][t]] == [0, 1]  # sorted
    assert first["0000000201"]["filings"] is None  # the page treats a missing list as empty
    shards = (out / "shards.js").read_text()
    assert re.findall(r'"(\d\d)": FileAttachment\("\./(\d\d)\.json"\)', shards) == [("01", "01"), ("99", "99")]
