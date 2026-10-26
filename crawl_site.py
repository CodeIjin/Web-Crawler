"""
Full-site crawler: discovers every internal page and records, page by page,
which links each page contains. Traverses from the homepage through all internal links
and subdomains until every link in the complete website is fetched.

Install:  pip install requests beautifulsoup4 lxml
Run:      python crawl_site.py https://example.com
          python crawl_site.py https://www.wikipedia.org 50
          python crawl_site.py https://mysite.com --unlimited
Output:   site_map.json  (page-wise structure)
          pages.csv      (flat list for spreadsheets)
"""
import argparse
import csv
import json
import sys
import time
from collections import deque
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urldefrag, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

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


def crawl(start_url, max_pages=DEFAULT_MAX_PAGES, include_subdomains=True, delay=DEFAULT_DELAY):
    start_url = normalize(start_url)
    parsed_start = urlparse(start_url)
    raw_host = parsed_start.netloc.lower()

    if include_subdomains:
        base_domain = extract_base_domain(raw_host)
    else:
        base_domain = raw_host[4:] if raw_host.startswith("www.") else raw_host

    print(f"[*] Starting crawl at: {start_url}")
    print(f"[*] Target domain: {base_domain} (include subdomains: {include_subdomains})")
    limit_str = f"{max_pages} pages" if max_pages and max_pages > 0 else "unlimited"
    print(f"[*] Max pages limit: {limit_str}, Politeness delay: {delay}s\n")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    robots_cache = {}

    queue = deque([(start_url, 0, None)])  # (url, depth, found_on)
    seen = {start_url}

    # Optionally seed with sitemap
    for u in sitemap_urls(session, start_url, base_domain, include_subdomains):
        n = normalize(u)
        if same_site(n, base_domain, include_subdomains) and n not in seen:
            seen.add(n)
            queue.append((n, 1, "sitemap.xml"))

    pages = {}

    while queue:
        if max_pages and max_pages > 0 and len(pages) >= max_pages:
            print(f"\n[!] Reached page limit ({max_pages}). Stopping crawl.")
            break

        url, depth, parent = queue.popleft()

        if not check_robots(url, session, robots_cache):
            pages[url] = {"url": url, "depth": depth, "status": "blocked_by_robots", "found_on": parent}
            print(f"[-] [robots.txt disallowed] {url}")
            continue

        try:
            resp = session.get(url, timeout=15, allow_redirects=True)
        except requests.RequestException as e:
            pages[url] = {"url": url, "depth": depth, "status": f"error: {e}", "found_on": parent}
            print(f"[!] [error] {url}: {e}")
            continue

        final = normalize(resp.url)
        record = {
            "url": url,
            "final_url": final,
            "depth": depth,
            "status": resp.status_code,
            "found_on": parent,
            "title": "",
            "internal_links": [],
            "external_links": [],
        }

        ctype = resp.headers.get("Content-Type", "")
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
                    internal.add(link)
                    if link not in seen:
                        seen.add(link)
                        queue.append((link, depth + 1, url))
                else:
                    external.add(link)

            record["internal_links"] = sorted(internal)
            record["external_links"] = sorted(external)

        pages[url] = record
        print(f"[{len(pages)}] (depth {depth}, queued {len(queue)}) {resp.status_code} {url}")
        time.sleep(delay)

    return pages


def save(pages):
    with open("site_map.json", "w", encoding="utf-8") as f:
        json.dump(pages, f, indent=2, ensure_ascii=False)

    with open("pages.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "depth", "status", "title", "found_on", "internal_link_count", "external_link_count"])
        for p in pages.values():
            w.writerow([
                p["url"], p["depth"], p["status"], p.get("title", ""), p.get("found_on", ""),
                len(p.get("internal_links", [])), len(p.get("external_links", [])),
            ])


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
    return parser.parse_args()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python crawl_site.py https://yoursite.com [max_pages] [--unlimited] [--exact-domain] [--delay SECS]")

    args = parse_args()
    page_limit = None if args.unlimited or args.max_pages == 0 else args.max_pages
    include_sub = not args.exact_domain

    result = crawl(
        start_url=args.url,
        max_pages=page_limit,
        include_subdomains=include_sub,
        delay=args.delay
    )
    save(result)
    print(f"\nDone! Crawled {len(result)} pages. Saved to site_map.json and pages.csv")
