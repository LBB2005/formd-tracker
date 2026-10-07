---
title: New funds
---

```js
import * as Plot from "npm:@observablehq/plot";
import {C, usd, pct, num, rows, key, tile, stackedColumns, lines, yearTicks, metroScope} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const db = DuckDBClient.of({
  offerings: FileAttachment("data/offerings.parquet"),
  fund_families: FileAttachment("data/fund_families.parquet")
});
```

<div class="kicker">Venture funds and SPVs</div>

# Which new venture funds are forming, and who is actually closing capital

<p class="dek">Most "Venture Capital Fund" filings are not new funds. They are SPVs and series vehicles buying into a single company. This page separates blind-pool funds from those vehicles, counts parallel vehicles once, and tracks first-time managers.</p>

<div class="receipt"><span>Source <b>SEC Form D</b></span><span>Data through ${meta.data_through}</span><span>Fund = industry "Pooled Investment Fund", fund type "Venture Capital"</span></div>

```js
const scope = metroScope(meta);
const metroChoice = view(Inputs.select(scope.options, {label: "Where", value: "All US"}));
```

```js
const metroSql = scope.sql(metroChoice);
const METRO_LABEL = scope.labels;
const asOf = meta.data_through;
const qExpr = `strftime(date_trunc('quarter', first_filed), '%Y') || ' Q' || quarter(first_filed)`;
const vehicles = rows(await db.query(`
  select ${qExpr} as q, date_trunc('quarter', first_filed) as qd,
         count(*) filter (where vehicle_type = 'blind_pool')::int as blind_pool,
         count(*) filter (where vehicle_type = 'spv')::int as spv,
         count(*) filter (where vehicle_type = 'unclear')::int as unclear
  from offerings
  where kind = 'fund' and fund_bucket = 'venture' and is_new_in_window and ${metroSql}
    and first_filed < date_trunc('quarter', date '${asOf}')
  group by all order by qd`));
const yearly = rows(await db.query(`
  select year(first_filed)::varchar as y, count(*)::int as n, count(*) filter (where is_emerging)::int as emerging,
         count(*) filter (where fund_number = 1)::int as fund_i,
         quantile_cont(target_amt, 0.25)::double as p25, quantile_cont(target_amt, 0.5)::double as p50,
         quantile_cont(target_amt, 0.75)::double as p75,
         avg((target_amt is null)::int)::double as share_indefinite
  from fund_families
  where fund_bucket = 'venture' and is_new_in_window and ${metroSql}
  group by all order by y`)).map((d) => ({...d, emerging_share: d.emerging / d.n, fund_i_share: d.fund_i / d.n}));
const fundI = rows(await db.query(`
  select o.offering_id, o.cik, o.issuer_name, o.manager_key, o.metro, o.first_sale_date::varchar as first_sale,
         o.first_filed::varchar as filed, o.offering_amt::double as target, o.sold_latest::double as sold,
         o.fill_pct::double as fill, o.investors_latest::int as investors, o.n_filings::int as n_filings,
         date_diff('day', coalesce(o.first_sale_date, o.first_filed), o.last_filed)::int as days,
         o.filing_url
  from offerings o
  where o.kind = 'fund' and o.fund_bucket = 'venture' and o.vehicle_type = 'blind_pool' and o.fund_number = 1
    and o.is_new_in_window and ${metroSql}
  order by o.first_filed desc`));
const sumv = (a, k) => a.reduce((t, d) => t + d[k], 0);
const lastY = yearly.at(-2) ?? yearly.at(-1);
```

<div class="tiles">
  ${tile("SPV share of new VC vehicles", pct(sumv(vehicles, "spv") / (sumv(vehicles, "spv") + sumv(vehicles, "blind_pool") + sumv(vehicles, "unclear"))), "since 2019 · each SPV buys into one company")}
  ${tile(`New VC funds in ${lastY?.y}`, num(lastY?.n), `${pct(lastY?.emerging_share)} emerging (Fund I–III) · ${num(lastY?.fund_i)} Fund I`)}
  ${tile(`Typical fund target, ${lastY?.y}`, `${usd(lastY?.p25, 0)}–${usd(lastY?.p75, 0)}`, `middle half of stated targets · ${pct(lastY?.share_indefinite)} said "Indefinite"`)}
  ${tile("Fund I vehicles tracked", num(fundI.length), `${pct(d3.mean(fundI.filter((d) => d.fill != null), (d) => Math.min(d.fill, 1)))} average share of target sold`)}
</div>

<div class="card">

### How much of the "new VC fund" wave is really SPVs?

