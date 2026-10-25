---
title: Findings
toc: false
---

```js
import {pct, num, rows} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const reviews = FileAttachment("data/reviews.json").json();
// Small slices of the files the linked pages read, with the same definitions.
const db = DuckDBClient.of({
  startup_raises: FileAttachment("data/findings_sf_raises.parquet"),
  fund_vehicles: FileAttachment("data/fund_vehicles.parquet"),
  fund_families: FileAttachment("data/fund_families.parquet")
});
```

```js
// Words for a share when it is close to a simple fraction, otherwise a percentage.
// Returns the phrase that goes before a plural noun: "three in four", "two-thirds of", "71% of".
function about(x) {
  const words = [[1 / 4, "one in four"], [1 / 3, "a third of"], [1 / 2, "half of"], [2 / 3, "two-thirds of"], [3 / 4, "three in four"], [4 / 5, "four in five"]];
  const hit = words.find(([v]) => Math.abs(x - v) < 0.02);
  return hit ? hit[1] : `${pct(x)} of`;
}
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const listing = (a) => (a.length < 2 ? a.join("") : `${a.slice(0, -1).join(", ")} and ${a.at(-1)}`);
// "Databricks, Inc." -> "Databricks"; "Harvey AI Corp" -> "Harvey AI".
const shortName = (n) => (n ?? "").replace(/[,.]?\s+(inc|corp|corporation|co|llc|ltd|plc)\.?$/i, "").trim();
const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
const asOf = meta.data_through;
const year = Number(asOf.slice(0, 4));
const fromYear = Number(meta.data_from.slice(0, 4));
const yearsSpan = Math.floor((new Date(asOf) - new Date(meta.data_from)) / (365.25 * 864e5));
const monthYear = new Date(asOf).toLocaleString("en-US", {month: "long", year: "numeric", timeZone: "UTC"});
```

```js
// 1. Coverage gap: the checked SF AI companies, without the non-AI controls (as on Coverage gap).
const controls = new Set(["Figma", "Notion Labs", "Vercel", "Ramp Business Corp"]);
const checked = (reviews.coverage_results ?? []).filter((d) => !controls.has(d.company));
const absent = checked.filter((d) => d.status.startsWith("no Form D"))
  .sort((a, b) => Number(b.vehicles_named_after || 0) - Number(a.vehicles_named_after || 0));
const absentShare = absent.length / checked.length;
const best = absent.slice(0, 2);
const bestVehicles = best.reduce((t, d) => t + Number(d.vehicles_named_after || 0), 0);
```

```js
// 2. AI share of SF startup dollars this year to date (startup raises as on Overview).
const raiseSql = `is_startup_raise and is_new_in_window and not flag_implausible and metro = 'sf_bay' and year(first_filed) = ${year}`;
const sf = rows(await db.query(`
  select sum(sold_latest) filter (where is_ai)::double as ai_usd, sum(sold_latest)::double as all_usd
  from startup_raises where ${raiseSql}`))[0];
const aiTop = rows(await db.query(`
  select cik, any_value(issuer_name) as name, sum(sold_latest)::double as usd
  from startup_raises where ${raiseSql} and is_ai
  group by cik order by usd desc limit 4`));
const top = aiTop[0];
const aiShare = sf.ai_usd / sf.all_usd;
const topShare = top ? top.usd / sf.all_usd : 0;
const restShare = top ? (sf.ai_usd - top.usd) / (sf.all_usd - top.usd) : aiShare;
```

```js
// 3. SPVs among new venture vehicles, with the overcount range defined as on New funds:
// low = all / (blind pool + unclear), high = all / blind pool.
const veh = rows(await db.query(`
  select metro,
         count(*) filter (where vehicle_type = 'blind_pool')::int as blind_pool,
         count(*) filter (where vehicle_type = 'spv')::int as spv,
         count(*) filter (where vehicle_type = 'unclear')::int as unclear
  from fund_vehicles
  where kind = 'fund' and fund_bucket = 'venture' and is_new_in_window
    and first_filed < date_trunc('quarter', date '${asOf}')
  group by metro`));
const tally = (a) => {
  const t = {blind_pool: 0, spv: 0, unclear: 0};
  for (const d of a) for (const k in t) t[k] += d[k];
  const all = t.blind_pool + t.spv + t.unclear;
  return {...t, all, spvShare: t.spv / all, low: all / (t.blind_pool + t.unclear), high: all / t.blind_pool};
};
const us = tally(veh);
const local = ["sf_bay", "la"].map((m) => tally(veh.filter((d) => d.metro === m)));
const x1 = (v) => `${v.toFixed(1)}x`;
const range = (lo, hi) => (lo.toFixed(1) === hi.toFixed(1) ? x1(lo) : `${lo.toFixed(1)}–${x1(hi)}`);
const localLow = Math.min(...local.map((d) => d.low)), localHigh = Math.max(...local.map((d) => d.high));
```

