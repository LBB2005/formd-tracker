---
title: Fund managers
---

```js
import {usd, pct, num, rows, tile, metroScope} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const key = new URLSearchParams(location.search).get("key");
// The manager list needs only the managers table; one manager's page also loads its
// vehicles, fund families and people.
const db = key ? DuckDBClient.of({
  managers: FileAttachment("data/managers.parquet"),
  manager_vehicles: FileAttachment("data/manager_vehicles.parquet"),
  fund_families: FileAttachment("data/fund_families.parquet"),
  manager_people: FileAttachment("data/manager_people.parquet")
}) : DuckDBClient.of({managers: FileAttachment("data/managers.parquet")});
const esc = (s) => s.replaceAll("'", "''");
```

```js
// A separate cell, so meta has resolved (FileAttachment.json() is a promise in its own cell).
const METRO_LABEL = metroScope(meta).labels;
```

```js
const mgr = key ? rows(await db.query(`select * replace (first_vehicle_filed::varchar as first_vehicle_filed,
    adv_since::varchar as adv_since) from managers where manager_key = '${esc(key)}'`))[0] : null;
```

```js
if (!mgr) display(html`<div class="kicker">Fund managers</div><h1>US venture fund managers</h1>
  <p class="dek">Fund vehicles grouped by manager. The manager key is the fund name up to its fund number, so "Accel XIV" and "Accel XV" group under "accel", and platform SPVs group under their platform. Sorted by blind-pool funds filed since 2019.</p>`);
```

```js
const list = mgr ? [] : rows(await db.query(`
  select manager_key, metro, n_blind_pool::int as n_blind_pool, n_spv::int as n_spv, n_vehicles::int as n_vehicles,
         fund_number_max::int as fund_number_max, first_vehicle_filed::varchar as since, sample_names
  from managers where has_vc_vehicle order by n_blind_pool desc, n_vehicles desc`));
const found = mgr ? [] : view(Inputs.search(list, {placeholder: "Manager or fund name…", columns: ["manager_key", "sample_names"]}));
```

```js
if (!mgr) display(Inputs.table(found, {
  rows: 25,
  columns: ["manager_key", "metro", "n_blind_pool", "n_spv", "fund_number_max", "since", "sample_names"],
  header: {manager_key: "Manager", metro: "Metro", n_blind_pool: "Funds", n_spv: "SPVs", fund_number_max: "Highest fund #", since: "First vehicle", sample_names: "Vehicles (sample)"},
  format: {manager_key: (m) => html`<a href="./manager?key=${encodeURIComponent(m)}">${m}</a>`, metro: (m) => METRO_LABEL[m] ?? "—", sample_names: (s) => (s ?? "").slice(0, 120)},
  width: {sample_names: 380}
}));
```

```js
if (mgr) {
  const vehicles = rows(await db.query(`
    select cik, issuer_name, vehicle_type, fund_number::int as fund_number, first_filed::varchar as filed,
           offering_amt::double as target, offering_indefinite, sold_latest::double as sold,
           investors_latest::int as investors, filing_url
    from manager_vehicles where manager_key = '${esc(key)}' order by first_filed`));
  const fams = rows(await db.query(`
    select fund_family_id, fund_number::int as fund_number, first_filed::varchar as filed, n_vehicles::int as n_vehicles,
           target_amt::double as target, is_emerging, emerging_basis
    from fund_families where manager_key = '${esc(key)}' order by first_filed`));
  const ciks = [...new Set(vehicles.map((d) => `'${d.cik}'`))].join(",");
  const ppl = ciks ? rows(await db.query(`
    select display_name, string_agg(distinct roles, '; ') as roles, count(distinct cik)::int as vehicles,
           bool_or(exec_turned_gp) as exec_turned_gp, bool_or(is_entity) as is_entity
    from manager_people where cik in (${ciks}) and not is_platform_staff
    group by display_name order by vehicles desc, display_name limit 60`)) : [];
  display(html`<div class="kicker">Fund manager · ${METRO_LABEL[mgr.metro] ?? ""}</div><h1>${mgr.manager_key}</h1>
    <p class="dek">${num(mgr.n_vehicles)} vehicles since ${mgr.first_vehicle_filed?.slice(0, 4)}: ${num(mgr.n_blind_pool)} blind-pool funds and ${num(mgr.n_spv)} SPVs. Highest fund number seen: ${mgr.fund_number_max ?? "—"}.</p>
    <div class="tiles">
      ${tile("Fund families", num(fams.length), `${num(fams.filter((d) => d.is_emerging).length)} counted as emerging (Fund I–III)`)}
      ${tile("Stated targets", usd(d3.sum(fams, (d) => d.target)), "sum of fund-family targets; parallel vehicles counted once")}
      ${tile("Reported sold", usd(d3.sum(vehicles, (d) => d.sold)), "latest amount sold, all vehicles")}
      ${tile("Form ADV adviser", mgr.adv_name ?? "not matched", mgr.adv_name
        ? html`${mgr.adv_registration === "exempt" ? "exempt reporting adviser" : "registered adviser"}${mgr.adv_vc_exemption ? " · VC-fund exemption" : ""} · ${num(mgr.adv_private_funds)} private funds · ${usd(mgr.adv_private_fund_assets)} gross assets · on SEC since ${String(mgr.adv_since).slice(0, 4)} · <a href="https://adviserinfo.sec.gov/firm/summary/${mgr.adv_crd}" target="_blank" rel="noopener">IAPD ↗</a>`
        : "no adviser with a matching name in the SEC's Form ADV files")}
    </div>`);
  display(html`<h2>Fund families</h2>`);
  display(Inputs.table(fams, {columns: ["fund_number", "filed", "n_vehicles", "target", "is_emerging", "emerging_basis"],
    header: {fund_number: "Fund #", filed: "First filed", n_vehicles: "Vehicles", target: "Target", is_emerging: "Emerging", emerging_basis: "Basis"},
    format: {target: (v) => (v == null ? "Indefinite" : usd(v)), is_emerging: (b) => (b ? "yes" : "no")}}));
  display(html`<h2>All vehicles</h2>`);
  display(Inputs.table(vehicles, {rows: 20, columns: ["issuer_name", "vehicle_type", "fund_number", "filed", "target", "sold", "investors", "filing_url"],
    header: {issuer_name: "Vehicle", vehicle_type: "Type", fund_number: "Fund #", filed: "First filed", target: "Target", sold: "Sold", investors: "Investors", filing_url: "Filing"},
    format: {issuer_name: (n, i, d) => html`<a href="./company?cik=${d[i].cik}">${n}</a>`, vehicle_type: (v) => (v ?? "").replace("_", " "),
      target: (v, i, d) => (d[i].offering_indefinite ? "Indefinite" : usd(v)), sold: (v) => usd(v),
      filing_url: (u) => html`<a class="mono" target="_blank" rel="noopener" href="${u}">SEC ↗</a>`},
    width: {issuer_name: 300}}));
  display(html`<h2>People and entities on these filings</h2><p>Platform administrators and fund-services staff named on 50+ issuers are left out.</p>`);
  display(Inputs.table(ppl, {rows: 20, columns: ["display_name", "roles", "vehicles", "exec_turned_gp"],
    header: {display_name: "Name", roles: "Roles", vehicles: "Vehicles", exec_turned_gp: "Former startup exec"},
    format: {exec_turned_gp: (b) => (b ? "yes" : "")}}));
}
```