<p class="answer">${`Since 2019, ${num(sumv(vehicles, "spv"))} of ${num(sumv(vehicles, "spv") + sumv(vehicles, "blind_pool") + sumv(vehicles, "unclear"))} new venture vehicles ${scope.phrase(metroChoice)} were SPVs or series vehicles. Counting raw filings would overstate new funds by about ${((sumv(vehicles, "spv") + sumv(vehicles, "blind_pool") + sumv(vehicles, "unclear")) / sumv(vehicles, "blind_pool")).toFixed(1)}x.`}</p>

${key([["Blind-pool funds", C.s1], ["SPVs and series vehicles", C.s2], ["Unclear (in review)", C.neutral]])}

```js
display(resize((width) => stackedColumns(vehicles, {x: "q", width, yFormat: (v) => num(v), series: [
  {key: "blind_pool", label: "Blind-pool funds", color: C.s1},
  {key: "spv", label: "SPVs and series vehicles", color: C.s2},
  {key: "unclear", label: "Unclear (in review)", color: C.neutral}
]})))
```

<p class="fineprint">Vehicles, not fund families: a fund with a parallel "-A" vehicle counts twice here and once in the next chart.</p>
</div>

<div class="grid grid-cols-2" style="margin-top:1rem">
<div class="card">

### Are first-time managers still getting funds off the ground?

<p class="answer">${`Emerging managers (Fund I–III) were ${pct(yearly.at(-1)?.emerging_share)} of new VC funds in ${yearly.at(-1)?.y}${yearly.length > 2 ? `, against ${pct(yearly[0].emerging_share)} in ${yearly[0].y}` : ""}.`}</p>

${key([["Emerging (Fund I–III)", C.s1], ["Fund I only", C.s2]])}

```js
display(resize((width) => lines(yearly, {x: "y", width, yFormat: (v) => pct(v), yDomain: [0, 1], series: [
  {key: "emerging_share", label: "Emerging (Fund I–III)", color: C.s1},
  {key: "fund_i_share", label: "Fund I only", color: C.s2}
]})))
```

<p class="fineprint">Unnumbered funds count as emerging when the manager has at most two earlier fund families in the data. The data starts in 2019, so the early years overstate emerging share.</p>
</div>
<div class="card">

### Which new managers are actually closing capital?

<p class="answer">Each dot is a Fund I. Its height is the share of the stated target sold by its latest filing; its position is months since first sale. Dots near the top have closed most of what they set out to raise.</p>

```js
display(resize((width) => Plot.plot({
  width, height: 300, marginLeft: 48,
  style: {fontFamily: "var(--sans)", fontSize: "12px", background: "transparent"},
  x: {label: "Months since first sale →", grid: false},
  y: {label: null, grid: true, domain: [0, 1.25], tickFormat: (v) => pct(v)},
  marks: [
    Plot.ruleY([0], {stroke: "var(--baseline)"}),
    Plot.ruleY([1], {stroke: "var(--ink-muted)", strokeOpacity: 0.6}),
    Plot.dot(fundI.filter((d) => d.fill != null), {
      x: (d) => d.days / 30.44, y: (d) => Math.min(d.fill, 1.25), r: 4, fill: C.s1, fillOpacity: 0.75,
      stroke: C.surface, strokeWidth: 1,
      tip: true, title: (d) => `${d.issuer_name}\nTarget ${usd(d.target)} · sold ${usd(d.sold)} (${pct(d.fill)})\nFirst sale ${d.first_sale ?? d.filed} · ${d.n_filings} filings`
    })
  ]
})))
```

<p class="fineprint">Only funds that state a dollar target are shown. A fund that never files an amendment freezes at its first reported amount, so low dots can mean "stopped reporting" rather than "stopped raising".</p>
</div>
</div>

## Fund I vehicles

```js
const fundSearch = view(Inputs.search(fundI, {placeholder: "Search fund or manager…", columns: ["issuer_name", "manager_key"]}));
```

```js
display(Inputs.table(fundSearch, {
  rows: 20,
  columns: ["issuer_name", "manager_key", "metro", "filed", "target", "sold", "fill", "investors", "filing_url"],
  header: {issuer_name: "Fund", manager_key: "Manager", metro: "Metro", filed: "First filed", target: "Target", sold: "Sold", fill: "Of target", investors: "Investors", filing_url: "Filing"},
  format: {
    issuer_name: (n, i, d) => html`<a href="./company?cik=${d[i].cik}">${n}</a>`,
    manager_key: (m) => html`<a href="./manager?key=${encodeURIComponent(m)}">${m}</a>`,
    metro: (m) => METRO_LABEL[m] ?? "—", target: (v) => (v == null ? "Indefinite" : usd(v)), sold: (v) => usd(v),
    fill: (v) => pct(v), filing_url: (u) => html`<a class="mono" target="_blank" rel="noopener" href="${u}">SEC ↗</a>`
  }
}))
```
