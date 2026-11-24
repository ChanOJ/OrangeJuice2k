#!/usr/bin/env python3
"""Statify the 2000 Orange Juice classic-ASP site into plain static HTML.

    python3 -I scripts/statify.py [--skins all|pl,poi] [--out site] [--raw PATH] [--db PATH] [--media PATH]

Reads ONLY from private/ (never writes to it): the ASP sources and skins
(private/site-raw/), the SQLite extraction of the SQL Server database
(private/db/ojuice.sqlite) and the picture library (private/media/piclib/).
The output folder is emptied and rebuilt on every run; the output is
deterministic (no timestamps, sorted traversal, stable SQL ordering).

What it reimplements (see docs/data-model.md):
  * the template engine of _inc/functions.inc + parameters.inc: OpenHTML_ (skin
    file with fallback to the default skin `tonic`, $$TOP$$ / $$BOTTOM$$ /
    $$LINKS$$ / $$KEYWORDS$$ / $$LINKDEMO$$ / $$SEARCHINC$$ / $$SIZEnnn$$ ...),
    ChangePicturesLocation_ (src= / background= moved to the skin's copy of the
    image) and the $$BEGIN$$..$$END$$ loops of each page;
  * the read-only logic of the public pages (news paging of oj_listnews,
    view_article, oneliner + popup, groups, parties + "who is coming",
    picture library, charts results and voters, static pages served by 404.asp).
Everything that wrote (forms, votes, counters, cookies, admin) is removed;
links to removed features point to /archived.html.

Skins: every real skin of skins/ is rendered (tonic at the site root, the others under
/skin/<name>/), plus /skins.html (chooser), /search.html and /idx/*.json (client-side search,
scripts/oj.js copied to /js/oj.js).

Content cut-off: everything dated on or after 2000-11-20 (Orange Juice v3
launch day) is left out (CUTOFF below).

Python 3.9+, standard library only. Sources are cp1252; every page is written as
iso-8859-1 (characters outside latin-1 become numeric character references) and
declares that charset, like OrangeJuice98.
"""
import argparse
import fnmatch
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urljoin, urlsplit

ENC = "iso-8859-1"
REPO = Path(__file__).resolve().parent.parent
PRIVATE = REPO / "private"
CUTOFF = "2000-11-20"          # Orange Juice v3 went live on 2000-11-20: keep what was dated before
NOW = datetime(2000, 11, 19, 23, 59, 59)   # "server clock" of the frozen site (parties incoming/past split)
MASK = "????"
META_CHARSET = '<META http-equiv="Content-Type" content="text/html; charset=iso-8859-1">'
DEFAULT_SKIN = "tonic"
PERPAGE_NEWS = 20              # oj_listnews: 20 news per page
PERPAGE_PICS = 10              # piclib/index.asp PERPAGE
VISITS_FROZEN = "1042"         # $$VISITS$$ (cnxweb counter DB, not preserved): last value seen in the Wayback Machine (2000-11-15)
STAFF_IDS = (145, 1123)        # charts/results.asp leaves the two webmasters out of the "news writers" top
OWN_HOSTS = ("www.ojuice.net", "ojuice.net", "ojuice.planet-d.net")   # ojuice.planet-d.net: the site's own first address
# Cross-links to other archives of the family (README "Cross-links"); only the bare host links are rewritten.
CROSS_HOSTS = {"ltp.ojuice.net": "https://ltp4.sauthier.family/", "ltp.planet-d.net": "https://ltp3.sauthier.family/"}
# Third-party ad / tracking / song-request services: links unwrapped, scripts and images removed.
# ICQ (mirabilis.com) links are kept; only the live presence badge is replaced by a local image (icq_badge).
# audiorealm.com/spacial/ (the broadcaster software the nectarine pages were made with) is a content link: kept.
KEEP_DEAD_PREFIXES = ("audiorealm.com/spacial",)
DEAD_HOSTS = ("audiorealm.com", "cnxweb.com", "extreme-dm.com")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ---- removal-request footer: added to the pages that show a family name, a postal address or an e-mail
# whose local part is a family name (`local@????`); never on nick-only pages
REMOVAL_MARK = 'id="removal-note"'
REMOVAL_NOTE = (
    '<div id="removal-note" style="clear:both;margin:28px 0 8px;padding:6px 8px 0;width:780px;max-width:96%;'
    'box-sizing:border-box;border-top:1px solid #808080;text-align:center;font:10px/14px Verdana,Arial,Helvetica,sans-serif;'
    'color:#808080;background:transparent">To be removed from this page, '
    '<a href="mailto:chandra_oj&#64;pm.me" style="color:#808080;text-decoration:underline">contact me</a>.</div>'
)
_BODY_END = re.compile(r"(?is)</body\s*>(?!.*</body\s*>)")
TRUNCATED_EMAIL = re.compile(r"(?<![A-Za-z0-9._+-])[A-Za-z0-9._+-]+@\?\?\?\?")


def add_removal_note(text):
    if REMOVAL_MARK in text:
        return text
    m = _BODY_END.search(text)
    if m:
        return text[: m.start()] + REMOVAL_NOTE + "\n" + text[m.start():]
    return text + "\n" + REMOVAL_NOTE + "\n"


# Pages that show real names without any e-mail (interviews, credits); people sheets decide per sheet
# (Page.family_name), party attendee lists and the voters list show nicks only.
FAMILY_NAME_PATHS = ("/credits.html",)
FAMILY_NAME_PREFIXES = ("/interviews/",)


def needs_removal_note(pg):
    if pg.family_name or TRUNCATED_EMAIL.search(pg.text):
        return True
    return pg.path in FAMILY_NAME_PATHS or pg.path.startswith(FAMILY_NAME_PREFIXES)

MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]   # Python weekday() order
EXEC_EXT = (".exe", ".com", ".dll", ".scr", ".bat", ".pif", ".cab", ".ex_", ".vbs", ".cmd", ".msi", ".sys")

# Templates filled by an ASP page (or a write path): never published at their own URL.
DYNAMIC_TEMPLATES = {
    "404.html", "addarticle.html", "addnews.html", "articlesaved.html", "newssaved.html", "badloginpass.html",
    "news.html", "oneliner_popup.html", "previewarticle.html", "previewnews.html", "search_help.html",
    "skins.html", "skins_cookie_ok.html", "skins_ip_ok.html", "view_article.html", "bar.html",
}
DYNAMIC_DIRS = ("admin/", "charts/", "groups/", "handles/", "parties/", "piclib/", "search/", "wap/")

# Old URLs (lower-case path) of removed write paths / dead features -> /archived.html
REMOVED = {
    "/addnews.asp", "/addarticle.asp", "/ielog.asp", "/width.asp",
    "/channel.cdf", "/null.html", "/pc.asp", "/charmap.asp", "/search_help.html", "/skins.html",
    "/groups/addgroup.asp", "/groups/search_help.html", "/parties/addparty.asp", "/piclib/addpic.asp",
    "/piclib/search_help.html", "/charts/vote.asp", "/charts/charts_list.asp", "/charts/logoff.asp",
    "/handles/edit.asp", "/handles/sendpass.asp", "/handles/search_help.html", "/badloginpass.html",
}
REMOVED_PREFIXES = ("/admin/", "/search/", "/wap/")
GENERATED = {"/js/oj.js", "/idx/news.json", "/idx/pictures.json", "/idx/people.json", "/idx/groups.json"}
V3_URLS = {"/pics/piclib.htm": "/piclib/index.html", "/pictures.htm": "/piclib/index.html",
           "/photos/piclib.htm": "/piclib/photos.html", "/charts.htm": "/charts/index.html",
           "/article/news_add.htm": "/archived.html"}
PEOPLE_PREFIX = "/handles/"
ARCHIVED = "/archived.html"


