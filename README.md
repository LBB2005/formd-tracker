# Form D Private Capital Tracker

An open dataset of every SF Bay Area and Los Angeles private raise filed with the SEC
on Form D since 2019, rebuilt from the SEC's free quarterly data sets, plus a static
dashboard (Observable Framework, DuckDB in the browser) for GitHub Pages. Total cost: $0.

## Run it

```bash
cp .env.example .env          # put your name + email in SEC_USER_AGENT
uv sync
uv run python -m pipeline.build
```

The first build downloads about 100 MB (30 quarterly ZIPs plus the Census ZIP-to-county
file), then fetches every Form D filed since the latest quarterly ZIP from EDGAR (about
16,000 XML files per quarter, roughly an hour). It never exceeds 5 requests/second and
caches everything under `data/cache/`. Later builds fetch only new filings;
`--skip-download` works fully offline.

| Command | What it does |
| --- | --- |
| `uv run python -m pipeline.build` | download → EDGAR filings since the last ZIP → load → `sql/*.sql` → data checks |
| `uv run python -m pipeline.daily` | fetch only the filings since the latest quarterly ZIP |
| `uv run python -m pipeline.build --sql-only` | rerun the SQL layer on already-loaded raw tables |
| `uv run python -m pipeline.checks` | data checks only |
| `uv run python -m pipeline.lookup "Databricks"` | one issuer's offerings and filings, with EDGAR links |
| `uv run python -m pipeline.verify_edgar` | compare the companies in `review/spot_check.csv` with EDGAR's live records |
| `uv run python -m pipeline.review sample` / `score` | 100-row fund/startup/SPV review sample / score your labels |
| `uv run python -m pipeline.review ai-sample` / `ai-score` | 400-company AI-tag sample / precision and recall from your labels |
| `uv run python -m pipeline.ai_tag` | AI evidence: YC directory, company homepages, local-model tie-break (writes `data/enrich/`) |
| `uv run python -m pipeline.geocode` | map coordinates for SF/LA issuers (Census geocoder, ZIP-centroid fallback) |
| `uv run python -m pipeline.adv` | Form ADV adviser files (exempt + registered), for matching fund managers |
| `uv run python -m pipeline.bigquery` | publish 10 tables to BigQuery with the `bq` CLI (`BQ_PROJECT`/`BQ_DATASET` in `.env`) |
| `uv run python -m pipeline.export` | site data (`site/src/data/`) and national downloads |
| `npm --prefix site run dev` / `build` | preview / build the dashboard (`site/dist/`) |
| `uv run pytest` | unit tests: amendment chains, name parsing, classification, AI tag, website layer |

A full local refresh, start to finish:

```bash
uv run python -m pipeline.build          # SEC data -> database
uv run python -m pipeline.ai_tag         # needs Ollama + llama3.2:3b for the tie-break (optional)
uv run python -m pipeline.geocode
uv run python -m pipeline.adv
uv run python -m pipeline.build --sql-only
uv run python -m pipeline.export
npm --prefix site run build
```

Everything lands in one file, `data/formd.duckdb`.

## Tables

| Table | Grain | Notes |
| --- | --- | --- |
| `raw_*` (6) | as shipped by the SEC | all text, plus `source_quarter` |
| `load_manifest` | one per quarter | `quarterly` ZIP or `edgar_daily` XML, and whether the quarter is complete |
| `filings` | one per accession (D or D/A) | typed; `offering_id` and `link_method` say how each filing joined its offering |
| `offerings` | one per raise | latest-filing amounts, first/last filing dates, `days_open`, `fill_pct`, flags, `metro` |
| `filing_increments` | one per filing | change in amount sold and investor count versus the prior filing in the same offering |
| `issuers` | one per CIK | latest address, county, `metro`, former names, EDGAR link |
| `offering_class` | one per offering | `kind`, `vehicle_type`, `fund_bucket`, `fund_number`, `manager_key`, `security_class`, `is_startup_raise`, plus the reason each rule fired |
| `managers` | one per fund manager | vehicle counts by type, highest fund number |
| `fund_families` | one per fund | parallel vehicles ("Fund I", "Fund I-A") collapsed; `is_emerging` |
| `manual_overrides` | one per correction | loaded from `config/manual_overrides.csv` |
| `issuer_people`, `people` | one per person per issuer / per person | names and roles from Form D; repeat founders, startup execs who later run VC funds, platform staff |
| `capital_momentum` | one per venture-stage startup | 0–100 capital score from recency, step-up, pace, fill speed, investors (percentiles within peer group) |
| `ai_signals`, `ai_tags` | evidence rows / one per CIK | every AI signal with its source; the resulting tier |
| `yc_matches`, `company_domains`, `web_signals`, `llm_labels`, `geocodes` | enrichment | loaded from `data/enrich/*.parquet` (committed snapshots of slow third-party lookups) |
| `zip_county`, `metro_counties` | lookups | Census 2020 ZCTA → county; metros from `config/geo.yaml` |

