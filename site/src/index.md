---
title: Overview
toc: false
---

```js
import {C, usd, pct, num, rows, key, tile, stackedColumns, lines, metroScope} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const db = DuckDBClient.of({
  offerings: FileAttachment("data/offerings.parquet"),
  fund_families: FileAttachment("data/fund_families.parquet")
});
```

<div class="kicker">Form D private capital tracker · United States, with 10 metros up close</div>

# Who is raising private money, where, and how much of it is AI

<p class="dek">Every US startup raise and venture fund filed with the SEC on Form D since 2019, with ten startup hubs tracked up close. Amendments are collapsed into single raises, startup rounds are separated from new venture funds, and AI companies are tagged with evidence. The page also states plainly where the public data is blind.</p>

<div class="receipt"><span>Source <b>SEC Form D</b></span><span>Filings ${meta.data_from} → ${meta.data_through}</span><span>${num(meta.n_filings)} filings · ${num(meta.n_offerings)} raises</span><span>Built ${meta.built_at.slice(0, 10)}</span></div>

```js
const scope = metroScope(meta);
const metroChoice = view(Inputs.select(scope.options, {label: "Where", value: "All US"}));
const excludeTop = view(Inputs.toggle({label: "Exclude the largest AI issuer", value: false}));
const aiDef = view(Inputs.radio(["Current evidence", "AI in the name when it raised"], {label: "AI tag", value: "Current evidence"}));
```

```js
// Current evidence (homepage, YC, model) describes companies today and leaks backwards in
// time; the name-at-filing definition is strict and time-correct but misses most AI firms.
const aiCol = aiDef === "Current evidence" ? "is_ai" : "ai_name_at_filing";
const metroSql = scope.sql(metroChoice);
const aiCoverage = scope.coverage(metroChoice);
const asOf = meta.data_through;
// The largest AI issuer by dollars in this selection (xAI for SF), for the exclusion toggle.
const top = rows(await db.query(`
  select cik, any_value(issuer_name) as name, sum(sold_latest) as usd
  from offerings where is_startup_raise and is_new_in_window and ${aiCol} and not flag_implausible and ${metroSql}
  group by cik order by usd desc limit 1`))[0];
const exclSql = excludeTop && top ? `and cik <> '${top.cik}'` : "";
const quarterly = rows(await db.query(`
  select strftime(date_trunc('quarter', first_filed), '%Y') || ' Q' || quarter(first_filed) as q,
         date_trunc('quarter', first_filed) as qd,
         sum(sold_latest) filter (where ${aiCol})::double as ai_usd,
         sum(sold_latest) filter (where not ${aiCol})::double as other_usd,
         count(*) filter (where ${aiCol})::int as ai_n,
         count(*)::int as n
  from offerings
  where is_startup_raise and is_new_in_window and not flag_implausible and ${metroSql} ${exclSql}
    and first_filed < date_trunc('quarter', date '${asOf}')
  group by all order by qd`)).map((d) => ({...d,
    share_usd: (d.ai_usd ?? 0) / ((d.ai_usd ?? 0) + (d.other_usd ?? 0) || 1),
    share_n: d.ai_n / (d.n || 1)}));
const year = Number(asOf.slice(0, 4));
const ytd = rows(await db.query(`
  select sum(sold_latest) filter (where ${aiCol})::double as ai_usd, sum(sold_latest)::double as all_usd,
         count(*) filter (where ${aiCol})::int as ai_n, count(*)::int as n,
         median(sold_latest) filter (where ${aiCol})::double as ai_median
  from offerings
  where is_startup_raise and is_new_in_window and not flag_implausible and ${metroSql} ${exclSql}
    and year(first_filed) = ${year}`))[0];
const funds = rows(await db.query(`
  select strftime(date_trunc('quarter', first_filed), '%Y') || ' Q' || quarter(first_filed) as q,
         date_trunc('quarter', first_filed) as qd,
         count(*) filter (where fund_number = 1)::int as fund_i,
         count(*) filter (where fund_number >= 2)::int as fund_ii,
         count(*) filter (where fund_number is null)::int as unnumbered,
         count(*) filter (where is_emerging)::int as emerging, count(*)::int as n
  from fund_families
  where fund_bucket = 'venture' and is_new_in_window and ${metroSql}
    and first_filed < date_trunc('quarter', date '${asOf}')
  group by all order by qd`));
const last4 = funds.slice(-4);
const prev4 = funds.slice(-8, -4);
const sum = (a, k) => a.reduce((t, d) => t + d[k], 0);
```

${aiCoverage != null && aiCoverage < 0.95 ? html`<p class="notice">Homepage AI checks cover ${pct(aiCoverage)} of startups ${scope.phrase(metroChoice)} so far; the rest are tagged from their names only, so AI shares here are a floor until the nationwide run finishes.</p>` : ""}

