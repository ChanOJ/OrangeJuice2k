# Orange Juice (1999 version): data model and static rendering

The site was classic ASP (VBScript) on IIS with a SQL Server 2000 database. This file
describes, in public-safe terms, what `scripts/statify.py` reads and how it renders the static
archive. No secret, personal data or IP address is reproduced here.

Inputs (all in the git-ignored `private/`, read-only):

| Input | Content |
| --- | --- |
| `private/site-raw/` | ASP pages, `_inc/` engine, `skins/` templates and images, `_data/` text files |
| `private/db/ojuice.sqlite` | Full extraction of the SQL Server database (state of 2006-09-07), incl. `_procedures` |
| `private/media/piclib/` | Picture library files (`<id>.jpg`, `tn/` thumbnails, `ori/` original formats) |

## 1. Template engine (`_inc/functions.inc`, `_inc/parameters.inc`)

- `OpenHTML_(page)` loads `skins/<skin>/<page>`, else `skins/tonic/<page>` (`PagePath_`), then
  replaces `$$TOP$$`, `$$BOTTOM$$` (or `bottom_index.inc` on pages with a page index),
  `$$KEYWORDS$$`, `$$LINKS$$`, `$$OJ_DOMAIN$$`, `$$OJMAIL$$`, `$$LINKDEMO$$`, `$$SEARCHINC$$`,
  `$$SIZEnnn$$` (input widths, browser-dependent; the non-IE values are used) and `$$CNXWEB$$`.
- Pages then fill data macros and cut `$$BEGIN$$..$$END$$` (or `$$BEGIN_O$$`, `$$BEGIN2$$`)
  blocks, repeated once per database row.
- `ChangePicturesLocation_` rewrites `src="..."` / `background="..."` (exact case, double quotes,
  not `http://`) resolved against the page folder to `/skins/<skin>/...`, else
  `/skins/tonic/...`, else the root file (`PageURL_`).
- Unknown URLs went to `404.asp`, which served `OpenHTML_(url)`: this is how the static pages
  (FAQ, links, credits, interviews, Nectarine playlists...) were delivered.
- Skin choice: cookie `OJ_SKIN`, else a per-IP list, else `(Day(Now) MOD 4) + 1` over
  tonic / pl / marblemad / poi, with tonic replaced by `tonic_snow` (January to March) or
  `tonic_xmas` (from 15 December). See §9 for the archive.

`statify.py` reimplements these functions (`Engine`), once per skin.

## 2. Tables and columns used

Only these columns are ever selected. Password, IP, host name, user agent, login and cookie
columns and tables (`LOGININFO`, `COOKIES`, `LOGGED`) are never read for output; of `PEOPLE.email`
only the part before `@` is output.

| Table | Columns read | Used for |
| --- | --- | --- |
| `NEWS` | id, dt, id_people, title, url, target, smalltxt, bigtxt, active, isarticle, category, clicks | news pages, articles, top writers |
| `CATEGORIES` | id, catname, urlcat | news icon and link |
| `PEOPLE` | id, dt_crea, nick, firstname, lastname, email (local part only), sex, birthmd (never birthyear), uin, pic_ext, letter, idgroup1, idgroup2, idcountry, idcomputer, url, status, scene, irc_chans, irc_net, parties, pref_party, pref_demo, pref_group, pref_coder, pref_zik, pref_gfx, pref_tart, pref_movies, pref_musics, fun1, fun2, isans, isasc, iscod, isgfx, isray, isorg, isppe, isswp, issys, iszik | people sheets and lists, author / voter names (with `_inc/nicklist.inc`) |
| `COMPUTERS` | id, computer | people sheets |
| `LINKS` | id, name | full group names behind `PEOPLE.idgroup1/2` (v3 group table) |
| `ONELINER` | id, dt, sentence | the 20 last oneliners, popups |
| `GROUPS` | id, name, url, letter, active, dt, id_country | groups list |
| `COUNTRIES` | id, country, flag | flags |
| `PARTIES` | id, id_country, dt_begin, dt_end, name, place, price, url, canceled, active | parties lists |
| `PARTIES_COMING` | id_people, id_party | "people who are going to this party" |
| `PICTURES` | id, isactive, dtadded, isphoto, filename, filenameori, width, height, nbcolors, picmonth, picyear, who, event, place, rank, author, description, legend | picture library |
| `CHARTS` | id_people, id_chart, id_item, rank | charts results and voters |
| `BANNERS` | id, url | `redirban.asp?id=` links resolved to the advertiser URL |

