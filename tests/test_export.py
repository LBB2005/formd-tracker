"""Every Parquet file a site page attaches is one that pipeline/export.py writes."""

import re

from pipeline import ROOT
from pipeline.export import PAGE_TABLES, TABLES

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
