// Test of the legacy redirects of infra/edge/redirects.js (Node >= 18, no dependency):
//   node scripts/test_edge_script.mjs
// Loads the pure resolve() part of the script (between the BEGIN/END resolve markers), then checks
// every old URL of build-meta/url-map.json, a few edge cases, and (when site/ exists) that every
// redirect target is a file of the build.
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(path.join(repo, "infra/edge/redirects.js"), "utf8");
const m = /\/\/ BEGIN resolve[^\n]*\n([\s\S]*)\/\/ END resolve/.exec(src);
if (!m) throw new Error("BEGIN/END resolve markers not found");
const resolve = new Function(m[1] + "\nreturn resolve;")();
const map = JSON.parse(readFileSync(path.join(repo, "build-meta/url-map.json"), "utf8"));
const site = path.join(repo, "site");
const hasSite = existsSync(site);

let failures = 0;
const fail = (msg) => { failures++; if (failures <= 30) console.log("FAIL " + msg); };

function run(old) {
  const u = new URL(old, "https://example.test");
  return resolve(u.pathname, u.search);
}

function expect(old, want) {
  const got = run(old);
  const ok = want === null ? got === null
    : want === 404 ? got && got.status === 404
    : got && got.status === 301 && got.location === want;
  if (!ok) fail(`${old}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// 1. Every exact entry of the url-map. "/" and "/index.html" are served as they are.
let n = 0;
for (const [old, want] of Object.entries(map.urls)) {
  n++;
  const u = new URL(old, "https://example.test");
  const passThrough = !u.search && !u.pathname.toLowerCase().endsWith(".asp") &&
    (u.pathname === "/" || u.pathname.toLowerCase() === want.toLowerCase());
  expect(old, passThrough ? null : want);
  if (hasSite && !existsSync(path.join(site, want))) fail(`${old}: target ${want} missing from site/`);
}

// 2. Edge cases.
const cases = [
  // static paths are never touched (fast path), even with a query string
  ["/index.html", null], ["/article/1111.html", null], ["/js/oj.js", null],
  ["/skin/pl/index.html", null], ["/search.html?t=news&q=party", null], ["/js/oj.js?v=2", null],
  ["/handles/pictures/1.jpg", null], ["/skins/tonic/pictures/logo.gif", null], ["/", null],
  // case-insensitive paths and parameter names, empty and extra parameters, parameter order
  ["/VIEW_ARTICLE.ASP?ID=1111", "/article/1111.html"],
  ["/view_article.asp?id=1111&foo=bar", "/article/1111.html"],
  ["/view_article.asp?id=01111", "/article/1111.html"],
  ["/piclib/index.asp?typeimage=photos&p=2", "/piclib/photos-2.html"],
  ["/piclib/index.asp?p=2&typeimage=photos&event=&rank=", "/piclib/photos-2.html"],
  ["/index.asp?p=2", "/news/page-2.html"], ["/index.asp?p=1", "/index.html"],
  ["/groups/index.asp?l=%23", "/groups/0-9.html"], ["/groups/index.asp?l=Q", "/groups/q.html"],
  // unknown numbers -> 404; known script with an unknown query -> its query-less page
  ["/view_article.asp?id=99999", 404], ["/view_article.asp?id=abc", 404],
  ["/view_article.asp", 404], ["/index.asp?p=9999", 404],
  ["/handles/handle.asp?id=99999999", 404], ["/piclib/display.asp?id=999999", 404],
  ["/index.asp?foo=1", "/index.html"], ["/charts/results.asp?c=3", "/charts/index.html"],
  // removed features -> /archived.html, other *.asp -> 404
  ["/admin/index.asp", "/archived.html"], ["/wap/index.asp?x=1", "/archived.html"],
  ["/handles/sendpass.asp", "/archived.html"], ["/search/index.asp?x=1", "/archived.html"],
  ["/nosuchpage.asp", 404], ["/foo/bar.asp?id=1", 404], ["/global.asa", null],
  // gonlink: internal targets resolved, external ones refused (no open redirect)
  ["/gonlink.asp?id=1&u=http://www.ojuice.net/view_article.asp?id=1111", "/article/1111.html"],
  ["/gonlink.asp?u=%2Fview_article.asp%3Fid%3D1111", "/article/1111.html"],
  ["/gonlink.asp?u=http%3A%2F%2Fojuice.net%2Ffaq.html", "/faq.html"],
  ["/gonlink.asp?u=http://evil.example/", 404], ["/gonlink.asp?u=//evil.example/x", 404],
  ["/gonlink.asp?u=javascript:alert(1)", 404], ["/gonlink.asp", 404],
  // old folders
  ["/search/", "/piclib/index.html"], ["/groups/", "/groups/a.html"], ["/charts/", "/charts/index.html"],
];
for (const [old, want] of cases) expect(old, want);

// 3. Numbered rules cover every id of the url-map patterns (sample of synthetic misses).
for (const r of [["/handles/handle.asp?id=", "/people/"], ["/piclib/display.asp?id=", "/piclib/"]]) {
  for (const old of Object.keys(map.urls).filter((k) => k.startsWith(r[0])).slice(0, 5)) {
    const got = run(old);
    if (!got || !got.location.startsWith(r[1])) fail(`${old}: ${JSON.stringify(got)}`);
  }
}

console.log(`${n} url-map entries and ${cases.length} edge cases checked` +
  (hasSite ? ", targets present in site/" : " (site/ absent: targets not checked)") +
  `: ${failures ? failures + " failure(s)" : "OK"}`);
process.exit(failures ? 1 : 0);
