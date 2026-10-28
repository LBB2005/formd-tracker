---
title: Company lookup
---

```js
import * as Plot from "npm:@observablehq/plot";
import {C, usd, pct, num, rows, secLink, STAGE_LABEL, metroScope} from "./components/viz.js";
import {SHARDS} from "./data/company/shards.js";
const meta = FileAttachment("data/meta.json").json();
const cik = new URLSearchParams(location.search).get("cik");
// Search queries the slim directory in DuckDB. One company's page needs no database: it
// reads its own entry from the company file for the CIK's last two digits (~1% of companies).
const db = cik ? null : DuckDBClient.of({directory: FileAttachment("data/directory.parquet")});
const titleCase = (s) => (s ?? "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
```

```js
const key = cik ? cik.replace(/\D/g, "").padStart(10, "0") : null;
const company = key ? (await SHARDS[key.slice(-2)]?.json())?.[key] ?? null : null;
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

// A company's page draws a placeholder at once and fills in when its file arrives.
const profileRoot = html`<div class="profile" aria-busy="true">
  <i class="sk" style="width:12rem;height:12px"></i><i class="sk" style="width:min(100%,28rem);height:46px;margin:0.9rem 0 0.7rem"></i>
  <i class="sk" style="width:16rem;height:16px"></i>
  <div class="pstats">${Array.from({length: 4}, () => html`<div><i class="sk" style="width:60%;height:11px"></i><i class="sk" style="width:45%;height:26px;margin-top:8px"></i></div>`)}</div>
</div>`;
if (cik) display(profileRoot);
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
const fmtDate = (d) => d ? new Date(`${String(d).slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US",
  {month: "short", day: "numeric", year: "numeric", timeZone: "UTC"}) : "—";
const year = (d) => (d ? String(d).slice(0, 4) : "");
const plural = (n, word) => `${num(n)} ${n === 1 ? word : `${word}s`}`;
const FIRST_SHOWN = 12;  // longer lists of raises or people fold the rest under "Show more"

function folded(items, render, className, noun) {
  const list = (xs) => html`<ol class=${className}>${xs.map(render)}</ol>`;
  if (items.length <= FIRST_SHOWN) return list(items);
  return html`<div>${list(items.slice(0, FIRST_SHOWN))}<details class="more">
    <summary>Show ${num(items.length - FIRST_SHOWN)} more ${noun}</summary>${list(items.slice(FIRST_SHOWN))}</details></div>`;
}

// One raise on the timeline: amount sold, how much of the target that is, and its facts.
function raiseItem(d) {
  const target = d.offering_indefinite ? null : d.offering_amt;
  const fill = target > 0 && d.sold_latest != null ? Math.min(1, d.sold_latest / target) : null;
  const facts = [
    d.offering_indefinite ? "no fixed target" : target != null ? `of ${usd(target)} target` : null,
    d.investors_latest != null ? plural(d.investors_latest, "investor") : null,
    d.first_sale_date ? `first sale ${fmtDate(d.first_sale_date)}` : null,
    plural(d.n_filings, "filing")
  ].filter(Boolean).join(" · ");
  return html`<li class="raise">
    <div class="raise-when">${fmtDate(d.first_filed)}</div>
    <div class="raise-head"><span class="raise-amt">${usd(d.sold_latest)}</span><span class="raise-sold">sold</span>
      ${d.inferred_stage && d.inferred_stage !== "unknown" ? html`<span class="badge">${STAGE_LABEL[d.inferred_stage]}</span>` : ""}
      ${d.security_class ? html`<span class="badge">${d.security_class.replaceAll("_", " ")}</span>` : ""}</div>
    ${fill == null ? "" : html`<div class="fill" role="img" aria-label=${`${pct(fill)} of the target sold`}><i style=${`width:${(100 * fill).toFixed(1)}%`}></i></div>`}
    <div class="raise-meta">${facts} · ${secLink(d.filing_url, "Latest filing")}</div>
  </li>`;
}

