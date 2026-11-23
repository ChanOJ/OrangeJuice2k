# OrangeJuice99

**Orange Juice (1999 version).** Orange Juice was a French-language online magazine about the
demoscene. This is the edition online from about September 1999, archived as of
2000-11-19: classic ASP (VBScript) on IIS with a SQL Server database,
news and articles, parties, groups, picture library, charts, oneliner and switchable skins.

Published as a **100 % static, read-only archive** on bunny.net at the hostname of
[`site.json`](site.json) (`orangejuice99.sauthier.family`), like
[OrangeJuice97](../OrangeJuice97) and [OrangeJuice98](../OrangeJuice98). Visitors can never write
anything: the CDN only serves files, every method except GET/HEAD/OPTIONS is refused at the edge,
and a small Edge Script redirects the old `*.asp?id=` URLs to the static pages.

## Layout

| Path | Content |
| --- | --- |
| `site/` | The generated static site, exactly as served (never edit by hand; **git-ignored**, about 530 MB): skin `tonic` at the root, the 12 other skins under `/skin/<name>/`, `/js/oj.js`, search indexes in `/idx/` |
| `hosting/404.html` | Custom 404 page (generated with `site/`), deployed next to it |
| `build-meta/` | Paths only: `url-map.json` (old URL -> new path, source of the Edge Script), `unlinked.tsv`, `dropped.tsv` |
| `site.json` | The only site-specific hosting settings: owner, repository, storage zone, pull zone, hostname, 404 page |
| `infra/` | OpenTofu: storage zone, pull zone, hostname, hardening edge rules, Edge Script (state encrypted, committed) |
| `infra/edge/redirects.js` | Edge Script (pull zone middleware) of the legacy redirects, **generated** from `build-meta/url-map.json` |
| `infra/secrets.env` | Proton Pass **references** to the secrets (vault `OldSites`), no secret values |
| `docs/data-model.md` | Template engine, tables and columns used, cut-off, URL scheme, removed features, redactions |
| `scripts/statify.py` | `private/` (ASP sources, SQLite, picture library) -> `site/` + `hosting/404.html` + `build-meta/` |
| `scripts/oj.js` | Source of `site/js/oj.js`: skin chooser, client-side search |
| `scripts/verify_build.py` | Checks `site/`: no ASP or forms, links and assets, charset, no e-mails, secrets or private IPs |
| `scripts/check_excludes.py` | Fails if `site/` or `hosting/` holds a path of `scripts/exclude-globs.txt` |
| `scripts/exclude-globs.txt` | What must never be published |
| `scripts/gen_edge_script.py` | `build-meta/url-map.json` -> `infra/edge/redirects.js` (`--check`: fails if stale) |
| `scripts/test_edge_script.mjs` | Node test of the redirects: every `url-map.json` entry, edge cases, targets present in `site/` |
| `scripts/publish.sh` | The one local entry point: statify, edge script check, deploy (with the checks), smoke test |
| `scripts/deploy.py` | Sync `site/` + `hosting/404.html` to the storage zone (changed files only, parallel), purge the CDN |
| `scripts/smoke_test.py` | Check the public site: pages of each kind and skin, legacy redirects, POST refused, headers, charset, 404 |
| `scripts/bunny.py` | bunny.net API helpers shared by the scripts |
| `scripts/bootstrap.sh` | One-time setup (`new` for this site, `github` for the repository and secrets) |
| `.github/workflows/` | `infra.yml` (plan on PR, manual apply), `smoke.yml` (weekly smoke test); no deploy workflow |
| `.claude/launch.json` | Local preview server (`site`) |
| `private/` | Raw material, **git-ignored, never published** (see below) |
| `scripts/mdf/` | Pure-Python reader for SQL Server 2000 `.MDF` files (schema, rows, text/image LOBs, procedure text) and the MDF to SQLite extractor |

`private/` (the only copy outside the NAS, keep it):

