"""Fill the gap since the latest quarterly data set from EDGAR's live filings.

For each quarter after the newest quarterly ZIP, read EDGAR's full-index form.idx
(updated nightly), keep D and D/A filings, fetch each filing's primary_doc.xml, and
write it out as TSVs in the exact layout of the quarterly data sets. load.py then
loads those through the same path, so the SQL layer sees one consistent schema.

When the SEC posts the quarterly ZIP for a quarter, load.py uses the ZIP and
ignores the daily files for that quarter.

Usage:
    uv run python -m pipeline.daily
"""

import argparse
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from pipeline import CACHE_DIR
from pipeline.download import QUARTERLY_DIR
from pipeline.sec_client import fetch_to_cache

DAILY_DIR = CACHE_DIR / "daily"
XML_DIR = DAILY_DIR / "xml"
INDEX_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/form.idx"
INDEX_LINE = re.compile(
    r"^(D|D/A)\s+(.+?)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/data/\d+/([\d-]+)\.txt)\s*$"
)

# Column order of each quarterly TSV.
COLUMNS = {
    "FORMDSUBMISSION": ["ACCESSIONNUMBER", "FILE_NUM", "FILING_DATE", "SIC_CODE", "SCHEMAVERSION",
                        "SUBMISSIONTYPE", "TESTORLIVE", "OVER100PERSONSFLAG", "OVER100ISSUERFLAG"],
    "ISSUERS": ["ACCESSIONNUMBER", "IS_PRIMARYISSUER_FLAG", "ISSUER_SEQ_KEY", "CIK", "ENTITYNAME",
                "STREET1", "STREET2", "CITY", "STATEORCOUNTRY", "STATEORCOUNTRYDESCRIPTION",
                "ZIPCODE", "ISSUERPHONENUMBER", "JURISDICTIONOFINC", "ISSUER_PREVIOUSNAME_1",
                "ISSUER_PREVIOUSNAME_2", "ISSUER_PREVIOUSNAME_3", "EDGAR_PREVIOUSNAME_1",
                "EDGAR_PREVIOUSNAME_2", "EDGAR_PREVIOUSNAME_3", "ENTITYTYPE", "ENTITYTYPEOTHERDESC",
                "YEAROFINC_TIMESPAN_CHOICE", "YEAROFINC_VALUE_ENTERED"],
    "OFFERING": ["ACCESSIONNUMBER", "INDUSTRYGROUPTYPE", "INVESTMENTFUNDTYPE", "IS40ACT",
                 "REVENUERANGE", "AGGREGATENETASSETVALUERANGE", "FEDERALEXEMPTIONS_ITEMS_LIST",
                 "ISAMENDMENT", "PREVIOUSACCESSIONNUMBER", "SALE_DATE", "YETTOOCCUR",
                 "MORETHANONEYEAR", "ISEQUITYTYPE", "ISDEBTTYPE", "ISOPTIONTOACQUIRETYPE",
                 "ISSECURITYTOBEACQUIREDTYPE", "ISPOOLEDINVESTMENTFUNDTYPE", "ISTENANTINCOMMONTYPE",
                 "ISMINERALPROPERTYTYPE", "ISOTHERTYPE", "DESCRIPTIONOFOTHERTYPE",
                 "ISBUSINESSCOMBINATIONTRANS", "BUSCOMBCLARIFICATIONOFRESP",
                 "MINIMUMINVESTMENTACCEPTED", "OVER100RECIPIENTFLAG", "TOTALOFFERINGAMOUNT",
                 "TOTALAMOUNTSOLD", "TOTALREMAINING", "SALESAMTCLARIFICATIONOFRESP",
                 "HASNONACCREDITEDINVESTORS", "NUMBERNONACCREDITEDINVESTORS",
                 "TOTALNUMBERALREADYINVESTED", "SALESCOMM_DOLLARAMOUNT", "SALESCOMM_ISESTIMATE",
                 "FINDERSFEE_DOLLARAMOUNT", "FINDERSFEE_ISESTIMATE", "FINDERFEECLARIFICATIONOFRESP",
                 "GROSSPROCEEDSUSED_DOLLARAMOUNT", "GROSSPROCEEDSUSED_ISESTIMATE",
                 "GROSSPROCEEDSUSED_CLAROFRESP", "AUTHORIZEDREPRESENTATIVE"],
    "RELATEDPERSONS": ["ACCESSIONNUMBER", "RELATEDPERSON_SEQ_KEY", "FIRSTNAME", "MIDDLENAME",
                       "LASTNAME", "STREET1", "STREET2", "CITY", "STATEORCOUNTRY",
                       "STATEORCOUNTRYDESCRIPTION", "ZIPCODE", "RELATIONSHIP_1", "RELATIONSHIP_2",
                       "RELATIONSHIP_3", "RELATIONSHIPCLARIFICATION"],
    "RECIPIENTS": ["ACCESSIONNUMBER", "RECIPIENT_SEQ_KEY", "RECIPIENTNAME", "RECIPIENTCRDNUMBER",
                   "ASSOCIATEDBDNAME", "ASSOCIATEDBDCRDNUMBER", "STREET1", "STREET2", "CITY",
                   "STATEORCOUNTRY", "STATEORCOUNTRYDESCRIPTION", "ZIPCODE", "STATES_OR_VALUE_LIST",
                   "DESCRIPTIONS_LIST", "FOREIGNSOLICITATION"],
    "SIGNATURES": ["ACCESSIONNUMBER", "SIGNATURE_SEQ_KEY", "ISSUERNAME", "SIGNATURENAME",
                   "NAMEOFSIGNER", "SIGNATURETITLE", "SIGNATUREDATE"],
}


