"""
Full-site crawler: discovers every internal page and records, page by page,
which links each page contains. Traverses from the homepage through all internal links
and subdomains until every link in the complete website is fetched.

Install:  pip install requests beautifulsoup4 lxml
Run:      python crawl_site.py https://example.com
          python crawl_site.py https://example.com 50
          python crawl_site.py https://example.com --unlimited
          python crawl_site.py https://example.com --login
          python crawl_site.py https://example.com --cookie "sessionid=abc123" --ignore-robots
          python crawl_site.py https://example.com --cookies-file cookies.json --ignore-robots
Output:   site_map.json  (page-wise structure)
          pages.csv      (flat list for spreadsheets)
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import deque
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urldefrag, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from database import (
    DEFAULT_DB_PATH,
    export_to_csv as db_export_to_csv,
    record_page as db_record_page,
    start_run as db_start_run,
)

USER_AGENT = "SiteAuditCrawler/1.0 (+mailto:you@example.com)"
DEFAULT_DELAY = 0.5
DEFAULT_MAX_PAGES = 10000

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_eid", "_ga"
}

SKIP_EXT = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4",
    ".mp3", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".css", ".js",
    ".ico", ".woff", ".woff2", ".xml", ".rss", ".atom", ".gz", ".tar", ".tgz",
)

TWO_PART_TLDS = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au", "edu.au",
    "co.nz", "org.nz",
    "co.jp", "ne.jp",
    "co.in", "net.in", "org.in", "gen.in", "firm.in", "ind.in",
    "com.br", "org.br",
    "com.mx", "org.mx",
    "com.sg", "org.sg",
}

# URLs or query patterns that could terminate session or trigger destructive actions
DANGEROUS_PATTERNS = (
    "/logout", "/signout", "/sign-out", "/log-out", "/exit",
    "logout=1", "action=logout", "auth/logout",
    "/delete", "/remove", "/cancel", "/unsubscribe",
    "/account/delete", "/users/delete"
)


def is_safe_link(url):
    """
    Filters out links that could log out the session or trigger destructive actions.
    Examines the path and query string against DANGEROUS_PATTERNS.
    """
    p = urlparse(url)
    target = f"{p.path}?{p.query}".lower()
    return not any(pat in target for pat in DANGEROUS_PATTERNS)


def load_cookies(session, cookie_str=None, cookies_file=None):
    """
    Loads cookies into the requests.Session from a raw string, JSON file, or Netscape cookies file.
    Supports:
      - Raw cookie string: 'name1=val1; name2=val2'
      - JSON list of cookie dicts (EditThisCookie, Cookie-Editor)
      - JSON dict of key-value pairs
      - Playwright storage_state format ({"cookies": [...]})
      - Netscape format text file
    """
    if cookie_str:
        for item in cookie_str.split(";"):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                name, val = item.split("=", 1)
                session.cookies.set(name.strip(), val.strip())

    if cookies_file:
        try:
            with open(cookies_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("[") or content.startswith("{"):
                data = json.loads(content)
                if isinstance(data, list):
                    for c in data:
                        if isinstance(c, dict) and "name" in c and "value" in c:
                            session.cookies.set(
                                c["name"],
                                c["value"],
                                domain=c.get("domain", ""),
                                path=c.get("path", "/")
                            )
                elif isinstance(data, dict):
                    if "cookies" in data and isinstance(data["cookies"], list):
                        for c in data["cookies"]:
                            if isinstance(c, dict) and "name" in c and "value" in c:
                                session.cookies.set(
                                    c["name"],
                                    c["value"],
                                    domain=c.get("domain", ""),
                                    path=c.get("path", "/")
                                )
                    else:
                        for k, v in data.items():
                            session.cookies.set(str(k), str(v))
            else:
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        session.cookies.set(parts[5], parts[6], domain=parts[0], path=parts[2])
        except Exception as e:
            print(f"[!] Error loading cookies from {cookies_file}: {e}")


STEALTH_INIT_SCRIPT = """
// 1. Remove automation indicators
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});

// 2. Ensure chrome runtime object exists
if (!window.chrome) {
    window.chrome = { runtime: {} };
}

// 3. Neutralize anti-debugging / debugger pause loops triggered on tab switch
const _origFunction = window.Function;
window.Function = function(...args) {
    if (args.length > 0 && typeof args[args.length - 1] === 'string') {
        const src = args[args.length - 1];
        if (src.includes('debugger')) {
            return function() {};
        }
    }
    return _origFunction.apply(this, args);
};

