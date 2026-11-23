#!/usr/bin/env python3
"""Check the public site: sample pages of every kind, legacy redirects, read-only hardening.

    python3 -I scripts/smoke_test.py               # CI (no site/): statuses, headers, redirects
    python3 -I scripts/smoke_test.py --sample 300  # on the Mac: also compares 300 random pages with site/

The checked pages and old URLs are fixed or read from build-meta/url-map.json (versioned), so the
test also runs in GitHub Actions, where site/ (git-ignored) does not exist. When site/ exists, each
fetched page is compared byte for byte with it. Retries for a while, since a deployment or purge
takes some time to reach every CDN edge. No secret needed.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python3 -I leaves the script folder out
import bunny  # noqa: E402

ATTEMPTS = 6
DELAY = 20  # seconds between attempts
WORKERS = 16
UA = {"User-Agent": "smoke-test", "Cache-Control": "no-cache"}
NOT_FOUND_MARKER = b"File not found"  # hosting/404.html

# One page of each kind, plus skins: (path, expected content type prefix).
PAGES = [
    ("/", "text/html"),
    ("/index.html", "text/html"),
    ("/news/page-2.html", "text/html"),
    ("/article/1111.html", "text/html"),
    ("/groups/a.html", "text/html"),
    ("/parties/index.html", "text/html"),
    ("/charts/index.html", "text/html"),
    ("/piclib/index.html", "text/html"),
    ("/people/birthdays.html", "text/html"),
    ("/skins.html", "text/html"),
    ("/search.html", "text/html"),
    ("/archived.html", "text/html"),
    ("/skin/pl/index.html", "text/html"),
    ("/skin/poi/article/1111.html", "text/html"),
    ("/js/oj.js", ""),
    ("/idx/news.json", "application/json"),
]

# Old URL -> expected Location (None: 404 with the custom page).
REDIRECTS = [
    ("/view_article.asp?id=1111", "/article/1111.html"),
    ("/index.asp?p=2", "/news/page-2.html"),
    ("/index.asp", "/index.html"),
    ("/groups/index.asp?l=a", "/groups/a.html"),
    ("/parties/index.asp?past=1", "/parties/past.html"),
    ("/admin/index.asp", "/archived.html"),
    ("/nosuchpage.asp", None),
    ("/view_article.asp?id=99999999", None),
]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(url: str, method: str = "GET", data: bytes | None = None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=UA, method=method, data=data)
    try:
        with OPENER.open(req, timeout=30) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    except OSError as e:
        return 0, {}, str(e).encode()


def url_map_samples() -> list[tuple[str, str]]:
    """First numbered URL of each kind from build-meta/url-map.json (people, pictures, oneliners)."""
    urls = json.loads((bunny.REPO_ROOT / "build-meta" / "url-map.json").read_text())["urls"]
    out = []
    for prefix in ("/handles/handle.asp?id=", "/piclib/display.asp?id=", "/oneliner_popup.asp?o=",
                   "/parties/coming.asp?p="):
        old = next((k for k in sorted(urls) if k.startswith(prefix)), None)
        if old:
            out.append((old, urls[old]))
    return out


def local_file(path: str) -> Path | None:
    rel = "index.html" if path == "/" else path.lstrip("/")
    p = bunny.SITE_DIR / rel
    return p if p.is_file() else None


def random_pages(n: int) -> list[tuple[str, str]]:
    if not n or not bunny.SITE_DIR.is_dir():
        return []
    every = [p for p in bunny.SITE_DIR.rglob("*.htm*") if p.is_file()]
    return [("/" + p.relative_to(bunny.SITE_DIR).as_posix(), "text/html")
            for p in random.sample(every, min(n, len(every)))]


def check(base: str, extra: list[tuple[str, str]]) -> list[str]:
    errors = []
    pages = PAGES + [(new, "text/html") for _old, new in url_map_samples()] + extra

    def verify_page(target: tuple[str, str]) -> str | None:
        path, ctype = target
        status, h, body = fetch(base + urllib.parse.quote(path))
        if status != 200:
            return f"{path}: HTTP {status}"
        if ctype and not h.get("content-type", "").startswith(ctype):
            return f"{path}: Content-Type {h.get('content-type')!r}, expected {ctype}"
        if path.endswith((".html", ".htm")) and "iso-8859-1" not in h.get("content-type", "").lower():
            return f"{path}: Content-Type {h.get('content-type')!r} lacks charset iso-8859-1"
        local = local_file(path)
        if local is not None and body != local.read_bytes():
            return f"{path}: content differs from site/{local.relative_to(bunny.SITE_DIR)}"
        return None

    def verify_redirect(target: tuple[str, str | None]) -> str | None:
        old, want = target
        status, h, body = fetch(base + old)
        if want is None:
            if status != 404 or NOT_FOUND_MARKER not in body:
                return f"{old}: HTTP {status}, expected 404 with the custom page"
            return None
        loc = urllib.parse.urlsplit(h.get("location", ""))
        if status != 301 or loc.path != want:
            return f"{old}: HTTP {status} Location {h.get('location')!r}, expected 301 {want}"
        return None

    with ThreadPoolExecutor(WORKERS) as pool:
        errors += [e for e in pool.map(verify_page, pages) if e]
        errors += [e for e in pool.map(verify_redirect, REDIRECTS + url_map_samples()) if e]
    errors += check_hardening(base)
    return errors


def check_hardening(base: str) -> list[str]:
    """The site must stay read-only: no write method, security headers, custom 404."""
    errors = []
    status, _h, _b = fetch(base + "/", method="POST", data=b"x=1")
    if status not in (403, 405):
        errors.append(f"POST /: HTTP {status}, expected 403/405")
    status, h, _b = fetch(base + "/index.html")
    for name in ("content-security-policy", "x-content-type-options", "x-frame-options", "referrer-policy",
                 "strict-transport-security", "cross-origin-opener-policy"):
        if name not in h:
            errors.append(f"/index.html: missing header {name}")
    if "form-action 'none'" not in h.get("content-security-policy", ""):
        errors.append("/index.html: CSP lacks form-action 'none'")
    if h.get("x-content-type-options", "").lower() != "nosniff":
        errors.append("/index.html: X-Content-Type-Options is not nosniff")
    status, _h, body = fetch(base + "/no-such-page-404-check.html")
    if status != 404:
        errors.append(f"missing page: HTTP {status}, expected 404")
    elif NOT_FOUND_MARKER not in body:
        errors.append("missing page: custom 404 page not served")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", type=int, default=0, help="also fetch N random pages of site/ and compare them")
    args = ap.parse_args()
    base = "https://" + bunny.site_config()["hostname"]
    extra = random_pages(args.sample)  # the same sample on every attempt
    for attempt in range(1, ATTEMPTS + 1):
        errors = check(base, extra)
        if not errors:
            print(f"{base}: sample pages, legacy redirects and hardening OK"
                  + (" (pages compared with site/)" if bunny.SITE_DIR.is_dir() else "") + ".")
            return 0
        print(f"Attempt {attempt}/{ATTEMPTS}: {len(errors)} problem(s), e.g. {errors[0]}", flush=True)
        if attempt < ATTEMPTS:
            time.sleep(DELAY)
    print("\n".join(errors))
    return 1


if __name__ == "__main__":
    sys.exit(main())
