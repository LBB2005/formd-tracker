---
title: Methodology
---

```js
import {num, pct} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const reviews = FileAttachment("data/reviews.json").json();
```

```js
// Hand-review scores, computed from the labelled CSVs in review/ (blank = rule was right).
const yes = (v) => ["y", "yes"].includes(String(v ?? "").trim().toLowerCase());
const cls = (reviews.classification_sample ?? []).filter((r) => yes(r.reviewed));
const clsKind = cls.filter((r) => !r.true_kind?.trim() || r.true_kind.trim() === r.kind).length;
const clsFunds = cls.filter((r) => (r.true_kind?.trim() || r.kind) === "fund");
const clsVeh = clsFunds.filter((r) => !r.true_vehicle_type?.trim() || r.true_vehicle_type.trim() === r.vehicle_type).length;
const ai = (reviews.ai_label_sample ?? []).filter((r) => ["ai", "not_ai"].includes(String(r.human_label ?? "").trim()));
const tp = ai.filter((r) => r.predicted === "ai" && r.human_label.trim() === "ai").length;
const fp = ai.filter((r) => r.predicted === "ai" && r.human_label.trim() === "not_ai").length;
const fn = ai.filter((r) => r.predicted !== "ai" && r.human_label.trim() === "ai").length;
// The sample takes equal numbers of tagged and untagged companies, but untagged ones are far more
// numerous, so recall is weighted by the size of each half (meta.ai_sample_pool).
const nTagged = tp + fp, nUntagged = ai.length - nTagged, pool = meta.ai_sample_pool ?? {};
const estTp = nTagged && pool.ai ? (tp / nTagged) * pool.ai : null;
const estFn = nUntagged && pool.not_ai ? (fn / nUntagged) * pool.not_ai : null;
const recallW = estTp != null && estFn != null ? estTp / (estTp + estFn) : null;
const shareTrue = recallW != null ? (estTp + estFn) / (pool.ai + pool.not_ai) : null;
const shareTagged = pool.ai ? pool.ai / (pool.ai + (pool.not_ai ?? 0)) : null;
```

<div class="kicker">Methodology and limitations</div>

# How the numbers are made, and where they are blind

<div class="receipt"><span>Source <b>SEC Form D</b></span><span>Filings ${meta.data_from} → ${meta.data_through}</span><span>${num(meta.n_filings)} filings · ${num(meta.n_offerings)} raises · ${num(meta.n_issuers)} issuers</span><span>Built ${meta.built_at}</span></div>

## Source

All data comes from Form D notices filed with the SEC: the SEC's quarterly Form D data sets for 2019 Q1 to the latest published quarter, plus every D and D/A filed since then, parsed from EDGAR (each filing's `primary_doc.xml`, located through EDGAR's form index). When the SEC publishes the quarterly file for a quarter, it replaces the EDGAR-parsed rows. The pipeline stays under the SEC's fair-access limits (5 requests per second, a User-Agent with contact details) and caches every file.

```js
display(Inputs.table(meta.sources.map(([q, s, c]) => ({Quarter: q, Source: s === "quarterly" ? "SEC quarterly data set" : "Parsed from EDGAR filings", Complete: c ? "yes" : "in progress"})), {rows: 6, sort: "Quarter", reverse: true}))
```

## Unit of analysis: a raise, not a filing

A "raise" is one Form D offering: the original filing plus all its amendments. Amendments usually point at the previous amendment rather than the original, so each chain is walked back to its root. Raises whose original filing predates 2019 are marked as such and left out of "new raise" counts. Where the pointer is missing, the amendment is matched to an earlier filing from the same issuer with the same first-sale date, then the same offering amount within 18 months. Only 0.08% of amendments stay unlinked. "Amount raised" is the latest reported total sold, never a sum across filings. Decreases are kept and flagged as corrections.

## Startups vs funds

A filing is a **fund** if the issuer picked "Pooled Investment Fund", ticked pooled-fund interests, claimed an Investment Company Act 3(c) exclusion, or has a vehicle name ("a Series of", "SPV", "Syndicate", "Co-Invest"). Funds are then split by name:
- **SPV**: series, syndicate or single-deal names, plus managers filing four or more numbered vehicles within 12 months (a deal-by-deal series, not 20 funds), plus three or more vehicles whose names differ only by a short code ("AL-D" / "CO-B" / "GW-E Ribbit Opportunity V", "FundersClub 6XV"), one per portfolio company.
- **Blind-pool fund**: a fund number or a fund word, an AngelList rolling fund's quarterly class ("Ravikant Capital, LP - G2"), or a limited partnership named only like a manager ("Indus Valley Capital, LP").
- **Unclear**: everything else, left for review.

Parallel vehicles ("Fund I" and "Fund I-A") are counted once. An **emerging manager** is one raising Fund I–III (our cut-off).

A **startup raise** is an operating company selling equity or a SAFE/convertible. Established companies are excluded: a CIK below 1,400,000 (EDGAR registration before about 2007) combined with incorporation more than five years before filing.

```js
display(html`<table><thead><tr><th>Hand check of these calls</th><th>Result</th></tr></thead><tbody>
  <tr><td>Fund vs company (${num(cls.length)} reviewed rows)</td><td>${cls.length ? pct(clsKind / cls.length) + " correct" : "pending: review/classification_sample.csv"}</td></tr>
  <tr><td>SPV vs blind-pool, among funds (${num(clsFunds.length)})</td><td>${clsFunds.length ? pct(clsVeh / clsFunds.length) + " correct" : "pending"}</td></tr>