| Path | Content |
| --- | --- |
| `private/site-raw/` | The original ASP site as found (sources, skins, pictures). Holds credentials and personal data |
| `private/db/` | `OJUICE_Data.MDF` + `.LDF` (SQL Server 2000, state of 2006-09-07) and the `.BAK.gz` backup of the same day, with `SHA256SUMS` |
| `private/media/piclib/` | Picture library images (1645 files), missing from `site-raw/pictures/piclib` |
| `private/notes/` | Research reports: page inventory and schema, security audit, Wayback coverage, OJ versions, MDF schema dumps |

DNS is not managed here: the hostname is a CNAME to `orangejuice99.b-cdn.net` at Infomaniak.

Full list of the sites: https://chanoj.github.io/

## What was changed from the original

The ASP pages were rendered once from their skin templates and the database; the full list is in
[`docs/data-model.md`](docs/data-model.md):

- News before the v3 launch (2000-11-20) only; one static file per page, old `*.asp?query` URLs
  answered by a 301 (section 5 of the data model, "URL scheme").
- Forms, counters, admin, oneliner posts, votes and skin cookies removed (an "archived,
  read-only" notice replaces them); searches are client-side (`/js/oj.js`, `/idx/*.json`).
- The 13 skins are all rendered (tonic at the root, the others under `/skin/<name>/`), with a
  JavaScript skin chooser; new visitors always get tonic (no 2000 skin of the day).
- Personal data redacted as in OrangeJuice98 (`nick@????`, `dd/mm/????`, IPs as `????`);
  sources, credentials and private pictures never published (`scripts/exclude-globs.txt`).

## Extracting the database

`scripts/mdf/extract_sqlite.py` copies every user table of the MDF (all live rows, original
table and column names, text/ntext/image values) into a SQLite file, plus `_procedures`
(procedure and view text), `_columns`, `_indexes`, `_extract_info` and `_extract_tables`
(source sha256, expected vs inserted rows per table) and `_decode_errors`. Pure Python 3
standard library, about 10 s. The output holds personal data and stays in `private/`:

```bash
python3 -I scripts/mdf/extract_sqlite.py private/db/OJUICE_Data.MDF private/db/ojuice.sqlite
```

## Regenerating `site/`

`site/` is generated: never edit it by hand, fix `scripts/statify.py` and regenerate. It reads
`private/` only (the SQLite file must exist, see above), empties `site/` and writes the same output
on every run. Render, then check:

```bash
python3 -I scripts/statify.py
```

```bash
python3 -I scripts/verify_build.py
```

```bash
python3 -I scripts/check_excludes.py
```

A full build renders the 13 skins (about 3 minutes, 49 000 pages, 530 MB). To render only
some skins while working (tonic is always rendered; never deploy such a partial build: the
other skins would be deleted from the storage zone, which the deletion guard stops):

```bash
python3 -I scripts/statify.py --skins pl,poi
```

When `build-meta/url-map.json` changes, regenerate and test the Edge Script, then commit it
(the infra workflow fails on a stale script, and `tofu apply` publishes it):

```bash
python3 -I scripts/gen_edge_script.py
```

```bash
node scripts/test_edge_script.mjs
```

`.DS_Store` files (recreated by macOS) are ignored by all scripts and never uploaded. Local
preview (the `site` entry of `.claude/launch.json`):

```bash
python3 -m http.server 3738 --directory site
```

## Hosting (bunny.net, `infra/main.tf`)

- Edge rule 1: any method other than GET, HEAD, OPTIONS is blocked at the edge, before the
  origin (bunny.net answers 403; edge rules cannot return 405). `block_post_requests` is also on.
- Edge rule 2: `Content-Security-Policy`, `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer`, HSTS, `Permissions-Policy`.
  COOP/CORP `same-origin`.
- CSP: `default-src 'self'`; scripts and styles same-origin plus `'unsafe-inline'` (every page
  loads `/js/oj.js`, and the 2000 HTML keeps inline `<script>` blocks, `on*` handlers and
  `javascript:` popup links that no hash or nonce can cover); no `'unsafe-eval'`, nothing from
  another origin; `connect-src 'self'` (search indexes); `img-src 'self'` (third-party images
  were removed by the build); `object-src 'self'` (the Flash intro `/ojuicefinal.swf` only);
  frames same-origin; `form-action 'none'`; `base-uri 'none'`; no workers or manifests.
- Edge rule 3: `Content-Type: text/html; charset=iso-8859-1` for `.htm/.html` (the pages are
  ISO-8859-1) and browser cache 1 hour.
- Edge rule 4: `zip` and the old picture formats no browser shows (`iff lbm pcx tga`):
  `application/octet-stream` + `Content-Disposition: attachment`.
- Edge rule 5: browser cache 7 days for images, the Flash intro and archives; CDN cache 30 days,
  purged on each deployment. The cache key keeps the query string (`cache_vary = querystring`).
- Cookies are stripped before the origin; TLS "Compatible"; forced HTTPS. Search engines are
  allowed (no `X-Robots-Tag`, no `robots.txt`).
- Custom 404: `hosting/404.html` (`not_found_page` in `site.json`), deployed as `/404.html`.

### Legacy redirects (Edge Script)

Old links to `www.ojuice.net` carry query strings (`/view_article.asp?id=1111`,
`/index.asp?p=2`, `/groups/index.asp?l=a`, `/handles/handle.asp?id=`, `/piclib/display.asp?id=`...).
They are answered by `infra/edge/redirects.js`, a **middleware Edge Script** attached to the pull
zone (`bunnynet_compute_script` + `origin.middleware_script`, managed by OpenTofu):

- Generated from `build-meta/url-map.json` (3901 old URLs): numbered URLs whose target only
  differs by the number become 6 rules with the set of known numbers, the other 266 stay exact
  (20 KB). Paths and parameter names are case-insensitive, empty parameters are ignored, the
  parameter order does not matter.
- Known old URL -> `301` to the static page (cached one day by browsers).
  `gonlink.asp?u=` resolves only `ojuice.net` targets (no open redirect). A known script with
  an unknown query goes to its query-less page (`/index.asp?x=1` -> `/index.html`); other `*.asp`
  under `/admin/`, `/handles/`, `/search/`, `/wap/` -> `/archived.html` (removed features).
- Any other `*.asp`, or a number not in the archive (`/view_article.asp?id=99999`: news after
  the cut-off, people not published) -> **404 with `/404.html`** rather than a redirect to the
  home page: crawlers and visitors see that the page is not archived (no soft 404). The script
  asks the storage zone for a path that never exists, so the status and page are the origin's.
- Static paths pass through untouched (fast path: no `.asp`, no query string), and the script
  only runs on cache misses.

Edge rules were the alternative: they can match a query string but cannot copy a parameter
into the target path, and accept 5 patterns per trigger, so ~4000 URLs would need hundreds of
rules. One script is smaller, testable (`scripts/test_edge_script.mjs`) and versioned.

## Deploying

`site/` is generated on the Mac (it needs `private/`) and is not in the repository, so the
**website is deployed from the Mac**, never by GitHub Actions. One command builds, checks,
deploys and tests (Proton Pass CLI logged in):

```bash
scripts/publish.sh
```

It runs `statify.py`, checks that the Edge Script matches `url-map.json`, then
`scripts/deploy.py`, which **refuses to deploy unless `verify_build.py` and `check_excludes.py`
pass** on this exact build (it runs them, about 90 s, and keeps a stamp in `tmp/` so a re-run of
the same build skips them), uploads only new or changed files (SHA-256 against the remote
listing, 24 in parallel), deletes removed ones (guarded against mass deletion), purges the CDN;
then `smoke_test.py --sample 300`. Interrupted or failed uploads: run it again, only what is
missing is sent. Dry run, or deploy the existing `site/` without rebuilding:

```bash
scripts/publish.sh --dry-run
```

```bash
scripts/publish.sh --no-build
```

The steps one by one:

```bash
pass-cli run --env-file infra/secrets.env -- python3 -I scripts/deploy.py --dry-run
```

```bash
pass-cli run --env-file infra/secrets.env -- python3 -I scripts/deploy.py
```

```bash
python3 -I scripts/smoke_test.py --sample 300
```

Without network or secret (checks, local manifest, unit test of the diff logic):

```bash
python3 -I scripts/deploy.py --local-check
```

```bash
python3 -I scripts/deploy.py --self-test
```

- **Infra**: pull requests touching `infra/`, `site.json` or `build-meta/url-map.json` show the
  plan (after checking that the Edge Script is up to date and passes its test); apply is a
  manual run of *Infra: bunny.net* (action `apply`, from `main`), which commits the encrypted
  state and runs the smoke test (untick it for the first apply, before the first deploy).
- **Monitoring**: *Monitoring: weekly smoke test* checks the public site every Monday (status,
  headers, redirects; pages are compared with `site/` only locally).

GitHub secrets: `BUNNY_API_KEY` (bunny.net account API key) and `TF_VAR_STATE_PASSPHRASE`
(state encryption), both from Proton Pass vault `OldSites`. Local plan:

```bash
cd infra && pass-cli run --env-file secrets.env -- tofu plan
```

## First setup (to do)

1. Validate offline and create the state passphrase item `OrangeJuice2k-tofu-state-passphrase`
   in Proton Pass (generated, never shown; the item kept its original name after the
   repository was renamed from OrangeJuice2k to OrangeJuice99, `infra/secrets.env` points to it):

   ```bash
   scripts/bootstrap.sh new OrangeJuice99 orangejuice99 orangejuice99 orangejuice99.sauthier.family
   ```

2. Initialise git and commit on a branch, then create the GitHub repository
   `ChanOJ/OrangeJuice99` and its secrets (also adds the `origin` remote):

   ```bash
   scripts/bootstrap.sh github
   ```

3. Push the branch, open the PR (the plan runs), merge.
4. Run the *Infra: bunny.net* workflow from `main` with action `apply` and **smoke test unticked**:
   it creates the storage zone, the Edge Script, the pull zone, the hostname and the edge rules,
   and commits the encrypted state.
5. At Infomaniak (Manager > Domaines > sauthier.family > Zone DNS), add a CNAME
   `orangejuice99` -> `orangejuice99.b-cdn.net`. The certificate is issued once DNS answers.
6. Pull the state commit, then deploy from the Mac (about 51 600 files, 530 MB; the smoke test
   runs at the end):

   ```bash
   git pull
   ```

   ```bash
   scripts/publish.sh --no-build
   ```

## Manual steps in the bunny.net dashboard (only if the apply cannot do it)

If the apply rejects an edge rule, the 404 path or the Edge Script, set it by hand:

1. CDN > pull zone `orangejuice99` > *Edge rules* > *Add Edge Rule*: condition *Request Method*
   is none of `GET`, `HEAD`, `OPTIONS` -> action *Block Request*.
2. *Edge rules* > *Add Edge Rule*: condition *URL* `*` -> one *Set Response Header* action per
   header listed above (values in `infra/main.tf`, `local.csp` and the rule 2 block).
3. *Edge rules*: condition *URL Extension* `htm`, `html` -> *Set Response Header*
   `Content-Type: text/html; charset=iso-8859-1`.
4. Storage > storage zone `orangejuice99` > *Error pages*: set the 404 file to `/404.html`.
5. Edge Scripting > *Add Script* > *Middleware*, name `orangejuice99-legacy-redirects`, paste
   `infra/edge/redirects.js`, *Publish*; link it to the pull zone `orangejuice99`. Then CDN >
   pull zone > *Caching* > *Vary cache*: *Query string* on.
