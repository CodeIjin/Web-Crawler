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
import csv
import json
import sys

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


def main(sitemap_path="site_map.json"):
    urls = load_urls(sitemap_path)
    if not urls:
        print("No crawled pages with status 200 found to scan.")
        return

    axe = Axe()
    all_results = {}
    rows = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
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


if __name__ == "__main__":
    sitemap = sys.argv[1] if len(sys.argv) > 1 else "site_map.json"
    main(sitemap)
