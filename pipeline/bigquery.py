"""Publish the built tables to BigQuery with the Google Cloud `bq` CLI.

    uv run python -m pipeline.bigquery                      # project/dataset from .env
    uv run python -m pipeline.bigquery --project my-proj --dataset formd

Each table is exported from data/formd.duckdb to Parquet and loaded with
`bq load --replace`, so every run fully refreshes the dataset. Requires `gcloud auth
login` (or a service account in CI). Project and dataset default to BQ_PROJECT and
BQ_DATASET in the environment or .env.

On a project without billing (the BigQuery sandbox), tables expire 60 days after they
are written; the replace-on-load here resets that clock on every run.
"""

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import duckdb

from pipeline import DB_PATH, ROOT

TABLES = {
    "offerings": ("One row per raise (an original Form D plus its amendments)",
                  "select * exclude (link_methods) from offerings"),
    "issuers": ("One row per issuer (CIK): address, metro, AI evidence", "select * from issuers"),
    "filings": ("One row per Form D or D/A filing", "select * from filings"),
    "filing_increments": ("Change in amount sold per filing within its raise",
                          "select * from filing_increments"),
    "fund_families": ("Blind-pool funds with parallel vehicles collapsed; emerging flag",
                      "select * from fund_families"),
    "managers": ("Fund managers with Form ADV adviser match", "select * from managers"),
    "people": ("People and entities named on Form D, one row per person",
               "select * from people"),
    "issuer_people": ("Person-to-issuer links with roles", "select * from issuer_people"),
    "capital_momentum": ("0-100 capital momentum score per venture-stage startup",
                         "select * from capital_momentum"),
    "ai_signals": ("Every AI evidence row (name, YC, website, model) per CIK",
                   "select * from ai_signals"),
}


def env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return None


def bq(*args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("bq")
    if not exe:
        raise SystemExit("The `bq` CLI was not found. Install the Google Cloud SDK.")
    return subprocess.run([exe, "--quiet", *args], capture_output=True, text=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=env("BQ_PROJECT"))
    parser.add_argument("--dataset", default=env("BQ_DATASET") or "formd")
    parser.add_argument("--location", default=env("BQ_LOCATION") or "US")
    parser.add_argument("--tables", nargs="*", default=list(TABLES))
    args = parser.parse_args(argv)
    if not args.project:
        raise SystemExit("Set BQ_PROJECT in .env (or pass --project).")
    ds = f"{args.project}:{args.dataset}"

    if bq("show", "--format=none", ds).returncode != 0:
        made = bq(f"--location={args.location}", "mk", "--dataset",
                  "--description=SF/LA private capital from SEC Form D filings "
                  "(github: formd-tracker). Rebuilt by pipeline/bigquery.py.", ds)
        if made.returncode != 0:
            raise SystemExit(made.stderr or made.stdout)
        print(f"Created dataset {ds}")

    with tempfile.TemporaryDirectory() as tmp, duckdb.connect(str(DB_PATH), read_only=True) as con:
        for name in args.tables:
            desc, query = TABLES[name]
            path = Path(tmp) / f"{name}.parquet"
            # BigQuery arrays can't hold NULL elements; drop them from every list column.
            lists = [c for c, t, *_ in con.execute(f"describe {query}").fetchall() if t.endswith("[]")]
            if lists:
                fixes = ", ".join(f"list_filter({c}, x -> x is not null) as {c}" for c in lists)
                query = f"select * replace ({fixes}) from ({query})"
            con.execute(f"copy ({query}) to '{path}' (format parquet, compression zstd)")
            n = con.execute(f"select count(*) from read_parquet('{path}')").fetchone()[0]
            res = bq(f"--project_id={args.project}", f"--location={args.location}", "load",
                     "--replace", "--source_format=PARQUET", "--parquet_enable_list_inference",
                     f"{ds}.{name}", str(path))
            if res.returncode != 0:
                raise SystemExit(f"bq load {name} failed:\n{res.stderr or res.stdout}")
            bq("update", f"--description={desc}", f"{ds}.{name}")
            print(f"  {ds}.{name}  {n:,} rows")
    print(f"Done. Query it: bq query --use_legacy_sql=false "
          f"'select count(*) from `{args.project}.{args.dataset}.offerings`'")


if __name__ == "__main__":
    main()
