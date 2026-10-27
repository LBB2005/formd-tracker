# Audit, 2026-10-26: do the SEC links hold up?

Two questions. Does every "SEC ↗" link on the site open the right filing? And is every
filing tied to the right company and the right raise? If either failed, every number built
on top would be suspect.

Method: an independent script fetched the exact URLs the site builds, plus each filing's
`primary_doc.xml` from EDGAR. It parsed that XML with its own code, not
`pipeline/daily.py`, and compared it with the published parquet files. Samples were drawn
by hash, so they are reproducible. Structural checks ran over all 428,901 filings in the
database. Data was as of 2026-10-02; the live site runs through 2026-10-23.

## What was checked, and the result

| Check | Sample | Result |
|---|---|---|
| Filing link (`/Archives/edgar/data/{cik}/{accession}/`) opens | 450 filings: 150 originals, 150 amendments linked in window, 150 linked to pre-2019 raises | 450 of 450 return 200 |
| Filing's CIK is the primary issuer on EDGAR | same 450 | 450 of 450 |
| Form type, amount sold, offering amount, investor count match EDGAR | same 450 | 450 of 450 on every field |
| Raise's "Latest filing" link opens | 200 raises | 200 of 200 |
| Company "All filings on EDGAR" link opens | 25 companies | 25 of 25 |
| Every D and D/A EDGAR lists for a company is in our data, and nothing extra | 300 companies, 498 filings | 498 of 498, none missing, none extra |
| An amendment's pointer reaches a filing of another company | all 158,454 amendments | 0 |
| A raise mixes filings from two CIKs | all raises | 0 |
| An amendment points at a later filing | all amendments | 0 |
| Raises marked "began before 2019" whose original is in fact in our data | 15,293 raises | 0; every root is a pre-2019 accession not in the SEC data sets we load |

Every SEC link on the site comes from one expression: CIK plus the raise's latest
accession, built in `pipeline/export.py`. The company page builds its per-filing links the
same way. So the 450 + 200 sample covers each link type.

## Flaws found

**1. Amendment chains longer than 50 filings were cut into phantom raises.**
The recursive walk in `sql/20_offerings.sql` stopped at depth 50. Four issuers amend one
offering roughly monthly: three Nationwide insurance separate accounts (70–96 D/As since
2019) and "Fund FG-ONJ, a series of Forge Investments". Past depth 50, each amendment's
walk ended on another amendment. 128 became `orphan` raises of their own, and 102 more were
rescued only by the first-sale-date fallback.
*Effect:* 128 phantom raises carrying about $660B of double-counted "sold". The real
total for the four issuers is about $16.4B. None of the four is a startup or venture
fund, so no site page, Finding or new-raise count changed. The public BigQuery
`formd_offerings` table and `n_offerings` did include them.
*Fix:* depth cap raised to 1,000, which still guards against pointer loops. Unit test
with a 96-filing chain.
*Result:* orphans 128 → 0. Fallback links 102 → 0: all 102 now link by their real
pointers. The four issuers have one raise each. New startup raises (82,198) and new VC
fund vehicles (50,594) are unchanged.

**2. Hand-typed previous-accession numbers with dropped zeros.**
Five amendments carry pointers like `001104659-21-005765` or `0001104659-20-94663`. None
matched a real filing, so the walk treated them as pre-2019 raises. Three were split off
from their real raise.
*Fix:* `parse_accession()` in `sql/00_macros.sql` pads each part back to 10-2-6 digits
before the walk. Unit tests cover each pattern seen.
*Result:* all five now reach the right raise (two by pointer, three via their real
pre-2019 roots).

## Not changed

- Filing links open the EDGAR folder listing rather than the rendered form. The folder
  always exists and shows every document. The human-readable Form D is one click further
  in.
- 128 orphan and 102 fallback amendments were also checked live before the fix. Each
  fallback had exactly one candidate original, so none was ambiguous. Each orphan's
  pointer named a real filing by the same issuer, which is how the depth limit came to
  light.
