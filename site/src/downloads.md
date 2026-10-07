---
title: Download the data
---

```js
import {num} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
```

<div class="kicker">Open data</div>

# Download the data

<p class="dek">Everything on this site is rebuilt from public SEC filings by code in the project repository. Take the cleaned tables and check our work, or build on it.</p>

<div class="receipt"><span>Filings ${meta.data_from} → ${meta.data_through}</span><span>${num(meta.n_offerings)} raises · ${num(meta.n_issuers)} issuers (national)</span><span>Built ${meta.built_at.slice(0, 10)}</span></div>

| File | What it is |
| --- | --- |
| <a href="./downloads/formd_offerings.parquet" download>formd_offerings.parquet</a> · <a href="./downloads/formd_offerings.csv.gz" download>.csv.gz</a> | One row per raise, nationally: amounts, dates, exemptions, fund vs company, SPV vs blind pool, manager, AI tier, inferred stage, metro |
| <a href="./downloads/formd_issuers.parquet" download>formd_issuers.parquet</a> · <a href="./downloads/formd_issuers.csv.gz" download>.csv.gz</a> | One row per issuer (CIK): latest address, county, metro, coordinates (SF/LA), former names, AI evidence |

The SF/LA tables that power the dashboard (raises, issuers, filings, fund families, managers, people) are in the site's `data/` folder as Parquet.

## Key columns

| Column | Meaning |
| --- | --- |
| `offering_id` | Accession number of the raise's original Form D (or the earliest one reachable) |
| `is_new_in_window` | The original filing is in our data (2019+). Use it to count new raises |
| `sold_latest` | Latest reported total amount sold. Never summed across amendments |
| `kind` | fund · operating_company · real_estate · other_investing |
| `vehicle_type` | spv · blind_pool · unclear (funds only) |
| `is_startup_raise` | Venture-stage operating company selling equity or a SAFE/convertible |
| `ai_tier` | strong · likely · possible (see Methodology); `is_ai` = strong or likely |
| `inferred_stage` | Seed / Series A / Series B / growth from amount bands. Inferred, not filed |
| `flag_implausible` | Amount at or above $100B, almost always a typo. Kept, flagged |

Please cite as: *Form D Private Capital Tracker, built from SEC Form D filings, data through ${meta.data_through}.* This is not investment advice.