Other inputs: `_data/charts/charts.txt` and `chart_<n>.txt` (chart names and item labels),
`_inc/nicklist.inc` (2000 "nick / group" labels), `_inc/keywords.txt` (meta keywords).
`_data/skins_ip.txt` and the admin page are read only to mask their IP addresses.

**Era mismatch.** The database is the 2006 state shared with Orange Juice v3. `PEOPLE` was
recreated in 2005 without the `groups` text column that the 2000 procedures used
(`nick & Groups_(groups)`): "nick / group" labels come from `_inc/nicklist.inc`, written by the
2000 site with `GroupsShort_` (group cut at 16 characters, `First^...` for several groups). A cut
name is restored when one of the person's groups in `LINKS` (`idgroup1/2`) or a unique `GROUPS`
name starts with the cut prefix (`Tokyo Dawn Recor...` -> `Tokyo Dawn Records`, 37 cases); the
other 16-character cuts (free text such as `Dcs Miasmah Noct...`) stay as they are. Other renamed
2005 columns: `idcountry`, `idcomputer`, `url` (homepage), `pic_ext` (photo), the `functions`
text (`COD/GFX/ZIK`) became booleans `isans`..`iszik` (rebuilt in the `handles/edit.asp` order;
`isweb`, added by v3, is ignored), `interview` is empty (interview pages are detected from the
files, as `PageURL_` did).

## 3. Cut-off: 2000-11-20

Orange Juice v3 went live on 2000-11-20; everything dated on or after that day is left out.

| Content | Rule | Result |
| --- | --- | --- |
| News | `active = 1 AND dt < '2000-11-20'` | 1174 news (1998-05-11 to 2000-11-18), 59 pages, 43 articles |
| Oneliner | 20 last with `dt < cut-off` (as `SELECT TOP 20 ... ORDER BY dt DESC`) | 20 messages of 2000-11-19 |
| Pictures | `isactive = 1 AND dtadded < cut-off` | 53 pictures + 729 photos (729 matches the Wayback capture of 2000-11-15) |
| Groups | `active = 1 AND dt < cut-off` | 1212 groups (the table was not used after 2000) |
| Parties | no creation date: `id <= 261` (see below); incoming / past split at 2000-11-19 23:59:59 | 14 incoming, 178 past |
| Party attendance | people of the 2000 nick list only | 192 pages |
| People | `PEOPLE.dt_crea < cut-off` (2460 rows, all in the 2000 nick list) | 2460 sheets, 27 letter lists, interviews, birthdays, 680 ID photos |
| Charts | votes carry no date: voters of the 2000 nick list only | 364 voters |
| Top news writers | news with `dt < cut-off` | |

Parties: `addparty.asp` inserted a news "This party will held in ..." with every new party;
the last one before the cut-off announces party 261 (2000-11-16), so ids above 261 belong to v3.
Click ratios (`clicks * 5 \ MAX(clicks)`) use the 2006 click counts, which kept growing after 2000:
they are approximate (18 of 20 equal on the Wayback page 2).

## 4. Ordering and paging (stored procedures in `_procedures`)

- `oj_listnews(page)`: count of active news, then 20 rows `ORDER BY news.dt DESC` through a
  scroll cursor. Ties on `dt` are broken by `id DESC` (SQL Server left them unspecified; the
  Wayback captures show a few swapped pairs). `index.asp` capped the page index at 23 pages;
  the archive renders and indexes all 59.
- `oj_listalphagroups` / `oj_listgroups(letter)`: distinct letters, then groups of one letter
  `ORDER BY name` (case-insensitive, as the SQL Server collation).
