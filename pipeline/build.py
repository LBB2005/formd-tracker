"""Full rebuild: download (cached) -> EDGAR daily gap -> load raw -> sql/ in order -> checks.

Usage:
    uv run python -m pipeline.build                  # everything
    uv run python -m pipeline.build --skip-download  # offline rebuild from cache
    uv run python -m pipeline.build --sql-only       # rerun sql/ on the loaded raw tables
"""

import argparse
import time

import duckdb

from pipeline import DB_PATH, SQL_DIR, daily, download, load
from pipeline.checks import run_checks


def run_sql(con: duckdb.DuckDBPyConnection) -> None:
    for path in sorted(SQL_DIR.glob("*.sql")):
        start = time.perf_counter()
        con.execute(path.read_text())
        print(f"  {path.name}  ({time.perf_counter() - start:.1f}s)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--sql-only", action="store_true")
    args = parser.parse_args(argv)

    if not (args.skip_download or args.sql_only):
        download.main([])
        print("EDGAR filings since the latest quarterly data set:")
        daily.main([])
    if not args.sql_only:
        load.main()

    with duckdb.connect(str(DB_PATH)) as con:
        load.load_config(con)
        load.load_enrichment(con)
        print("Building tables:")
        run_sql(con)
        print("Data checks:")
        ok = run_checks(con)
        for table in ("filings", "offerings", "issuers"):
            n = con.execute(f"select count(*) from {table}").fetchone()[0]
            print(f"  {table}: {n:,} rows")
    if not ok:
        raise SystemExit("Build finished with failing checks.")


if __name__ == "__main__":
    main()