function personItem(p) {
  const span = year(p.first_seen) === year(p.last_seen) ? year(p.first_seen) : `${year(p.first_seen)}–${year(p.last_seen)}`;
  return html`<li class="person">
    <div class="person-name">${p.display_name}</div>
    <div class="person-role">${p.roles ?? ""}</div>
    <div class="person-foot"><span>On filings ${span}</span>${p.is_entity ? html`<span class="badge">entity</span>` : ""}${
      p.is_repeat_founder ? html`<span class="badge">repeat founder</span>` : ""}${
      p.exec_turned_gp ? html`<span class="badge">startup exec → VC GP</span>` : ""}</div>
  </li>`;
}

function aiCard(issuer) {
  const tagged = issuer.ai_tier && issuer.ai_tier !== "none";
  const evidence = (issuer.ai_evidence ?? "").split(" | ").filter(Boolean);
  return html`<section class="pcard">
    <div class="pcard-label">AI tag</div>
    <div class="pcard-value">${tagged ? issuer.ai_tier[0].toUpperCase() + issuer.ai_tier.slice(1) : "Not tagged"}</div>
    ${evidence.length ? html`<ul class="evidence">${evidence.map((e) => {
      const i = e.indexOf(":");
      return i > 0 ? html`<li><b>${e.slice(0, i)}</b>${e.slice(i + 1)}</li>` : html`<li>${e}</li>`;
    })}</ul>` : html`<p class="pcard-note">${issuer.ai_checked
      ? "No AI signal in its name, Y Combinator tags or homepage."
      : "No AI signal in its name or Y Combinator tags. Its homepage hasn't been checked yet."}</p>`}
    <p class="pcard-note"><a href="./methodology#ai-tag">How the AI tag works</a></p>
  </section>`;
}

function momentumCard(issuer) {
  const s = Math.round(issuer.capital_score);
  return html`<section class="pcard">
    <div class="pcard-label">Capital momentum</div>
    <div class="pcard-value">${s}<small> / 100</small></div>
    <div class="meter" role="img" aria-label=${`${s} out of 100`}><i style=${`width:${s}%`}></i></div>
    <p class="pcard-note">Against ${num(issuer.peer_group_size)} peer startups (${issuer.size_band?.slice(2)}): how recent, how fast and how much bigger its raises are, and how quickly they fill. Confidence ${issuer.data_confidence}.</p>
    <p class="pcard-note"><a href="./methodology#capital-momentum-score">How the score works</a></p>
  </section>`;
}

