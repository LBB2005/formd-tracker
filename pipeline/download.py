"""Download the SEC quarterly Form D data sets and the Census ZIP-to-county file.

Usage:
    uv run python -m pipeline.download            # quarters from config start_year onward
    uv run python -m pipeline.download --refresh-latest
"""

import argparse
import re
from urllib.parse import urljoin

import yaml

from pipeline import CACHE_DIR, CONFIG_DIR
from pipeline.sec_client import fetch_to_cache, get

FORM_D_INDEX = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"
ZCTA_COUNTY_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
    "tab20_zcta520_county20_natl.txt"
)
QUARTERLY_DIR = CACHE_DIR / "quarterly"
CENSUS_DIR = CACHE_DIR / "census"

ZIP_LINK = re.compile(r'href="([^"]*?(\d{4})q([1-4])_d\.zip)"', re.IGNORECASE)


def start_year() -> int:
    return int(yaml.safe_load((CONFIG_DIR / "geo.yaml").read_text())["start_year"])


def quarterly_links(min_year: int) -> dict[str, str]:
    """Map 'YYYYqN' -> absolute ZIP URL, read from the SEC index page.

    The SEC moves files between folders (2026q2 sits under a different path
    than earlier quarters), so links are scraped rather than constructed.
    """
    html = get(FORM_D_INDEX).text
    links = {}
    for href, year, quarter in ZIP_LINK.findall(html):
        if int(year) >= min_year:
            links[f"{year}q{quarter}"] = urljoin(FORM_D_INDEX, href)
    if not links:
        raise SystemExit("No quarterly ZIP links found; the SEC page layout may have changed.")
    return dict(sorted(links.items()))


def download_quarterlies(min_year: int, refresh_latest: bool = False) -> list[str]:
    links = quarterly_links(min_year)
    latest = max(links)
    for quarter, url in links.items():
        refresh = refresh_latest and quarter == latest
        _, downloaded = fetch_to_cache(url, QUARTERLY_DIR / f"{quarter}_d.zip", refresh=refresh)
        print(f"  {quarter}: {'downloaded' if downloaded else 'cached'}")
    return list(links)


def download_census() -> None:
    _, downloaded = fetch_to_cache(ZCTA_COUNTY_URL, CENSUS_DIR / "zcta_county_2020.txt")
    print(f"  census ZCTA-county: {'downloaded' if downloaded else 'cached'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=None)
    parser.add_argument(
        "--refresh-latest", action="store_true",
        help="re-download the newest quarter (the SEC occasionally reposts it)",
    )
    args = parser.parse_args(argv)
    print("Form D quarterly data sets:")
    download_quarterlies(args.start_year or start_year(), args.refresh_latest)
    print("Census:")
    download_census()


if __name__ == "__main__":
    main()