</tbody></table>`)
```

**Fund managers and Form ADV.** A manager's vehicles are grouped by name stem: the fund name up to its fund number, with platform SPVs grouped under the platform. Managers are then matched by name to the SEC's monthly Form ADV adviser files, which add whether the adviser claims the venture-capital fund exemption, how many private funds it reports, their gross assets, and when it began reporting to the SEC.

## AI tag

**Definition (written before labelling):** an AI company is one whose core product is an AI or ML model, or is built on one. Mentioning AI features does not qualify.

How the hand labels applied it to hard cases:
- **AI agents** count when the agent is the product (an AI sales rep, an agent runtime), not when an assistant is one feature of other software (a CRM with an "AI order taker").
- **Consultancies and agencies** that build on other companies' models do not count; firms whose deliverable is a model they train themselves do.
- **Hardware** counts only when it exists to run AI (AI chips), not when AI is one customer use (lidar sensors).
- **Teaching or investing in AI** (online courses, AI-themed VC funds) does not make a company an AI company.
- **The filer is judged, not the website.** Where the guessed homepage belonged to a different company with the same name, the label describes the filer.

Form D has no AI field, so evidence comes from four independent sources:
1. **The company name**, including former names ("AI", ".ai", "Artificial Intelligence", "LLM"…; "Neural" and "Intelligence" count only as weak signals).
2. **Y Combinator's directory**, where a matched company is tagged with AI categories.
3. **The company's homepage**, fetched once, politely, with robots.txt respected. AI terms in the title, description or headline count as strong; mentions only in body text count as weak.
4. **A local language model's yes/no/unclear judgment**, used only for ambiguous homepages.

The tier follows from the evidence:
- **strong**: two sources agree.
- **likely**: one strong signal.
- **possible**: weak signals only.

Charts count strong and likely. Every company carries its evidence trail.

```js
display(html`<table><thead><tr><th>AI tag accuracy on a hand-labelled sample</th><th>Result</th></tr></thead><tbody>
  <tr><td>Labelled companies</td><td>${ai.length ? num(ai.length) : "pending: review/ai_label_sample.csv"}</td></tr>
  <tr><td>Precision: of companies tagged AI, share that really are</td><td>${tp + fp ? pct(tp / (tp + fp)) : "pending"}</td></tr>
  <tr><td>Recall: of real AI companies, share the tag finds</td><td>${recallW != null ? pct(recallW) + ` (weighted; ${pct(tp / (tp + fn))} unweighted)` : tp + fn ? pct(tp / (tp + fn)) + " (unweighted)" : "pending"}</td></tr>
  <tr><td>Share of SF/LA venture-stage startups that are AI</td><td>${shareTrue != null ? `about ${pct(shareTrue)} estimated, against ${pct(shareTagged)} tagged` : "pending"}</td></tr>
</tbody></table>`)
```

The sample took equal numbers of tagged and untagged companies, so raw recall overstates how many AI companies the tag finds: untagged companies are about four times as numerous, and the few AI companies among them add up. The weighted figures scale each half by its real size. The tag therefore **undercounts AI**: treat AI shares in the charts as a floor.

**The tag looks backwards from today.** Homepage, YC and model evidence describe a company as it is now, so a company that pivoted to AI in 2024 also counts as AI for its 2019 raise, which inflates early years. The Overview therefore offers a strict, time-correct alternative: *AI in the company's name as filed on that raise*. It misses most AI companies but can't leak. Treat the two as bounds.

Evidence collected in this build: ${Object.entries(meta.ai_signals).map(([k, v]) => `${k}: ${num(v)} companies`).join(" · ")}.

## Geography

Issuers are placed by the principal business address on their latest filing. The ZIP maps to a county using the Census 2020 ZIP-to-county file (the county with most of the ZIP's land). Mistyped and PO-box ZIPs fall back to the county most issuers in the same city use, then to the 3-digit ZIP prefix. **SF Bay Area** = San Francisco, San Mateo, Santa Clara and Alameda counties. **Los Angeles** = Los Angeles and Orange counties. Map coordinates come from the Census geocoder (${num(meta.geocode.address ?? 0)} by street address, ${num(meta.geocode.zip_centroid ?? 0)} at the ZIP's centre).

## What this data misses

Research on about 40,000 VC rounds found most were not accompanied by a Form D, with early-stage tech firms the least likely to file. Many seed rounds (often SAFEs) are filed late or not at all. Amounts go stale when issuers stop amending. Form D never reports valuation, investor names or round labels: any stage shown here is **inferred from amount** (under $3M seed, $3–15M Series A, $15–50M Series B, $50M+ growth). Of 25 well-known SF AI companies, more than half never filed a Form D under their own name (see [Coverage gap](./coverage)). **Treat totals as a floor, and trends as more reliable than levels.**

## Choices that are ours, not standards

- The 2019 start date.
- The county lists.
- Fund I–III as "emerging".
- The CIK-age test for established companies.
- The four stage bands.
- The 50-issuer cut-off for platform staff.
- The 30-day merge window for raise pace.

Each is one line of SQL or config in the project repository and is easy to change and rerun.

## Capital momentum score

For each venture-stage startup, five inputs are scored as percentiles within the peer group (AI or not × metro × size band), then averaged to give 0–100:
- **recency**: days since the latest filing
- **step-up**: latest raise vs the previous one
- **pace**: months between raises, with gaps under 30 days ignored
- **fill speed**: share of target sold within 90 days
- **investor count** on the latest raise

Confidence is "low" with one filing, because a missing Form D is not missing momentum.