const _origEval = window.eval;
window.eval = function(code) {
    if (typeof code === 'string' && code.includes('debugger')) {
        return undefined;
    }
    return _origEval.apply(this, arguments);
};
"""


def interactive_browser_login(start_url, profile_dir=".browser_profile", save_cookies_file="cookies.json"):
    """
    Opens a real Chrome / Edge browser window via Playwright with a persistent user data directory.
    Uses real system Chrome (channel='chrome') and masks automation indicators to allow
    Google OAuth ('Continue with Google') and neutralize anti-debugging tab-freeze traps.
    Saves the session state to cookies.json and storage_state.json for all future crawl runs.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[!] Playwright is required for browser login. Run: pip install playwright && playwright install chromium")
        return None

    print("\n" + "=" * 70)
    print("  [BROWSER LOGIN MODE]")
    print(f"  Target: {start_url}")
    print("  A browser window is opening. Please log in to your account.")
    print("=" * 70)

    cookies = []
    with sync_playwright() as p:
        context = None
        # Try real system Google Chrome first (bypasses Google's 'browser not secure' OAuth block)
        for channel in ["chrome", "msedge", None]:
            try:
                kwargs = {
                    "user_data_dir": profile_dir,
                    "headless": False,
                    "viewport": {"width": 1280, "height": 820},
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--disable-infobars",
                        "--disable-popup-blocking",
                    ],
                    "ignore_default_args": ["--enable-automation"],
                }
                if channel:
                    kwargs["channel"] = channel
                context = p.chromium.launch_persistent_context(**kwargs)
                if channel:
                    print(f"[*] Using installed {channel.capitalize()} browser (Google OAuth enabled).")
                break
            except Exception:
                continue

        if not context:
            print("[!] Failed to launch browser window.")
            return None

        # Inject stealth script
        context.add_init_script(STEALTH_INIT_SCRIPT)

        # Attach CDP to V8 engine to ignore all 'debugger;' traps and prevent tab freezing
        def disable_v8_debugger(target_page):
            try:
                cdp = context.new_cdp_session(target_page)
                cdp.send("Debugger.disable")
            except Exception:
                pass

        page = context.pages[0] if context.pages else context.new_page()
        disable_v8_debugger(page)
        context.on("page", disable_v8_debugger)

        try:
            page.goto(start_url, wait_until="commit", timeout=45000)
        except Exception as e:
            print(f"[*] Navigating to {start_url}: {e}")

        print("\n" + "-" * 70)
        print(">>> Option A: Complete login in the opened browser, then press [ENTER].")
        print(">>> Option B: If mkissa.to anti-bot blocks the Google popup, log into")
        print("              mkissa.to in your normal browser, copy your cookies, and PASTE HERE:")
        print("-" * 70)
        user_input = ""
        try:
            user_input = input(">>> Press [ENTER] when logged in (or paste cookie string): ").strip()
        except (EOFError, KeyboardInterrupt):
            pass

        if user_input and "=" in user_input:
            cookies = []
            parsed_host = urlparse(start_url).netloc.split(":")[0]
            for item in user_input.split(";"):
                item = item.strip()
                if "=" in item:
                    k, v = item.split("=", 1)
                    cookies.append({
                        "name": k.strip(),
                        "value": v.strip(),
                        "domain": parsed_host,
                        "path": "/"
                    })
            try:
                context.close()
            except Exception:
                pass
        else:
            cookies = context.cookies()
            context.storage_state(path="storage_state.json")
            context.close()

    if cookies:
        with open(save_cookies_file, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        print(f"\n[*] Success! Captured {len(cookies)} cookies.")
        print(f"[*] Saved to '{save_cookies_file}' and 'storage_state.json'.")
        print("[*] These credentials will be automatically reused for this and future runs.\n")
        return save_cookies_file
    return None


def extract_base_domain(host):
    """
    Extracts the root domain (e.g., 'wikipedia.org' from 'www.wikipedia.org' or 'en.wikipedia.org').
    Handles common two-part public suffixes like .co.uk or .org.in.
    """
    host = host.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3:
        last_two = f"{parts[-2]}.{parts[-1]}"
        if last_two in TWO_PART_TLDS and len(parts) >= 3:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])
    return host


