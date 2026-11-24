#!/usr/bin/env python3
"""Verify the statified build (stdlib only, read-only).

    python3 -I scripts/verify_build.py [--build site] [--hosting hosting] [--raw private/site-raw]

Hard checks (exit 1 on failure):
  * the build looks complete (key pages present, enough pages)
  * no server-side or executable file (*.asp, *.inc, *.asa, *.shtml, *.exe, ...) and nothing matching
    scripts/exclude-globs.txt
  * no ASP/macro leftovers (`<%`, `%>`, `$$MACRO$$`, `#include`, VBScript API calls) and no `<form` at all
  * every html page declares the charset it is written in (iso-8859-1)
  * every internal link (href/src/background/action) resolves to a file of the build; no internal link
    keeps an old dynamic form (`.asp`, query string)
  * no link or script pointing to http(s)://(www.)ojuice.net
  * no e-mail address with a family name in its local part (it must be `local@????`; first names and nicks keep
    their address)
  * none of the secret values of the original site (read at run time from private/site-raw/global.asa,
    admin/global.bak and admin/index.asp, so they never appear in the repository) and none of the visitor
    or office IP addresses of private/site-raw/_data/skins_ip.txt / admin/index.asp
  * people pages: no birth year, no e-mail domain, no department; ID photos only of published people
    (values read at run time from private/db/ojuice.sqlite)
.DS_Store files (recreated by macOS) are ignored.
  * every skin rendered under /skin/<name>/ links within its own prefix; /js/oj.js and the search
    indexes /idx/*.json exist and hold only the expected public fields
  * no IPv4 address outside the host part of a URL
When the raw tree or the database is absent (CI), those checks are skipped with a notice.
"""
import argparse
import fnmatch
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

ENC = "iso-8859-1"
REPO = Path(__file__).resolve().parent.parent
TEXT_EXT = (".htm", ".html", ".txt", ".js", ".css", ".json", ".xml", ".wml")
HTML_EXT = (".htm", ".html")
FORBIDDEN_EXT = (".asp", ".aspx", ".asa", ".inc", ".shtml", ".aspsav", ".bak", ".exe", ".dll", ".com", ".scr",
                 ".bat", ".cgi", ".pl", ".php", ".php3")
