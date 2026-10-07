---
title: Raise velocity
---

```js
import {C, usd, pct, num, rows, key, tile, lines, metroScope} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const db = DuckDBClient.of({offerings: FileAttachment("data/offerings.parquet")});
```

<div class="kicker">Raise velocity · AI vs everyone else</div>

# Do AI companies raise faster?

<p class="dek">Form D gives three clocks: when the first dollar came in (date of first sale), when the issuer told the SEC (filing date), and how the amount sold climbs across amendments. This page compares AI and non-AI startup raises on all three.</p>

<div class="receipt"><span>Source <b>SEC Form D</b></span><span>Startup raises first filed ${meta.data_from.slice(0, 4)}–${meta.data_through.slice(0, 4)}</span><span>Medians, not means: a few raises run for years</span></div>

```js
const scope = metroScope(meta);
const metroChoice = view(Inputs.select(scope.options, {label: "Where", value: "All US"}));
```

```js
const metroSql = scope.sql(metroChoice);
const byYear = rows(await db.query(`
  select year(first_filed)::varchar as y,
    median(days_sale_to_first_filing) filter (where is_ai and days_sale_to_first_filing between 0 and 730)::double as ai_lag,
    median(days_sale_to_first_filing) filter (where not is_ai and days_sale_to_first_filing between 0 and 730)::double as other_lag,
    median(days_open) filter (where is_ai and n_amendments > 0)::double as ai_open,
    median(days_open) filter (where not is_ai and n_amendments > 0)::double as other_open,
    avg((n_amendments > 0)::int) filter (where is_ai)::double as ai_amended,
    avg((n_amendments > 0)::int) filter (where not is_ai)::double as other_amended,
    median(least(fill_pct, 1)) filter (where is_ai and offering_amt > 0)::double as ai_fill,
    median(least(fill_pct, 1)) filter (where not is_ai and offering_amt > 0)::double as other_fill,
    median(sold_latest) filter (where is_ai and sold_latest > 0)::double as ai_size,
    median(sold_latest) filter (where not is_ai and sold_latest > 0)::double as other_size,
    count(*) filter (where is_ai)::int as ai_n, count(*)::int as n
  from offerings
  where is_startup_raise and is_new_in_window and not flag_implausible and ${metroSql}
  group by all order by y`));
const pooled = rows(await db.query(`
  select is_ai,
    median(days_sale_to_first_filing) filter (where days_sale_to_first_filing between 0 and 730)::double as lag,
    avg((days_sale_to_first_filing > 15)::int) filter (where days_sale_to_first_filing >= 0)::double as late,
    median(least(fill_pct, 1)) filter (where offering_amt > 0)::double as fill,
    median(sold_latest) filter (where sold_latest > 0)::double as size, count(*)::int as n
  from offerings where is_startup_raise and is_new_in_window and not flag_implausible and ${metroSql}
    and year(first_filed) >= 2023
  group by is_ai`));
const ai = pooled.find((d) => d.is_ai) ?? {}, other = pooled.find((d) => !d.is_ai) ?? {};
```

<div class="tiles">
  ${tile("Days from first sale to filing, AI", num(ai.lag), `everyone else: ${num(other.lag)} · since 2023`)}
  ${tile("Filed after the 15-day deadline", pct(ai.late), `AI raises · everyone else ${pct(other.late)}`)}
  ${tile("Median raise, AI", usd(ai.size), `everyone else: ${usd(other.size)} · since 2023`)}
  ${tile("Share of target sold, AI", pct(ai.fill), `median at latest filing · everyone else ${pct(other.fill)}`)}
</div>

<div class="grid grid-cols-2">
<div class="card">

### How long after the first dollar do companies tell the SEC?

<p class="answer">The rule is 15 days from first sale. A longer gap usually means a rolling round (often SAFEs) filed once the round was well under way.</p>

${key([["AI companies", C.s1], ["Everyone else", C.neutral]])}

```js
display(resize((width) => lines(byYear, {x: "y", width, yFormat: (v) => `${Math.round(v)}d`, series: [
  {key: "ai_lag", label: "AI companies", color: C.s1}, {key: "other_lag", label: "Everyone else", color: C.neutral}]})))
```
</div>
<div class="card">

### How big is the typical raise?

<p class="answer">Median latest amount sold per raise. The median ignores the mega-rounds that dominate dollar totals.</p>

${key([["AI companies", C.s1], ["Everyone else", C.neutral]])}

```js
display(resize((width) => lines(byYear, {x: "y", width, yFormat: (v) => usd(v), series: [
  {key: "ai_size", label: "AI companies", color: C.s1}, {key: "other_size", label: "Everyone else", color: C.neutral}]})))
```
</div>
<div class="card">

### How full does the round get?

<p class="answer">Median share of the stated target sold by the latest filing (capped at 100%). Raises with an "Indefinite" target are left out.</p>

${key([["AI companies", C.s1], ["Everyone else", C.neutral]])}

```js
display(resize((width) => lines(byYear, {x: "y", width, yFormat: (v) => pct(v), yDomain: [0, 1], series: [
  {key: "ai_fill", label: "AI companies", color: C.s1}, {key: "other_fill", label: "Everyone else", color: C.neutral}]})))
```
</div>
<div class="card">

### How long do amended raises stay open?

<p class="answer">Days from first to last filing, for raises with at least one amendment. Longer means the issuer kept selling (and reporting) for longer.</p>

${key([["AI companies", C.s1], ["Everyone else", C.neutral]])}

```js
display(resize((width) => lines(byYear, {x: "y", width, yFormat: (v) => `${Math.round(v)}d`, series: [
  {key: "ai_open", label: "AI companies", color: C.s1}, {key: "other_open", label: "Everyone else", color: C.neutral}]})))
```
</div>
</div>

```js
display(Inputs.table(byYear.map((d) => ({Year: d.y, "AI raises": d.ai_n, "All raises": d.n,
  "Lag AI (days)": d.ai_lag, "Lag other": d.other_lag, "Median AI $": usd(d.ai_size), "Median other $": usd(d.other_size),
  "Fill AI": pct(d.ai_fill), "Fill other": pct(d.other_fill), "Open AI (days)": d.ai_open, "Open other": d.other_open})), {rows: 10}))
```

<p class="fineprint" style="color:var(--ink-muted);font-size:0.8rem">Lags outside 0–730 days are left out as data-entry errors (e.g. a first-sale date years before the company existed). AI counts are small before 2023, so early-year medians are noisy.</p>
