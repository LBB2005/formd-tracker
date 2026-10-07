// Shared chart helpers. Marks follow the dataviz spec: 2px lines, thin bars separated by
// a 2px surface-coloured gap, hairline solid grid, tooltips on by default, colours by role.
import * as Plot from "npm:@observablehq/plot";
import {html} from "npm:htl";

export const C = {
  s1: "var(--series-1)",
  s2: "var(--series-2)",
  s3: "var(--series-3)",
  neutral: "var(--neutral)",
  ink: "var(--ink)",
  ink2: "var(--ink-2)",
  muted: "var(--ink-muted)",
  surface: "var(--surface-1)",
  ordinal: ["var(--ord-1)", "var(--ord-2)", "var(--ord-3)", "var(--ord-4)"]
};

export const STAGES = ["seed", "series_a", "series_b", "growth"];
export const STAGE_LABEL = {seed: "Seed (<$3M)", series_a: "Series A ($3–15M)", series_b: "Series B ($15–50M)", growth: "Growth ($50M+)", unknown: "Unknown"};
// Metros come from config/geo.yaml via data/meta.json, so adding one needs no site change.
// Scope choices: "All US", "10 tracked metros", or one metro by label.
export function metroScope(meta) {
  const labels = Object.fromEntries(meta.metros.map((m) => [m.key, m.label]));
  const tracked = `${meta.metros.length} tracked metros`;
  const options = ["All US", tracked, ...meta.metros.map((m) => m.label)];
  const keyOf = (choice) => meta.metros.find((m) => m.label === choice)?.key;
  const sql = (choice, col = "metro") =>
    choice === "All US" ? "true" : choice === tracked ? `${col} is not null` : `${col} = '${keyOf(choice)}'`;
  const phrase = (choice) =>
    choice === "All US" ? "across the US" : choice === tracked ? `across the ${tracked}` : `in ${choice}`;
  // Share of startups in the selection whose homepage has been checked for AI evidence.
  const coverage = (choice) => {
    const cov = meta.ai_coverage ?? {};
    if (choice === "All US" || choice === tracked) {
      const vals = Object.entries(cov).filter(([k]) => choice === "All US" || k !== "rest_of_us").map(([, v]) => v);
      return vals.length ? Math.min(...vals) : null;
    }
    return cov[keyOf(choice)] ?? null;
  };
  return {labels, options, sql, phrase, keyOf, tracked, coverage};
}

// $1,234,567 -> "$1.2M"; compact, proportional.
export function usd(x, digits = 1) {
  if (x == null || Number.isNaN(x)) return "—";
  const a = Math.abs(x);
  if (a >= 1e9) return `$${(x / 1e9).toFixed(digits)}B`;
  if (a >= 1e6) return `$${(x / 1e6).toFixed(digits)}M`;
  if (a >= 1e3) return `$${(x / 1e3).toFixed(0)}K`;
  return `$${Math.round(x)}`;
}
export const pct = (x, digits = 0) => (x == null || Number.isNaN(x) ? "—" : `${(100 * x).toFixed(digits)}%`);
export const num = (x) => (x == null ? "—" : Number(x).toLocaleString("en-US"));
export const day = (d) => (d == null ? "—" : new Date(d).toISOString().slice(0, 10));

// Arrow -> plain objects; BigInt -> Number so Plot and formatters behave.
export function rows(table) {
  return Array.from(table, (r) => {
    const o = {};
    for (const [k, v] of Object.entries(r.toJSON ? r.toJSON() : r)) o[k] = typeof v === "bigint" ? Number(v) : v;
    return o;
  });
}

export function key(items) {
  return html`<div>${items.map(([label, color]) => html`<span class="key"><i style="background:${color}"></i>${label}</span>`)}</div>`;
}

export function tile(label, value, context = "") {
  const isText = typeof value === "string" && !/^[$\d—]/.test(value);
  return html`<div class="tile"><div class="label">${label}</div><div class="${isText ? "value text" : "value"}">${value}</div><div class="context">${context}</div></div>`;
}