- `parties/index.asp`: incoming `ORDER BY dt_begin`, past `ORDER BY dt_begin DESC`.
- `piclib/index.asp` without search terms: `ORDER BY dtadded DESC` (ties: `id`), 10 per page;
  the page index cap of 17 is lifted.
- `oj_vote_results(chart)`: `sum(rank)` per item, items with at least 5 votes, share of the
  chart total; `oj_vote_voters`: voters by letter and nick.

## 5. URL scheme

| Old URL | Static path |
| --- | --- |
| `/`, `/index.asp`, `/index.asp?p=1` | `/index.html` |
| `/index.asp?p=N` | `/news/page-N.html` |
| `/view_article.asp?id=N` (and v3 `/N/article.htm`) | `/article/N.html` |
| `/oneliner_popup.asp?o=N` | `/oneliner/N.html` |
| `/groups/index.asp?l=X` | `/groups/x.html` (`#` = `/groups/0-9.html`); `/groups/` -> `a.html` |
| `/parties/`, `/parties/index.asp?past=1` | `/parties/index.html`, `/parties/past.html` |
| `/parties/coming.asp?p=N` | `/parties/coming-N.html` |
| `/piclib/index.asp?p=N&typeimage=pics|photos` | `/piclib/index.html`, `/piclib/pics-N.html`, `/piclib/photos.html`, `/piclib/photos-N.html` |
| `/piclib/display.asp?id=N` | `/piclib/N.html` |
| `/charts/`, `/charts/results.asp`, `/charts/voters.asp` | `/charts/index.html`, `/charts/voters.html` |
| `/nectarine/`, `http://nectarine.ojuice.net/` | `/nectarine/index.html` |
| static pages served by `404.asp` (`/faq.html`, `/links/links.html`, `/interviews/145_e.htm`...) | same path |
| `/handles/handle.asp?id=N`, v3 `/N/nick.htm` | `/people/N.html` |
| `/handles/list.asp?l=X` | `/people/x.html` (`#` = `0-9.html`), default `a.html` |
| `/handles/birthdates.asp` (the PEOPLE menu entry) | `/people/birthdays.html` |
| `/handles/pictures/<id>.<ext>` | same path (published people only) |
| `/handles/interviews.asp` | `/people/interviews.html` |
| `/search/` (DOWNLOAD menu, no query) | `/piclib/index.html` |
| removed features (see §6) | `/archived.html` |
| `/gonlink.asp?id=&u=URL`, `/redirban.asp?id=` | the target URL directly |

Images keep their path (`/skins/tonic/pictures/...`, `/pictures/piclib/<file>`, thumbnails in
`/pictures/piclib/tn/`); `ori/` originals are published at their original URL
`/pictures/piclib/<file>`. Internal links are root-relative; links to `www.ojuice.net` and
`ojuice.net` are rewritten. `build-meta/url-map.json` lists every old URL form seen in the
pages (3901 exact entries) plus wildcard patterns, for the Bunny Edge Script.

## 6. Removed or frozen

| Feature | Archive |
| --- | --- |
| Oneliner form, add news / article / party / group / picture, chart login and votes, party registration, profile edit, sendpass, admin | form removed (lists inside a form keep their content), links to `/archived.html` |
| Skin chooser (`skins.asp`: cookie or per-IP) | `/skins.html`, choice kept in the browser (§9) |
| News, picture, photo, people, group searches | client-side search (§10); the home box (`_inc/search.inc`) keeps its table and texts, with "Browse the 53 pictures" / "Browse the 729 photos" links under the picture fields |
| Production search (`ix_*` FTP servers) | "[ archived: search not available ]" |
| Birthdays page (`handles/birthdates.asp`, the PEOPLE menu entry) | `/people/birthdays.html`: "Today" (frozen date 19 Nov 2000) then every birthday of the year from 20 Nov, as `[26 Dec ????]` (the ASP showed the next 20); no ICQ badges |
| Sheet edit, sendpass | `/archived.html` |
| WAP (`wap/*.asp`, WML) | dropped (no WML browsers; same content as the news pages) |
| Click counters `gonlink.asp`, `redirban.asp`, `width.asp`, `ielog.asp` | direct links / removed |
| Banner exchange (`banner*.js`, `$$PUB$$`, `pictures/pubs/` missing) | removed |
| cnxweb visit beacon, `$$VISITS$$` counter | beacon removed; counter frozen at 1042 (last Wayback value, 2000-11-15) |
| Nectarine stream status (live SHOUTcast queries) | frozen, every stream and the hard-coded relays (live365.com, mycaster.com) shown "Down"; the "IP\|port" server column shows the relay host name; `.pls` playlists not published |
| Third-party images, scripts and iframes (webcams, AudioRealm ads, ICQ status) | removed; AudioRealm song-request links unwrapped |
| `ojuicefinal.swf` (`flash.html`) | kept with its markup |
| `parties_incoming.htm`, `parties_past.htm`, `rech.html` (1998 leftovers, not linked) | not published (part of the OrangeJuice98 archive) |

