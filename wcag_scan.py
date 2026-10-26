"""
Runs axe-core (WCAG 2.0 / 2.1 / 2.2, levels A and AA) on every page found
by crawl_site.py.

Install:
    pip install playwright axe-playwright-python
    playwright install chromium
Run:
    python wcag_scan.py                # reads site_map.json
Output:
    wcag_results.json   full details per page
    wcag_summary.csv    one row per violation per page
"""
import argparse
import csv
import json
import os
import sys
from urllib.parse import urlparse

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import sync_playwright

WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def load_urls(path="site_map.json"):
    try:
        with open(path, encoding="utf-8") as f:
            pages = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Error: '{path}' not found. Please run crawl_site.py first to generate the sitemap.")
    return [p["url"] for p in pages.values() if p.get("status") == 200 and p.get("title") is not None]


def load_playwright_cookies(cookie_str=None, cookies_file=None, default_url=None):
    """Parses cookies into Playwright-compatible cookie dictionaries."""
    cookies = []
    default_domain = urlparse(default_url).netloc.split(":")[0] if default_url else ""

    if cookie_str:
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                cookie_entry = {
                    "name": k.strip(),
                    "value": v.strip(),
                    "path": "/",
                }
                if default_domain:
                    cookie_entry["domain"] = default_domain
                elif default_url:
                    cookie_entry["url"] = default_url
                cookies.append(cookie_entry)

    if cookies_file:
        try:
            with open(cookies_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for c in data:
                    if isinstance(c, dict) and "name" in c and "value" in c:
                        cookie_entry = {
                            "name": c["name"],
                            "value": c["value"],
                            "path": c.get("path", "/"),
                        }
                        if "domain" in c and c["domain"]:
                            cookie_entry["domain"] = c["domain"]
                        elif default_url:
                            cookie_entry["url"] = default_url
                        cookies.append(cookie_entry)
            elif isinstance(data, dict):
                if "cookies" in data and isinstance(data["cookies"], list):
                    for c in data["cookies"]:
                        if isinstance(c, dict) and "name" in c and "value" in c:
                            cookie_entry = {
                                "name": c["name"],
                                "value": c["value"],
                                "path": c.get("path", "/"),
                            }
                            if "domain" in c and c["domain"]:
                                cookie_entry["domain"] = c["domain"]
                            elif default_url:
                                cookie_entry["url"] = default_url
                            cookies.append(cookie_entry)
                else:
                    for k, v in data.items():
                        cookie_entry = {
                            "name": str(k),
                            "value": str(v),
                            "path": "/",
                        }
                        if default_domain:
                            cookie_entry["domain"] = default_domain
                        cookies.append(cookie_entry)
        except Exception as e:
            print(f"[!] Error reading cookies from {cookies_file}: {e}")

    return cookies


def main(sitemap_path="site_map.json", cookie=None, cookies_file=None, storage_state=None):
    urls = load_urls(sitemap_path)
    if not urls:
        print("No crawled pages with status 200 found to scan.")
        return

    axe = Axe()
    all_results = {}
    rows = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context_kwargs = {}
        if storage_state:
            context_kwargs["storage_state"] = storage_state
        context = browser.new_context(**context_kwargs)

        cookie_list = load_playwright_cookies(
            cookie_str=cookie,
            cookies_file=cookies_file,
            default_url=urls[0] if urls else None
        )
        if cookie_list:
            try:
                context.add_cookies(cookie_list)
                print(f"[*] Injected {len(cookie_list)} cookies into Playwright browser context")
            except Exception as e:
                print(f"[!] Warning: failed to inject some cookies into browser context: {e}")

        page = context.new_page()

        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] Scanning {url} ...")
            try:
                page.goto(url, wait_until="load", timeout=30000)
                result = axe.run(page, options={"runOnly": {"type": "tag", "values": WCAG_TAGS}})
                violations = result.response.get("violations", [])
                incomplete = result.response.get("incomplete", [])
            except Exception as e:
                print(f"  [Error scanning {url}]: {e}")
                all_results[url] = {"error": str(e)}
                continue

            all_results[url] = {"violations": violations, "needs_manual_review": incomplete}

            for v in violations:
                wcag_refs = [t for t in v.get("tags", []) if t.startswith("wcag") and t[4:].isdigit()]
                rows.append({
                    "page": url,
                    "rule": v.get("id", ""),
                    "impact": v.get("impact", ""),
                    "wcag_criteria": ",".join(wcag_refs),
                    "description": v.get("help", ""),
                    "instances": len(v.get("nodes", [])),
                    "help_url": v.get("helpUrl", ""),
                })

        browser.close()

    with open("wcag_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    with open("wcag_summary.csv", "w", newline="", encoding="utf-8") as f:
        fields = ["page", "rule", "impact", "wcag_criteria", "description", "instances", "help_url"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"\nScanned {len(urls)} pages, found {len(rows)} violation entries.")
    print("See wcag_summary.csv and wcag_results.json")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Runs axe-core accessibility audits on pages discovered in site_map.json."
    )
    parser.add_argument(
        "sitemap",
        nargs="?",
        default="site_map.json",
        help="Path to site_map.json (default: site_map.json)"
    )
    parser.add_argument(
        "--cookie",
        help="Raw cookie string for authentication (e.g. 'sessionid=abc123; user_id=456')"
    )
    parser.add_argument(
        "--cookies-file",
        help="Path to a JSON cookies file exported from your browser"
    )
    parser.add_argument(
        "--storage-state",
        help="Path to Playwright storage_state.json authentication state"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    storage_state = args.storage_state
    cookies_file = args.cookies_file

    if not storage_state and not cookies_file and not args.cookie:
        if os.path.exists("storage_state.json"):
            storage_state = "storage_state.json"
            print("[*] Auto-detected 'storage_state.json' from previous login. Reusing authentication state.")
        elif os.path.exists("cookies.json"):
            cookies_file = "cookies.json"
            print("[*] Auto-detected 'cookies.json' from previous login. Reusing session cookies.")

    main(
        sitemap_path=args.sitemap,
        cookie=args.cookie,
        cookies_file=cookies_file,
        storage_state=storage_state
    )
