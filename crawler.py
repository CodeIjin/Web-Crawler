"""
Minimal polite web crawler (Starter file).
Install deps:  pip install requests beautifulsoup4
Run:           python crawler.py https://example.com 50
"""
import sys
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "MyCrawler/0.1 (+mailto:you@example.com)"
DELAY_SECONDS = 1.0  # politeness delay between requests


def get_robots(base_url):
    parsed = urlparse(base_url)
    rp = RobotFileParser()
    rp.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        rp.allow_all = True
    return rp


def normalize(url):
    url, _ = urldefrag(url)  # drop #fragment
    return url.rstrip("/")


def crawl(start_url, max_pages=50):
    domain = urlparse(start_url).netloc
    robots = get_robots(start_url)
    queue = deque([normalize(start_url)])
    seen = {normalize(start_url)}
    results = []

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    while queue and len(results) < max_pages:
        url = queue.popleft()

        if not robots.can_fetch(USER_AGENT, url):
            print(f"[robots] skipped {url}")
            continue

        try:
            resp = session.get(url, timeout=10)
        except requests.RequestException as e:
            print(f"[error] {url}: {e}")
            continue

        if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", ""):
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        results.append({"url": url, "title": title})
        print(f"[{len(results)}] {url} - {title}")

        for a in soup.find_all("a", href=True):
            link = normalize(urljoin(url, a["href"]))
            if urlparse(link).scheme in ("http", "https") \
                    and urlparse(link).netloc == domain \
                    and link not in seen:
                seen.add(link)
                queue.append(link)

        time.sleep(DELAY_SECONDS)

    return results


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    crawl(start, limit)