def _t(node, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    return (found.text or "").strip() if found is not None else ""


def _values(node, path: str) -> list[str]:
    if node is None:
        return []
    return [(v.text or "").strip() for v in node.findall(path) if (v.text or "").strip()]


def _names(node, path: str) -> list[str]:
    # Names appear as <previousName> or <value>; "None" means there are none.
    names = [n for n in _values(node, f"{path}/previousName") + _values(node, f"{path}/value")
             if n.upper() != "NONE"]
    return (names + ["", "", ""])[:3]


def _address(node) -> list[str]:
    return [_t(node, "street1"), _t(node, "street2"), _t(node, "city"),
            _t(node, "stateOrCountry"), _t(node, "stateOrCountryDescription"), _t(node, "zipCode")]


def _issuer(acc: str, node, primary: bool, seq: int) -> list[str]:
    year = node.find("yearOfInc")
    choice = ""
    if year is not None:
        for tag in ("overFiveYears", "withinFiveYears", "yetToBeFormed"):
            if _t(year, tag).lower() == "true":
                choice = tag
    return [acc, "YES" if primary else "NO", str(seq), _t(node, "cik"), _t(node, "entityName"),
            *_address(node.find("issuerAddress")), _t(node, "issuerPhoneNumber"),
            _t(node, "jurisdictionOfInc"),
            *_names(node, "issuerPreviousNameList"),
            *_names(node, "edgarPreviousNameList"),
            _t(node, "entityType"), _t(node, "entityTypeOtherDesc"), choice, _t(year, "value")]


def parse(xml_bytes: bytes, accession: str, filing_date: str) -> dict[str, list[list[str]]]:
    """One primary_doc.xml -> rows for each of the six quarterly tables."""
    root = ET.fromstring(xml_bytes)
    off = root.find("offeringData")
    fund = off.find("industryGroup/investmentFundInfo")
    filing = off.find("typeOfFiling")
    sec = off.find("typesOfSecuritiesOffered")
    amounts = off.find("offeringSalesAmounts")
    investors = off.find("investors")
    fees = off.find("salesCommissionsFindersFees")
    proceeds = off.find("useOfProceeds")
    combo = off.find("businessCombinationTransaction")
    flag = lambda node, tag: _t(node, tag) or ("false" if node is not None else "")

    rows: dict[str, list[list[str]]] = {k: [] for k in COLUMNS}
    rows["FORMDSUBMISSION"].append([
        accession, "", filing_date, "", _t(root, "schemaVersion"), _t(root, "submissionType"),
        _t(root, "testOrLive"), _t(root, "over100PersonsFlag"), _t(root, "over100IssuerFlag"),
    ])

    rows["ISSUERS"].append(_issuer(accession, root.find("primaryIssuer"), True, 101))
    for i, node in enumerate(root.findall("issuerList/issuer"), start=102):
        rows["ISSUERS"].append(_issuer(accession, node, False, i))

    rows["OFFERING"].append([
        accession,
        _t(off, "industryGroup/industryGroupType"),
        _t(fund, "investmentFundType"),
        _t(fund, "is40Act"),
        _t(off, "issuerSize/revenueRange"),
        _t(off, "issuerSize/aggregateNetAssetValueRange"),
        ", ".join(_values(off, "federalExemptionsExclusions/item")),
        _t(filing, "newOrAmendment/isAmendment"),
        _t(filing, "newOrAmendment/previousAccessionNumber"),
        _t(filing, "dateOfFirstSale/value"),
        _t(filing, "dateOfFirstSale/yetToOccur"),
        _t(off, "durationOfOffering/moreThanOneYear"),
        *(_t(sec, tag) for tag in (
            "isEquityType", "isDebtType", "isOptionToAcquireType", "isSecurityToBeAcquiredType",
            "isPooledInvestmentFundType", "isTenantInCommonType", "isMineralPropertyType",
            "isOtherType")),
        _t(sec, "descriptionOfOtherType"),
        flag(combo, "isBusinessCombinationTransaction"),
        _t(combo, "clarificationOfResponse"),
        _t(off, "minimumInvestmentAccepted"),
        _t(off, "salesCompensationList/over100RecipientFlag"),
        _t(amounts, "totalOfferingAmount"),
        _t(amounts, "totalAmountSold"),
        _t(amounts, "totalRemaining"),
        _t(amounts, "clarificationOfResponse"),
        _t(investors, "hasNonAccreditedInvestors"),
        _t(investors, "numberNonAccreditedInvestors"),
        _t(investors, "totalNumberAlreadyInvested"),
        _t(fees, "salesCommissions/dollarAmount"),
        _t(fees, "salesCommissions/isEstimate"),
        _t(fees, "findersFees/dollarAmount"),
        _t(fees, "findersFees/isEstimate"),
        _t(fees, "clarificationOfResponse"),
        _t(proceeds, "grossProceedsUsed/dollarAmount"),
        _t(proceeds, "grossProceedsUsed/isEstimate"),
        _t(proceeds, "clarificationOfResponse"),
        _t(off, "signatureBlock/authorizedRepresentative"),
    ])

    for i, person in enumerate(root.findall("relatedPersonsList/relatedPersonInfo"), start=101):
        rels = (_values(person, "relatedPersonRelationshipList/relationship") + ["", "", ""])[:3]
        rows["RELATEDPERSONS"].append([
            accession, str(i), _t(person, "relatedPersonName/firstName"),
            _t(person, "relatedPersonName/middleName"), _t(person, "relatedPersonName/lastName"),
            *_address(person.find("relatedPersonAddress")), *rels,
            _t(person, "relationshipClarification"),
        ])

    for i, rec in enumerate(off.findall("salesCompensationList/recipient"), start=101):
        # Either <value>All States</value> or <state>IL</state><description>ILLINOIS</description>.
        states = _values(rec, "statesOfSolicitationList/value") + _values(
            rec, "statesOfSolicitationList/state")
        descriptions = _values(rec, "statesOfSolicitationList/value") + _values(
            rec, "statesOfSolicitationList/description")
        rows["RECIPIENTS"].append([
            accession, str(i), _t(rec, "recipientName"), _t(rec, "recipientCRDNumber"),
            _t(rec, "associatedBDName"), _t(rec, "associatedBDCRDNumber"),
            *_address(rec.find("recipientAddress")),
            ", ".join(sorted(states)), ", ".join(sorted(descriptions)),
            _t(rec, "foreignSolicitation"),
        ])

    for i, sig in enumerate(off.findall("signatureBlock/signature"), start=101):
        rows["SIGNATURES"].append([
            accession, str(i), _t(sig, "issuerName"), _t(sig, "signatureName"),
            _t(sig, "nameOfSigner"), _t(sig, "signatureTitle"), _t(sig, "signatureDate"),
        ])
    return rows


def _clean(value: str) -> str:
    return re.sub(r"[\t\r\n]+", " ", value)


def write_tsvs(rows: dict[str, list[list[str]]], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for table, cols in COLUMNS.items():
        with open(folder / f"{table}.TSV", "w", encoding="utf-8") as fh:
            fh.write("\t".join(cols) + "\n")
            for row in rows[table]:
                fh.write("\t".join(_clean(v) for v in row) + "\n")


def quarters_after_latest_zip(today: date) -> list[tuple[int, int]]:
    zips = sorted(QUARTERLY_DIR.glob("*_d.zip"))
    if not zips:
        raise SystemExit("No quarterly ZIPs cached. Run: uv run python -m pipeline.download")
    year, q = int(zips[-1].name[:4]), int(zips[-1].name[5])
    out = []
    current = (today.year, (today.month - 1) // 3 + 1)
    while (year, q) < current:
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)
        out.append((year, q))
    return out


def read_index(year: int, q: int, today: date) -> list[tuple[str, str, str, str]]:
    """(accession, cik, filing_date, form) for every D and D/A in a quarter's form.idx."""
    dest = DAILY_DIR / "index" / f"{year}q{q}_form.idx"
    current = (year, q) == (today.year, (today.month - 1) // 3 + 1)
    refresh = False
    if dest.exists():
        cached_on = date.fromtimestamp(dest.stat().st_mtime)
        quarter_end = date(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
        # The current quarter's index grows nightly: re-fetch when older than 12 hours.
        # A past quarter cached before it closed is re-fetched once to pick up the tail.
        refresh = (current and time.time() - dest.stat().st_mtime > 12 * 3600) or (
            not current and cached_on < quarter_end)
    fetch_to_cache(INDEX_URL.format(year=year, q=q), dest, refresh=refresh)
    seen: dict[str, tuple[str, str, str, str]] = {}
    for line in dest.read_text(encoding="latin-1").splitlines():
        m = INDEX_LINE.match(line)
        if m:
            form, _, cik, filed, _, accession = m.groups()
            # Co-issuer filings appear once per CIK; keep one row per accession.
            seen.setdefault(accession, (accession, cik, filed, form))
    return list(seen.values())


def _download(entry: tuple[str, str, str, str], folder: Path) -> tuple[Path | None, bool, str]:
    accession, cik, _, form = entry
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{accession.replace('-', '')}/primary_doc.xml")
    try:
        path, downloaded = fetch_to_cache(url, folder / f"{accession}.xml")
        return path, downloaded, ""
    except Exception as exc:  # noqa: BLE001 - record and keep going
        return None, False, f"{accession}\t{form}\t{url}\t{type(exc).__name__}: {exc}"


def fetch_quarter(year: int, q: int, today: date, workers: int = 4) -> None:
    entries = sorted(read_index(year, q, today), key=lambda e: (e[2], e[0]))
    xml_folder = XML_DIR / f"{year}q{q}"
    # Several workers keep requests in flight; sec_client's shared throttle still
    # caps the total at 5 requests/second.
    results: dict[str, tuple[Path | None, bool, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download, e, xml_folder): e[0] for e in entries}
        for n, fut in enumerate(as_completed(futures), 1):
            results[futures[fut]] = fut.result()
            if n % 1000 == 0:
                print(f"    {n:,}/{len(entries):,} fetched", flush=True)
    # A temporary SEC block can fail a run of requests; give each failure one more try.
    retry = [e for e in entries if results[e[0]][0] is None]
    if retry:
        print(f"    retrying {len(retry):,} failed downloads", flush=True)
        for e in retry:
            results[e[0]] = _download(e, xml_folder)

    rows: dict[str, list[list[str]]] = {k: [] for k in COLUMNS}
    failures = [err for _, _, err in results.values() if err]
    for accession, _, filed, form in entries:
        path = results[accession][0]
        if path is None:
            continue
        try:
            parsed = parse(path.read_bytes(), accession, filed)
        except Exception as exc:  # noqa: BLE001 - a malformed XML shouldn't stop the run
            failures.append(f"{accession}\t{form}\t{path}\t{type(exc).__name__}: {exc}")
            continue
        for table, table_rows in parsed.items():
            rows[table].extend(table_rows)
    folder = DAILY_DIR / f"{year}q{q}"
    write_tsvs(rows, folder)
    (folder / "failures.tsv").write_text("\n".join(failures) + ("\n" if failures else ""))
    downloaded = sum(d for _, d, _ in results.values())
    print(f"  {year}q{q}: {len(rows['FORMDSUBMISSION']):,} filings parsed, "
          f"{downloaded:,} downloaded, {len(failures)} failed")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    today = date.today()
    quarters = quarters_after_latest_zip(today)
    if not quarters:
        print("  quarterly data sets are current; nothing to fetch")
    for year, q in quarters:
        fetch_quarter(year, q, today)


if __name__ == "__main__":
    main()
