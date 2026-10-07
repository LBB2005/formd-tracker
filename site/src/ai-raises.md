---
title: AI raises
---

```js
import {usd, num, day, rows, companyLink, secLink, STAGE_LABEL, metroScope} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const db = DuckDBClient.of({
  offerings: FileAttachment("data/offerings.parquet"),
  issuers: FileAttachment("data/issuers.parquet")
});
```

<div class="kicker">Startup raises tagged AI</div>

# Every AI startup raise on Form D

<p class="dek">Each row is one raise: an original Form D plus its amendments. Amounts are the latest reported total sold. The stage is <span class="inferred">inferred</span> from size, because Form D never states a round. The evidence column shows why a company is tagged AI.</p>

<div class="receipt"><span>Source <b>SEC Form D</b></span><span>Data through ${meta.data_through}</span><span>AI signals: ${Object.entries(meta.ai_signals).map(([k, v]) => `${k} ${num(v)}`).join(" · ")}</span></div>

```js
const all = rows(await db.query(`
  select o.cik, o.issuer_name, o.metro, o.first_filed::varchar as filed, year(o.first_filed)::int as year,
         o.sold_latest::double as sold, o.offering_amt::double as target, o.inferred_stage,
         o.security_class, o.ai_tier, o.n_filings::int as n_filings, o.filing_url,
         i.ai_evidence, i.domain, i.yc_batch, i.city
  from offerings o join issuers i using (cik)
  where o.is_startup_raise and o.is_new_in_window and o.ai_tier is not null and o.ai_tier <> 'none'
  order by o.first_filed desc`));
const years = [...new Set(all.map((d) => d.year))].sort();
```

<div class="filters">

```js
const scope = metroScope(meta);
const METRO_LABEL = scope.labels;
const metro = view(Inputs.select(scope.options, {label: "Where", value: "All US"}));
```

```js
const yearFrom = view(Inputs.select(years, {label: "From", value: years.at(-3)}));
```

```js
const tiers = view(Inputs.checkbox(["strong", "likely", "possible"], {label: "AI tier", value: ["strong", "likely"]}));
```

```js
const stages = view(Inputs.checkbox(["seed", "series_a", "series_b", "growth"], {label: "Stage (inferred)", value: ["seed", "series_a", "series_b", "growth"], format: (s) => STAGE_LABEL[s]}));
```

</div>

```js
const filtered = all.filter((d) =>
  (metro === "All US" || (metro === scope.tracked ? d.metro != null : METRO_LABEL[d.metro] === metro)) && d.year >= yearFrom &&
  tiers.includes(d.ai_tier) && stages.includes(d.inferred_stage));
const search = view(Inputs.search(filtered, {placeholder: "Search company, city, evidence…", columns: ["issuer_name", "city", "ai_evidence", "domain"]}));
```

<p>${num(search.length)} raises · ${usd(search.reduce((t, d) => t + (d.sold ?? 0), 0))} reported sold · median ${usd(d3.median(search, (d) => d.sold))}</p>

```js
display(Inputs.table(search, {
  rows: 25,
  columns: ["issuer_name", "metro", "filed", "sold", "inferred_stage", "ai_tier", "ai_evidence", "yc_batch", "filing_url"],
  header: {issuer_name: "Company", metro: "Metro", filed: "First filed", sold: "Sold", inferred_stage: "Stage (inferred)", ai_tier: "AI tier", ai_evidence: "Why tagged AI", yc_batch: "YC", filing_url: "Filing"},
  format: {
    issuer_name: (name, i, data) => companyLink(data[i].cik, name),
    metro: (m) => METRO_LABEL[m] ?? "—",
    sold: (v) => usd(v),
    inferred_stage: (s) => STAGE_LABEL[s] ?? "—",
    ai_evidence: (e) => (e ?? "").replaceAll(" | ", " · ").slice(0, 160),
    filing_url: (u) => secLink(u)
  },
  width: {issuer_name: 200, ai_evidence: 340, filing_url: 70},
  sort: "filed", reverse: true
}))
```

<p class="fineprint" style="color:var(--ink-muted);font-size:0.8rem">AI tiers: <b>strong</b> means two independent sources agree (for example an AI name plus AI-first homepage copy); <b>likely</b> means one strong signal; <b>possible</b> means weak signals only ("Neural", "Intelligence"). Definition: a company whose core product is an AI or ML model, or is built on one. How often the tag is right is measured on the <a href="./methodology">Methodology</a> page.</p>