Links whose target never existed in the sources (e.g. `pictures/wallpapers/*`, `demos/*`)
become plain text and their images are dropped: list in `build-meta/unlinked.tsv`,
dropped elements in `build-meta/dropped.tsv`.

## 7. Redactions and exclusions

- E-mail addresses: `mailto:` links become their label, `local@domain` becomes `local@????`
  (as OrangeJuice98), everywhere including news, interviews and links pages.
- Oneliner popups: date kept, host name and browser shown as `????`; the IP was never shown.
- People sheets (owner decision, 2026-11-23) show nick, groups, real name, e-mail as `local@????`
  (text, no `mailto:`), sex, birth date as `dd/mm/????` (the year is never output, nor an age),
  country and flag, computer, functions, status, homepage, ICQ number (as text: the status badge
  came from the ICQ servers and is not shown, nor the online/offline legend), IRC, scene year,
  parties, preferences, the two "fun" answers, interview links and the ID photo. Hidden: French
  department (`[dep / number]` next to the country in 2000: a location detail of little value),
  password, logins, cookies, IPs (`LOGININFO`, `COOKIES`, `LOGGED` are never read).
- ID photos: `handles/pictures/<id>.<ext>` (path of the 2000 template) only for published people
  whose `pic_ext` file exists and is a GIF, JPEG or PNG (680 photos; size rule of
  `handle_inc.asp`: width max 240, small pictures doubled, height max 500); otherwise the
  template text "EDIT THE SHEET TO ADD AN ID PICTURE". `handles/pictures/bak/` and the photos of
  other ids are never copied; `check_excludes.py` and `verify_build.py` enforce it. The nick lists
  show the original "there is an ID photo" icon.
- `verify_build.py` reads birth years, e-mails and departments from the private database at run
  time: it fails on a published person's birth year next to their day/month, on their e-mail with
  its domain, on any real e-mail address anywhere, on their department as the 2000 sheet printed it,
  and on any ID photo that is not of a published person.
- IPv4: masked as `????` unless it is the host of a URL; the visitor and office IPs of the
  original site are masked everywhere.
- Never published (`scripts/exclude-globs.txt`, from `private/notes/security_report.md`):
  ASP / include sources, `global.asa`, `admin/`, `_data/`, `_inc/`, `*.bak`, `*.aspsav`,
  `.DS_Store` (ignored by all scripts, never uploaded), `pictures/photo_private/`,
  `handles/pictures/bak/`, debug pages, `archives/vncviewer/`,
  executables and the zips holding executables (`statify.py` also opens every zip it would
  publish and refuses executables), `*.pls`, `pictures/piclib/wait/`.

## 8. Output conventions

- Charset: sources are cp1252; pages are written as iso-8859-1 (characters outside latin-1,
  such as `’` or `€`, become numeric character references) and every page declares
  `charset=iso-8859-1`, like OrangeJuice98.
- Deterministic: sorted traversal, stable SQL ordering, no timestamps; `site/` is emptied first.
- Only assets referenced by a published page are copied (skin images with the skin fallback,
  root files, picture library files, `favicon.ico`).