// For band scales of "2024 Q3" labels: show the year at each Q1 only.
export const yearTicks = (d) => (typeof d === "string" && / Q\d$/.test(d) ? (d.endsWith("Q1") ? d.slice(0, 4) : "") : d);

const base = (width, extra = {}) => ({
  width,
  height: 300,
  marginLeft: 56,
  marginBottom: 34,
  style: {fontFamily: "var(--sans)", fontSize: "12px", color: "var(--ink-2)", background: "transparent", overflow: "visible"},
  x: {label: null, tickSize: 0},
  y: {grid: true, label: null, tickSize: 0},
  ...extra
});

// Stacked columns by period. series: [{key, label, color}] bottom-to-top.
export function stackedColumns(data, {x, series, width, yFormat = usd, xLabel = null, height = 300}) {
  const long = data.flatMap((d) => series.map((s) => ({x: d[x], series: s.label, value: d[s.key] ?? 0, total: series.reduce((t, ss) => t + (d[ss.key] ?? 0), 0)})));
  return Plot.plot(base(width, {
    height,
    x: {label: xLabel, tickSize: 0, type: "band", tickFormat: yearTicks},
    y: {grid: true, label: null, tickSize: 0, tickFormat: (v) => yFormat(v)},
    color: {domain: series.map((s) => s.label), range: series.map((s) => s.color)},
    marks: [
      Plot.barY(long, {x: "x", y: "value", fill: "series", order: series.map((s) => s.label),
        stroke: C.surface, strokeWidth: 2, inset: 0.5,
        tip: true, title: (d) => `${d.x}\n${d.series}: ${yFormat(d.value)}\nTotal: ${yFormat(d.total)}`}),
      Plot.ruleY([0], {stroke: "var(--baseline)"})
    ]
  }));
}

// Lines over time on one shared axis. series: [{key, label, color}].
export function lines(data, {x, series, width, yFormat = (v) => v, yDomain, height = 280, xType = "point"}) {
  const long = data.flatMap((d) => series.filter((s) => d[s.key] != null).map((s) => ({x: d[x], series: s.label, value: d[s.key]})));
  const last = series.map((s) => long.filter((d) => d.series === s.label).at(-1)).filter(Boolean);
  // Thin point-scale labels so they never collide: keep every k-th that fits ~44px.
  const domain = [...new Set(data.map((d) => d[x]))];
  const k = Math.max(1, Math.ceil((domain.length * 44) / Math.max(width - 80, 1)));
  const thin = (d, i) => (i % k === 0 ? yearTicks(d) : "");
  return Plot.plot(base(width, {
    height,
    marginRight: 16,
    x: {label: null, tickSize: 0, type: xType, tickFormat: (d) => thin(d, domain.indexOf(d))},
    y: {grid: true, label: null, tickSize: 0, tickFormat: (v) => yFormat(v), domain: yDomain},
    color: {domain: series.map((s) => s.label), range: series.map((s) => s.color)},
    marks: [
      Plot.ruleY([0], {stroke: "var(--baseline)"}),
      Plot.line(long, {x: "x", y: "value", stroke: "series", strokeWidth: 2, curve: "monotone-x"}),
      // End dots only: converging end labels collide, so identity stays with the legend.
      Plot.dot(last, {x: "x", y: "value", fill: "series", r: 4, stroke: C.surface, strokeWidth: 2}),
      Plot.tip(long, Plot.pointerX({x: "x", y: "value", title: (d) => `${d.x}\n${d.series}: ${yFormat(d.value)}`}))
    ]
  }));
}

// Quarter label "2024 Q3" from a date.
export function quarter(d) {
  const t = new Date(d);
  return `${t.getUTCFullYear()} Q${Math.floor(t.getUTCMonth() / 3) + 1}`;
}

export function companyLink(cik, name) {
  return html`<a href="./company?cik=${encodeURIComponent(cik)}">${name}</a>`;
}
export function secLink(url, label = "SEC") {
  return html`<a href="${url}" target="_blank" rel="noopener" class="mono">${label} ↗</a>`;
}
