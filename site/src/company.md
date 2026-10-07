---
title: Company lookup
---

```js
import * as Plot from "npm:@observablehq/plot";
import {C, usd, pct, num, rows, tile, companyLink, secLink, STAGE_LABEL, metroScope} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const db = DuckDBClient.of({
  offerings: FileAttachment("data/offerings.parquet"),
  issuers: FileAttachment("data/issuers.parquet"),
  filings: FileAttachment("data/filings.parquet"),
  people: FileAttachment("data/people.parquet")
});
const cik = new URLSearchParams(location.search).get("cik");
```

```js
const issuer = cik ? rows(await db.query(`select * replace (first_filing::varchar as first_filing,
    last_filing::varchar as last_filing) from issuers
    where cik = '${cik.replace(/\D/g, "").padStart(10, "0")}'`))[0] : null;
const titleCase = (s) => (s ?? "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
```

```js
if (!issuer) {
  display(html`<div class="kicker">Company lookup</div><h1>Find a company or fund</h1>
  <p class="dek">Search every US startup and venture fund that filed a Form D since 2019. Each page shows its raises, every filing with a link to the SEC, the people named on the filings, and why it is or isn't tagged AI.</p>`);
}
```

```js
const METRO_LABEL = metroScope(meta).labels;
const directory = issuer ? [] : rows(await db.query(`
  select cik, name, metro, city, state, kind, ai_tier, n_offerings::int as n_offerings, last_filing::varchar as last_filing
  from issuers order by last_filing desc`));