- `hosting/404.html` is rendered from `skins/tonic/404.html` with bilingual text.
- Every page loads `/js/oj.js` (one static file, no inline script added, no cookie): skin
  chooser, home redirect to the chosen skin, search fields and results.
- Checks: `scripts/verify_build.py` (forms, macros, links, charset, e-mails, secrets and private
  IPs read at run time from `private/site-raw/`) and `scripts/check_excludes.py`.

## 9. Skins

- Rendered: the 13 folders of `skins/` that hold files: tonic, ak, danube, eggbird, mairsil,
  mairsilbeta, marblemad, pl, poi, sdk, tonic_snow, tonic_xmas, yes (`kano` and `pixol` are
  empty: not skins). Each skin is rendered completely with the original fallback rules
  (template missing -> tonic; `ChangePicturesLocation_` image fallback skin -> tonic -> root).
- `tonic` stays at the site root with unchanged URLs; every other skin is at
  `/skin/<name>/<same path>` and its pages link within that prefix. Images are shared
  (`/skins/<name>/...`, `/pictures/...`, picture library): nothing is duplicated but HTML.
- Size: about 33 to 44 MB of HTML per skin, 49 000 pages and about 530 MB of HTML in all (plus
  50 MB of pictures). Below the 1 GB limit set for the project, so no page type is restricted;
  a full build takes about 3 minutes. `statify.py --skins pl,poi` renders a subset (tonic is
  always rendered).
- Repairs over the 2000 rendering, applied to every skin: images that skin includes reference
  relative to the root (`pictures/x.gif`) are found from pages in sub-folders too (the ASP
  rendered them broken there); the `ak` skin's links to its author's disk
  (`../../Program Files/ojuice sdk/pl/SKINPL/...`) are made root-relative; `$$CPT$$` (old host
  counter) and stray `$$INDEX$$` are removed; a skin file that is really a data template under a
  static name (`yes/soon.html` is a news page) is replaced by the tonic page in the skin's frame;
  `charset=windows-1252` (yes) is declared `iso-8859-1` like the rest.
- `/skins.html` reproduces `skins.asp` / `skins.html`: every rendered skin with its preview
  (`skins/<name>.gif` when it exists) and authors (`skin.txt`, else the name; skins the 2000
  page hid are marked "not in the 2000 list"), and the popularity table frozen from the `SKINS`
  table (`oj_skins_usage`, hidden skins left out as in 2000). "USE THIS SKIN" stores the choice
  in `localStorage` (no cookie, wrapped in try/catch) and opens the page the visitor came from
  under the chosen skin; "none" clears it (back to tonic).
- Default skin: always `tonic`. The 2000 rotation (cookie, else per-IP list, else `Day MOD 4`
  over tonic/pl/marblemad/poi with snow and xmas variants) is not reproduced: an archive should
  look the same on every visit.
- Home page only (`/` and `/index.html` at the root): a visitor who chose a skin other than tonic
  is sent to `/skin/<name>/index.html`
  (`location.replace`). No other page redirects, so direct links and crawlers see the URL they
  asked for.

## 10. Client-side search

- `statify.py` writes four indexes (compact JSON arrays, ASCII) with fields already shown on
  public pages, text stripped of tags and redacted like the pages:
  `/idx/news.json` (1174 news: id, date, title, text cut at 300 characters, author, category,
  page, link; about 306 KB), `/idx/pictures.json` (782: id, picture or photo, legend,
  description, author, event, who, place, year; 65 KB), `/idx/people.json` (2460: id,
  "nick / group"; 64 KB), `/idx/groups.json` (1212: name, letter, homepage, country; 54 KB).
  No real name, e-mail or birth data is indexed.
- Search fields (`<SPAN class="oj-search">`, no `<form>`) replace the 2000 forms: home box
  (pictures, photos, news), nick lists (people), groups list (groups), picture library (pictures
  and photos). Enter or "go" opens `/search.html?t=<type>&q=<words>` in the current skin; the
  results page (skin top / bottom, results drawn with the class names of the news and picture
  library templates) matches every word, case and accent insensitive, and shows 300 results at
  most. It needs JavaScript (a `<NOSCRIPT>` line says so).