def normalize(url):
    """Drops fragments and tracking query params, normalizes path and query order."""
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    if p.query:
        query_pairs = [
            (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
        query_pairs.sort()
        clean_query = urlencode(query_pairs)
    else:
        clean_query = ""
    return urlunparse((p.scheme, p.netloc.lower(), path, "", clean_query, ""))


def same_site(url, base_domain, include_subdomains=True):
    """
    Checks if a URL belongs to the target site.
    If include_subdomains is True, matches base_domain and any *.base_domain.
    If False, strictly matches only the exact host (ignoring 'www.').
    """
    host = urlparse(url).netloc.lower().split(":")[0]
    if not host:
        return False
    if include_subdomains:
        return host == base_domain or host.endswith("." + base_domain)
    else:
        strip = lambda h: h[4:] if h.startswith("www.") else h
        return strip(host) == strip(base_domain)


def check_robots(url, session, robots_cache):
    """Checks robots.txt for the specific host, caching parsers per host."""
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.netloc.lower().split(":")[0]

    if host not in robots_cache:
        rp = RobotFileParser()
        robots_url = f"{scheme}://{host}/robots.txt"
        rp.set_url(robots_url)
        try:
            r_rob = session.get(robots_url, timeout=10)
            if r_rob.ok:
                rp.parse(r_rob.text.splitlines())
            elif r_rob.status_code in (401, 403):
                rp.disallow_all = True
            else:
                rp.allow_all = True
        except Exception:
            rp.allow_all = True
        robots_cache[host] = rp

    return robots_cache[host].can_fetch(USER_AGENT, url)


def sitemap_urls(session, base, base_domain, include_subdomains=True):
    """Seed the queue from sitemap.xml if one exists (finds orphan pages)."""
    found = []
    try:
        r = session.get(urljoin(base, "/sitemap.xml"), timeout=10)
        if r.ok:
            soup = BeautifulSoup(r.text, "xml")
            locs = [l.text.strip() for l in soup.find_all("loc")]
            for loc in locs:
                if loc.endswith(".xml"):
                    try:
                        sub = session.get(loc, timeout=10)
                        found += [l.text.strip() for l in BeautifulSoup(sub.text, "xml").find_all("loc")]
                    except Exception:
                        pass
                else:
                    found.append(loc)
    except Exception:
        pass
    return [u for u in found if same_site(u, base_domain, include_subdomains)]


def crawl(
    start_url,
    max_pages=DEFAULT_MAX_PAGES,
    include_subdomains=True,
    delay=DEFAULT_DELAY,
    ignore_robots=False,
    cookie=None,
    cookies_file=None,
    custom_headers=None,
    db_path=DEFAULT_DB_PATH,
):
    start_url = normalize(start_url)
    parsed_start = urlparse(start_url)
    raw_host = parsed_start.netloc.lower()

    if include_subdomains:
        base_domain = extract_base_domain(raw_host)
    else:
        base_domain = raw_host[4:] if raw_host.startswith("www.") else raw_host

    run_id = None
    try:
        run_id = db_start_run(start_url, base_domain, db_path=db_path)
        print(f"[*] Initialized database audit run: {run_id} ({db_path})")
    except Exception as dbe:
        print(f"[!] Warning: database init failed: {dbe}")

    print(f"[*] Starting crawl at: {start_url}")
    print(f"[*] Target domain: {base_domain} (include subdomains: {include_subdomains})")
    limit_str = f"{max_pages} pages" if max_pages and max_pages > 0 else "unlimited"
    print(f"[*] Max pages limit: {limit_str}, Politeness delay: {delay}s")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    if custom_headers:
        for h in custom_headers:
            if ":" in h:
                k, v = h.split(":", 1)
                session.headers[k.strip()] = v.strip()
                print(f"[*] Injected custom header: {k.strip()}")

    if cookie or cookies_file:
        load_cookies(session, cookie_str=cookie, cookies_file=cookies_file)
        print(f"[*] Injected session cookies ({len(session.cookies)} cookies loaded)")

    if ignore_robots:
        print("[*] robots.txt compliance is DISABLED (--ignore-robots active)\n")
    else:
        print("[*] robots.txt compliance is ENABLED\n")

    robots_cache = {}

    queue = deque([(start_url, 0, None)])  # (url, depth, found_on)
    seen = {start_url}

    # Optionally seed with sitemap
    for u in sitemap_urls(session, start_url, base_domain, include_subdomains):
        n = normalize(u)
        if same_site(n, base_domain, include_subdomains) and is_safe_link(n) and n not in seen:
            seen.add(n)
            queue.append((n, 1, "sitemap.xml"))

    pages = {}

    while queue:
        if max_pages and max_pages > 0 and len(pages) >= max_pages:
            print(f"\n[!] Reached page limit ({max_pages}). Stopping crawl.")
            break

        url, depth, parent = queue.popleft()

        if not ignore_robots and not check_robots(url, session, robots_cache):
            blocked_rec = {"url": url, "depth": depth, "status": "blocked_by_robots", "found_on": parent}
            pages[url] = blocked_rec
            if run_id:
                try:
                    db_record_page(run_id, blocked_rec, db_path=db_path)
                except Exception:
                    pass
            print(f"[-] [robots.txt disallowed] {url}")
            continue

        # Scrapy-inspired retry & exponential backoff
        t0 = time.time()
        max_retries = 3
        resp = None
        err_msg = None
        for attempt in range(max_retries):
            try:
                resp = session.get(url, timeout=15, allow_redirects=True)
                if resp.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                    retry_after = resp.headers.get("Retry-After")
                    sleep_time = float(retry_after) if retry_after and retry_after.isdigit() else (2 ** attempt * 1.5)
                    print(f"  [!] HTTP {resp.status_code} on {url}. Backing off {sleep_time:.1f}s (retry {attempt + 1}/{max_retries})...")
                    time.sleep(sleep_time)
                    continue
                break
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < max_retries - 1:
                    sleep_time = 2 ** attempt * 1.0
                    print(f"  [!] Connection issue on {url}. Retrying in {sleep_time:.1f}s...")
                    time.sleep(sleep_time)
                else:
                    err_msg = str(e)
                    break
            except requests.RequestException as e:
                err_msg = str(e)
                break

        resp_time_ms = round((time.time() - t0) * 1000, 2)

        if resp is None:
            err_rec = {"url": url, "depth": depth, "status": f"error: {err_msg}", "found_on": parent, "response_time_ms": resp_time_ms}
            pages[url] = err_rec
            if run_id:
                try:
                    db_record_page(run_id, err_rec, db_path=db_path)
                except Exception:
                    pass
            print(f"[!] [error] {url}: {err_msg}")
            continue

        final = normalize(resp.url)
        record = {
            "url": url,
            "final_url": final,
            "depth": depth,
            "status": resp.status_code,
            "found_on": parent,
            "title": "",
            "content_type": resp.headers.get("Content-Type", ""),
            "response_time_ms": resp_time_ms,
            "body_sample": resp.text[:2000] if resp.text else "",
            "internal_links": [],
            "external_links": [],
        }

        ctype = record["content_type"]
        if resp.status_code == 200 and "text/html" in ctype and same_site(final, base_domain, include_subdomains):
            soup = BeautifulSoup(resp.text, "html.parser")
            record["title"] = (soup.title.string or "").strip() if soup.title else ""

            internal, external = set(), set()
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
                    continue

                link = normalize(urljoin(final, href))
                parsed_link = urlparse(link)
                if parsed_link.scheme not in ("http", "https"):
                    continue

                if same_site(link, base_domain, include_subdomains):
                    if link.lower().endswith(SKIP_EXT):
                        continue
                    if not is_safe_link(link):
                        continue
                    internal.add(link)
                    if link not in seen:
                        seen.add(link)
                        queue.append((link, depth + 1, url))
                else:
                    external.add(link)

            record["internal_links"] = sorted(internal)
            record["external_links"] = sorted(external)

        pages[url] = record
        if run_id:
            try:
                db_record_page(run_id, record, db_path=db_path)
            except Exception:
                pass

        print(f"[{len(pages)}] (depth {depth}, queued {len(queue)}, {resp_time_ms}ms) {resp.status_code} {url}")
        time.sleep(delay)

    return pages, run_id


def save(pages):
    with open("site_map.json", "w", encoding="utf-8") as f:
        json.dump(pages, f, indent=2, ensure_ascii=False)

    def write_pages_csv(f):
        w = csv.writer(f)
        w.writerow(["url", "depth", "status", "title", "found_on", "internal_link_count", "external_link_count"])
        for p in pages.values():
            w.writerow([
                p["url"], p["depth"], p["status"], p.get("title", ""), p.get("found_on", ""),
                len(p.get("internal_links", [])), len(p.get("external_links", [])),
            ])

    try:
        with open("pages.csv", "w", newline="", encoding="utf-8") as f:
            write_pages_csv(f)
    except PermissionError:
        print("[!] Warning: 'pages.csv' is open in another program (e.g., Excel). Saving to 'pages_latest.csv'.")
        with open("pages_latest.csv", "w", newline="", encoding="utf-8") as f:
            write_pages_csv(f)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Full-site crawler: scans homepage, follows every link recursively across the whole website."
    )
    parser.add_argument("url", help="Starting website URL (e.g. https://example.com or https://www.wikipedia.org)")
    parser.add_argument(
        "max_pages",
        nargs="?",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"Maximum pages to crawl (default: {DEFAULT_MAX_PAGES}, set 0 for unlimited)"
    )
    parser.add_argument(
        "--unlimited",
        action="store_true",
        help="Crawl the complete website without any page limit until every link is fetched."
    )
    parser.add_argument(
        "--exact-domain",
        action="store_true",
        help="Restrict crawling strictly to the exact host provided (ignore subdomains)."
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Politeness delay between HTTP requests in seconds (default: {DEFAULT_DELAY})"
    )
    parser.add_argument(
        "--cookie",
        help="Raw cookie string for authentication (e.g. 'sessionid=abc123; user_id=456')"
    )
    parser.add_argument(
        "--cookies-file",
        help="Path to a JSON or Netscape cookies file exported from your browser"
    )
    parser.add_argument(
        "--header",
        action="append",
        dest="headers",
        help="Custom HTTP header in 'Key: Value' format (e.g. 'Authorization: Bearer <token>'). Can be specified multiple times."
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open an interactive browser window to log in once. Automatically captures and saves your session for all future runs."
    )
    parser.add_argument(
        "--browser-profile",
        default=".browser_profile",
        help="Path to persistent browser profile directory (default: .browser_profile)"
    )
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Bypass robots.txt checks (essential for crawling logged-in pages like /auth, /profile, /dashboard)"
    )
    parser.add_argument(
        "--no-scan",
        action="store_true",
        help="Skip automatically running the WCAG compliance scan after crawling."
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Export all audit results from database to downloadable CSV files."
    )
    return parser.parse_args()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python crawl_site.py https://yoursite.com [max_pages] [--unlimited] [--login] [--exact-domain] [--delay SECS] [--no-scan] [--db PATH] [--export-csv]")

    args = parse_args()

    if args.export_csv:
        db_export_to_csv(db_path=args.db)
        sys.exit(0)

    page_limit = None if args.unlimited or args.max_pages == 0 else args.max_pages
    include_sub = not args.exact_domain

    cookies_file = args.cookies_file
    ignore_robots = args.ignore_robots

    if args.login:
        saved_file = interactive_browser_login(
            start_url=args.url,
            profile_dir=args.browser_profile,
            save_cookies_file="cookies.json"
        )
        if saved_file:
            cookies_file = saved_file
            ignore_robots = True  # Authenticated crawling typically requires accessing /auth /profile
    elif not args.cookie and not cookies_file and os.path.exists("cookies.json"):
        cookies_file = "cookies.json"
        print("[*] Auto-detected existing 'cookies.json' from previous login. Automatically reusing session.")

    result, run_id = crawl(
        start_url=args.url,
        max_pages=page_limit,
        include_subdomains=include_sub,
        delay=args.delay,
        ignore_robots=ignore_robots,
        cookie=args.cookie,
        cookies_file=cookies_file,
        custom_headers=args.headers,
        db_path=args.db,
    )
    save(result)
    print(f"\nDone! Crawled {len(result)} pages. Saved to site_map.json and pages.csv")

    if not args.no_scan:
        print("\n" + "=" * 75)
        print(" [>] AUTOMATICALLY STARTING WCAG COMPLIANCE SCAN (A, AA, AAA)...")
        print("=" * 75 + "\n")
        try:
            from wcag_scan import run_scan
            run_scan(
                sitemap_path="site_map.json",
                run_id=run_id,
                db_path=args.db,
                cookie=args.cookie,
                cookies_file=cookies_file
            )
            # Export freshest CSVs from database
            db_export_to_csv(db_path=args.db, run_id=run_id)
        except Exception as e:
            print(f"[!] Error running WCAG accessibility scan: {e}")


