"""Latitude/longitude for issuers in the tracked metros, for the map.

Street addresses go to the Census batch geocoder (free, no key, 10,000 per request).
Unmatched addresses (PO boxes, suites it can't parse) fall back to the ZIP's centroid
from the Census 2020 ZCTA gazetteer. geocode_method records which was used.

    uv run python -m pipeline.geocode

Results are cached per address, so reruns only send new addresses. Output:
data/enrich/geocodes.parquet, loaded by the build.
"""

import csv
import io
import json
import time
import zipfile
from hashlib import sha1

import duckdb
import requests

from pipeline import CACHE_DIR, DB_PATH
from pipeline.ai_tag import ENRICH_DIR

BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
GAZETTEER_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/"
                 "2020_Gaz_zcta_national.zip")
CACHE = CACHE_DIR / "geocode" / "addresses.json"
BATCH_SIZE = 5000  # under the 10,000 limit; smaller batches fail less often


def address_key(street, city, state, zip5) -> str:
    return sha1(f"{street}|{city}|{state}|{zip5}".upper().encode()).hexdigest()[:16]


def zip_centroids() -> dict[str, tuple[float, float]]:
    path = CACHE_DIR / "census" / "2020_Gaz_zcta_national.zip"
    if not path.exists():
        resp = requests.get(GAZETTEER_URL, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    with zipfile.ZipFile(path) as zf:
        text = zf.read(zf.namelist()[0]).decode("utf-8")
    out = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
        row = {k.strip(): v.strip() for k, v in row.items()}
        out[row["GEOID"]] = (float(row["INTPTLAT"]), float(row["INTPTLONG"]))
    return out


def geocode_batch(rows: list[tuple[str, str, str, str, str]]) -> dict[str, dict] | None:
    """Geocode one batch; None if the Census service keeps failing (retried next run)."""
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    for attempt in range(4):
        try:
            resp = requests.post(BATCH_URL, timeout=900,
                                 data={"benchmark": "Public_AR_Current"},
                                 files={"addressFile": ("addresses.csv", buf.getvalue(), "text/csv")})
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            print(f"  Census geocoder: {type(exc).__name__} (attempt {attempt + 1}/4)", flush=True)
            if attempt == 3:
                return None
            time.sleep(30 * 2 ** attempt)
    out = {}
    for rec in csv.reader(io.StringIO(resp.text)):
        if len(rec) >= 6 and rec[2] == "Match":
            lon, lat = (float(x) for x in rec[5].split(","))
            out[rec[0]] = {"lat": lat, "lon": lon, "matched": rec[4], "match_type": rec[3]}
        elif rec:
            out[rec[0]] = None
    return out


def main() -> None:
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        issuers = con.execute("""
            select cik, coalesce(street1, ''), coalesce(city, ''), coalesce(state, ''),
                   coalesce(zip5, '')
            from issuers where metro is not null
        """).fetchall()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    keyed = {cik: address_key(*addr) for cik, *addr in issuers}
    todo = {}
    for cik, street, city, state, zip5 in issuers:
        k = keyed[cik]
        if k not in cache and street:
            todo[k] = (k, street, city, state, zip5)
    print(f"Issuers in tracked metros: {len(issuers):,}; new addresses to geocode: {len(todo):,}")
    rows = list(todo.values())
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        result = geocode_batch(chunk)
        if result is None:
            print(f"  batch {i // BATCH_SIZE + 1}: skipped; those addresses are retried next run")
            continue
        for k, *_ in chunk:
            cache[k] = result.get(k)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
        print(f"  batch {i // BATCH_SIZE + 1}: {sum(1 for k, *_ in chunk if cache[k]):,} "
              f"of {len(chunk):,} matched", flush=True)

    centroids = zip_centroids()
    out = []
    for cik, street, city, state, zip5 in issuers:
        hit = cache.get(keyed[cik])
        if hit:
            out.append((cik, hit["lat"], hit["lon"], "address", hit["matched"]))
        elif zip5 in centroids:
            lat, lon = centroids[zip5]
            out.append((cik, lat, lon, "zip_centroid", None))
    with duckdb.connect() as con:
        con.execute("create temp table g (cik varchar, lat double, lon double, "
                    "geocode_method varchar, matched_address varchar)")
        con.executemany("insert into g values (?, ?, ?, ?, ?)", out)
        ENRICH_DIR.mkdir(parents=True, exist_ok=True)
        con.execute(f"copy (select * from g order by cik) to '{ENRICH_DIR / 'geocodes.parquet'}' "
                    "(format parquet)")
        print(con.sql("select geocode_method, count(*) n from g group by 1 order by 1"))


if __name__ == "__main__":
    main()
