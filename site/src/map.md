---
title: Map
toc: false
---

```js
import * as L from "npm:leaflet";
import {usd, num, rows, STAGES, STAGE_LABEL, metroScope} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const db = DuckDBClient.of({
  offerings: FileAttachment("data/offerings.parquet"),
  issuers: FileAttachment("data/issuers.parquet")
});
```


<div class="kicker">Map</div>

# Where the money lands

<p class="dek">One dot per raise, placed at the issuer's principal office: 87% by street address (Census geocoder) and the rest at their ZIP's centre. Dots are sized by amount sold and coloured by <span class="inferred">inferred</span> stage.</p>

<div class="filters">

```js
const metro = view(Inputs.select(meta.metros.map((m) => m.label), {label: "Metro", value: meta.metros[0].label}));
```

```js
const scope = view(Inputs.select(["AI startup raises", "All startup raises", "New VC funds (blind pool)"], {label: "Show"}));
```

```js
const window_ = view(Inputs.select(["Last 12 months", "2025", "2024", "2023", "Since 2019"], {label: "Period"}));
```

</div>

```js
const metroInfo = meta.metros.find((m) => m.label === metro);
const metroKey = metroInfo.key;
const periodSql = window_ === "Last 12 months" ? `o.first_filed > date '${meta.data_through}' - interval 365 day`
  : window_ === "Since 2019" ? "true" : `year(o.first_filed) = ${window_}`;
const scopeSql = scope === "AI startup raises" ? "o.is_startup_raise and o.is_ai"
  : scope === "All startup raises" ? "o.is_startup_raise"
  : "o.kind = 'fund' and o.fund_bucket = 'venture' and o.vehicle_type = 'blind_pool'";
const points = rows(await db.query(`
  select o.cik, o.issuer_name, o.first_filed::varchar as filed, o.sold_latest::double as sold,
         coalesce(o.inferred_stage, 'fund') as stage, i.lat::double as lat, i.lon::double as lon,
         i.city, i.geocode_method, i.ai_tier
  from offerings o join issuers i using (cik)
  where o.metro = '${metroKey}' and o.is_new_in_window and not o.flag_implausible
    and i.lat is not null and ${scopeSql} and ${periodSql}
  order by o.sold_latest desc nulls last`));
```

```js
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const stageColor = {seed: css("--ord-1"), series_a: css("--ord-2"), series_b: css("--ord-3"), growth: css("--ord-4"), unknown: css("--neutral"), fund: css("--series-2")};
const div = display(document.createElement("div"));
div.style = "height: 620px; border: 1px solid var(--hairline);";
const map = L.map(div, {preferCanvas: true, scrollWheelZoom: false})
  .setView([metroInfo.view[0], metroInfo.view[1]], metroInfo.view[2]);
// OpenStreetMap standard tiles (no key; light use with attribution), greyed in CSS so
// the data stays the loudest thing on the map.
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 18
}).addTo(map);
// Area ∝ amount, capped at the 95th percentile so one mega-round doesn't shrink the rest.
const cap = Math.max(1, d3.quantile(points.map((d) => d.sold ?? 0).sort(d3.ascending), 0.95) ?? 1);
for (const d of points) {
  const r = 3 + 16 * Math.sqrt(Math.min(d.sold ?? 0, cap) / cap);
  L.circleMarker([d.lat, d.lon], {radius: r, color: css("--surface-1"), weight: 1, fillColor: stageColor[d.stage] ?? stageColor.unknown, fillOpacity: 0.8})
    .bindPopup(`<b><a href="./company?cik=${d.cik}">${d.issuer_name}</a></b><br>${d.city ?? ""} · first filed ${d.filed}<br>Sold ${usd(d.sold)} · ${STAGE_LABEL[d.stage] ?? "VC fund"}${d.geocode_method === "zip_centroid" ? "<br><i>placed at ZIP centre</i>" : ""}`)
    .addTo(map);
}
invalidation.then(() => map.remove());
```

<div>${scope === "New VC funds (blind pool)" ? html`<span class="key"><i style="background:var(--series-2)"></i>VC fund</span>` : [...STAGES, "unknown"].map((s) => html`<span class="key"><i style="background:${s === "unknown" ? "var(--neutral)" : `var(--ord-${STAGES.indexOf(s) + 1})`}"></i>${STAGE_LABEL[s]}</span>`)}<span class="key">· dot area ∝ amount sold (raises above the 95th percentile drawn at the largest size)</span></div>

<p>${num(points.length)} ${scope.toLowerCase()} in ${metro}, ${window_.toLowerCase()} · ${usd(d3.sum(points, (d) => d.sold))} reported sold. Click a dot for the company.</p>

```js
display(Inputs.table(points, {rows: 12, columns: ["issuer_name", "city", "filed", "sold", "stage", "geocode_method"],
  header: {issuer_name: "Company", city: "City", filed: "First filed", sold: "Sold", stage: "Stage (inferred)", geocode_method: "Placed by"},
  format: {issuer_name: (n, i, d) => html`<a href="./company?cik=${d[i].cik}">${n}</a>`, sold: (v) => usd(v), stage: (s) => STAGE_LABEL[s] ?? "VC fund", geocode_method: (g) => (g === "address" ? "street address" : "ZIP centre")}}))
```