def cmp_date(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- io
def decode(raw):
    """cp1252 like the IIS server; the 5 undefined bytes map to the same code points as latin-1."""
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return "".join(raw[i:i + 1].decode("cp1252", errors="strict")
                       if raw[i] not in (0x81, 0x8D, 0x8F, 0x90, 0x9D) else chr(raw[i]) for i in range(len(raw)))


def read_text(p):
    return decode(Path(p).read_bytes())


def write_text(p, s):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(s.encode(ENC, errors="xmlcharrefreplace"))


def load_globs(path):
    out = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def is_excluded(rel, globs):
    """gitignore-like: no '/' -> basename anywhere; trailing '/' -> a directory at any depth when it
    has no other '/'; otherwise fnmatch on the whole path ('*' crosses '/'). Case-insensitive (IIS)."""
    rel = rel.lstrip("/").lower()
    base = rel.rsplit("/", 1)[-1]
    for g in globs:
        g = g.lstrip("/").lower()
        if g.endswith("/"):
            d = g.rstrip("/")
            if rel == d or rel.startswith(d + "/") or ("/" not in d and ("/" + d + "/") in ("/" + rel)):
                return True
        elif "/" not in g:
            if fnmatch.fnmatchcase(base, g):
                return True
        elif fnmatch.fnmatchcase(rel, g):
            return True
    return False


# ------------------------------------------------------------- VBScript helpers
def html_encode(s):
    """Server.HTMLEncode: & < > " and every non-ASCII character as a numeric reference."""
    if s is None or s == "":
        return ""
    out = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return re.sub(r"[^\x00-\x7f]", lambda m: "&#%d;" % ord(m.group(0)), out)


ICQ_ICONS = ("pictures/icqon.gif", "pictures/icqoff.gif", "pictures/icqdis.gif")   # scripts/assets/ (missing in the tonic skin)


def icq_badge(uin, nick, size=18):
    """The pager link of the original (UIN in its href) around the red flower ("offline" badge) hard-coded:
    the live presence image came from the dead ICQ servers and nothing is requested from them."""
    return ('<A href="http://wwp.mirabilis.com/scripts/Search.dll?to=%s"><IMG src="/pictures/icqoff.gif" align="top" '
            'width=%d height=%d border=0 alt="Click here to add %s to your ICQ contact list"></A>'
            % (uin, size, size, html_encode(nick)))


def two(n):
    return "%02d" % n


def five(n):
    return "%05d" % n


def groups_(grp):
    """Groups_: "ACME/Fudge" -> " / ACME^..." """
    grp = (grp or "").rstrip()
    if not grp:
        return ""
    i = grp.find("/")
    if i >= 0:
        grp = grp[:i] + "^..."
    return " / " + grp


def ncase(s):
    """NCASE: upper-case the first letter of each word."""
    keep = "abcdefghijklmnopqrstuvwxyz\xe0\xe4\xe2\xe9\xe8\xea\xeb\xef\xee\xf6\xf4\xfc\xfb\xf9\xe7\xf8\xe5\xe60123456789"
    out, prev = [], " "
    for c in s or "":
        out.append(c.lower() if prev.lower() in keep else c.upper())
        prev = c
    return "".join(out)


def make_href(url):
    return "mailto:" + url if "@" in url else url


def vb_round(x):
    """CLng: banker's rounding, like Python's round()."""
    return int(round(x))


def news_date(dt):
    return "%s %s %d" % (two(dt.day), MONTHS[dt.month - 1], dt.year)


def vb_now_str(dt):
    """Default VBScript date/time rendering of the (US-English) server: 11/14/2000 6:00:00 PM."""
    h = dt.hour % 12 or 12
    return "%d/%d/%d %d:%02d:%02d %s" % (dt.month, dt.day, dt.year, h, dt.minute, dt.second,
                                          "AM" if dt.hour < 12 else "PM")


def loop(buf, begin, end, rows):
    """Cut the template between `begin` and `end` and repeat it once per row (rows: callables or strings)."""
    i = buf.find(begin)
    if i < 0:
        return buf
    j = buf.find(end, i)
    tpl = buf[i + len(begin):j]
    return buf[:i] + "".join(r(tpl) if callable(r) else r for r in rows) + buf[j + len(end):]


def rep(buf, mapping):
    for k, v in mapping.items():
        buf = buf.replace(k, "" if v is None else str(v))
    return buf


# ----------------------------------------------------------------------- engine
class Engine:
    """_inc/functions.inc + _inc/parameters.inc, for one skin (fallback: tonic, then the site root)."""

    def __init__(self, raw, skin):
        self.raw = raw
        self.skin = skin
        self.index = {}          # lower-case rel path -> real rel path (IIS is case-insensitive)
        for root, dirs, files in os.walk(raw):
            dirs.sort()
            for f in sorted(files):
                rel = Path(root, f).relative_to(raw).as_posix()
                self.index.setdefault(rel.lower(), rel)
        self.keywords = read_text(raw / "_inc" / "keywords.txt")

    def real(self, rel):
        return self.index.get(rel.lstrip("/").lower())

    def page_path(self, page):
        """PagePath_: skins/<skin>/page, else skins/tonic/page (real relative path or None)."""
        page = page.lstrip("/").replace("\\", "/")
        for s in (self.skin, DEFAULT_SKIN):
            r = self.real("skins/%s/%s" % (s, page))
            if r:
                return r
        return None

    def page_url(self, page):
        """PageURL_: /skins/<skin>/page, else /skins/tonic/page, else /page if it exists at the root."""
        r = self.page_path(page)
        if r:
            return "/" + r
        r = self.real(page)
        return "/" + r if r else ""

    def read_page(self, page):
        r = self.page_path(page)
        if not r:
            raise FileNotFoundError("template not found: %s (skin %s)" % (page, self.skin))
        return sdk_paths(read_text(self.raw / r))

    def open_html(self, page, is_index=False, size_ie=False):
        """OpenHTML_ with the static choices: no spider keywords spam, no cnxweb beacon, no listeners."""
        return self.open_text(self.read_page(page), is_index, size_ie)

    def open_text(self, buf, is_index=False, size_ie=False):
        """The macro expansion of OpenHTML_ applied to a template given as text (pages the archive adds)."""
        buf = buf.replace("$$TOP$$", self.read_page("_inc/top.inc"))
        buf = buf.replace("$$BOTTOM$$", self.read_page("_inc/bottom_index.inc" if is_index else "_inc/bottom.inc"))
        buf = buf.replace("$$KEYWORDS$$", self.keywords)
        buf = buf.replace("$$CNXWEB$$", "")                       # cnxweb.com visit beacon: dropped
        buf = buf.replace("$$LINKS$$", self.read_page("_inc/links.inc"))
        buf = buf.replace("$$OJ_DOMAIN$$", "www.ojuice.net")
        buf = buf.replace("$$OJMAIL$$", "webmaster@ojuice.net")   # redacted later like every e-mail
        buf = buf.replace("$$AEVOCJMAIL$$", "aevoc@ojuice.net")
        buf = buf.replace("$$LISTENERS$$", "")                    # Nectarine listeners (empty file)
        if "$$LINKDEMO$$" in buf:
            buf = buf.replace("$$LINKDEMO$$", self.read_page("_inc/linkdemo.inc"))
        if "$$SEARCHINC$$" in buf:
            buf = buf.replace("$$SEARCHINC$$", self.read_page("_inc/search.inc"))
        sizes = {"150": ("11", "15"), "170": ("12", "16"), "180": ("24", "20"), "200": ("15", "16"),
                 "300": ("25", "29"), "630": ("45", "49")}
        for k, (ie, other) in sizes.items():
            buf = buf.replace("$$SIZE%s$$" % k, ie if size_ie else other)
        buf = buf.replace("$$PUB$$", "")                          # banner exchange: images not preserved
        buf = buf.replace("$$CPT$$", "")                          # hit counter of the old citeweb host (some skins)
        return buf

    def change_pictures_location(self, buf, local_dir):
        """ChangePicturesLocation_: src="x" / background="x" (exact case, double quotes, not http://)
        resolved against the page folder, then moved to the skin copy (PageURL_). Unlike the ASP, a
        value that resolves nowhere is left unchanged (the link pass reports and drops it)."""
        def fn(m):
            val = m.group(2)
            if val[:7].lower() == "http://" or not val or val.startswith(("javascript:", "#", "$$")):
                return m.group(0)
            path = val.split("?", 1)[0].split("#", 1)[0]
            absp = os.path.normpath(path if path.startswith("/") else "/" + local_dir.strip("/") + "/" + path)
            absp = absp.replace(os.sep, "/")
            if absp.startswith("//"):
                absp = absp[1:]
            url = self.page_url(absp)
            if not url and not path.startswith("/"):
                # skin includes written for the root page ("pictures/x.gif") also used on pages of
                # sub-folders, where the ASP found nothing: the archive falls back to the root folder
                url = self.page_url(os.path.normpath("/" + path).replace(os.sep, "/"))
            return m.group(1) + (url if url else val) + '"'
        return re.sub(r'((?:src|SRC|background|BACKGROUND)=")([^"]*)"', fn, buf)


# ------------------------------------------------------------------------- data
class Data:
    """Read-only queries on the SQLite extraction. Only public columns are ever selected
    (never password, email, ip, hostname, user_agent, login or cookie columns)."""

    def __init__(self, db, raw):
        self.cx = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        self.cx.row_factory = sqlite3.Row
        self.nicklist = self._nicklist(raw / "_inc" / "nicklist.inc")

    def q(self, sql, args=()):
        return self.cx.execute(sql, args).fetchall()

    @staticmethod
    def _nicklist(p):
        """_inc/nicklist.inc: `<OPTION value=<id>>nick / group` written by handles/edit.asp from the 2000
        people table (HTMLEncode_(nick & GroupsShort_(groups))). PEOPLE was recreated in 2005 without the
        `groups` column, so this file is the only source of the 2000 "nick / group" labels."""
        out = {}
        for line in read_text(p).splitlines():
            m = re.match(r"\s*<OPTION value=(\d+)>(.*)$", line, re.I)
            if m:
                out[int(m.group(1))] = html.unescape(m.group(2).rstrip())
        return out

    def people2000(self):
        """id -> (nick, group label, multi) of the 2000 nick list. GroupsShort_ cut group names at 16
        characters ("Tokyo Dawn Recor..."): when one of the person's groups in LINKS (the v3 group table
        referenced by PEOPLE.idgroup1/2) or a unique GROUPS name starts with the cut prefix, the full
        name is restored. "First^..." (several groups) is kept as Groups_ showed it."""
        if hasattr(self, "_p2000"):
            return self._p2000
        links = {r["id"]: r["name"] for r in self.q("SELECT id, name FROM links WHERE name IS NOT NULL")}
        gnames = [r["name"] for r in self.q("SELECT name FROM groups WHERE name IS NOT NULL")]
        pg = {r["id"]: (r["idgroup1"], r["idgroup2"]) for r in self.q("SELECT id, idgroup1, idgroup2 FROM people")}
        out, self.restored = {}, 0
        for pid, label in self.nicklist.items():
            k = label.rfind(" / ")
            nick, g = (label[:k], label[k + 3:]) if k >= 0 else (label, "")
            multi = g.endswith("^...")
            if not multi and len(g) == 19 and g.endswith("..."):
                pre = g[:16].lower()
                cand = [links[x] for x in pg.get(pid, ()) if x in links and links[x].lower().startswith(pre)]
                cand2 = sorted({n for n in gnames if n.lower().startswith(pre)})
                full = cand[0] if cand else (cand2[0] if len(cand2) == 1 else None)
                if full:
                    g, self.restored = full, self.restored + 1
            out[pid] = (nick, g, multi)
        self._p2000 = out
        return out

    def family_locals(self):
        """Local parts of the e-mail addresses that contain their owner's family name (people table)."""
        out = set()
        for r in self.q("SELECT lastname, email FROM people WHERE email LIKE '%@%' AND lastname IS NOT NULL"):
            local = r["email"].split("@", 1)[0].strip()
            ln = norm_name(r["lastname"])
            if len(ln) >= 3 and ln in norm_name(local):
                out.add(local.lower())
        return out

    def author(self, id_people, nick=None):
        """nick & Groups_(groups) as shown in 2000 (nicklist label, 16-character cuts restored when
        possible; nick alone when unknown)."""
        p = self.people2000().get(id_people)
        if p:
            return p[0] + (" / " + p[1] if p[1] else "")
        if nick is None:
            r = self.q("SELECT nick FROM people WHERE id=?", (id_people,))
            nick = r[0]["nick"] if r else ""
        return (nick or "").rstrip()

    # news ------------------------------------------------------------------
    def news(self):
        """oj_listnews: active news joined with people and categories, ORDER BY dt DESC (id DESC on ties)."""
        return self.q("""SELECT n.id, n.title, n.url, n.target, n.smalltxt, n.isarticle, n.category,
                                c.catname, c.urlcat, n.dt, n.id_people, p.nick, n.clicks
                         FROM news n JOIN people p ON p.id = n.id_people JOIN categories c ON c.id = n.category
                         WHERE n.active = 1 AND n.dt < ? ORDER BY n.dt DESC, n.id DESC""", (CUTOFF,))

    def max_clicks(self):
        return self.q("SELECT MAX(clicks) m FROM news WHERE dt < ?", (CUTOFF,))[0]["m"] or 1

    def articles(self):
        return self.q("""SELECT n.id, n.title, n.dt, n.bigtxt, n.id_people, p.nick FROM news n
                         JOIN people p ON p.id = n.id_people
                         WHERE n.active = 1 AND n.dt < ? AND n.bigtxt IS NOT NULL ORDER BY n.id""", (CUTOFF,))

    def oneliners(self, n=20):
        """SELECT TOP 20 * FROM ONELINER ORDER BY dt DESC (only id, dt, sentence are read)."""
        return self.q("SELECT id, dt, sentence FROM oneliner WHERE dt < ? ORDER BY dt DESC, id DESC LIMIT ?",
                      (CUTOFF, n))

    def pic_counts(self):
        r = self.q("""SELECT SUM(isphoto = 0) pics, SUM(isphoto = 1) photos FROM pictures
                      WHERE isactive = 1 AND dtadded < ?""", (CUTOFF,))[0]
        return r["pics"] or 0, r["photos"] or 0


# ----------------------------------------------------------------------- the site
class Page:
    def __init__(self, path, old_url, text, kind):
        self.path = path          # published path, '/x/y.html'
        self.old_url = old_url    # URL the page had on www.ojuice.net (base of its relative links)
        self.text = text
        self.kind = kind
        self.family_name = False  # the page shows a family name (set by the renderer that knows)
        self.title = None         # <title> chosen by the renderer (else derived by page_title)


class Site:
    def __init__(self, a, skin, prefix, data, skins):
        self.raw = Path(a.raw)
        self.media = Path(a.media)
        self.out = Path(a.out)
        self.skin = skin
        self.prefix = prefix       # "" for the default skin (site root), "/skin/<name>" for the others
        self.skins = skins         # every rendered skin (for the skins page)
        self.eng = Engine(self.raw, skin)
        self.data = data
        self.globs = []
        for f in (REPO / "scripts" / "exclude-globs.txt",):
            self.globs += load_globs(f)
        self.pages = {}            # path -> Page
        self.assets = {}           # published path -> source file
        self.urlmap = {}           # old URL -> new path
        self.unlinked = []         # (page, target, reason)
        self.dropped = []          # (page, what)
        self.counts = {}
        self.articles = set()
        self.oneliner_ids = set()
        self.people_ids = set()
        self.people_letters = []
        self.party_ids = set()
        self.picture_ids = set()
        self.group_letters = []
        self.news_pages = 0
        self.zip_ok = {}
        self.private_ips = private_ips(self.raw)

    # ---------------------------------------------------------------- helpers
    def add(self, path, old_url, text, kind, old_forms=(), title=None, family_name=False):
        if path in self.pages:
            raise RuntimeError("output collision: " + path)
        self.pages[path] = Page(path, old_url, text, kind)
        self.pages[path].title = title
        self.pages[path].family_name = family_name
        self.counts[kind] = self.counts.get(kind, 0) + 1
        for o in old_forms:
            self.urlmap.setdefault(o, path)

    def cpl(self, buf, local_dir):
        return self.eng.change_pictures_location(buf, local_dir)

    # ------------------------------------------------------------------- news
    def render_news(self):
        """index.asp: oj_listnews paging (20 per page) + oneliner + picture counts."""
        d = self.data
        rows = d.news()
        maxclicks = d.max_clicks()
        nb_pages = max(1, -(-len(rows) // PERPAGE_NEWS))   # the ASP capped the page index at 23; all pages kept
        self.news_pages = nb_pages
        npics, nphotos = d.pic_counts()
        ones = list(reversed(d.oneliners()))
        self.oneliner_ids = {o["id"] for o in ones}

        def news_row(r):
            def fn(tpl):
                title = r["title"] or ""
                url = r["url"] or ""
                if r["isarticle"]:
                    title = '<A href="view_article.asp?id=%d">%s</A>' % (r["id"], html_encode(title))
                elif url:
                    title = '<A href="%s" target="%s">%s</A>' % (make_href(url), r["target"] or "", html_encode(title))
                t = rep(tpl, {"$$TITLE$$": title, "$$DATE$$": news_date(cmp_date(r["dt"])),
                              "$$TEXT$$": r["smalltxt"] or "", "$$IDN$$": r["id_people"],
                              "$$AUTHOR$$": d.author(r["id_people"], r["nick"]),
                              "$$CATEGORY$$": two(r["category"]), "$$CATNAME$$": r["catname"] or "",
                              "$$CLICKS$$": (r["clicks"] or 0) * 5 // maxclicks})
                if r["urlcat"] is not None:
                    t = t.replace("$$URLCAT_$$", '<A href="%s">' % r["urlcat"]).replace("$$_URLCAT$$", "</A>")
                else:
                    t = t.replace("$$URLCAT_$$", "").replace("$$_URLCAT$$", "")
                return t
            return fn

        def one_row(o):
            dt = cmp_date(o["dt"])
            sentence = html_encode(o["sentence"] or "").replace(" ?", " &nbsp;?").lower()
            return lambda tpl: rep(tpl, {"$$HOUR$$": "%02d:%02d" % (dt.hour, dt.minute), "$$ID$$": o["id"],
                                         "$$SENTENCE$$": sentence})

        for p in range(1, nb_pages + 1):
            buf = self.eng.open_html("news.html", True)
            idx = []
            for i in range(1, nb_pages + 1):
                idx.append(' / <A href="index.asp?p=%d">%d</A>' % (i, i) if i != p else " / %d" % i)
            if p > 1:
                idx.insert(0, ' / <A href="index.asp?p=%d">&lt;&lt;</A>' % (p - 1))
            if p < nb_pages:
                idx.append(' / <A href="index.asp?p=%d">&gt;&gt;</A>' % (p + 1))
            buf = buf.replace("$$SEARCH$$", "").replace("$$INDEX$$", "[ " + "".join(idx)[3:] + " ]")
            buf = loop(buf, "$$BEGIN$$", "$$END$$", [news_row(r) for r in rows[(p - 1) * PERPAGE_NEWS:p * PERPAGE_NEWS]])
            buf = loop(buf, "$$BEGIN_O$$", "$$END_O$$", [one_row(o) for o in ones])
            buf = rep(buf, {"$$ERRMSG$$": "", "$$NBPICS$$": npics, "$$NBPHOTOS$$": nphotos, "$$VISITS$$": VISITS_FROZEN})
            buf = buf.replace("'/oneliner_popup.asp?o='+param", "'%s/oneliner/'+param+'.html'" % self.prefix)
            buf = self.static_search_box(buf, npics, nphotos)
            buf = self.cpl(buf, "/")
            if p == 1:
                self.add("/index.html", "/index.asp", buf, "news index",
                         ("/", "/index.asp", "/index.asp?p=1", "/index.html", "/default.asp"))
            else:
                self.add("/news/page-%d.html" % p, "/index.asp?p=%d" % p, buf, "news index", ("/index.asp?p=%d" % p,))

    def static_search_box(self, buf, npics, nphotos):
        """_inc/search.inc keeps its table and texts ("Find a picture among N"...), but each of its four
        search forms is unwrapped: the picture and photo boxes get a "browse" link to the library pages
        under a search field run by /js/oj.js (the library was only reachable through these forms); the news
        search is run by /js/oj.js too; the production search (ix_* FTP servers) stays archived."""
        s = buf.find('<form action="/search/index.asp"')
        if s < 0:
            return buf
        e = buf.find("</form>", buf.find('name="searchengine"', s)) + len("</form>")

        def fn(m):
            attrs, inner = m.group(1), m.group(2)
            inner = re.sub(r"(?is)\s*\(<a href=\"[^\"]*\">help</a>\)", "", inner)
            inner = re.sub(r"(?is)<input\s+type=hidden[^>]*>\s*", "", inner)
            if "/piclib/" in attrs:
                photos = "photos" in m.group(0)
                link = (search_widget("photos" if photos else "pics") + '<br>'
                        '<A href="/piclib/index.asp?p=1&typeimage=%s"><B>&gt; Browse the %s %s</B></A>'
                        % (("photos", nphotos, "photos") if photos else ("pics", npics, "pictures")))
            elif 'name="searchengine"' in attrs:
                link = search_widget("news")
            else:
                link = "<FONT class=discreet>[ archived: search not available ]</FONT>"
            return re.sub(r"(?is)<input\b[^>]*>", lambda _m: link, inner, count=1)
        box = re.sub(r"(?is)<form\b([^>]*)>(.*?)</form\s*>", fn, buf[s:e])
        return buf[:s] + box + buf[e:]

    def render_articles(self):
        d = self.data
        for r in d.articles():
            buf = self.eng.open_html("view_article.html", False)
            buf = rep(buf, {"$$TITLE$$": r["title"] or "", "$$DATE$$": news_date(cmp_date(r["dt"])),
                            "$$BIGTXT$$": (r["bigtxt"] or "").replace("\r\n", "<BR>"),
                            "$$IDP$$": r["id_people"], "$$NICK$$": r["nick"] or "", "$$GROUPS$$": ""})
            buf = self.cpl(buf, "/")
            self.articles.add(r["id"])
            self.add("/article/%d.html" % r["id"], "/view_article.asp?id=%d" % r["id"], buf, "article",
                     ("/view_article.asp?id=%d" % r["id"],))

    def render_oneliner_popups(self):
        """oneliner_popup.asp?o=: date, host name and browser of a oneliner. Host and browser are masked
        (personal data, OrangeJuice98 did the same); the IP was never shown."""
        for o in self.data.q("SELECT id, dt FROM oneliner WHERE id IN (%s) ORDER BY id"
                             % ",".join(str(i) for i in sorted(self.oneliner_ids)) or "0"):
            buf = self.eng.open_html("oneliner_popup.html", False)
            buf = rep(buf, {"$$DT$$": vb_now_str(cmp_date(o["dt"])), "$$HOSTNAME$$": MASK, "$$UA$$": MASK})
            self.add("/oneliner/%d.html" % o["id"], "/oneliner_popup.asp?o=%d" % o["id"], buf, "oneliner popup",
                     ("/oneliner_popup.asp?o=%d" % o["id"],))

    # ----------------------------------------------------------------- groups
    def render_groups(self):
        """groups/index.asp?l=: oj_listalphagroups + oj_listgroups (active groups of one letter, by name)."""
        d = self.data
        letters = [r["letter"] for r in d.q("SELECT DISTINCT letter FROM groups WHERE active = 1 AND dt < ? "
                                             "ORDER BY letter", (CUTOFF,))]
        self.group_letters = letters
        for L in letters:
            buf = self.eng.open_html("groups/index.html", True)
            buf = buf.replace("$$SEARCH$$", "").replace("$$EDIT$$", "")
            buf = buf.replace("$$EDITURL$$", '<A href="index.asp?l=%s&e=1"><B>click here</B></A> to modify an existing '
                              'group.<BR><BR>' % quote(L))
            buf = rep(buf, {"$$EDITBUTTON1$$": "", "$$EDITBUTTON2$$": ""})
            lst, prev, nxt = [], "", ""
            for i, x in enumerate(letters):
                if x != L:
                    lst.append(' / <a href="index.asp?l=%s">%s</A>' % (quote(x), x))   # URLEncode_ ('#' -> %23)
                else:
                    lst.append(" / " + x)
                    prev = letters[i - 1] if i > 0 else letters[-1]
                    nxt = letters[i + 1] if i + 1 < len(letters) else letters[0]
            buf = buf.replace("$$INDEX$$", "[ " + "".join(lst)[3:] + " ]")
            buf = buf.replace("$$PREVIOUS-NEXT$$", '[ <A href="index.asp?l=%s">&gt;previous</A> / <A href="index.asp?l=%s">'
                              '&gt;next</A> ]' % (quote(prev), quote(nxt)))
            rows = d.q("""SELECT g.id, g.name, g.url, c.country, c.flag FROM groups g JOIN countries c ON c.id = g.id_country
                          WHERE g.letter = ? AND g.active = 1 AND g.dt < ? ORDER BY g.name COLLATE NOCASE, g.name, g.id""", (L, CUTOFF))

            def row(r):
                def fn(tpl):
                    url = r["url"] or ""
                    t = rep(tpl, {"$$ID$$": r["id"], "$$NAME$$": html_encode(r["name"]), "$$FLAG$$": (r["flag"] or "").lower(),
                                  "$$COUNTRY$$": r["country"] or "", "$$RADIO$$": ""})
                    return rep(t, {"$$LINK1$$": '<A HREF="%s">' % make_href(url) if url else "",
                                   "$$LINK2$$": "</A>" if url else ""})
                return fn
            buf = loop(buf, "$$BEGIN$$", "$$END$$", [row(r) for r in rows])
            buf = self.cpl(buf, "/groups/")
            path = "/groups/%s.html" % self.letter_slug(L)
            self.add(path, "/groups/index.asp?l=%s" % quote(L), buf, "groups letter",
                     ("/groups/index.asp?l=%s" % quote(L.lower()), "/groups/index.asp?l=%s" % quote(L)))
        first = "/groups/%s.html" % self.letter_slug("A" if "A" in letters else letters[0])
        for o in ("/groups/", "/groups/index.asp"):
            self.urlmap[o] = first
        self.add("/groups/index.html", "/groups/index.asp", self.redirect_stub(self.prefix + first), "redirect stub")

    @staticmethod
    def letter_slug(L):
        return "0-9" if L == "#" else L.lower()

    def redirect_stub(self, target):
        return ('<HTML>\n<HEAD>\n%s\n<META http-equiv="refresh" content="0; url=%s">\n<TITLE>Orange Juice</TITLE>\n'
                '</HEAD>\n<BODY bgcolor="#000000" text="#FFFFFF" link="#FF8040" vlink="#FF8040">\n'
                '<A href="%s">Orange Juice</A>\n</BODY>\n</HTML>\n' % (META_CHARSET, target, target))

    # ---------------------------------------------------------------- parties
    def party_cut(self):
        """Parties have no creation date. They were added through addparty.asp, which also inserted a news
        ("This party will held in ..."): the last one before the cut-off announces party id 261 (Coven 2001,
        2000-11-16), the next ids belong to v3. So: parties with id <= 261."""
        last = 0
        for r in self.data.q("""SELECT n.title FROM news n WHERE n.dt < ? AND n.smalltxt LIKE 'This party will held%'""",
                             (CUTOFF,)):
            for p in self.data.q("SELECT id FROM parties WHERE name = ?", (r["title"],)):
                last = max(last, p["id"])
        return last

    @staticmethod
    def party_dates(b, e):
        s = "[%d] " % b.year if b.year != NOW.year else ""
        hb = '<FONT size=2>(%s:%s)</FONT>' % (two(b.hour), two(b.minute))
        he = '<FONT size=2>(%s:%s)</FONT>' % (two(e.hour), two(e.minute))
        if b.month == e.month:
            return s + "%s, %s %d %s - %s %d %s" % (MONTHS_LONG[b.month - 1], WEEKDAYS[b.weekday()], b.day, hb,
                                                    WEEKDAYS[e.weekday()], e.day, he)
        return s + "%s %s, %d %s - %s %s, %d %s" % (WEEKDAYS[b.weekday()], MONTHS[b.month - 1], b.day, hb,
                                                    WEEKDAYS[e.weekday()], MONTHS[e.month - 1], e.day, he)

    def render_parties(self):
        d = self.data
        last = self.party_cut()
        self.counts["party id cut"] = last
        now = NOW.strftime("%Y-%m-%d %H:%M:%S")
        for past in (False, True):
            buf = self.eng.open_html("parties/parties.html", False)
            buf = buf.replace("$$EDITURL$$", '<A href="index.asp?%se=1"><B>click here</B></A> to modify an existing party '
                              '(everybody can edit any party).<BR><BR>' % ("past=1&" if past else ""))
            buf = rep(buf, {"$$EDITBUTTON1$$": "", "$$EDITBUTTON2$$": ""})
            if past:
                buf = buf.replace("$$LINKSPARTIES$$", '<A HREF="index.asp">&gt;incoming</A> /\r\n&gt;past parties')
                buf = buf.replace("$$MSG$$", "Here are the $$NB$$ parties which already took place around the world "
                                  "(chronological order). Following the links will bring you more information about them "
                                  "or show you who went to them.")
                rows = d.q("""SELECT p.*, c.country, c.flag FROM parties p JOIN countries c ON c.id = p.id_country
                              WHERE p.dt_end < ? AND p.active = 1 AND p.id <= ? ORDER BY p.dt_begin DESC, p.id DESC""",
                           (now, last))
            else:
                buf = buf.replace("$$LINKSPARTIES$$", '&gt;incoming /\r\n<A HREF="index.asp?past=1">&gt;past parties</A>')
                buf = buf.replace("$$MSG$$", "Here are the $$NB$$ parties which will soon take place around the world "
                                  "(chronological order). Following the links will bring you more information about them "
                                  "or show you who are going to them.")
                rows = d.q("""SELECT p.*, c.country, c.flag FROM parties p JOIN countries c ON c.id = p.id_country
                              WHERE p.dt_end >= ? AND p.active = 1 AND p.id <= ? ORDER BY p.dt_begin, p.id""", (now, last))

            def row(r):
                def fn(tpl):
                    url = (r["url"] or "").strip()
                    if url:
                        u, tg = ("mailto:" + url, "_self") if "@" in url else (url, "_blank")
                    else:
                        u, tg = "javascript:alert('No url')", "_self"
                    return rep(tpl, {
                        "$$NAME$$": html_encode(r["name"]),
                        "$$CANCELED$$": ' - <FONT color="#4080FF" size=2>CANCELED</FONT>' if r["canceled"] else "",
                        "$$DATES$$": self.party_dates(cmp_date(r["dt_begin"]), cmp_date(r["dt_end"])),
                        "$$URL$$": u, "$$TARGET$$": tg,
                        "$$PLACE$$": html_encode(r["place"]) if r["place"] else "???",
                        "$$PRICE$$": html_encode(r["price"]) if r["price"] else "???",
                        "$$COUNTRY$$": ncase(r["country"]), "$$FLAG$$": r["flag"] or "",
                        "$$URLCOME$$": "coming.asp?p=%d" % r["id"], "$$RADIO$$": ""})
                return fn
            for r in rows:
                self.party_ids.add(r["id"])
            buf = loop(buf, "$$BEGIN$$", "$$END$$", [row(r) for r in rows])
            buf = self.cpl(buf.replace("$$NB$$", str(len(rows))), "/parties/")
            if past:
                self.add("/parties/past.html", "/parties/index.asp?past=1", buf, "parties list",
                         ("/parties/index.asp?past=1",))
            else:
                self.add("/parties/index.html", "/parties/index.asp", buf, "parties list",
                         ("/parties/", "/parties/index.asp"))
        self.render_coming()

    def render_coming(self):
        """parties/coming.asp?p=: people registered for a party (nick / group), from parties_coming.
        Only people of the 2000 nick list (registered before the cut-off) are listed."""
        d = self.data
        for pid in sorted(self.party_ids):
            name = d.q("SELECT name FROM parties WHERE id = ?", (pid,))[0]["name"]
            people = d.q("""SELECT DISTINCT c.id_people, p.nick FROM parties_coming c JOIN people p ON p.id = c.id_people
                            WHERE c.id_party = ?""", (pid,))
            labels = sorted((d.author(r["id_people"]) for r in people if r["id_people"] in d.nicklist),
                            key=lambda s: (s.lower(), s))
            buf = self.eng.open_html("parties/coming.html", False)
            buf = rep(buf, {"$$PARTYNAME$$": html_encode(name), "$$IDPARTY$$": pid, "$$NICKLIST$$": "", "$$ERRMSG$$": ""})
            buf = loop(buf, "$$BEGIN$$", "$$END$$", [(lambda lab: lambda t: t.replace("$$NAME$$", html_encode(lab)))(x)
                                                     for x in labels])
            buf = self.cpl(buf.replace("$$NB$$", str(len(labels))), "/parties/")
            self.add("/parties/coming-%d.html" % pid, "/parties/coming.asp?p=%d" % pid, buf, "party coming",
                     ("/parties/coming.asp?p=%d" % pid,), title="Who is coming to %s" % html.unescape(html_encode(name)))

    # ----------------------------------------------------------------- piclib
    def render_piclib(self):
        """piclib/index.asp without search terms (all active pictures or photos, dtadded DESC, 10 per page)
        and piclib/display.asp?id=."""
        d = self.data
        for kind, isphoto in (("pics", 0), ("photos", 1)):
            rows = d.q("""SELECT * FROM pictures WHERE isactive = 1 AND isphoto = ? AND dtadded < ?
                          ORDER BY dtadded DESC, id ASC""", (isphoto, CUTOFF))
            nb_pages = max(1, -(-len(rows) // PERPAGE_PICS))   # the ASP capped the index at 17 pages: all kept
            for p in range(1, nb_pages + 1):
                qs = "&typeimage=%s&event=&author=&who=&keywords=&rank=" % kind
                buf = self.eng.open_html("piclib/index.html", True)
                buf = rep(buf, {"$$CHECKPICS$$": " CHECKED" if not isphoto else "",
                                "$$CHECKPHOTOS$$": " CHECKED" if isphoto else "", "$$P$$": p})
                for k in ("SEVENT", "SRANK", "SYEAR", "SAUTHOR", "SWHO", "SKEYWORDS"):
                    buf = buf.replace("$$%s$$" % k, "")
                buf = buf.replace("$$NBPICS$$", str(len(rows)) if rows else "no")
                idx = []
                for i in range(1, nb_pages + 1):
                    idx.append(' / <A href="index.asp?p=%d%s">%d</A>' % (i, qs, i) if i != p else " / %d" % i)
                if nb_pages > 1:
                    if p > 1:
                        idx.insert(0, ' / <A href="index.asp?p=%d%s">&lt;&lt;</A>' % (p - 1, qs))
                    if p < nb_pages:
                        idx.append(' / <A href="index.asp?p=%d%s">&gt;&gt;</A>' % (p + 1, qs))
                    buf = buf.replace("$$INDEX$$", "[ " + "".join(idx)[3:] + " ]")
                else:
                    buf = buf.replace("$$INDEX$$", "")
                buf = loop(buf, "$$BEGIN$$", "$$END$$", [self.pic_row(r) for r in rows[(p - 1) * PERPAGE_PICS:p * PERPAGE_PICS]])
                buf = self.cpl(buf, "/piclib/")
                path = self.piclib_path(kind, p)
                forms = ["/piclib/index.asp?p=%d&typeimage=%s" % (p, kind)]
                if kind == "pics" and p == 1:
                    forms += ["/piclib/", "/piclib/index.asp"]
                self.add(path, "/piclib/index.asp?p=%d%s" % (p, qs), buf, "piclib index", forms)
            for r in rows:
                self.render_display(r)

    @staticmethod
    def piclib_path(kind, p):
        base = "index" if kind == "pics" else "photos"
        return "/piclib/%s.html" % base if p == 1 else "/piclib/%s-%d.html" % ("pics" if kind == "pics" else "photos", p)

    @staticmethod
    def pic_infos(r, display):
        infos = ""
        if display and r["who"] is not None:
            infos += "<BR><I>Who:</I> " + html_encode(r["who"])
        if r["width"] is not None and r["height"] is not None:
            infos += "<BR><I>Size:</I> %s x %s".replace(" x ", "x") % (r["width"], r["height"])
        if r["nbcolors"] is not None:
            infos += " (%sc)" % ("16M" if r["nbcolors"] == 16777216 else r["nbcolors"])
        if r["picmonth"] is not None and r["picyear"] is not None:
            infos += "<BR><I>Date:</I> %s/%s" % (two(r["picmonth"]), r["picyear"])
        if r["author"] is not None:
            infos += "<BR><I>Author:</I> " + html_encode(r["author"])
        if r["event"] is not None:
            infos += "<BR><I>Event:</I> " + html_encode(r["event"])
        if r["rank"] is not None:
            infos += "<BR><I>Rank:</I> %s" % r["rank"]
        if r["place"] is not None:
            infos += "<BR><I>Place:</I> " + html_encode(r["place"])
        if not display and r["who"] is not None:
            infos += "<BR><I>Who:</I> " + html_encode(r["who"])
        return infos

    def pic_row(self, r):
        def fn(tpl):
            ext = (r["filenameori"] or "").lower()
            t = tpl.replace("$$URL$$", "http://www.ojuice.net/piclib/display.asp?id=%d" % r["id"])
            t = t.replace("$$ORI$$", "(<A href=http://www.ojuice.net/pictures/piclib/%s>original</A>)" % ext if ext else "")
            t = t.replace("$$URLTHUMB$$", five(r["id"]) + ".jpg")
            t = t.replace("$$LEGEND$$", html_encode(r["legend"] if r["legend"] is not None else "[no legend]"))
            t = t.replace("$$DESCRIPTION$$", html_encode(r["description"] or ""))
            return t.replace("$$INFOS$$", self.pic_infos(r, False))
        return fn

    def render_display(self, r):
        buf = self.eng.open_html("piclib/display.html", True)
        buf = rep(buf, {"$$IMGURL$$": r["filename"], "$$LEGEND$$": html_encode(r["legend"] or ""),
                        "$$DESCRIPTION$$": html_encode(r["description"] or ""), "$$INFOS$$": self.pic_infos(r, True)})
        buf = self.cpl(buf, "/piclib/")
        self.picture_ids.add(r["id"])
        self.add("/piclib/%d.html" % r["id"], "/piclib/display.asp?id=%d" % r["id"], buf, "piclib picture",
                 ("/piclib/display.asp?id=%d" % r["id"],))

    # ----------------------------------------------------------------- charts
    def render_charts(self):
        """charts/results.asp (oj_vote_results per chart of _data/charts/charts.txt, + top news writers)
        and charts/voters.asp (oj_vote_voters). Votes carry no date: only voters of the 2000 nick list."""
        d = self.data
        voters = sorted(set(r["id_people"] for r in d.q("SELECT DISTINCT id_people FROM charts")) & set(d.nicklist))
        vset = ",".join(str(i) for i in voters) or "0"
        buf = self.cpl(self.eng.open_html("charts/results.html", False), "/charts/")
        buf = buf.replace("$$NBVT$$", str(len(voters)))
        i = buf.find("$$BEGIN$$")
        j = buf.find("$$END$$", i)
        tpl1 = buf[i + 9:j]
        cdir = self.raw / "_data" / "charts"

        def chart_block(name, items):
            t = tpl1.replace("$$CHART_NAME$$", name)
            rows, rank, anc, mx = [], 0, None, 0
            for label, score, shown in items:
                if mx == 0:
                    mx = score
                rank += 1
                nb = "%d -&nbsp;" % rank if score != anc else ""
                anc = score
                rows.append((label, shown, vb_round(score * 100 / mx) if mx else 0, nb))
            return loop(t, "$$BEGIN2$$", "$$END2$$",
                        [(lambda x: lambda u: rep(u, {"$$NAME$$": html_encode(x[0]), "$$SCORE$$": x[1],
                                                      "$$W$$": x[2], "$$NB$$": x[3]}))(x) for x in rows])

        blocks = []
        for line in read_text(cdir / "charts.txt").splitlines():
            line = line.strip()
            if len(line) <= 2:
                continue
            cid, cname = line.split(",", 1)
            cid, cname = int(cid.strip()), cname.strip()
            if cname.startswith("@"):
                cname = cname[1:]
            names = {}
            for l2 in read_text(cdir / ("chart_%d.txt" % cid)).splitlines():
                l2 = l2.strip()
                if len(l2) > 2 and "," in l2:
                    k, v = l2.split(",", 1)
                    names.setdefault(int(k.strip()), v.strip())
            tot = d.q("SELECT SUM(rank) t FROM charts WHERE id_chart = ? AND id_item > 0 AND id_people IN (%s)" % vset,
                      (cid,))[0]["t"] or 0
            res = d.q("""SELECT id_item, SUM(rank) points, COUNT(rank) nb FROM charts
                         WHERE id_chart = ? AND id_item > 0 AND id_people IN (%s)
                         GROUP BY id_item HAVING COUNT(rank) >= 5 ORDER BY points DESC, id_item""" % vset, (cid,))
            items = []
            for r in res:
                score = r["points"] / tot
                items.append((names.get(r["id_item"], "?"), score, "%.1f%%" % (score * 100)))
            blocks.append(chart_block(cname, items))
        # top news & articles writers
        res = d.q("""SELECT n.id_people, COUNT(*) c, p.nick FROM news n JOIN people p ON p.id = n.id_people
                     WHERE n.id_people NOT IN (%s) AND n.dt < ? GROUP BY n.id_people, p.nick
                     ORDER BY c DESC, p.nick, n.id_people""" % ",".join(map(str, STAFF_IDS)), (CUTOFF,))
        items = []
        for r in res:
            if r["c"] < 3:
                break
            items.append((d.author(r["id_people"], r["nick"]), r["c"], "%d posts" % r["c"]))
        blocks.append(chart_block("News &amp; articles writers", items).replace(html_encode("News &amp; articles writers"),
                                                                               "News &amp; articles writers"))
        buf = buf[:i] + "".join(blocks) + buf[j + 7:]
        self.add("/charts/index.html", "/charts/results.asp", buf, "charts",
                 ("/charts/", "/charts/index.asp", "/charts/results.asp"))
        # voters
        rows = d.q("""SELECT DISTINCT a.id_people, b.nick, b.letter, c.flag, c.country FROM charts a
                      JOIN people b ON b.id = a.id_people JOIN countries c ON c.id = b.idcountry
                      WHERE a.id_people IN (%s)""" % vset)
        rows = sorted(rows, key=lambda r: ((r["letter"] or ""), (r["nick"] or "").lower(), r["id_people"]))
        buf = self.eng.open_html("charts/voters.html", False)
        buf = loop(buf, "$$BEGIN$$", "$$END$$", [
            (lambda r: lambda t: rep(t, {"$$NICK$$": d.author(r["id_people"], r["nick"]), "$$ID$$": r["id_people"],
                                         "$$DATE$$": "", "$$FLAG$$": r["flag"] or "", "$$COUNTRY$$": r["country"] or ""}))(r)
            for r in rows])
        buf = self.cpl(buf.replace("$$NBVT$$", str(len(rows))), "/charts/")
        self.add("/charts/voters.html", "/charts/voters.asp", buf, "charts", ("/charts/voters.asp",))

    # ----------------------------------------------------------------- people
    FUNCTIONS = ("ans", "asc", "cod", "gfx", "ray", "org", "ppe", "swp", "sys", "zik")   # handles/edit.asp order

    def render_people(self):
        """handles/handle.asp?id= (MakeHandleSheet of handle_inc.asp + oj_nick), handles/list.asp?l=
        (oj_listalpha + oj_list), handles/interviews.asp and handles/birthdates.asp, as they were until
        the v3 launch (the Response.Redirect "/v3.htm" at the top of each page dates from 2000-11-20).
        Population: PEOPLE rows created before the cut-off (all of them are in the 2000 nick list).
        Shown (owner decision): nick, groups, real name, e-mail as `local@????`, sex, birth date as
        `dd/mm/????` (the year is never output), country + flag, computer, functions, status, homepage,
        ICQ link (UIN in the pager link, red-flower (offline) badge: no request to the ICQ servers), IRC, scene year, parties,
        preferences, fun answers, interview links, ID photo. Hidden: French department, password,
        logins, cookies, IPs."""
        d = self.data
        p2000 = d.people2000()
        rows = d.q("""SELECT p.id, p.letter, p.idcountry, c.country, c.flag, k.computer, p.url, p.status, p.scene,
                             p.irc_chans, p.irc_net, p.parties, p.pref_party, p.pref_demo, p.pref_group, p.pref_coder,
                             p.pref_zik, p.pref_gfx, p.pref_tart, p.pref_movies, p.pref_musics, p.fun1, p.fun2,
                             p.isans, p.isasc, p.iscod, p.isgfx, p.isray, p.isorg, p.isppe, p.isswp, p.issys, p.iszik,
                             p.firstname, p.lastname, p.email, p.isemailhidden, p.sex, p.birthmd, p.uin, p.pic_ext
                      FROM people p LEFT JOIN countries c ON c.id = p.idcountry
                      LEFT JOIN computers k ON k.id = p.idcomputer
                      WHERE p.dt_crea < ? ORDER BY p.id""", (CUTOFF,))
        rows = [r for r in rows if r["id"] in p2000]
        self.people_ids = {r["id"] for r in rows}
        letters = sorted({(r["letter"] or "").upper() for r in rows if r["letter"]})
        self.people_letters = letters

        def index_list(cur):
            return " / ".join('<a href="list.asp?l=%s">%s</A>' % (quote(x), x) if x != cur else x for x in letters)

        def s2h(v):
            v = (v or "").rstrip()
            return html_encode(v) if v else "..."

        def interviews(pid):
            out = []
            for lang, flag, alt in (("e", "uk", "Interview in english"), ("f", "fr", "Interview en fran\xe7ais")):
                if self.eng.page_path("interviews/%d_%s.htm" % (pid, lang)):
                    out.append('<A HREF="../interviews/%d_%s.htm"><img src="../pictures/flags/%s.gif" width=18 height=12 '
                               'alt="%s" border=0></A>' % (pid, lang, flag, html_encode(alt)))
            return "&nbsp;".join(out)

        def photo(r):
            """handles/pictures/<id>.<pic_ext> if the file exists; size rule of handle_inc.asp
            (max width 240, small pictures doubled, max height 500)."""
            ext = (r["pic_ext"] or "").strip()
            real = self.eng.real("handles/pictures/%d.%s" % (r["id"], ext)) if ext else None
            if not real:
                return None
            size = image_size((self.raw / real).read_bytes())
            if not size:
                return None
            w, h = size
            pw, ph = w, 0
            if w > 240:
                pw = 240
            elif w <= 100:
                pw = w * 2
            else:
                ph = h
                if h > 500:
                    pw, ph = 0, 500
                else:
                    pw = w
            return real, pw, ph

        photos = {r["id"]: photo(r) for r in rows}
        self.counts["people photos"] = sum(1 for v in photos.values() if v)

        def email(v, hidden, last):
            """local@domain when the local part is a first name or a nick; local@???? when it holds a family
            name (or the person asked to hide the address)."""
            v = (v or "").strip()
            if "@" not in v:
                return "..."
            local, domain = v.split("@", 1)
            if hidden or not keep_email_whole(local, (last,)):
                return html_encode(local) + "@" + MASK
            return html_encode(local) + "@" + html_encode(domain.strip())

        def birth(r, fmt):
            md = (r["birthmd"] or "").strip()
            if len(md) != 4 or not md.isdigit() or not (1 <= int(md[:2]) <= 12):
                return None
            return fmt(int(md[2:]), int(md[:2]))

        for r in rows:
            pid = r["id"]
            nick, grp, multi = p2000[pid]
            groups = (grp[:-4] + "/..." if multi else grp)
            buf = self.eng.open_html("handles/handle.html", False)
            func = "/".join(f.upper() for f in self.FUNCTIONS if r["is" + f])
            irc_net = html_encode(r["irc_net"] or "")
            url = (r["url"] or "").strip()
            first, last = (r["firstname"] or "").strip(), (r["lastname"] or "").strip()
            ph = photos[pid]
            if ph:
                pic = '<IMG src="/%s"%s%s alt="ID photo of %s">' % (ph[0], " width=%d" % ph[1] if ph[1] else "",
                                                                   " height=%d" % ph[2] if ph[2] else "", html_encode(nick))
            else:
                pic = "EDIT THE<BR>SHEET TO<BR>ADD AN<BR>ID PICTURE"
            uin = r["uin"] or 0
            buf = rep(buf, {
                "$$INDEX$$": index_list(None), "$$ID$$": pid, "$$NICK$$": s2h(nick), "$$GROUPS$$": s2h(groups),
                "$$SCENE$$": r["scene"] if (r["scene"] or 0) > 0 else "...", "$$COMPUTER$$": html_encode((r["computer"] or "").rstrip()),
                "$$FUNC$$": func, "$$IRC_CHANS$$": s2h(r["irc_chans"]), "$$IRC_NET$$": "[%s]" % irc_net if irc_net else "",
                "$$PARTIES$$": s2h(r["parties"]), "$$PREF_PARTY$$": s2h(r["pref_party"]), "$$PREF_DEMO$$": s2h(r["pref_demo"]),
                "$$PREF_GROUP$$": s2h(r["pref_group"]), "$$PREF_CODER$$": s2h(r["pref_coder"]),
                "$$PREF_ZIK$$": s2h(r["pref_zik"]), "$$PREF_GFX$$": s2h(r["pref_gfx"]), "$$PREF_TART$$": s2h(r["pref_tart"]),
                "$$PREF_MOVIES$$": s2h(r["pref_movies"]), "$$PREF_MUSICS$$": s2h(r["pref_musics"]),
                "$$FUN1$$": s2h(r["fun1"]), "$$FUN2$$": s2h(r["fun2"]),
                "$$HOMEPAGE$$": '<A HREF="%s">%s</A>' % (url, html_encode(url)) if url else "...",
                "$$NAME$$": (html_encode(first) + " " + html_encode(last)).strip() or "...",
                "$$EMAIL$$": email(r["email"], r["isemailhidden"], last),
                "$$SEX$$": "FEMALE" if r["sex"] else "MALE",
                "$$BIRTHDATE$$": birth(r, lambda dd, mm: "%02d/%02d/%s" % (dd, mm, MASK)) or "...",
                "$$INTERVIEW$$": interviews(pid), "$$MSG$$": "",
                "$$ICQ$$": ('<FONT face="Verdana" size=2><A href="http://www.icq.com" target="_top">ICQ</A> state :%s</FONT>'
                            % icq_badge(uin, nick)) if uin > 0 else "",
                "$$ID_PIC$$": pic,
                "$$FLAG$$": '<img src="../pictures/flags/%s.gif" align="bottom" width=18 height=12 alt="%s">'
                            % ((r["flag"] or "").lower(), html_encode((r["country"] or "").rstrip())),
                "$$COUNTRY$$": html_encode((r["country"] or "").rstrip()), "$$DEPARTMENT$$": "",
                "$$STATUS$$": {0: "ACTIVE", 1: "INACTIVE"}.get(r["status"], "RETIRED")})
            buf = self.cpl(buf, "/handles/")
            self.add("/people/%d.html" % pid, "/handles/handle.asp?id=%d" % pid, buf, "people sheet",
                     ("/handles/handle.asp?id=%d" % pid,), title=nick.strip(), family_name=bool(last))

        def link(r):
            return '<A HREF="handle.asp?id=%d">%s</A><font size=+1>&nbsp;<br></font>\r\n' % (r["id"], html_encode(d.author(r["id"])))

        def entry(r):
            out = ""
            if r["country"]:
                out += ('<img src="../pictures/flags/%s.gif" align="bottom" width=18 height=12 alt="%s">\r\n'
                        % ((r["flag"] or "").lower(), html_encode(r["country"].rstrip())))
            if photos[r["id"]]:
                out += '<img src="../pictures/id.gif" align="bottom" width=20 height=14 alt="There is an ID Photo">\r\n'
            if (r["uin"] or 0) > 0:
                out += icq_badge(r["uin"], p2000[r["id"]][0].strip()) + "\r\n"
            return out + link(r)

        for i, L in enumerate(letters):
            buf = self.eng.open_html("handles/list.html", True)
            buf = rep(buf, {"$$SEARCH$$": "", "$$INDEX$$": "[ " + index_list(L) + " ]",
                            "$$PREVIOUS-NEXT$$": '[ <A href="list.asp?l=%s">&gt;previous</A> / <A href="list.asp?l=%s">'
                                                 '&gt;next</A> ]' % (quote(letters[i - 1]), quote(letters[(i + 1) % len(letters)]))})
            lst = sorted((r for r in rows if (r["letter"] or "").upper() == L),
                         key=lambda r: (p2000[r["id"]][0].lower(), p2000[r["id"]][0], r["id"]))
            buf = buf.replace("$$LIST$$", "".join(entry(r) for r in lst))
            buf = self.cpl(buf, "/handles/")
            self.add("/people/%s.html" % self.letter_slug(L), "/handles/list.asp?l=%s" % quote(L), buf, "people list",
                     ("/handles/list.asp?l=%s" % quote(L.lower()), "/handles/list.asp?l=%s" % quote(L)))
        # interviews.asp: people with an interview page (the interview flag column is empty in the 2006 table)
        buf = self.eng.open_html("handles/interview.html", False)
        lst = sorted((r for r in rows if interviews(r["id"])), key=lambda r: (p2000[r["id"]][0].lower(), r["id"]))
        buf = rep(buf, {"$$SEARCH$$": "", "$$PREVIOUS-NEXT$$": "",
                        "$$LIST$$": "".join(interviews(r["id"]) + " " + link(r) for r in lst)})
        buf = self.cpl(buf, "/handles/")
        self.add("/people/interviews.html", "/handles/interviews.asp", buf, "people list", ("/handles/interviews.asp",))
        # birthdates.asp: "Today" (frozen server date 2000-11-19) and "Next days": the ASP showed the next 20
        # birthdays; the archive lists every birthday of the year from the day after, day and month only.
        buf = self.eng.open_html("handles/birthdates.html", False)
        today = "%02d%02d" % (NOW.month, NOW.day)
        born = sorted((r for r in rows if birth(r, lambda dd, mm: True)),
                      key=lambda r: (r["birthmd"], p2000[r["id"]][0].lower(), r["id"]))

        def bline(r):
            dd, mm = int(r["birthmd"][2:]), int(r["birthmd"][:2])
            return lambda t: rep(t, {"$$DATE$$": "%02d %s %s" % (dd, MONTHS[mm - 1], MASK), "$$UIN$$": icq_badge(r["uin"], p2000[r["id"]][0].strip()) if (r["uin"] or 0) > 0 else "",
                                     "$$ID$$": r["id"], "$$NICK$$": html_encode(d.author(r["id"]))})
        buf = buf.replace("$$CURRENTDATE$$", news_date(NOW))
        buf = loop(buf, "$$BEGIN1$$", "$$END1$$", [bline(r) for r in born if r["birthmd"] == today])
        nxt = [r for r in born if r["birthmd"] > today] + [r for r in born if r["birthmd"] < today]
        buf = loop(buf, "$$BEGIN2$$", "$$END2$$", [bline(r) for r in nxt])
        buf = self.cpl(buf, "/handles/")
        self.add("/people/birthdays.html", "/handles/birthdates.asp", buf, "people list", ("/handles/birthdates.asp",))

    # ---------------------------------------------------------- static pages
    def render_static(self):
        """Pages served by 404.asp: OpenHTML_(url) of the skin (fallback tonic), ChangePicturesLocation_
        with the folder of the requested URL, $$PUB$$ removed."""
        rels = set()
        for s in (self.skin, DEFAULT_SKIN):
            base = "skins/%s/" % s
            for low, real in self.eng.index.items():
                if low.startswith(base) and low.endswith((".html", ".htm")):
                    rels.add(real[len(base):].lower())
        for low in sorted(rels):
            if low in DYNAMIC_TEMPLATES or low.startswith(DYNAMIC_DIRS) or is_excluded(low, self.globs):
                continue
            real = self.eng.page_path(low)
            rel = real.split("/", 2)[2]                    # keep the file's own case
            url = "/" + rel
            if low == "nectarine/index.html":
                continue
            buf = self.eng.open_html(rel, False).replace("$$INDEX$$", "")   # bottom.inc of some skins
            if re.search(r"\$\$(?!TOP|BOTTOM)[A-Z]", buf):
                # the skin's file is a data template under a static name (yes/soon.html is a news page):
                # the default skin's page, in this skin's frame
                tpl = self.eng.real("skins/%s/%s" % (DEFAULT_SKIN, low))
                if not tpl:
                    continue
                buf = self.eng.open_text(sdk_paths(read_text(self.raw / tpl))).replace("$$INDEX$$", "")
                if re.search(r"\$\$(?!TOP|BOTTOM)[A-Z]", buf):
                    continue
            buf = self.cpl(buf, url.rsplit("/", 1)[0] + "/")
            self.add(url, url, buf, "static page")
        self.render_nectarine()

    def render_nectarine(self):
        """nectarine/index.asp: stream status read live from SHOUTcast servers ($$IF:streamN:up|down:..:ENDIF$$).
        Frozen: every stream is shown 'down' (the servers are long gone)."""
        buf = self.eng.open_html("nectarine/index.html", False)
        buf = re.sub(r"\$\$IF:(stream\d):(\w+):(.*?):ENDIF\$\$",
                     lambda m: m.group(3) if m.group(2) == "down" else "", buf, flags=re.S)

        def row(m):
            """Server table rows: the hard-coded relays (live365.com, mycaster.com) go 'Down' too, and the
            "IP|port" server column shows the relay host name of the NAME column instead."""
            tr = m.group(0)
            cells = re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", tr)
            if len(cells) < 6 or not re.search(r"\d+\.\d+\.\d+\.\d+\|", cells[1]):
                return tr
            name = re.sub(r"<[^>]+>", "", cells[2]).strip()
            tr = re.sub(r"\d+\.\d+\.\d+\.\d+\|[\w/]+", lambda _m: name, tr)
            return re.sub(r'(<font class="menutop">)Up(</font>)', r"\1Down\2", tr)
        buf = re.sub(r"(?is)<tr>(?:(?!</tr>).)*</tr>", row, buf)
        # the public instructions of the dead FTP server carried its login and password: not published
        buf = re.sub(r'(?is)<a href="ftp://ojradio:ojradio@ftp\.ojuice\.net">ftp\.ojuice\.net</a>\s*\|\s*user : ojradio\s*\|\s*pass : ojradio',
                     '<a href="ftp://ftp.ojuice.net">ftp.ojuice.net</a>; login and password not published', buf)
        buf = self.cpl(buf, "/nectarine/")
        self.add("/nectarine/index.html", "/nectarine/index.asp", buf, "static page", ("/nectarine/", "/nectarine/index.asp"))

    NOTICE_TPL = ('$$TOP$$\r\n<TABLE border="0" cellpadding="0" cellspacing="0" width="100%%">\r\n<TR><TD valign="top" '
                  'align="center">\r\n<A href="/" target="_top"><IMG src="/pictures/ojgl.gif" alt="Orange Juice" border="0" '
                  'width="400" height="186"></A><BR><BR><BR>\r\n%s\r\n</TD></TR>\r\n</TABLE>\r\n$$BOTTOM$$')

    def render_archived(self):
        """The single notice page every removed feature points to, in the style of skins/tonic/404.html
        (built on the skin's own top / bottom includes, so every skin has it)."""
        body = ('<FONT class=title6>archived / archiv&eacute;</FONT><BR><BR>\n'
                '<FONT class=title2>Orange Juice is now a read-only archive.</FONT><BR>\n'
                'This feature (forms, votes, registration, sending news, editing a sheet...) is no longer '
                'available: nothing can be added, sent or modified here.<BR><BR>\n'
                '<FONT class=title2>Orange Juice est d&eacute;sormais une archive en lecture seule.</FONT><BR>\n'
                'Cette fonction (formulaires, votes, inscriptions, envoi de news, modification d\'une fiche...) '
                'n\'est plus disponible : rien ne peut &ecirc;tre ajout&eacute;, envoy&eacute; ou '
                'modifi&eacute; ici.<BR><BR>\n<A href="/">&gt; back to the news / retour aux news</A>')
        buf = self.cpl(self.eng.open_text(self.NOTICE_TPL % body), "/")
        self.add(ARCHIVED, ARCHIVED, buf, "notice")

    def render_skins_page(self):
        """skins.asp: the skin chooser (skins/tonic/skins.html), with the skins of the archive, their
        authors (skin.txt) and preview (skins/<name>.gif), and the popularity table frozen from the SKINS
        table (oj_skins_usage). The choice is made by /js/oj.js (localStorage, no cookie); the radio
        buttons and the button stay, the form goes."""
        hidden = ("sdk", "tonic_snow", "tonic_xmas", "yes", "mairsilbeta", "pixol", "kano", "eggbird", "ak")
        buf = self.eng.open_html("skins.html", False)

        def one(name):
            def fn(t):
                txt = self.raw / "skins" / name / "skin.txt"
                authors = read_text(txt).strip().replace("\r\n", "<BR>").replace("\n", "<BR>") if txt.is_file() else name
                if name in hidden:
                    authors += "<BR><FONT class=discreet>(not in the 2000 list: seasonal, beta or test skin)</FONT>"
                img = '<IMG src="/skins/%s.gif">' % name if (self.raw / "skins" / (name + ".gif")).is_file() else ""
                return rep(t, {"$$NAME$$": name, "$$IMG$$": img, "$$AUTHORS$$": html_encode(authors).replace(
                    "&lt;BR&gt;", "<BR>").replace("&lt;FONT class=discreet&gt;", "<FONT class=discreet>").replace(
                    "&lt;/FONT&gt;", "</FONT>")})
            return fn
        buf = loop(buf, "$$BEGIN$$", "$$END$$", [one(n) for n in self.skins])
        tot = sum(r["nb"] for r in self.data.q("SELECT nb FROM skins")) or 1
        usage = "".join('<TR><TD>- %s</TD><TD align="right">&nbsp;%.1f%%</TD></TR>\r\n' % (r["skin"], r["nb"] * 100.0 / tot)
                        for r in self.data.q("SELECT skin, nb FROM skins ORDER BY nb DESC, skin")
                        if r["skin"] not in hidden)
        buf = rep(buf, {"$$INPUT_IP$$": "", "$$SKINSUSAGE$$": "<TABLE border=0 cellpadding=0 cellspacing=0>%s</TABLE>" % usage})
        buf = re.sub(r'(?i)<FORM action="skins\.asp" method="POST">', '<DIV id="oj-skins">', buf)
        buf = re.sub(r'(?i)<INPUT type="SUBMIT" name="cookie" value="SAVE IN A COOKIE">',
                     '<INPUT type="BUTTON" id="oj-skin-go" value="USE THIS SKIN">', buf)
        buf = re.sub(r"(?is)</FORM>", "</DIV>", buf, count=1)
        buf = buf.replace("You've the choice between saving your selection either to a cookie, or\r\n\t\t\tin the "
                          "webserver based upon your IP if this one is fixed.",
                          "The archive remembers your choice in this browser (no cookie); &quot;none&quot; brings "
                          "back the default skin (tonic).")
        buf = buf.replace("it depends of the day :)", "the default skin (tonic)")
        buf = self.cpl(buf, "/")
        self.add("/skins.html", "/skins.asp", buf, "skins", ("/skins.asp",))

    def render_search_page(self):
        """Results page of the client-side search (/js/oj.js + /idx/*.json): the skin's top / bottom, a
        search box and the results, drawn with the class names of the news and picture library templates."""
        icons = self.eng.page_url("/pictures/icons/01.gif").rsplit("/", 1)[0] + "/"
        body = ('<DIV align="left"><FONT class=title6>Search</FONT><BR><HR size="1">\r\n'
                '<SPAN class="oj-search" data-t="news"><SELECT class="oj-type">'
                '<OPTION value="news">news</OPTION><OPTION value="pics">pictures</OPTION>'
                '<OPTION value="photos">photos</OPTION><OPTION value="people">people</OPTION>'
                '<OPTION value="groups">groups</OPTION></SELECT> '
                '<INPUT type="TEXT" size=30 maxlength=100 value=""> <INPUT type="BUTTON" value="go"></SPAN><BR><BR>\r\n'
                '<FONT class=smtxt>Words are searched in the titles and texts of the news, the legends and '
                'descriptions of the pictures, the nicks and groups of the people (archive search, '
                'computed in your browser).</FONT><BR><BR>\r\n'
                '<DIV id="oj-results" data-icons="%s"><NOSCRIPT>The search needs JavaScript.</NOSCRIPT></DIV>'
                '</DIV>' % icons)
        buf = self.eng.open_text("$$TOP$$\r\n%s\r\n$$BOTTOM$$" % body)
        self.add("/search.html", "/search.html", self.cpl(buf, "/"), "search")

    def render_404(self):
        buf = self.eng.open_html("404.html", False)
        body = ('<FONT class=title6><B>Error 404<BR>File not found</FONT><BR><BR>\n'
                'This page does not exist in the Orange Juice archive (1999 version, read-only).<BR>\n'
                'Cette page n\'existe pas dans l\'archive d\'Orange Juice (&eacute;dition 2000, lecture seule).<BR><BR>\n'
                '<A href="/">&gt; back to the news / retour aux news</A>')
        buf = re.sub(r"<FONT class=title6><B>Error 404<BR>File not found</FONT>", lambda m: body, buf)
        return self.cpl(buf, "/")

    # ------------------------------------------------------------ link pass
    def route(self, path, query):
        """Old URL (absolute path, lower-cased for matching, + parsed query) -> (new path | None, reason)."""
        low = path.lower()
        q = {k.lower(): v for k, v in parse_qsl(query, keep_blank_values=True)}
        if low in ("/", "/index.asp", "/default.asp", "/index.html", "/index.htm"):
            if q.get("s"):
                return ARCHIVED, "removed"
            try:
                p = int(q.get("p") or 1)
            except ValueError:
                p = 1
            if 1 <= p <= self.news_pages:
                return ("/index.html" if p == 1 else "/news/page-%d.html" % p), "ok"
            return None, "missing"
        if low == "/view_article.asp":
            i = q.get("id", "")
            return ("/article/%s.html" % i, "ok") if i.isdigit() and int(i) in self.articles else (None, "missing")
        if low == "/oneliner_popup.asp":
            i = q.get("o", "")
            return ("/oneliner/%s.html" % i, "ok") if i.isdigit() and int(i) in self.oneliner_ids else (ARCHIVED, "removed")
        if low in ("/groups/", "/groups/index.asp", "/groups"):
            if q.get("e") == "1" or q.get("s"):
                return ARCHIVED, "removed"
            L = (q.get("l") or "A").upper()
            if L in self.group_letters:
                return "/groups/%s.html" % self.letter_slug(L), "ok"
            return None, "missing"
        if low in ("/parties/", "/parties/index.asp", "/parties"):
            if q.get("e") == "1":
                return ARCHIVED, "removed"
            return ("/parties/past.html" if q.get("past") else "/parties/index.html"), "ok"
        if low == "/parties/coming.asp":
            i = q.get("p", "")
            return ("/parties/coming-%s.html" % i, "ok") if i.isdigit() and int(i) in self.party_ids else (None, "missing")
        if low in ("/piclib/", "/piclib/index.asp", "/piclib"):
            if any(q.get(k) for k in ("event", "author", "who", "keywords", "rank", "year")):
                return ARCHIVED, "removed"
            kind = "photos" if q.get("typeimage") == "photos" else "pics"
            try:
                p = int(q.get("p") or 1)
            except ValueError:
                p = 1
            path = self.piclib_path(kind, p)
            return (path, "ok") if path in self.pages else (None, "missing")
        if low == "/piclib/display.asp":
            i = q.get("id", "")
            return ("/piclib/%s.html" % i, "ok") if i.isdigit() and int(i) in self.picture_ids else (None, "missing")
        if low in ("/charts/", "/charts/index.asp", "/charts/results.asp", "/charts"):
            return "/charts/index.html", "ok"
        if low == "/charts/voters.asp":
            return "/charts/voters.html", "ok"
        if low in ("/nectarine/", "/nectarine/index.asp", "/nectarine"):
            return "/nectarine/index.html", "ok"
        if low in V3_URLS:                         # v3 (2001) URLs already linked from late-2000 news
            return V3_URLS[low], "ok"
        m = re.match(r"^/(\d+)/nick\.htm$", low)       # /<id>/nick.htm: the v3 URL of a people sheet
        if low == "/handles/handle.asp" or m:
            i = m.group(1) if m else q.get("id", "")
            return ("/people/%s.html" % i, "ok") if i.isdigit() and int(i) in self.people_ids else (None, "missing")
        if low in ("/skins.asp", "/skincookie.asp", "/skins.html"):
            return "/skins.html", "ok"
        if low in GENERATED:
            return low, "ok"
        if low == "/search.html":
            return "/search.html", "ok"
        if low == "/handles/birthdates.asp":
            return "/people/birthdays.html", "ok"
        if low.startswith("/handles/pictures/"):       # ID photos: only those of published people
            m = re.match(r"^/handles/pictures/(\d+)\.\w+$", low)
            if m and int(m.group(1)) in self.people_ids:
                return self.asset(path)
            return None, "excluded"
        if low in ("/handles/list.asp", "/handles/", "/handles", "/_a/nicklist.htm"):
            if q.get("s"):
                return ARCHIVED, "removed"
            L = (q.get("l") or "A").upper()
            return ("/people/%s.html" % self.letter_slug(L), "ok") if L in self.people_letters else (None, "missing")
        if low == "/handles/interviews.asp":
            return "/people/interviews.html", "ok"
        if low.startswith(PEOPLE_PREFIX):
            return ARCHIVED, "removed"
        m = re.match(r"^/(\d+)/article\.htm$", low)    # v3 URL of an article
        if m:
            return ("/article/%s.html" % m.group(1), "ok") if int(m.group(1)) in self.articles else (None, "missing")
        m = re.match(r"^/skins/[^/]+/(.+\.html?)$", low)  # a skin template addressed directly: its rendered page
        if m and self.page_ci.get("/" + m.group(1)):
            return self.page_ci["/" + m.group(1)], "ok"
        if low in ("/search/", "/search", "/search/index.asp") and not query:
            return "/piclib/index.html", "ok"     # DOWNLOAD menu: the FTP search is gone, the picture library stays
        if low in REMOVED or low.startswith(REMOVED_PREFIXES) or re.match(r"/banner\d*(/.*)?(\.js)?$", low):
            return ARCHIVED, "removed"
        # static pages and files
        for cand in (path, path.rstrip("/") + "/index.html", path.rstrip("/") + "/index.htm"):
            hit = self.page_ci.get(cand.lower())
            if hit:
                return hit, "ok"
        if low.endswith(".asp") or low.endswith(".inc") or low.endswith(".asa"):
            return ARCHIVED, "removed"
        return self.asset(path)

    def asset(self, path):
        """A file: picture library media, then the site root, then skins/<skin>, then skins/tonic (IIS + 404.asp)."""
        rel = unquote(path).lstrip("/")
        low = rel.lower()
        if low.startswith("pictures/piclib/"):
            sub = rel[len("pictures/piclib/"):]
            for cand in (sub, "ori/" + sub.rsplit("/", 1)[-1]):
                src = self.media_ci.get(cand.lower())
                if src and not is_excluded("pictures/piclib/" + src, self.globs):
                    return self.publish_asset("/pictures/piclib/" + (src if not src.startswith("ori/") else src[4:]),
                                              self.media / src)
            return None, "missing"
        if low in ICQ_ICONS and not self.eng.real(rel) and (REPO / "scripts" / "assets" / Path(rel).name).is_file():
            return self.publish_asset("/" + rel, REPO / "scripts" / "assets" / Path(rel).name)
        cands = [rel]
        if not low.startswith("skins/"):
            cands += ["skins/%s/%s" % (self.skin, rel), "skins/%s/%s" % (DEFAULT_SKIN, rel)]
        for c in cands:
            real = self.eng.real(c)
            if real:
                if is_excluded(real, self.globs) or real.lower().endswith((".html", ".htm", ".asp", ".inc", ".asa")):
                    return None, "excluded"
                if real.lower().endswith(".zip") and not self.zip_safe(self.raw / real):
                    return None, "excluded"
                return self.publish_asset("/" + real, self.raw / real)
        return None, "missing"

    def publish_asset(self, path, src):
        if is_excluded(path, self.globs):
            return None, "excluded"
        self.assets[path] = src
        return path, "ok"

    def zip_safe(self, p):
        if p not in self.zip_ok:
            try:
                with zipfile.ZipFile(p) as z:
                    self.zip_ok[p] = not any(n.lower().endswith(EXEC_EXT + (".zip",)) for n in z.namelist())
            except zipfile.BadZipFile:
                self.zip_ok[p] = False
        return self.zip_ok[p]

    def resolve(self, url, base, page):
        """Return (new url | None, reason). External links are returned unchanged; pages of a skin
        link within its prefix (/skin/<name>/...)."""
        new, why = self._resolve(url, base, page)
        if self.prefix and new and new.startswith("/") and new.split("#", 1)[0] in self.pages:
            new = self.prefix + new
        return new, why

    def _resolve(self, url, base, page):
        u = html.unescape(url.strip())
        if not u or u.startswith(("#", "$$")) or re.match(r"(?i)^(javascript|mailto|news|ftp|telnet|irc|file|about):", u):
            return url, "external"
        m = re.match(r"(?i)^https?://([^/:?#]+)(?::(\d+))?(.*)$", u)
        if m:
            host = m.group(1).lower()
            port = m.group(2)
            rest = m.group(3) or "/"
            if host in CROSS_HOSTS and rest in ("/", "") and not port:
                return CROSS_HOSTS[host], "cross"          # another archive of the family
            if host in OWN_HOSTS and port not in (None, "80"):
                return url, "external"                       # the radio streams (:8000): dead, kept as written
            if host in OWN_HOSTS:
                u = rest if rest.startswith("/") else "/" + rest
            elif host.endswith(DEAD_HOSTS):
                if re.sub(r"^www\.", "", host) + rest.rstrip("/") in KEEP_DEAD_PREFIXES:
                    return url, "external"
                return None, "dead third-party service"
            elif host == "nectarine.ojuice.net":       # parameters.inc: the nectarine host showed /nectarine/
                u = "/nectarine/" + rest.lstrip("/")
            else:
                return url, "external"
        elif re.match(r"(?i)^[a-z][a-z0-9+.-]*:", u) or u.startswith("//"):
            return url, "external"
        absu = urljoin("http://x" + base, u)
        parts = urlsplit(absu)
        path = parts.path or "/"
        if path.lower() == "/gonlink.asp":               # click counter: straight to the target
            q = dict(parse_qsl(parts.query, keep_blank_values=True))
            return self._resolve(q.get("u", "/"), "/", page) if q.get("u") else (ARCHIVED, "removed")
        if path.lower() == "/redirban.asp":              # banner click counter: straight to the advertiser
            q = dict(parse_qsl(parts.query, keep_blank_values=True))
            r = self.data.q("SELECT url FROM banners WHERE id = ?", (q.get("id", "0"),)) if q.get("id", "").isdigit() else []
            return (r[0]["url"], "external") if r and r[0]["url"] else (ARCHIVED, "removed")
        new, why = self.route(path, parts.query)
        if new is None:
            return None, why
        old = path + ("?" + parts.query if parts.query else "")
        if why in ("ok", "removed", "people") and (path.lower().endswith((".asp", "/")) or parts.query) and old != new:
            self.urlmap.setdefault(old, new)
        if parts.fragment and new.endswith((".html", ".htm")):
            new += "#" + parts.fragment
        return new, why

    def finalize(self, pg):
        pg.text = pg.text.replace("$$INDEX$$", "")     # some skins put the page index in bottom.inc too
        text = self.neutralize_forms(pg.text, pg)
        text = re.sub(r"(?is)<script\b[^>]*\bsrc\s*=\s*[\"']?https?://[^>]*>\s*</script\s*>", "", text)   # banners, ads
        text = re.sub(r"(?is)<!--\s*AudioRealm banner code.*?End AudioRealm Banner Code\s*-->", "", text)
        text = re.sub(r"(?is)<script\b[^>]*>(?:(?!</script).)*document\.write\((?:(?!</script).)*https?://(?!(?:www\.)?ojuice\.net)"
                      r"(?:(?!</script).)*</script\s*>", "", text)
        text = redact_emails(text)
        text = self.rewrite_links(text, pg)
        text = self.sweep_own_host(text, pg)
        text = mask_ips(text, self.private_ips)
        # a link to a masked host (private server address) leads nowhere: keep its label only
        text = re.sub(r"(?is)<a\b[^>]*\bhref\s*=\s*[\"']?(?:https?|ftp)://[^\"'\s>]*\?\?\?\?[^>]*>(.*?)</a\s*>", r"\1", text)
        if "/js/oj.js" not in text:                     # search fields, skin chooser, home redirect to the chosen skin
            tag = '<SCRIPT src="/js/oj.js"></SCRIPT>\r\n'
            i = text.lower().rfind("</body>")
            text = text[:i] + tag + text[i:] if i >= 0 else text + "\r\n" + tag
        # one charset everywhere: the files are written in iso-8859-1 (the yes skin declared windows-1252)
        text = re.sub(r"(?i)(charset\s*=\s*)(?:windows-1252|cp1252|us-ascii)", r"\1iso-8859-1", text)
        if not re.search(r"(?i)charset\s*=", text):
            m = re.search(r"(?i)<head[^>]*>", text) or re.search(r"(?i)<html[^>]*>", text)
            text = text[:m.end()] + "\n" + META_CHARSET + text[m.end():] if m else META_CHARSET + "\n" + text
        return self.set_title(self.tidy(text, pg), pg)

    GENERIC_TITLES = ("orange juice - the demoscene information center", "untitled document", "")
    HEADING_RE = re.compile(r'(?is)<(?:font|td|span|div|p|h\d)[^>]*class="?(?:title[0-9])"?[^>]*>(.*?)</(?:font|td|span|div|p|h\d)>')

    def derive_title(self, text, pg):
        """A title per page ("<what> - Orange Juice"): the original had the same one everywhere."""
        path = pg.path
        if pg.title:
            return html_encode(pg.title)
        m = re.match(r"/news/page-(\d+)\.html$", path)
        if m:
            return "News, page " + m.group(1)
        m = re.match(r"/people/([0-9a-z]|0-9)\.html$", path)
        if m:
            return "People, " + m.group(1).upper()
        m = re.match(r"/groups/([0-9a-z]|0-9)\.html$", path)
        if m:
            return "Groups, " + m.group(1).upper()
        m = re.match(r"/interviews/(\d+)_[ef]\.htm$", path)
        if m:
            p = self.data.people2000().get(int(m.group(1)))
            return "Interview, " + html_encode(p[0].strip()) if p else "Interview"
        m = re.match(r"/nectarine/(\w+?)playlist\.htm$", path)
        if m:
            return "Nectarine playlist, " + m.group(1).replace("_", "-").upper()
        if path.startswith("/oneliner/"):
            return "Oneliner"
        if path == "/charts/voters.html":
            return "Charts, voters"
        h = self.HEADING_RE.search(text)
        head = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", "", h.group(1))).strip() if h else ""
        if head and head.lower() not in ("archived / archiv&eacute;",):
            return head
        stem = re.sub(r"\.html?$", "", path.rsplit("/", 1)[-1])
        return html_encode((path.strip("/").split("/")[0] + ", " + stem) if "/" in path.strip("/") else stem)

    def set_title(self, text, pg):
        if pg.path == "/index.html":
            return text
        m = re.search(r"(?is)<title[^>]*>(.*?)</title\s*>", text)
        cur = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        if cur.lower() not in self.GENERIC_TITLES:
            return text
        new = "<TITLE>%s - Orange Juice</TITLE>" % self.derive_title(text, pg)
        if m:
            return text[:m.start()] + new + text[m.end():]
        h = re.search(r"(?i)<head[^>]*>", text)
        return text[:h.end()] + "\n" + new + text[h.end():] if h else text

    def tidy(self, text, pg):
        """Small fixes of dead behaviour that the archive would otherwise keep."""
        if pg.path.startswith("/nectarine/"):
            # the "now playing" pages reloaded themselves every 90 s: frozen, they would reload forever
            text = re.sub(r'(?is)<meta\s+http-equiv="?refresh"?\s+content="?\d+"?\s*/?>\s*', "", text)
            # menu of the radio pages: "#what" is the "what is Nectarine" block (a name="how" anchor of index.html)
            index = pg.path == "/nectarine/index.html"
            if 'name="what"' not in text:
                text = text.replace('href="#what"', 'href="%s#how"' % ("" if index else "index.html"))
            if not index:     # the other radio pages share the menu, whose targets are all on index.html
                text = re.sub(r'href="#(\w+)"', lambda m: m.group(0) if 'name="%s"' % m.group(1) in text
                              else 'href="index.html#%s"' % m.group(1), text)
        if pg.path == "/index.html":      # fav(): "add to favorites" of IE channels, no longer called
            text = re.sub(r"(?s)function fav\(\)\s*\{.*?\n\}\s*\n", "", text, count=1)
        if pg.path == "/banners.html":
            text = text.replace("<FONT class=title5>Banners exchange system</FONT>",
                                "<FONT class=title5>Banners exchange system</FONT><BR>"
                                "<FONT class=discreet>[ archived: the banner scripts of ojuice.net below no longer exist / "
                                "les scripts de banni&egrave;res d'ojuice.net ci-dessous n'existent plus ]</FONT>", 1)
        return text

    FORM_RE = re.compile(r"(?is)<form\b([^>]*)>(.*?)</form\s*>")

    def neutralize_forms(self, text, pg):
        """No <form> survives. Lists wrapped in an edit form (groups, parties, oneliner) keep their content;
        the currency converter of the parties page (client-side only) becomes a <DIV>; every other form
        (logins, votes, searches, submissions) is replaced by an inline notice."""
        def fn(m):
            attrs, inner = m.group(1), m.group(2)
            if "TxList" in inner and "action" not in attrs.lower():
                return "<DIV>" + inner.replace('name="Price"', 'name="Price" id="Price"') + "</DIV>"
            keep = re.search(r"(?i)name=\"f_oneliner\"|action=\"(?:addgroup|addparty)\.asp\"", attrs)
            if keep:
                inner = re.sub(r"(?is)<input\b[^>]*>|<select\b.*?</select\s*>|<textarea\b.*?</textarea\s*>", "", inner)
                if "f_oneliner" in attrs:
                    inner += ('<FONT class=discreet>[ archived: the oneliner is closed / archiv&eacute; : '
                              'le oneliner est ferm&eacute; ]</FONT>')
                return inner
            low = attrs.lower()
            kind = None
            if 'action="list.asp"' in low:
                kind = "people"
            elif 'name="searchengine"' in low and pg.old_url.startswith("/groups/"):
                kind = "groups"
            elif 'name="searchengine"' in low and pg.old_url.startswith("/piclib/"):
                return ("search the pictures:<BR>%s<BR><BR>search the photos:<BR>%s<BR><BR>"
                        % (search_widget("pics", 12), search_widget("photos", 12)))
            if kind:                                   # search forms: the same text, searched by /js/oj.js
                inner = re.sub(r"(?is)\s*\(<a href=\"[^\"]*\">help</a>\)", "", inner)
                inner = re.sub(r"(?is)<input\s+type=\"?hidden\"?[^>]*>\s*", "", inner)
                inner = re.sub(r"(?is)<script\b.*?</script\s*>", "", inner)
                return re.sub(r"(?is)<input\b[^>]*>", lambda _m: search_widget(kind), inner, count=1)
            self.dropped.append((pg.path, "form " + attrs.strip()[:60]))
            return ('<FONT class=discreet>[ <A href="%s" class=txtg>archived</A>: this form is no longer available / '
                    'ce formulaire n\'est plus disponible ]</FONT>' % ARCHIVED)
        text = self.FORM_RE.sub(fn, text)
        # focus() scripts of the removed forms
        return re.sub(r"(?is)<script[^>]*>\s*<!--\s*document\.\w+\.\w+\.focus\(\);?\s*//\s*-->\s*</script>", "", text)

    ATTR_RE = re.compile(r"""(?is)\b(href|src|background|action|lowsrc)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
    TAG_RE = re.compile(r"(?s)<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>")

    def rewrite_links(self, text, pg):
        base = pg.old_url.split("?", 1)[0]

        def attr_val(m):
            return m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))

        # 1. anchors: unwrap when the target is gone
        def anchor(m):
            open_tag, label = m.group(1), m.group(2)
            hm = re.search(r"""(?is)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", open_tag)
            if not hm:
                return m.group(0)
            val = next(g for g in hm.groups() if g is not None)
            new, why = self.resolve(val, base, pg)
            if new is None:
                self.unlinked.append((pg.path, val, why))
                return label
            return m.group(0)
        text = outside_scripts(text, lambda t: re.sub(r"(?is)(<a\b[^>]*>)(.*?)</a\s*>", anchor, t))

        # 2. every attribute of every tag
        def tag(m):
            name, attrs = m.group(1).lower(), m.group(2)
            drop = []

            def one(am):
                kind = am.group(1).lower()
                val = attr_val(am)
                new, why = self.resolve(val, base, pg)
                if why == "cross":
                    return '%s="%s"' % (am.group(1), new)
                if why == "external":
                    if kind in ("src", "background", "lowsrc") and re.match(r"(?i)^\s*(https?:)?//", val):
                        drop.append(val)       # third-party image/script: never requested from the archive
                    return am.group(0)
                if new is None:
                    self.unlinked.append((pg.path, val, why))
                    drop.append(val)
                    return ""
                return '%s="%s"' % (am.group(1), new)
            new_attrs = self.ATTR_RE.sub(one, attrs)
            if drop and name in ("img", "embed", "script", "input", "iframe", "frame", "bgsound"):
                self.dropped.append((pg.path, "%s %s" % (name, drop[0][:80])))
                return "" if name != "script" else "<script>"
            if drop:
                new_attrs = re.sub(r"""(?is)\b(src|background|lowsrc)\s*=\s*(?:"(?:https?:)?//[^"]*"|'(?:https?:)?//[^']*'|(?:https?:)?//[^\s"'>]+)""",
                                   "", new_attrs)
            return "<%s%s>" % (m.group(1), new_attrs)
        text = outside_scripts(text, lambda t: self.TAG_RE.sub(tag, t))
        text = re.sub(r"(?is)<script>\s*</script\s*>", "", text)

        # 3. image / flash file names in scripts and event handlers (rollovers, <PARAM value=...>)
        def lit(m):
            val = m.group(2)
            if re.match(r"(?i)^(https?:)?//", val) and not re.match(r"(?i)^https?://(www\.)?ojuice\.net/", val):
                return m.group(0)
            new, why = self.resolve(val, base, pg)
            return m.group(1) + new + m.group(1) if new and why == "ok" else m.group(0)
        return re.sub(r"""(["'])([^"'<>\s()]+\.(?:gif|jpe?g|png|swf))\1""", lit, text, flags=re.I)

    def sweep_own_host(self, text, pg):
        """http://www.ojuice.net/... left inside scripts (window.open, AddFavorite...) -> archive path."""
        def fn(m):
            new, why = self.resolve(m.group(0), "/", pg)
            return new if new and why != "external" else "/"

        def script(m):
            return re.sub(r"(?i)https?://(?:www\.)?ojuice\.net(?:/[^\s\"'<>)]*)?", fn, m.group(0))
        text = re.sub(r"(?is)<script\b.*?</script\s*>", script, text)
        text = re.sub(r"""(?is)\bhref\s*=\s*"javascript:[^"]*\"""", script, text)
        return re.sub(r"""(?is)\bon[a-z]+\s*=\s*"[^"]*\"""", script, text)

    # ------------------------------------------------------------------ run
    def build(self):
        """Render and finalize every page of this skin (nothing is written yet)."""
        self.media_ci = {}
        for root, dirs, files in os.walk(self.media):
            dirs.sort()
            for f in sorted(files):
                rel = Path(root, f).relative_to(self.media).as_posix()
                self.media_ci.setdefault(rel.lower(), rel)
        self.render_news()
        self.render_articles()
        self.render_oneliner_popups()
        self.render_groups()
        self.render_parties()
        self.render_piclib()
        self.render_charts()
        self.render_people()
        self.render_static()
        self.render_archived()
        self.render_skins_page()
        self.render_search_page()
        self.page_ci = {p.lower(): p for p in self.pages}
        for path in sorted(self.pages):
            pg = self.pages[path]
            pg.text = self.finalize(pg)
        self.assets["/favicon.ico"] = self.raw / self.eng.real("favicon.ico")

    def write_pages(self):
        for path in sorted(self.pages):
            pg = self.pages[path]
            write_text(self.out / (self.prefix + path).lstrip("/"),
                       add_removal_note(pg.text) if needs_removal_note(pg) else pg.text)
        self.pages_written = len(self.pages)
        self.pages = {p: None for p in self.pages}      # free the text, keep the set

    def write_meta(self, meta_dir, hosting):
        """Default skin only: hosting/404.html and build-meta/."""
        page404 = Page("/404.html", "/404.html", self.render_404(), "404")
        write_text(hosting / "404.html", self.finalize(page404))
        # build-meta
        meta_dir.mkdir(parents=True, exist_ok=True)
        um = {k: v for k, v in self.urlmap.items()}
        for a_id in sorted(self.articles):
            um.setdefault("/view_article.asp?id=%d" % a_id, "/article/%d.html" % a_id)
        doc = {
            "comment": "Old www.ojuice.net URL (path?query, as linked) -> path in the static archive. "
                       "'urls' are exact matches (query parameter names lower-case); 'patterns' apply when no exact "
                       "match exists ('*' = anything, query ignored). gonlink.asp?u=<url> redirected to <url>.",
            "urls": dict(sorted(um.items())),
            "patterns": [
                {"match": "/gonlink.asp?*", "to": "<value of the u parameter>"},
                {"match": "/view_article.asp?id=*", "to": "/article/<id>.html"},
                {"match": "/index.asp?p=*", "to": "/news/page-<p>.html"},
                {"match": "/handles/handle.asp?id=*", "to": "/people/<id>.html"},
                {"match": "/handles/list.asp?l=*", "to": "/people/<letter>.html ('#' = 0-9)"},
                {"match": "/handles/pictures/<id>.<ext>", "to": "same path (published people only)"},
                {"match": "/handles/*", "to": ARCHIVED},
                {"match": "/admin/*", "to": ARCHIVED},
                {"match": "/search/*", "to": ARCHIVED},
                {"match": "/wap/*", "to": ARCHIVED},
                {"match": "*.asp", "to": ARCHIVED},
                {"match": "*.asp?*", "to": ARCHIVED},
            ],
        }
        (meta_dir / "url-map.json").write_text(json.dumps(doc, indent=1, ensure_ascii=True) + "\n", encoding="ascii")
        # paths only, redacted like the pages (dead third-party links are counted, not listed)
        dead = sum(1 for _p, _t, r in self.unlinked if r.startswith("dead"))
        lines = sorted(set("%s\t%s\t%s" % (p, html.unescape(t), r) for p, t, r in self.unlinked if not r.startswith("dead")))
        lines.append("*\t(%d links to dead third-party services: %s)\tdead third-party service" % (dead, ", ".join(DEAD_HOSTS)))
        clean = lambda t: mask_ips(redact_emails(t), self.private_ips)
        (meta_dir / "unlinked.tsv").write_text(clean("page\ttarget\treason\n" + "\n".join(lines) + "\n"), encoding="utf-8")
        drops = sorted(set("%s\t%s" % (p, re.sub(r"\?.*", "?...", w) if "://" in w else w) for p, w in self.dropped))
        (meta_dir / "dropped.tsv").write_text(clean("page\twhat\n" + "\n".join(drops) + "\n"), encoding="utf-8")


def private_ips(raw):
    """Visitor IPs of _data/skins_ip.txt and the office IP of admin/index.asp (read, never published)."""
    ips = set()
    for line in read_text(raw / "_data" / "skins_ip.txt").splitlines():
        ip = line.split("=", 1)[0].strip()
        if IPV4.fullmatch(ip):
            ips.add(ip)
    t = read_text(raw / "admin" / "index.asp")
    ips.update(re.findall(r'REMOTE_ADDR"\)\s*=\s*"([\d.]+)"', t))
    return ips


def outside_scripts(text, fn):
    """Apply fn to the parts of the page that are not inside <script>...</script>."""
    parts = re.split(r"(?is)(<script\b.*?</script\s*>)", text)
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


def sdk_paths(text):
    """The ak skin was saved from the skin development kit with links to the author's disk
    ("../../Program%20Files/ojuice%20sdk/pl/SKINPL/x"): they are made relative to the site root."""
    return re.sub(r"(?i)(?:\.\./)*Program(?:%20| )Files/ojuice(?:%20| )sdk/[^/\"']+/[^/\"']+/", "/", text)


def search_widget(kind, size=20):
    """A search field without <form>: /js/oj.js reads it on Enter or on the button and opens /search.html."""
    return ('<SPAN class="oj-search" data-t="%s"><INPUT type="TEXT" size=%d maxlength=100 value="">'
            '<INPUT type="BUTTON" value="go"></SPAN>' % (kind, size))


def image_size(b):
    """(width, height) of a GIF, PNG or JPEG file, else None (stdlib only)."""
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return b[6] | b[7] << 8, b[8] | b[9] << 8
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    if b[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(b):
            if b[i] != 0xFF:
                i += 1
                continue
            marker = b[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                i += 2 if marker != 0xFF else 1
                continue
            seg = int.from_bytes(b[i + 2:i + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(b[i + 7:i + 9], "big"), int.from_bytes(b[i + 5:i + 7], "big")
            i += 2 + seg
    return None


# ------------------------------------------------------------------- redaction
MAILTO_A_RE = re.compile(r"<a\b[^>]*href\s*=\s*[\"']?\s*mailto:[^>]*>(.*?)</a\s*>", re.I | re.S)
EMAIL_RE = re.compile(r"([A-Za-z0-9._+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")


FAMILY_LOCALS = set()      # lower-case local parts that hold a family name (private/email-family.txt + the people table)


def norm_name(v):
    return re.sub(r"[^a-z]", "", (v or "").lower())


def keep_email_whole(local, family_names=()):
    """Owner rule: the domain is truncated (`local@????`) only when the local part holds a family name;
    a first name or a nick alone keeps the full address."""
    low = local.lower()
    if low in FAMILY_LOCALS:
        return False
    n = norm_name(low)
    return not any(len(f) >= 3 and f in n for f in map(norm_name, family_names))


def redact_emails(text):
    """mailto: links of truncated addresses become their label; every local@domain whose local part
    holds a family name becomes local@????, the others stay complete (links included)."""
    def anchor(m):
        a = re.search(r"(?i)mailto:\s*([^\"'>\s?]+)", m.group(0))
        e = EMAIL_RE.fullmatch(html.unescape(a.group(1))) if a else None
        if e and keep_email_whole(e.group(1)):
            kept = re.sub(r"(?i)(mailto:\s*[^\"'?>\s]+)\?[^\"'>]*", r"\1", m.group(0))   # no pre-filled subject
            return re.sub(r"(?i)mailto:", "\x00MT\x00", kept)
        return m.group(1)
    text = MAILTO_A_RE.sub(anchor, text)
    text = re.sub(r"(?i)mailto:", "", text)
    text = text.replace("\x00MT\x00", "mailto:")
    return EMAIL_RE.sub(lambda m: m.group(0) if keep_email_whole(m.group(1)) else m.group(1) + "@" + MASK, text)


def mask_ips(text, private):
    """IPv4 addresses are masked unless they are the host of an http(s)/ftp URL (1990s public servers);
    the office / visitor IPs of the original site (`private`) are masked everywhere."""
    def fn(m):
        before = text[max(0, m.start() - 12):m.start()].lower()
        return m.group(0) if before.endswith(("://", "@")) and m.group(0) not in private else MASK
    return IPV4.sub(fn, text)


def skin_names(raw):
    """Real skins: folders of skins/ holding files (kano and pixol are empty). tonic first."""
    names = sorted(d.name for d in (raw / "skins").iterdir()
                   if d.is_dir() and any(f.is_file() and f.name != ".DS_Store" for f in d.rglob("*")))
    return [DEFAULT_SKIN] + [n for n in names if n != DEFAULT_SKIN]


def clean_text(s, private, limit=0):
    """Plain text for the search indexes: tags removed, entities decoded, e-mails and IPs redacted."""
    t = html.unescape(re.sub(r"(?s)<[^>]*>", " ", s or ""))
    t = mask_ips(redact_emails(re.sub(r"\s+", " ", t).strip()), private)
    if limit and len(t) > limit:
        cut = t[:limit]
        if t[limit] != " " and "@" in cut.rsplit(" ", 1)[-1]:     # never cut an e-mail address in the middle
            cut = cut.rsplit(" ", 1)[0] if " " in cut else ""
        return cut.rstrip() + "..."
    return t


def write_indexes(site, out):
    """/idx/*.json for /js/oj.js: only fields already shown on public pages (no real name, e-mail or
    birth data). Compact arrays; ASCII JSON."""
    d, priv = site.data, site.private_ips
    news = []
    for i, r in enumerate(d.news()):
        url = (r["url"] or "").strip()
        link = "/article/%d.html" % r["id"] if r["isarticle"] else (url if re.match(r"(?i)^(https?|ftp)://", url) else "")
        link = mask_ips(redact_emails(link), priv)
        news.append([r["id"], r["dt"][:10], clean_text(r["title"], priv), clean_text(r["smalltxt"], priv, 300),
                     clean_text(d.author(r["id_people"], r["nick"]), priv), r["category"], i // PERPAGE_NEWS + 1, link])
    pics = []
    for r in d.q("""SELECT * FROM pictures WHERE isactive = 1 AND dtadded < ? ORDER BY dtadded DESC, id""", (CUTOFF,)):
        pics.append([r["id"], r["isphoto"]] + [clean_text(r[k], priv, 200 if k == "description" else 0)
                                              for k in ("legend", "description", "author", "event", "who", "place")]
                    + [r["picyear"] or 0])
    people = [[pid, clean_text(d.author(pid), priv)] for pid in sorted(site.people_ids)]
    groups = []
    for r in d.q("""SELECT g.name, g.letter, g.url, c.country FROM groups g LEFT JOIN countries c ON c.id = g.id_country
                    WHERE g.active = 1 AND g.dt < ? ORDER BY g.name COLLATE NOCASE, g.id""", (CUTOFF,)):
        url = (r["url"] or "").strip()
        groups.append([clean_text(r["name"], priv), site.letter_slug(r["letter"]),
                       url if re.match(r"(?i)^https?://", url) else "", clean_text(r["country"], priv)])
    sizes = {}
    for name, data in (("news", news), ("pictures", pics), ("people", people), ("groups", groups)):
        p = out / "idx" / (name + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="ascii")
        sizes[name] = (len(data), p.stat().st_size)
    return sizes


def write_js(skins, out):
    js = (REPO / "scripts" / "oj.js").read_text(encoding="utf-8")
    js = js.replace("/*SKINS*/[]", json.dumps(skins))
    p = out / "js" / "oj.js"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(js, encoding="ascii")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skins", default="all",
                    help="comma-separated skins to render (default: all real skins; tonic is always rendered, at the root)")
    ap.add_argument("--raw", default=str(PRIVATE / "site-raw"), help="original ASP tree (read-only)")
    ap.add_argument("--db", default=str(PRIVATE / "db" / "ojuice.sqlite"), help="SQLite extraction (read-only)")
    ap.add_argument("--media", default=str(PRIVATE / "media" / "piclib"), help="picture library files (read-only)")
    ap.add_argument("--out", default=str(REPO / "site"), help="published tree, emptied first (default: <repo>/site)")
    ap.add_argument("--meta-dir", default=str(REPO / "build-meta"), help="where url-map.json & co are written")
    ap.add_argument("--hosting", default=str(REPO / "hosting"), help="where 404.html is written")
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    for p in (a.raw, a.db, a.media):
        if not Path(p).exists():
            ap.error("input not found: %s" % p)
    if out in (REPO, Path.home(), Path("/")) or PRIVATE in out.parents or out == PRIVATE or (out / ".git").exists():
        ap.error("refusing to empty %s (not a dedicated output folder)" % out)
    if out.exists():
        for _ in range(3):   # macOS may drop a .DS_Store while we delete
            shutil.rmtree(out, ignore_errors=True)
            if not out.exists():
                break
    out.mkdir(parents=True)
    a.out = str(out)
    raw = Path(a.raw)
    skins = skin_names(raw)
    if a.skins != "all":
        want = [x.strip() for x in a.skins.split(",") if x.strip()]
        unknown = [x for x in want if x not in skins]
        if unknown:
            ap.error("unknown skin(s): %s (known: %s)" % (", ".join(unknown), ", ".join(skins)))
        skins = [DEFAULT_SKIN] + [x for x in skins[1:] if x in want]
    data = Data(a.db, raw)
    # local parts that hold a family name: the people table + the reviewed list private/email-family.txt
    FAMILY_LOCALS.update(data.family_locals())
    fam_file = PRIVATE / "email-family.txt"
    if fam_file.exists():
        FAMILY_LOCALS.update(l.strip().lower() for l in read_text(fam_file).splitlines()
                             if l.strip() and not l.startswith("#"))
    assets, tonic = {}, None
    for skin in skins:
        site = Site(a, skin, "" if skin == DEFAULT_SKIN else "/skin/" + skin, data, skins)
        site.build()
        site.write_pages()
        for k, v in site.assets.items():
            if k in assets and assets[k] != v:
                raise RuntimeError("asset collision: %s (%s / %s)" % (k, assets[k], v))
            assets[k] = v
        size = sum(f.stat().st_size for f in (out / site.prefix.lstrip("/") if site.prefix else out).rglob("*.htm*")
                   if not (not site.prefix and "skin" in f.relative_to(out).parts[:1]))
        print("skin %-12s pages %5d  html %6.1f MB  unlinked %d  dropped %d"
              % (skin, site.pages_written, size / 1e6, len(site.unlinked), len(site.dropped)))
        if skin == DEFAULT_SKIN:
            site.write_meta(Path(a.meta_dir), Path(a.hosting))
            tonic = site
            for k in sorted(site.counts):
                print("  %-16s %s" % (k, site.counts[k]))
    for path in sorted(assets):
        if path.lower().endswith((".htm", ".html")):
            continue
        dst = out / path.lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(assets[path], dst)
    write_js(skins, out)
    sizes = write_indexes(tonic, out)
    print("assets %d; search indexes: %s" % (len(assets), ", ".join("%s %d entries %.0f KB" % (k, n, b / 1024)
                                                                     for k, (n, b) in sizes.items())))
    print("url-map %d entries" % len(tonic.urlmap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