<div class="tiles">
  ${tile(`AI startup dollars, ${year} to date`, usd(ytd.ai_usd), `${pct(ytd.ai_usd / ytd.all_usd)} of ${usd(ytd.all_usd)} raised by startups${excludeTop && top ? ` · excl. ${top.name}` : ""}`)}
  ${tile(`AI raises, ${year} to date`, num(ytd.ai_n), `of ${num(ytd.n)} startup raises · median ${usd(ytd.ai_median)}`)}
  ${tile("New VC funds, last 4 quarters", num(sum(last4, "n")), `${pct(sum(last4, "emerging") / sum(last4, "n"))} emerging · prior 4 quarters: ${num(sum(prev4, "n"))}`)}
  ${tile("Largest AI issuer", top ? top.name : "—", top ? `${usd(top.usd)} across its Form D raises` : "")}
</div>

<div class="grid grid-cols-2">
<div class="card">

### How fast is AI's share of startup money growing?

<p class="answer">${(() => {
  const lastYear = quarterly.filter((d) => d.q.startsWith(String(year - 1)));
  const ai = sum(lastYear, "ai_usd"), all = ai + sum(lastYear, "other_usd");
  return `In ${year - 1}, AI companies took ${pct(ai / all)} of startup dollars ${scope.phrase(metroChoice)}${excludeTop && top ? ` excluding ${top.name}` : ""}, and ${pct(sum(lastYear, "ai_n") / sum(lastYear, "n"))} of raises by count. ${!excludeTop && top ? `One company, ${top.name}, accounts for ${usd(top.usd)}; toggle it off to see the rest of the market.` : ""}`;
})()}</p>

${key([["AI companies", C.s1], ["Everyone else", C.neutral]])}

```js
display(resize((width) => stackedColumns(quarterly, {x: "q", width, series: [
  {key: "ai_usd", label: "AI companies", color: C.s1},
  {key: "other_usd", label: "Everyone else", color: C.neutral}
]})))
```

<p class="fineprint">Dollars are the latest reported "total amount sold" of raises first filed in each quarter. Form D misses many rounds, so treat levels as a floor (see Coverage gap).</p>
</div>
<div class="card">

### AI share by dollars and by number of raises

<p class="answer">Dollar share swings with a few mega-rounds; count share is the steadier read of how many companies are AI.</p>

${key([["Share of dollars", C.s1], ["Share of raises", C.s2]])}

```js
display(resize((width) => lines(quarterly, {x: "q", width, yFormat: (v) => pct(v), series: [
  {key: "share_usd", label: "Share of dollars", color: C.s1},
  {key: "share_n", label: "Share of raises", color: C.s2}
]})))
```

<p class="fineprint">"Current evidence" = AI tier strong or likely (AI name, YC AI tags, AI-first homepage copy, or a quote-verified model check). It describes companies as they are today, so a company that later pivoted to AI also counts for its earlier raises, which inflates early years. "AI in the name when it raised" is strict and time-correct but misses most AI companies. The truth sits between the two lines.</p>
</div>
</div>

<div class="card" style="margin-top:1rem">

### Is new venture-fund formation recovering?

<p class="answer">${`In the last four complete quarters, ${num(sum(last4, "n"))} new VC funds were filed ${scope.phrase(metroChoice)}, versus ${num(sum(prev4, "n"))} in the four before; ${num(sum(last4, "fund_i"))} were a manager's Fund I.`}</p>

${key([["Fund I", C.s1], ["Fund II and later", C.s2], ["Unnumbered", C.neutral]])}

```js
display(resize((width) => stackedColumns(funds, {x: "q", width, yFormat: (v) => num(v), height: 260, series: [
  {key: "fund_i", label: "Fund I", color: C.s1},
  {key: "fund_ii", label: "Fund II and later", color: C.s2},
  {key: "unnumbered", label: "Unnumbered", color: C.neutral}
]})))
```

<p class="fineprint">Blind-pool venture funds only: SPVs, series vehicles and deal-by-deal series are excluded. Parallel vehicles ("Fund I" and "Fund I-A") count once. More on <a href="./funds">New funds</a>.</p>
</div>

```js
const tableView = view(Inputs.toggle({label: "Show the numbers behind the charts", value: false}));
```

```js
tableView ? display(Inputs.table(quarterly.map((d) => ({Quarter: d.q, "AI $": usd(d.ai_usd), "Other $": usd(d.other_usd), "AI raises": d.ai_n, "All raises": d.n, "AI share $": pct(d.share_usd, 1), "AI share #": pct(d.share_n, 1)})), {rows: 40})) : null
```