function profileView({issuer, offerings, filings, people}) {
  const offs = offerings ?? [];  // export.py sorts raises by first filed,
  const fil = filings ?? [];     // filings by date,
  const ppl = people ?? [];      // and people by name, entities last
  const total = offs.reduce((t, d) => t + (d.sold_latest ?? 0), 0);
  const latest = offs.at(-1);
  const place = [titleCase(issuer.city), issuer.state].filter(Boolean).join(", ");
  const kicker = [KIND_LABEL[issuer.kind] ?? (issuer.kind ?? "").replace("_", " "), METRO_LABEL[issuer.metro]].filter(Boolean).join(" · ");
  const tagged = issuer.ai_tier && issuer.ai_tier !== "none";
  const nodes = [
    html`<a class="back" href="./company">← All companies</a>`,
    html`<div class="kicker">${kicker} · CIK <span class="mono">${issuer.cik}</span></div>`,
    html`<h1>${issuer.name}</h1>`,
    issuer.yc_one_liner ? html`<p class="dek">${issuer.yc_one_liner}</p>` : "",
    html`<div class="profile-meta">
      <span>${place || "Location not given"}</span>
      ${issuer.domain ? html`<a href="https://${issuer.domain}" target="_blank" rel="noopener">${issuer.domain} ↗</a>` : ""}
      ${issuer.former_names?.length ? html`<span>Formerly ${Array.from(issuer.former_names).join(", ")}</span>` : ""}
      <span class="lookup-badges">${tagged ? html`<span class="badge ai">AI · ${issuer.ai_tier}</span>` : ""}${
        issuer.yc_batch ? html`<span class="badge">Y Combinator ${issuer.yc_batch}</span>` : ""}${
        issuer.year_inc ? html`<span class="badge">Incorporated ${issuer.year_inc}</span>` : ""}</span>
    </div>`,
    html`<div class="pstats">
      <div><span>Raised (reported)</span><b>${usd(total)}</b><small>across ${plural(offs.length, "raise")}</small></div>
      <div><span>Latest raise</span><b>${latest ? usd(latest.sold_latest) : "—"}</b><small>${latest ? `first filed ${fmtDate(latest.first_filed)}` : ""}</small></div>
      <div><span>First filing</span><b>${year(issuer.first_filing) || "—"}</b><small>${fmtDate(issuer.first_filing)}</small></div>
      <div><span>Latest filing</span><b>${year(issuer.last_filing) || "—"}</b><small>${issuer.recency_days != null ? `${fmtDate(issuer.last_filing)} · ${num(issuer.recency_days)} days before the data ends` : fmtDate(issuer.last_filing)}</small></div>
    </div>`,
    html`<div class="receipt"><span>Source <b>SEC Form D</b></span><span>${plural(fil.length, "filing")} · ${plural(offs.length, "raise")}</span><span><a href="${issuer.edgar_url}" target="_blank" rel="noopener">All filings on EDGAR ↗</a></span></div>`,
    html`<div class="pcards">${aiCard(issuer)}${issuer.capital_score == null ? "" : momentumCard(issuer)}</div>`
  ];

  if (fil.length > 1) nodes.push(
    html`<h2>Amount sold, filing by filing</h2>`,
    html`<p>Each line is one raise; each dot is a filing (the original D or an amendment). A drop is a correction the issuer filed, kept as reported.</p>`,
    resize((width) => Plot.plot({
      width, height: 240, marginLeft: 60,
      style: {fontFamily: "var(--sans)", fontSize: "12px", background: "transparent"},
      x: {type: "utc", label: null}, y: {grid: true, label: null, tickFormat: (v) => usd(v)},
      marks: [
        Plot.ruleY([0], {stroke: "var(--baseline)"}),
        Plot.line(fil, {x: (d) => new Date(d.filing_date), y: "sold", z: "offering_id", stroke: C.s1, strokeWidth: 2, curve: "step-after"}),
        Plot.dot(fil, {x: (d) => new Date(d.filing_date), y: "sold", fill: C.s1, r: 4, stroke: C.surface, strokeWidth: 2,
          tip: true, title: (d) => `${fmtDate(d.filing_date)} · Form ${d.form}\nSold ${usd(d.sold)} of ${d.offering_amt ? usd(d.offering_amt) : "indefinite"}\nInvestors ${num(d.investors)}`})
      ]
    })));

  nodes.push(
    html`<h2>Raises</h2>`,
    html`<p>Newest first. The bar is the share of the target sold so far, as last reported. Stage is inferred from the raise's size.</p>`,
    folded(offs.slice().reverse(), raiseItem, "raises", "raises"));

  if (ppl.length) nodes.push(
    html`<h2>People on the filings</h2>`,
    html`<p>Names and roles as filed: executive officers, directors and promoters.</p>`,
    folded(ppl, personItem, "people", "people"));

  nodes.push(html`<details class="all-filings"><summary>Every filing (${num(fil.length)}): dates, amendments and links to each on the SEC site</summary>${
    Inputs.table(fil, {
      columns: ["filing_date", "form", "accession", "sold", "sold_increment", "investors", "link_method"],
      header: {filing_date: "Filed", form: "Form", accession: "Accession", sold: "Sold to date", sold_increment: "Change", investors: "Investors", link_method: "Linked by"},
      format: {
        accession: (a) => html`<a class="mono" target="_blank" rel="noopener" href="https://www.sec.gov/Archives/edgar/data/${Number(issuer.cik)}/${a.replaceAll("-", "")}/">${a}</a>`,
        sold: (v) => usd(v), sold_increment: (v, i, d) => v == null ? "—" : `${v >= 0 ? "+" : ""}${usd(v)}${d[i].is_correction ? " (correction)" : ""}`,
        link_method: (m) => (m ?? "").replaceAll("_", " ")
      },
      rows: 15
    })}</details>`);
  return nodes;
}
```

```js
if (cik) {
  profileRoot.replaceChildren(...(company ? profileView(company) : [
    html`<div class="kicker">Company lookup</div>`, html`<h1>No company with that CIK</h1>`,
    html`<p class="dek">CIK ${cik} is not a startup or venture fund in this data. <a href="./company">Search all companies</a>.</p>`]));
  profileRoot.removeAttribute("aria-busy");
}
```
