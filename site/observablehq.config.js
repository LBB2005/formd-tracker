// Observable Framework config. Static build -> site/dist, deployable to GitHub Pages
// (links are relative, so it works under /<repo>/ without a base path).
const SITE_URL = "https://lbb2005.github.io/formd-tracker/";
const REPO_URL = "https://github.com/LBB2005/formd-tracker";
const DESCRIPTION = "Every US startup raise and venture fund filed with the SEC on Form D since 2019: who is raising, where, how much of it is AI, and how many new VC funds are really SPVs.";

export default {
  title: "Form D Tracker",
  root: "src",
  style: "style.css",
  pages: [
    {name: "Findings", path: "/findings"},
    {name: "AI raises", path: "/ai-raises"},
    {name: "Company lookup", path: "/company"},
    {name: "New funds", path: "/funds"},
    {name: "Fund managers", path: "/manager"},
    {name: "Raise velocity", path: "/velocity"},
    {name: "Map", path: "/map"},
    {name: "Coverage gap", path: "/coverage"},
    {name: "Methodology", path: "/methodology"},
    {name: "Download the data", path: "/downloads"}
  ],
  // Sharing metadata: the page's own title and URL, one site description. No og:image
  // until there is a real one in the repo; twitter:card "summary" is a text card.
  head: ({title, path}) => {
    const name = title && title !== "Overview" ? `${title} | Form D Tracker` : "Form D Tracker";
    const url = path === "/index" || path === "/" ? SITE_URL : SITE_URL + path.replace(/^\//, "");
    return `<meta name="description" content="${DESCRIPTION}">
<meta property="og:title" content="${name}">
<meta property="og:description" content="${DESCRIPTION}">
<meta property="og:type" content="website">
<meta property="og:url" content="${url}">
<meta property="og:site_name" content="Form D Tracker">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="${name}">
<meta name="twitter:description" content="${DESCRIPTION}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,400;6..72,600&display=swap" rel="stylesheet">`;
  },
  footer: `Built from SEC Form D filings (public record). Not investment advice. <a href="./methodology">Methodology and limitations</a> · <a href="${REPO_URL}">Source code on GitHub</a>.`,
  toc: true,
  search: true
};