## Methodology decisions (so far)

**Offerings, not filings.** A D/A's previous-accession field usually points at the
*previous amendment*, not the original (86k of 135k resolvable pointers), so we walk the
whole chain back to the root. The original D's accession becomes `offering_id`.

- Chains that leave the 2019+ window keep the out-of-window accession as their id and
  are marked `began_before_window`. Use `is_new_in_window` when counting new raises, or
  2017 funds filing annual amendments will look like 2023 raises.
- External chains that enter the window at different points are merged on
  (CIK, first-sale date).
- A D/A with a broken pointer falls back to an earlier D from the same CIK with the same
  first-sale date, then the same offering amount within 18 months. 87 filings link this way.
- 119 D/As (0.08%) stay unlinked as `orphan` offerings.
- "Raised" is the latest `TOTALAMOUNTSOLD`, never a sum. Decreases are kept and
  flagged as corrections.

**Geography.** The issuer's latest principal-office ZIP maps to a county, using the
county that holds most of the ZIP's land area. Typo'd or PO-box ZIPs fall back to the
county most issuers in the same city resolve to (at least 5 issuers, 80% agreement), then
to the dominant county of the 3-digit ZIP prefix. `geo_method` records which rule placed
each issuer. To add a metro, add its county FIPS codes to `config/geo.yaml`.

**Flags, not deletions.** Offerings at or above $100B (`flag_implausible`, e.g. typed
"$1 trillion sold") and sold-amount decreases are flagged and kept.

**Freshness.** The SEC's quarterly ZIPs lag by a quarter. For any quarter without a ZIP,
the pipeline reads EDGAR's form index, fetches each filing's `primary_doc.xml`, and
writes it in the quarterly layout, so both sources go through the same SQL. When the ZIP
for that quarter is published, it replaces the XML-built rows. On 400 filings present in
both sources, the parsed XML matched the ZIP on every offering field (amounts, dates,
exemptions, previous-accession pointers, security types). One difference favours the
XML: the ZIP's RELATEDPERSONS table copies one person's middle name and second street
line onto everyone else in the same filing. So people are matched on first and last
name only.

**Fund or company.** An offering is a fund if any of these hold: the issuer picked the
industry "Pooled Investment Fund", ticked pooled-fund interests, claimed an Investment
Company Act 3(c) exclusion, or has a vehicle name ("a Series of", "SPV", "Syndicate",
"Co-Invest", "– Series 4"). The name rule catches SPVs filed under other industries, such
as "Anthropic – a Series of Aurum VP Fund LLC", filed as "Other". Of the rest, real
estate industries become `real_estate` and "Investing" becomes `other_investing`, unless
the issuer is a corporation: fintech startups such as FalconX file as "Investing".
Everything else is an `operating_company`. `is_startup_raise` keeps operating companies
selling equity or a SAFE/convertible (SAFEs usually appear as "option" or "other: SAFE").

