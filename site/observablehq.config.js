// Observable Framework config. Static build -> site/dist, deployable to GitHub Pages
// (links are relative, so it works under /<repo>/ without a base path).
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
  head: `<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,400;6..72,600&display=swap" rel="stylesheet">`,
  footer: `Built from SEC Form D filings (public record). Not investment advice. <a href="./methodology">Methodology and limitations</a>.`,
  toc: true,
  search: true
};
