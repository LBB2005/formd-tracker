---
title: Coverage gap
---

```js
import {num, pct, tile} from "./components/viz.js";
const reviews = FileAttachment("data/reviews.json").json();
const meta = FileAttachment("data/meta.json").json();
```

<div class="kicker">What Form D misses</div>

# The biggest AI companies mostly don't file Form D. Their SPVs do.

<p class="dek">Form D is a notice, not a disclosure. Raises that rely only on Section 4(a)(2), without the Rule 506 safe harbor, carry no federal filing requirement, and research on about 40,000 VC rounds found most were not accompanied by a Form D. So we checked well-known SF AI companies against EDGAR one by one.</p>

<div class="receipt"><span>Source <b>EDGAR</b> submissions API + registrant list</span><span>Checked ${meta.built_at.slice(0, 10)}</span><span>Reproduce: <code>uv run python -m pipeline.verify_edgar</code></span></div>

```js
const results = reviews.coverage_results ?? [];
const controls = new Set(["Figma", "Notion Labs", "Vercel", "Ramp Business Corp"]);
const ai = results.filter((d) => !controls.has(d.company));
const files = ai.filter((d) => d.status === "matches EDGAR" || d.status === "files Form D");
const absent = ai.filter((d) => d.status.startsWith("no Form D"));
const vehicles = ai.reduce((t, d) => t + Number(d.vehicles_named_after || 0), 0);
```

<div class="tiles">
  ${tile("Well-known AI companies checked", num(ai.length), "SF Bay Area; chosen for announced venture rounds")}
  ${tile("Never filed a Form D as a company", `${num(absent.length)} of ${num(ai.length)}`, `${pct(absent.length / ai.length)} invisible in this dataset`)}
  ${tile("SPVs named after them", num(vehicles), "vehicles on EDGAR buying their shares")}
  ${tile("Filings we hold vs EDGAR", "100%", `every Form D EDGAR lists for the ${num(files.length)} that file is in the data`)}
</div>

<div class="card">

### Which companies are visible, and which only through their SPVs?

<p class="answer">${`${absent.map((d) => d.company).slice(0, 6).join(", ")} and others have no Form D of their own, yet ${num(vehicles)} SPVs and series vehicles named after these companies did file. Investor demand for these companies is visible in the data; the companies themselves are not.`}</p>

```js
display(Inputs.table(ai, {
  rows: 30,
  columns: ["company", "status", "edgar_form_d", "vehicles_named_after", "registrants"],
  header: {company: "Company", status: "On EDGAR", edgar_form_d: "Form D filings", vehicles_named_after: "SPVs named after it", registrants: "Registrant (CIK)"},
  format: {status: (s) => (s === "matches EDGAR" ? "Files Form D (all in our data)" : s === "files Form D" ? "Files Form D" : "No Form D as a company"),
    edgar_form_d: (v) => (v === "" || v === "0" ? "—" : v), registrants: (r) => r || "—"},
  sort: "status"
}))
```

<p class="fineprint">"No Form D as a company" means no EDGAR registrant whose name starts with the company's name has filed one; a company filing under an unrelated legal name would be missed, so treat each "no" as a prompt to check, not proof. Companies that do file often file once for a round that, by announcements, was far larger or one of several. Perplexity and Replit each show one small filing. Controls (Figma, Notion, Vercel, Ramp) are not AI and are left out of the counts.</p>
</div>

## What this means for every chart on this site

- **Totals are a floor.** The largest AI rounds of 2023–2026 are mostly absent, so AI dollar shares understate AI's real weight, and the gap is largest at the top.
- **Trends beat levels.** Filing behaviour changes slowly, so quarter-over-quarter movement is more reliable than any single total.
- **SPV filings are a demand signal.** When dozens of vehicles form to buy one company's shares, that company is raising (or its shareholders are selling), even if it never files.
- **Early-stage coverage is weakest.** SAFE rounds are often filed late, once, or not at all, so seed counts are the least complete.

Sources: Hanley &amp; Yu, *The Case of the Missing Form D* ([ECGI](https://www.ecgi.global/content/strategic-regulatory-non-disclosure-case-missing-form-d)); [Tucker Ellis on 4(a)(2)](https://www.tuckerellis.com/lingua-negoti-blog/missed-form-d-filings-are-there-consequences-to-operating-in-stealth-mode/); [Latham on what Form D omits](https://www.lathamdrive.com/resources/insights/what-startups-should-consider-when-deciding-whether-to-file-a-form-d).