**SPV or blind pool.** Among funds, vehicle names decide:
- `spv`: SPV/series/syndicate names, or a single deal in parentheses ("OurCrowd
  (Investment in X)").
- `spv` also covers deal-by-deal series: a manager that files 4+ distinct fund numbers
  within 12 months ("HCM G I" to "HCM G XX") isn't raising 20 funds.
- `blind_pool`: a fund number ("Accel XIV", "Partners Four"), or a fund word or vintage
  year ("Fund", "Opportunity", "Seed", "2023").
- `unclear`: everything else; these go to review.

Nationally, 57% of new "Venture Capital Fund" filings since 2019 are named as SPVs or
series vehicles. In SF/LA the share is about 27%, because the big SPV platforms
(AngelList-style series LLCs) file from Delaware addresses and fall outside both metros.
Counting raw VC filings would still overstate new SF/LA funds by about 1.6x.

**Managers and emerging managers.** `manager_key` is the fund name up to its fund number,
minus vehicle words. For example, "Andreessen Horowitz Fund IX – AI Infrastructure, L.P."
becomes "andreessen horowitz". Platform SPVs take the platform as manager ("X, a Series
of Rhone VC" becomes "rhone vc"). Generic stems ("capital partners") are not merged
across CIKs. Parallel vehicles with the same manager and fund number (or vintage year)
form one fund family. A family is **emerging** if it is Fund I–III; for unnumbered funds,
if the manager has at most two earlier families in the data. The Fund I–III cutoff is
our choice, matching common LP usage, and data that starts in 2019 can't see older funds.

**Form ADV.** Fund managers are matched to the SEC's monthly adviser files (exempt
reporting and registered advisers that report private funds) on the same name stem.
Adviser words ("Management", "Advisors", "GP") are dropped, so "Kindred Ventures
Management, LLC" becomes "kindred ventures". Ties go to the adviser with a California
office, then the one with the most private funds, and one-word stems match only California
advisers. Each match adds:
- whether the adviser claims the venture-capital fund exemption (Form ADV 2B(1))
- its private-fund count and their gross assets
- the date it began reporting to the SEC

388 of 2,469 SF/LA venture managers match. Most emerging managers rely on state
exemptions, or file under a name unlike their funds'.

**Hand corrections.** `config/manual_overrides.csv` (`cik, offering_id, field, value,
note`) can set `kind`, `vehicle_type`, `fund_bucket`, `manager_key` or `metro`. Leave
`offering_id` blank to apply a row to every offering of that CIK. Overrides apply last,
and every edited offering lists the fields in `overridden_fields`.

**Determinism.** Every "pick the latest" step breaks ties on accession number, so
identical inputs always produce identical tables.

## BigQuery

`uv run python -m pipeline.bigquery` loads 10 tables into `BQ_PROJECT:BQ_DATASET`
(default dataset `formd`) with the Google Cloud `bq` CLI. The tables are offerings,
issuers, filings, filing_increments, fund_families, managers, people, issuer_people,
capital_momentum and ai_signals.
- Every run replaces every table, so the dataset always mirrors the latest build.
- NULLs are stripped from list columns, because BigQuery arrays can't hold them.
- A query on the BigQuery copy reconciles exactly with DuckDB: 285,868 offerings and
  $26,526,160,695,836 sold.

The current target is project `YOUR_PROJECT`, dataset `formd`. That project
has no billing, so it runs in the free BigQuery sandbox, where tables expire 60 days after
each load. Run the command at least every two months, or turn on the optional step in the
weekly workflow, to keep them live.

```sql
-- example: AI share of SF startup raises by year
select extract(year from first_filed) as yr, count(*) as raises, countif(is_ai) as ai
from `YOUR_PROJECT.formd.offerings`
where is_startup_raise and is_new_in_window and metro = 'sf_bay'
group by yr order by yr
```

## Data checks

Each build fails if:
- a raw filing is lost
- an accession is duplicated
- a filing date fails to parse
- a filing has no offering
- dollar totals differ between the filing and offering layers

It warns on:
- orphan amendments at 1% or more
- offerings spanning several CIKs
- flagged amounts
- quarter-over-quarter swings above 20% (2021 Q1 trips this; it is the 2021 boom)
- California issuers with no county
- unclear SF/LA VC vehicles at 20% or more
- override rows that match no CIK or offering

Classification checks fail the build if any offering lacks a `kind`, or any fund lacks a
`vehicle_type` or `manager_key`.

## Status

- [x] **Week 1:** download, load, filings → offerings, data checks
- [x] Week 1 gate, automated: `pipeline.verify_edgar` compares the spot-check companies with EDGAR
- [x] **Week 2:** EDGAR filings since the latest quarter; fund vs startup, SPV, managers, emerging managers
- [ ] **Week 2 gate (you):** review `review/classification_sample.csv` (100 rows), then `pipeline.review score`
- [x] **Week 3:** AI tagging in four layers (name, YC directory, homepage text, quote-verified model tie-break)
- [ ] **Week 3 gate (you):** label `review/ai_label_sample.csv` (400 rows), then `pipeline.review ai-score`
- [x] **Week 4:** dashboard (`site/`), map, coverage-gap study, downloads, weekly GitHub Actions refresh
- [ ] **Week 4 (you):** write the "so what" lines in your own voice; publish (create the repo, add the
  `SEC_USER_AGENT` secret, set Pages to GitHub Actions)

**Why not Evidence.dev:** the plan named Evidence, but its open-source build is deprecated.
The template now prints "npm run build is deprecated" and points to the hosted Evidence
Studio CLI. Observable Framework does the same job: Markdown pages, SQL over Parquet with
DuckDB in the browser, and a static build for GitHub Pages. It's open source (ISC) and
maintained.

### Week 1 spot check (run 2026-10-02: all match)

`uv run python -m pipeline.verify_edgar` checks every company in `review/spot_check.csv`.
- **With a CIK:** it confirms that every D and D/A EDGAR lists in our date range is in the
  database, and nothing else.
- **Without a CIK:** it searches EDGAR's full registrant list (`cik-lookup-data.txt`) for
  names starting with the company name, separates SPV/fund vehicles from possible
  operating entities, and checks each candidate's whole EDGAR history for a Form D. That
  distinguishes "never filed" from "our pipeline dropped it". Databricks and Harvey AI
  serve as positive controls.

| Company | EDGAR vs ours |
| --- | --- |
| Databricks | 15 = 15 filings → 14 offerings |
| Figma | 5 = 5 → 5 |
| Notion Labs | 3 = 3 → 3 |
| Harvey AI | 6 = 6 → 6 |
| Vercel | 1 = 1 → 1 |
| Ramp Business Corp | 12 = 12 → 12 (NY address, outside both metros) |
| Perplexity AI | 1 = 1 → 1 ($28.8M; it has announced far more) |
| Replit | 1 = 1 → 1 ($0.3M; it has announced far more) |
| Anthropic | no operating entity on EDGAR; 34 SPV/fund vehicles named after it |
| OpenAI | no operating entity on EDGAR; 23 vehicles |
| Anduril Industries | no operating entity on EDGAR; 2 vehicles |
| Scale AI | no registrant of that name on EDGAR at all |

This is the start of the "coverage gap" finding. The best-known AI companies don't file
Form D under their own names, but dozens of SPVs buying their shares do. The pipeline
hasn't lost anything; the gap is in the source.

## AI tag (Week 3)

Definition, written before labelling: *an AI company is one whose core product is an AI
or ML model, or is built on one. Mentioning AI features does not qualify.*

`sql/40_ai_tag.sql` turns evidence rows (`ai_signals`) into a tier:
- `strong`: strong signals from two independent sources
- `likely`: one strong signal
- `possible`: weak signals only

**Layer 1, names.** Strong name signals:
- a whole-word "AI" or "A.I."
- a ".ai" name ("Nowcasting.ai")
- a CamelCase suffix ("RelationalAI"); a lowercase "ai" ending is ignored, which keeps
  Ikigai and Elevai out
- the phrases artificial intelligence, machine/deep learning, LLM, GPT, agentic

Weak signals are "neural", "cognit-" and "intelligence". Former names count, which
catches renames to and from AI names.

**Layer 2, Y Combinator directory** (public yc-oss mirror). A company is matched on a
unique normalised name, then on a unique core name. AI-category tags are a strong signal,
and the YC website gives a verified domain.

**Layer 3, homepages.** The universe is about 7,100 venture-stage SF/LA operating
companies, excluding restaurants, energy, travel and similar industries. The domain comes
from YC, or is guessed from the name (`slug.com/.ai/.io/.co`, `get…`, `try…`, `…hq`) and
accepted only if the company's distinctive name token appears in the page title,
description or headings, and the page isn't parked. Each page gets one polite request,
with robots.txt respected and a cache of 90 days. Scoring:
- **strong:** AI terms in the hero (title, meta description, top headings), six or more
  in the body, or a `.ai` domain known to be the company's own (from YC, or the name
  itself says AI).