LINK_RE = re.compile(r"""(?is)\b(?:href|src|background|action|lowsrc)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
SKIP_SCHEME = re.compile(r"(?i)^(?:[a-z][a-z0-9+.-]*:|//|#)")
LEFTOVERS = [
    ("asp open tag", re.compile(r"<%")),
    ("asp close tag", re.compile(r"(?<!\d)%>")),          # "height=100%>" is HTML, not ASP
    ("ssi/asp include", re.compile(r"(?i)<!--\s*#(?:include|exec|echo|config)")),
    ("$$ macro", re.compile(r"\$\$[A-Za-z0-9_:\-]+")),
    ("vbscript api", re.compile(r"(?i)\b(?:Server\.CreateObject|FileSystemObject|OpenTextFile|Request\.(?:Form|QueryString|"
                                r"ServerVariables|Cookies)|Response\.(?:Write|Redirect)|Server\.MapPath)\b")),
    ("form tag", re.compile(r"(?i)<form\b")),
]
# The removal-request footer (statify.py) carries the only mailto: of the site: leave it out of the scans.
REMOVAL_NOTE_RE = re.compile(r'(?s)<div id="removal-note".*?</div>')
EMAIL_RE = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def norm(v):
    return re.sub(r"[^a-z]", "", (v or "").lower())


def family_locals():
    """Local parts holding a family name (private/email-family.txt + people table); None without private/."""
    lst, db = REPO / "private" / "email-family.txt", REPO / "private" / "db" / "ojuice.sqlite"
    if not lst.is_file() or not db.is_file():
        return None
    out = {l.strip().lower() for l in lst.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}
    cx = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    for last, email in cx.execute("SELECT lastname, email FROM people WHERE email LIKE '%@%' AND lastname IS NOT NULL"):
        local = email.split("@", 1)[0].strip()
        if len(norm(last)) >= 3 and norm(last) in norm(local):
            out.add(local.lower())
    return out
OWN_HOST_RE = re.compile(r"(?i)https?://(?:www\.)?ojuice\.net(?![\w.-])(?!:(?!80\b)\d)")   # the radio streams (:8000) are dead links kept as written
IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")
KEY_PAGES = ("index.html", "skins.html", "search.html", "archived.html", "news/page-2.html", "article/1111.html", "groups/a.html",
             "parties/index.html", "parties/past.html", "piclib/index.html", "piclib/photos.html",
             "charts/index.html", "people/a.html", "people/145.html", "people/interviews.html", "faq.html", "credits.html", "links/links.html", "v3.htm")


def rd(p):
    return Path(p).read_bytes().decode(ENC)


def all_files(root):
    out = []
    for r, ds, fs in os.walk(root):
        ds.sort()
        for f in sorted(fs):
            if f != ".DS_Store":               # macOS recreates them on its own: ignored by every script
                out.append(Path(r, f).relative_to(root).as_posix())
    return out


def load_globs(path):
    if not path.is_file():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def excluded(rel, globs):
    rel_l = rel.lower()
    base = rel_l.rsplit("/", 1)[-1]
    for g in globs:
        g = g.lstrip("/").lower()
        if g.endswith("/"):
            d = g.rstrip("/")
            if rel_l == d or rel_l.startswith(d + "/") or ("/" not in d and ("/" + d + "/") in ("/" + rel_l)):
                return True
        elif "/" not in g:
            if fnmatch.fnmatchcase(base, g):
                return True
        elif fnmatch.fnmatchcase(rel_l, g):
            return True
    return False


def links(text):
    text = re.sub(r"(?is)<script\b.*?</script\s*>", "", text)   # URLs built by scripts are not links
    for m in LINK_RE.finditer(text):
        v = next(g for g in m.groups() if g is not None)
        yield v.strip()


def scripts_and_attrs(text):
    """Where a URL is a link: tag attributes and script blocks (visible prose may name the old host)."""
    for m in re.finditer(r"(?is)<script\b.*?</script\s*>", text):
        yield m.group(0)
    for m in re.finditer(r"(?s)<[a-zA-Z][^>]*>", text):
        yield m.group(0)


def html_ref(s):
    """The pages encode non-ASCII characters as numeric references (Server.HTMLEncode)."""
    return re.sub(r"[^\x00-\x7f]", lambda m: "&#%d;" % ord(m.group(0)), s)


def secrets_and_ips(raw):
    """Secret values and private IPs of the original site, read at run time (never stored in the repo)."""
    secrets, ips = set(), set()
    for f in ("global.asa", "admin/global.bak", "admin/index.asp"):
        p = raw / f
        if not p.is_file():
            continue
        t = p.read_bytes().decode("cp1252", errors="replace")
        for m in re.finditer(r'Application\("([^"]*)"\)\s*=\s*"([^"]+)"', t):
            if "PASS" in m.group(1).upper():
                secrets.add(m.group(2))
        for m in re.finditer(r'Request\.Form\("password"\)\s*=\s*"([^"]+)"', t, re.I):
            secrets.add(m.group(1))
        for m in re.finditer(r'Request\.ServerVariables\("REMOTE_ADDR"\)\s*=\s*"([^"]+)"', t, re.I):
            ips.add(m.group(1))
    p = raw / "_data" / "skins_ip.txt"
    if p.is_file():
        for line in p.read_text(encoding="latin-1").splitlines():
            ip = line.split("=", 1)[0].strip()
            if IPV4.fullmatch(ip):
                ips.add(ip)
    return secrets, ips


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", default=str(REPO / "site"))
    ap.add_argument("--hosting", default=str(REPO / "hosting"))
    ap.add_argument("--raw", default=str(REPO / "private" / "site-raw"),
                    help="original tree, only to read the secret values and private IPs to look for")
    ap.add_argument("--show", type=int, default=12)
    a = ap.parse_args(argv)
    build, hosting, raw = Path(a.build), Path(a.hosting), Path(a.raw)
    if not build.is_dir():
        print("build folder missing: %s (run scripts/statify.py first)" % build)
        return 2
    failures = 0

    def fail(title, items):
        nonlocal failures
        failures += 1
        items = sorted(set(items))
        print("FAIL %s: %d" % (title, len(items)))
        for it in items[:a.show]:
            print("   ", it)
        if len(items) > a.show:
            print("    ... +%d more" % (len(items) - a.show))

    files = all_files(build)
    fset = set(files)
    extra = [("hosting/" + f, hosting / f) for f in all_files(hosting)] if hosting.is_dir() else []
    pages = [f for f in files if f.lower().endswith(HTML_EXT)]
    print("build: %d files, %d html pages, %.1f MB" % (len(files), len(pages),
                                                        sum((build / f).stat().st_size for f in files) / 1e6))
    missing = [p for p in KEY_PAGES if p not in fset]
    if missing or len(pages) < 1000:
        fail("build looks incomplete (missing key pages / too few pages)", missing or [str(len(pages))])

    # 1. file types and exclusions
    bad = [f for f in files if f.lower().endswith(FORBIDDEN_EXT)]
    if bad:
        fail("server-side or executable files present", bad)
    globs = load_globs(REPO / "scripts" / "exclude-globs.txt")
    pub = [f for f in files if excluded(f, globs)] + [n for n, _ in extra if excluded(n.split("/", 1)[1], globs)]
    if pub:
        fail("published files matching exclusion globs", pub)

    texts = {}
    for f in files:
        if f.lower().endswith(TEXT_EXT):
            texts[f] = REMOVAL_NOTE_RE.sub("", rd(build / f))
    for n, p in extra:
        if n.lower().endswith(TEXT_EXT):
            texts[n] = rd(p)

    # 2. leftovers, e-mails, own host. Owner rule: an address is complete when its local part is a first
    # name or a nick, `local@????` when it holds a family name: the reviewed list and the people table
    # (private, never committed) say which local parts must not appear complete.
    family = family_locals()
    if family is None:
        print("notice: private e-mail list not found: family-name e-mail check skipped (CI mode)")
        family = set()
    hits = defaultdict(list)
    for f, t in texts.items():
        for label, rx in LEFTOVERS:
            m = rx.search(t)
            if m:
                hits[label].append("%s: %s" % (f, t[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")))
        for m in EMAIL_RE.finditer(t):
            if m.group(0).split("@", 1)[0].lower() in family:
                hits["e-mail address with a family name"].append("%s: %s" % (f, m.group(0)))
        for chunk in scripts_and_attrs(t):
            m = OWN_HOST_RE.search(chunk)
            if m:
                hits["link/script to www.ojuice.net"].append("%s: %s" % (f, chunk[:90].replace("\n", " ")))
    for label in sorted(hits):
        fail("leftover %s" % label, hits[label])

    # 3. charset
    nocs = [f for f in pages + [n for n, _ in extra if n.endswith(HTML_EXT)]
            if not re.search(r'(?i)<meta[^>]+charset\s*=\s*iso-8859-1', texts[f])]
    if nocs:
        fail("html without an iso-8859-1 charset declaration", nocs)
    other = [f for f in pages if re.search(r"(?i)charset\s*=\s*(?!iso-8859-1)[\w-]+", texts[f])]
    if other:
        fail("html declaring another charset", other)

    # 4. internal links
    lower = {f.lower() for f in fset}
    broken, dynamic, case = [], [], []
    for f in pages + [n for n, _ in extra if n.endswith(HTML_EXT)]:
        here = "" if f.startswith("hosting/") else os.path.dirname(f)
        for raw_link in links(texts[f]):
            if not raw_link or SKIP_SCHEME.match(raw_link):
                continue
            u = raw_link.split("#", 1)[0]
            path, _, query = u.partition("?")
            if query or path.lower().endswith((".asp", ".inc", ".asa")):
                dynamic.append("%s -> %s" % (f, raw_link))
            if not path:
                continue
            path = unquote(path)
            target = os.path.normpath(path.lstrip("/") if path.startswith("/") else os.path.join(here, path))
            target = target.replace(os.sep, "/")
            if target.startswith(".."):
                broken.append("%s -> %s" % (f, raw_link))
                continue
            if target == ".":
                target = ""
            cands = [target] if "." in target.rsplit("/", 1)[-1] else []
            cands += [(target.rstrip("/") + "/" if target else "") + n for n in ("index.html", "index.htm")]
            if any(c in fset for c in cands):
                continue
            (case if any(c.lower() in lower for c in cands) else broken).append("%s -> %s" % (f, raw_link))
    # pages of a skin (/skin/<name>/...) link to pages of the same skin
    escaped = []
    for f in pages:
        ms = re.match(r"skin/([^/]+)/", f)
        if not ms:
            continue
        for raw_link in links(texts[f]):
            p0 = raw_link.split("#", 1)[0]
            if p0.startswith("/") and p0.lower().endswith((".html", ".htm")) and not p0.startswith("/skin/%s/" % ms.group(1)):
                escaped.append("%s -> %s" % (f, raw_link))
    if escaped:
        fail("skin pages linking out of their skin", escaped)
    if broken:
        fail("broken internal links / missing assets", broken)
    if case:
        fail("internal links differing only by case (404 on bunny.net)", case)
    if dynamic:
        fail("internal links keeping a dynamic form (.asp / query string)", dynamic)
    print("internal links checked in %d pages" % (len(pages) + len(extra)))

    # 4b. client-side search: script and indexes present; indexes hold only the public fields
    # (news: id, date, title, text, author, category, page, link; pictures: id, type, legend, description,
    # author, event, who, place, year; people: id, "nick / group"; groups: name, letter, url, country)
    import json
    shapes = {"news": 8, "pictures": 9, "people": 2, "groups": 4}
    bad_idx = [] if "js/oj.js" in fset else ["js/oj.js missing"]
    for name, n in shapes.items():
        f = "idx/%s.json" % name
        if f not in fset:
            bad_idx.append(f + " missing")
            continue
        rows = json.loads(texts[f])
        if not rows or any(len(r) != n for r in rows):
            bad_idx.append("%s: entries are not the %d expected fields" % (f, n))
    if bad_idx:
        fail("search script / indexes", bad_idx)
    else:
        print("ok: search indexes %s" % ", ".join("%s %.0f KB" % (k, (build / "idx" / (k + ".json")).stat().st_size / 1024)
                                                   for k in shapes))

    # 5. secrets and private IPs (also in build-meta/, committed with the site)
    meta = REPO / "build-meta"
    if meta.is_dir():
        for f in all_files(meta):
            t = (meta / f).read_text(encoding="utf-8", errors="replace")
            texts["build-meta/" + f] = t
            for m in EMAIL_RE.finditer(t):
                if m.group(0).split("@", 1)[0].lower() in family:
                    fail("e-mail address with a family name in build-meta", ["build-meta/%s: %s" % (f, m.group(0))])
    if raw.is_dir():
        secrets, ips = secrets_and_ips(raw)
        if not secrets:
            fail("could not read the secret values from the raw tree", [str(raw)])
        found = []
        for f, t in texts.items():
            for s in secrets:
                if s in t:
                    found.append("%s: <secret of %d chars>" % (f, len(s)))
            for m in IPV4.finditer(t):
                if m.group(0) in ips:
                    found.append("%s: <private IP>" % f)
        if found:
            fail("secret values or private IPs of the original site", found)
        else:
            print("ok: none of the %d secret values and %d private IPs found" % (len(secrets), len(ips)))
    else:
        print("notice: raw tree not found (%s): secret and private IP checks skipped (CI mode)" % raw)

    # 5b. people data. Shown by owner decision: real name, e-mail as local@????, ICQ number, sex, birth
    # day/month, ID photo. Checked against the private database at run time (values never in the repo):
    # no birth year next to a published person's day/month (sheet "dd/mm/yyyy", birthdays "dd Mon yyyy"),
    # no e-mail of a published person with its domain (any real address also fails the global e-mail
    # check above), no French
    # department as the 2000 sheet printed it ("[dep"), and ID photos only for published people.
    db = REPO / "private" / "db" / "ojuice.sqlite"
    if db.is_file():
        cx = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        leaks, checked = [], 0
        cutoff = "2000-11-20"
        info = {r[0]: r[1:] for r in cx.execute(
            "SELECT id, email, birthyear, birthmd, department, department_num, dt_crea, irc_net, "
            "lastname, isemailhidden FROM people")}
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        for f in pages:
            m = re.fullmatch(r"(?:skin/[^/]+/)?people/(\d+)\.html", f)
            if m and int(m.group(1)) in info:
                checked += 1
                t = texts[f].lower()
                email, by, bmd, dep, depn, _dt, ircn, last, hidden = [("" if v is None else str(v)).strip().lower()
                                                                      for v in info[int(m.group(1))]]
                if len(bmd) == 4 and by.isdigit() and int(by) > 1900 and \
                        re.search(r"%s/%s/\s*(?:19)?%s\b" % (bmd[2:], bmd[:2], by[-2:]), t):
                    leaks.append("%s: birth year" % f)
                if "@" in email and email in t and (hidden == "1" or email.split("@", 1)[0] in family
                                                    or (len(norm(last)) >= 3 and norm(last) in norm(email.split("@", 1)[0]))):
                    leaks.append("%s: e-mail with its domain" % f)
                if (dep or depn) and (dep or depn) not in ircn and ("[" + html_ref(dep or depn)) in t:
                    leaks.append("%s: department" % f)
            if re.fullmatch(r"(?:skin/[^/]+/)?people/birthdays\.html", f):
                for date, pid in re.findall(r"\[([^\]]*)\][^\n]*?people/(\d+)\.html", texts[f]):
                    r = info.get(int(pid))
                    if r and r[1] and re.search(r"\b%s\b" % r[1], date):
                        leaks.append("%s: birth year of %s" % (f, pid))
                if re.search(r"\[\d\d (?:%s) \d{4}\]" % "|".join(months), texts[f]):
                    leaks.append("%s: a birth year" % f)
        for f in files:
            m = re.match(r"handles/pictures/(.*)$", f)
            if m:
                mm = re.fullmatch(r"(\d+)\.\w+", m.group(1))
                r = info.get(int(mm.group(1))) if mm else None
                if not r or not r[5] or r[5] >= cutoff:
                    leaks.append("photo of an unpublished person or unexpected file: " + f)
        if leaks:
            fail("people data beyond what is published (birth year, e-mail domain, department, photos)", leaks)
        else:
            print("ok: people data as published (no birth year, no e-mail domain, photos of published people only) "
                  "on %d people pages" % checked)
    else:
        print("notice: private database not found: people leak check skipped (CI mode)")

    # 6. IPv4 outside URL hosts
    ips_out, urlips = [], 0
    for f, t in texts.items():
        for m in IPV4.finditer(t):
            if t[max(0, m.start() - 12):m.start()].lower().endswith(("://", "@")):
                urlips += 1
            else:
                ips_out.append("%s: %s" % (f, t[max(0, m.start() - 25):m.end() + 10].replace("\n", " ")))
    if ips_out:
        fail("IPv4 outside a URL host", ips_out)
    else:
        print("ok: %d IPv4 only as URL hosts, 0 elsewhere" % urlips)

    print("RESULT:", "FAIL" if failures else "OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