const found = issuer ? [] : view(Inputs.search(directory, {placeholder: "Company or fund name, city, state…", columns: ["name", "city", "state"]}));
```

```js
if (!issuer) display(Inputs.table(found, {
  rows: 20,
  columns: ["name", "kind", "metro", "city", "state", "n_offerings", "last_filing", "ai_tier"],
  header: {name: "Name", kind: "Type", metro: "Metro", city: "City", state: "State", n_offerings: "Raises", last_filing: "Last filing", ai_tier: "AI tier"},
  format: {name: (n, i, data) => companyLink(data[i].cik, n), metro: (m) => METRO_LABEL[m] ?? "—", kind: (k) => (k ?? "").replace("_", " ")}
}));
```

```js
if (issuer) {
  const offs = rows(await db.query(`select *, first_filed::varchar as ff, last_filed::varchar as lf,
      first_sale_date::varchar as fs from offerings where cik = '${issuer.cik}' order by first_filed`));
  const fil = rows(await db.query(`select f.*, f.filing_date::varchar as fd from filings f
      where cik = '${issuer.cik}' order by filing_date, accession`));
  const ppl = rows(await db.query(`select display_name, roles, first_seen::varchar as first_seen, last_seen::varchar as last_seen,
      is_repeat_founder, exec_turned_gp, is_platform_staff, is_entity
      from people where cik = '${issuer.cik}' order by is_entity, display_name`));
  const kind = (issuer.kind ?? "").replace("_", " ");
  display(html`<div class="kicker">${kind} · ${METRO_LABEL[issuer.metro] ?? ""} · CIK <span class="mono">${issuer.cik}</span></div>
    <h1>${issuer.name}</h1>
    <p class="dek">${[titleCase(issuer.city), issuer.county_name && issuer.county_name.toLowerCase() !== (issuer.city ?? "").toLowerCase() ? `${issuer.county_name} County` : null].filter(Boolean).join(", ")}${issuer.domain ? html` · <a href="https://${issuer.domain}" target="_blank" rel="noopener">${issuer.domain}</a>` : ""}${issuer.yc_batch ? ` · Y Combinator ${issuer.yc_batch}` : ""}${issuer.former_names?.length ? ` · formerly ${Array.from(issuer.former_names).join(", ")}` : ""}</p>
    <div class="receipt"><span>Source <b>SEC Form D</b></span><span>${num(fil.length)} filings · ${num(offs.length)} raises</span><span><a href="${issuer.edgar_url}" target="_blank" rel="noopener">All filings on EDGAR ↗</a></span></div>`);

  const total = offs.reduce((t, d) => t + (d.sold_latest ?? 0), 0);
  display(html`<div class="tiles">
    ${tile("Reported sold, all raises", usd(total), `${num(offs.length)} raises since ${offs[0]?.ff?.slice(0, 4) ?? "—"}`)}
    ${tile("AI tag", issuer.ai_tier ?? "not tagged", issuer.ai_evidence ? issuer.ai_evidence.split(" | ").slice(0, 2).join(" · ") : "No AI signal in name, YC tags or homepage")}
    ${tile("Capital momentum", issuer.capital_score == null ? "—" : String(Math.round(issuer.capital_score)), issuer.capital_score == null ? "Startups only" : `0–100 vs ${num(issuer.peer_group_size)} peers (${issuer.size_band?.slice(2)}) · confidence ${issuer.data_confidence}`)}
    ${tile("Latest filing", issuer.last_filing ? String(issuer.last_filing).slice(0, 10) : "—", issuer.recency_days != null ? `${num(issuer.recency_days)} days before the data cut-off` : "")}
  </div>`);

  if (fil.length > 1) {
    display(html`<h2>Amount sold, filing by filing</h2><p>Each line is one raise; each dot is a filing (the original D or an amendment). A drop is a correction the issuer filed, kept as reported.</p>`);
    display(resize((width) => Plot.plot({
      width, height: 260, marginLeft: 60,
      style: {fontFamily: "var(--sans)", fontSize: "12px", background: "transparent"},
      x: {type: "utc", label: null}, y: {grid: true, label: null, tickFormat: (v) => usd(v)},
      color: {legend: false, range: [C.s1, C.s2, C.s3, C.ink2]},
      marks: [
        Plot.ruleY([0], {stroke: "var(--baseline)"}),
        Plot.line(fil, {x: (d) => new Date(d.fd), y: "sold", z: "offering_id", stroke: C.s1, strokeWidth: 2, curve: "step-after"}),
        Plot.dot(fil, {x: (d) => new Date(d.fd), y: "sold", fill: C.s1, r: 4, stroke: C.surface, strokeWidth: 2,
          tip: true, title: (d) => `${d.fd} · Form ${d.form}\nSold ${usd(d.sold)} of ${d.offering_amt ? usd(d.offering_amt) : "indefinite"}\nInvestors ${num(d.investors)}`})
      ]
    })));
  }

  display(html`<h2>Raises</h2>`);
  display(Inputs.table(offs, {
    columns: ["ff", "fs", "kind", "inferred_stage", "security_class", "offering_amt", "sold_latest", "investors_latest", "n_filings", "exemptions", "filing_url"],
    header: {ff: "First filed", fs: "First sale", kind: "Type", inferred_stage: "Stage (inferred)", security_class: "Security", offering_amt: "Target", sold_latest: "Sold", investors_latest: "Investors", n_filings: "Filings", exemptions: "Exemptions", filing_url: "Latest filing"},
    format: {offering_amt: (v, i, d) => d[i].offering_indefinite ? "Indefinite" : usd(v), sold_latest: (v) => usd(v), inferred_stage: (s) => STAGE_LABEL[s] ?? "—", kind: (k) => (k ?? "").replace("_", " "), security_class: (s) => (s ?? "").replaceAll("_", " "), filing_url: (u) => secLink(u)},
    rows: 12
  }));

  display(html`<h2>Every filing</h2>`);
  display(Inputs.table(fil, {
    columns: ["fd", "form", "accession", "sold", "sold_increment", "investors", "link_method"],
    header: {fd: "Filed", form: "Form", accession: "Accession", sold: "Sold to date", sold_increment: "Change", investors: "Investors", link_method: "Linked by"},
    format: {
      accession: (a) => html`<a class="mono" target="_blank" rel="noopener" href="https://www.sec.gov/Archives/edgar/data/${Number(issuer.cik)}/${a.replaceAll("-", "")}/">${a}</a>`,
      sold: (v) => usd(v), sold_increment: (v, i, d) => v == null ? "—" : `${v >= 0 ? "+" : ""}${usd(v)}${d[i].is_correction ? " (correction)" : ""}`,
      link_method: (m) => (m ?? "").replaceAll("_", " ")
    },
    rows: 15
  }));

  display(html`<h2>People and entities on the filings</h2><p>Names and roles as filed (executive officer, director, promoter).</p>`);
  display(Inputs.table(ppl, {
    columns: ["display_name", "roles", "first_seen", "last_seen", "is_repeat_founder", "exec_turned_gp"],
    header: {display_name: "Name", roles: "Roles", first_seen: "First seen", last_seen: "Last seen", is_repeat_founder: "Repeat founder", exec_turned_gp: "Startup exec → VC GP"},
    format: {is_repeat_founder: (b) => (b ? "yes" : ""), exec_turned_gp: (b) => (b ? "yes" : ""), first_seen: (d) => String(d).slice(0, 10), last_seen: (d) => String(d).slice(0, 10)},
    rows: 12
  }));
}
```
