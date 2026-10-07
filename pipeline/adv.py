"""Form ADV adviser data: who manages the funds, and how established they are.

Downloads the SEC's latest monthly adviser files (exempt reporting advisers and
registered advisers), keeps advisers that report private funds, and writes
data/enrich/adv_advisers.parquet. sql/55_adv.sql matches them to fund managers.

    uv run python -m pipeline.adv

Fields kept per adviser: CRD number, name, main office, website, registration type,
venture-capital exemption (Form ADV item 2B(1), exempt reporting advisers only), number
of private funds (7.B.(1)), gross assets of private funds, and the date its SEC status
became effective.
"""

import re
import zipfile
from urllib.parse import urljoin

import duckdb

from pipeline import CACHE_DIR
from pipeline.ai_tag import ENRICH_DIR
from pipeline.sec_client import fetch_to_cache, get

INDEX = ("https://www.sec.gov/data-research/sec-markets-data/"
         "information-about-registered-investment-advisers-exempt-reporting-advisers")
ADV_DIR = CACHE_DIR / "adv"


def _release_date(href: str) -> tuple[int, int, int]:
    """ia100226-exempt.zip -> (2026, 10, 2); ia09012026.zip -> (2026, 9, 1)."""
    digits = re.search(r"ia(\d{6,8})", href).group(1)
    if len(digits) == 8:
        m, d, y = int(digits[:2]), int(digits[2:4]), int(digits[4:])
    else:
        m, d, y = int(digits[:2]), int(digits[2:4]), 2000 + int(digits[4:6])
    return (y, m, d)


def latest_files() -> dict[str, str]:
    """{'exempt': url, 'registered': url} for the newest monthly release of each."""
    html = get(INDEX).text
    links = set(re.findall(r'href="([^"]+/ia\d{6,8}[^"/]*\.zip)"', html))
    out = {}
    for kind in ("exempt", "registered"):
        # The folder name itself contains "exempt" and "registered"; judge by filename.
        cands = [h for h in links if ("exempt" in h.rsplit("/", 1)[1].lower()) == (kind == "exempt")]
        if not cands:
            raise SystemExit(f"No {kind} adviser file found on the SEC page.")
        out[kind] = urljoin(INDEX, max(cands, key=_release_date))
    return out


def main() -> None:
    con = duckdb.connect()
    parts = []
    for kind, url in latest_files().items():
        zpath, downloaded = fetch_to_cache(url, ADV_DIR / url.rsplit("/", 1)[1])
        print(f"  {kind}: {zpath.name} ({'downloaded' if downloaded else 'cached'})")
        folder = ADV_DIR / zpath.stem
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(folder)
        csv_path = next(p for p in folder.rglob("*") if p.suffix.lower() == ".csv")
        vc = "\"2B(1)\" = 'Y'" if kind == "exempt" else "null::boolean"
        raum = "null::double" if kind == "exempt" else (
            "try_cast(replace(\"5F(2)(c)\", ',', '') as double)")
        parts.append(f"""
            select "Organization CRD#" as crd, trim("Primary Business Name") as name,
                   trim("Legal Name") as legal_name,
                   upper(trim("Main Office City")) as city, upper(trim("Main Office State")) as state,
                   nullif(trim("Website Address"), '') as website, '{kind}' as registration,
                   {vc} as vc_exemption,
                   try_cast(trim("Count of Private Funds - 7B(1)") as integer) as n_private_funds,
                   try_cast(replace(trim("Total Gross Assets of Private Funds"), ',', '') as double)
                       as private_fund_gross_assets,
                   {raum} as regulatory_aum,
                   try_strptime("SEC Status Effective Date", '%m/%d/%Y')::date as sec_status_date,
                   try_strptime("Latest ADV Filing Date", '%m/%d/%Y')::date as latest_adv_date
            from read_csv('{csv_path}', all_varchar=true, encoding='latin-1', ignore_errors=true)
        """)
    con.execute(f"create table adv as {' union all '.join(parts)}")
    ENRICH_DIR.mkdir(parents=True, exist_ok=True)
    out = ENRICH_DIR / "adv_advisers.parquet"
    con.execute(f"""
        copy (select * from adv where coalesce(n_private_funds, 0) > 0 order by crd)
        to '{out}' (format parquet, compression zstd)
    """)
    print(con.sql(f"""select registration, count(*) advisers, count(*) filter (where vc_exemption) vc_exempt,
                     count(*) filter (where state = 'CA') in_ca from read_parquet('{out}') group by 1"""))


if __name__ == "__main__":
    main()