- **weak:** body mentions only, or a guessed `.ai` domain.

**Layer 4, model tie-break**, for weak-only cases. A local `llama3.2:3b` (Ollama, $0)
answers yes/no/unclear and must quote the homepage phrase that shows the core product is
AI. A "yes" counts only if that quote appears verbatim in the text sent and contains an
AI term. Without that check, the 3B model said "yes" to solar electronics, display optics
and a breathing-test device; with it, those become "no" and real AI companies still pass.
Earlier answers are reused from `data/enrich/llm_labels.parquet`, so CI runs without a
model keep them.

An `ai_tier` row in `manual_overrides.csv` wins over every rule
(`strong | likely | possible | none`). Funds get `ai_theme` instead of `is_ai`, as in
"Benchmark AI Infrastructure Fund". Precision and recall come from your 400-row labelled
sample (`pipeline.review ai-sample`); the Methodology page shows them once labelled.

## Analysis caveats found so far

- **The AI tag looks backwards from today.** Homepage, YC and model evidence describe a
  company as it is now, so a company that pivoted to AI in 2024 also counts as AI for its
  2019 raise. `ai_name_at_filing` is the time-correct alternative: AI in the issuer name
  as filed on that raise. In SF, startup raises by AI-named companies went from 1.2% (2019)
  to 10.4% (2026). By current evidence the share went from 18.6% to 36.5%. Show both; the
  truth sits between them.

- **One issuer drives AI dollars.** xAI's four filings ($33B since 2024) account for most
  SF AI dollars. AI's dollar share is 39% in 2024 and 65% in 2026 with xAI, and in the
  single digits without it. Every AI-share chart should show both views, plus counts
  and medians.
- **Fund targets cluster on round numbers.** $50M is the most common VC fund target, and
  the median lands on it every year. A third of new funds state "Indefinite". Report
  quartiles and the share left indefinite. Parallel vehicles often repeat the whole fund's
  target, so a fund family's target is its largest vehicle's target, not the sum.
- **Emerging-manager counts are biased upward early on.** For unnumbered funds the rule
  counts earlier fund families in the data, and the data starts in 2019.
