---
title: Company lookup
---

```js
import * as Plot from "npm:@observablehq/plot";
import {C, usd, pct, num, rows, tile, secLink, STAGE_LABEL, metroScope} from "./components/viz.js";
const meta = FileAttachment("data/meta.json").json();
const cik = new URLSearchParams(location.search).get("cik");
// Search reads only the slim directory; one company's page also loads its raises,
// filings and people.
const db = cik ? DuckDBClient.of({
  offerings: FileAttachment("data/offerings.parquet"),
  issuers: FileAttachment("data/issuers.parquet"),
  filings: FileAttachment("data/filings.parquet"),
  people: FileAttachment("data/people.parquet")
}) : DuckDBClient.of({directory: FileAttachment("data/directory.parquet")});
const titleCase = (s) => (s ?? "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
```

```js
const issuer = cik ? rows(await db.query(`select * replace (first_filing::varchar as first_filing,
    last_filing::varchar as last_filing) from issuers
    where cik = '${cik.replace(/\D/g, "").padStart(10, "0")}'`))[0] : null;
```

```js
// The search view draws before the data arrives: title, search box and placeholder cards
// first, then the cards fill in once the directory has loaded.
const KIND_LABEL = {operating_company: "Startup", fund: "Fund"};
const SHOW = ["All", "Startups", "Funds", "AI only"];

function searchBox() {
  const input = html`<input type="search" placeholder="Try Harvey, Crusoe or San Francisco" autocomplete="off" spellcheck="false">`;
  const el = html`<label class="lookup-search"><span>Search startups and venture funds by name, city or state</span>
    <div class="lookup-field"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-4-4"></path></svg>${input}</div></label>`;
  Object.defineProperty(el, "value", {get: () => input.value});
  return el;
}

function chips(options) {
  const el = html`<div class="chips" role="group" aria-label="Show">${options.map((o, i) =>
    html`<button type="button" aria-pressed=${i === 0 ? "true" : "false"}>${o}</button>`)}</div>`;
  el.value = options[0];
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.textContent === el.value) return;
    el.value = b.textContent;
    for (const x of el.querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
    el.dispatchEvent(new Event("input", {bubbles: true}));
  });
  return el;
}

const skeletonCard = () => html`<div class="lookup-card skeleton" aria-hidden="true">
  <i class="sk" style="width:62%;height:18px"></i><i class="sk" style="width:40%;height:12px"></i>
  <i class="sk" style="width:30%;height:16px"></i><i class="sk" style="width:100%;height:44px"></i></div>`;

if (!cik) display(html`<div class="kicker">Company lookup</div><h1>Find a company or fund</h1>
  <p class="dek">Every US startup and venture fund that filed a Form D since 2019. Each page shows its raises, every filing with a link to the SEC, the people named on the filings, and why it is or isn't tagged AI.</p>`);
```

```js
const query = cik ? "" : view(searchBox());
```

```js
const show = cik ? "All" : view(chips(SHOW));
```

```js
const METRO_LABEL = metroScope(meta).labels;
const lookupStatus = html`<p class="lookup-status" aria-live="polite">Loading the company list…</p>`;
const lookupGrid = html`<div class="lookup-grid">${Array.from({length: 6}, skeletonCard)}</div>`;
if (!cik) display(html`<div>${lookupStatus}${lookupGrid}</div>`);
```

```js
function lookupCard(d) {
  const years = Array.from(d.sold_by_year ?? [], Number);
  const max = Math.max(0, ...years);
  const last = d.first_year + years.length - 1;
  const place = [titleCase(d.city), d.state].filter(Boolean).join(", ") + (METRO_LABEL[d.metro] ? ` · ${METRO_LABEL[d.metro]}` : "");
  const spark = years.map((v, i) => `${d.first_year + i} ${v ? usd(v) : "none"}`).join(", ");
  return html`<a class="lookup-card" href="./company?cik=${encodeURIComponent(d.cik)}">
    <div><div class="lookup-name">${d.name}</div><div class="lookup-place">${place}</div></div>
    <div class="lookup-badges"><span class="badge">${KIND_LABEL[d.kind] ?? (d.kind ?? "").replace("_", " ")}</span>${
      d.ai_tier && d.ai_tier !== "none" ? html`<span class="badge ai">AI · ${d.ai_tier}</span>` : ""}</div>
    <div class="lookup-stats">
      <div><span>Raised (reported)</span><b>${usd(d.sold_total)}</b></div>
      <div><span>Raises</span><b>${num(d.n_offerings)}</b></div>
      <div class="spark" role="img" aria-label=${`Money raised by year of first filing: ${spark}`}>
        <div class="spark-bars">${years.map((v) => html`<i class=${v ? "on" : ""} style=${`height:${v ? Math.max(3, Math.round((v / max) * 36)) : 1}px`}></i>`)}</div>
        <div class="spark-axis"><span>${d.first_year}</span><span>${last}</span></div>
      </div>
    </div>
    <div class="lookup-foot"><span>Last filed ${d.lf ?? "—"}</span><span class="go">View raises and filings →</span></div>
  </a>`;
}
```

```js
if (!cik) {
  // contains() rather than LIKE, so a typed % or _ is matched literally.
  const q = query.trim().toLowerCase().replaceAll("'", "''");
  const text = q ? `(contains(lower(name), '${q}') or contains(lower(city), '${q}') or lower(state) = '${q}')` : "true";
  const kind = {Startups: "kind = 'operating_company'", Funds: "kind = 'fund'", "AI only": "ai_tier in ('strong', 'likely')"}[show] ?? "true";
  const where = `${text} and ${kind}`;
  // With a query, names that start with it come first; otherwise the latest filers.
  const order = q ? `starts_with(lower(name), '${q}') desc, sold_total desc` : "last_filing desc, sold_total desc";
  const [{n}] = rows(await db.query(`select count(*)::int as n from directory where ${where}`));
  const found = rows(await db.query(`select *, last_filing::varchar as lf
    from directory where ${where} order by ${order}, name limit 48`));
  lookupStatus.textContent = n === 0 ? "No matches. Try part of the name, or a city."
    : `${num(n)} ${n === 1 ? "match" : "matches"}${n > found.length ? `, showing the first ${found.length}` : ""} · ${q ? "names starting with your search first, then by money raised" : "most recent filers first"}`;
  lookupGrid.replaceChildren(...found.map(lookupCard));
}
```

```js
if (cik && !issuer) display(html`<div class="kicker">Company lookup</div><h1>No company with that CIK</h1>
  <p class="dek">CIK ${cik} is not a startup or venture fund in this data. <a href="./company">Search all companies</a>.</p>`);
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