```js
// 4. Emerging managers (Fund I–III) among new venture fund families, by year (as on New funds).
const yearly = rows(await db.query(`
  select year(first_filed)::int as y, count(*)::int as n, count(*) filter (where is_emerging)::int as emerging
  from fund_families
  where fund_bucket = 'venture' and is_new_in_window
  group by all order by y`)).map((d) => ({...d, share: d.emerging / d.n}));
const lastY = yearly.at(-2) ?? yearly.at(-1); // latest complete year
const firstY = yearly[0];
const drift = lastY.share - firstY.share;
```

<div class="kicker">Findings · ${monthYear}</div>

# What ${NUMBER_WORDS[yearsSpan] ?? num(yearsSpan)} years of SEC private-fundraising filings show

<p class="dek">Every startup raise and venture fund filed on Form D since ${fromYear}, cleaned up into single raises, sorted into startups, funds and SPVs, and tagged for AI. Four things stood out. Each links to the page with the live numbers.</p>

<div class="card">

### 1. The biggest AI companies are ${absentShare > 0.5 ? "mostly" : "often"} invisible

${absentShare > 0.5 ? "More than half of" : cap(about(absentShare))} the well-known SF AI companies I checked (${num(absent.length)} of ${num(checked.length)}) never filed a Form D under their own name. ${listing(best.map((d) => d.company))} don't.${bestVehicles > 0 ? ` ${num(bestVehicles)} SPVs buying their shares do.` : ""} You can see investor demand for these companies in the data, but not the companies themselves.

<a href="./coverage">Coverage gap →</a>

</div>

<div class="card">

### 2. ${aiShare > 0.5 ? "AI has taken over SF startup money" : `AI took ${pct(aiShare)} of SF startup money`}${top && restShare > 0.25 ? `, and it isn't just ${shortName(top.name)}` : ""}

AI companies raised ${pct(aiShare)} of SF startup dollars in ${year} to date.${top ? ` ${shortName(top.name)} alone was ${pct(topShare)}, but take it out and AI is still ${pct(restShare)}: ${aiTop.slice(1).map((d) => shortName(d.name)).join(", ")} and many smaller companies make up the rest.` : ""}

<a href="./">Overview →</a> · <a href="./ai-raises">AI raises →</a>

</div>

<div class="card">

### 3. The "VC fund boom" is partly an SPV boom

${cap(about(us.spvShare))} "new venture fund" filings in the US are really single-deal SPVs. Counting raw filings overstates new funds by about ${range(us.low, us.high)} nationally and ${range(localLow, localHigh)} in SF and LA.

<a href="./funds">New funds →</a>

</div>

<div class="card">

### 4. ${lastY.share > 0.5 ? "First-time managers still dominate" : "First-time managers no longer dominate"}

About ${about(lastY.share)} new VC funds are a manager's first, second or third fund (${pct(lastY.share)} in ${lastY.y}), and that share has ${Math.abs(drift) < 0.05 ? `barely moved since ${firstY.y} (${pct(firstY.share)} then)` : `${drift > 0 ? "risen" : "fallen"} from ${pct(firstY.share)} in ${firstY.y}`}.

<a href="./funds">New funds →</a> · <a href="./manager">Fund managers →</a>

</div>

<p class="fineprint" style="color:var(--ink-muted);font-size:0.8rem">Figures are computed from the same data as the linked pages (filings through ${asOf}) and update with them. AI shares use the current-evidence AI tag; the largest AI issuer is the one with the most dollars in ${year}. SPV counts cover vehicles first filed since ${fromYear}, up to the last complete quarter, and the low end of each range counts unclear vehicles as funds. Form D misses many raises, so totals are a floor. How each number is made, and how often the classifications are right, is on the <a href="./methodology">Methodology</a> page.</p>
