"""
Runs axe-core across WCAG 2.0 / 2.1 / 2.2 Level A, Level AA, and Level AAA standards
on every page discovered by crawl_site.py.

Outputs:
  - wcag_compliance_report.csv : Hierarchical CSV report grouped by URL and labeled by WCAG A, AA, and AAA
  - wcag_page_compliance.csv   : Executive page-level compliance matrix (A / AA / AAA Pass/Fail status)
  - wcag_summary.csv           : Standard violation summary with WCAG level column
  - wcag_results.json          : Full detailed JSON with DOM element snippets and selectors
"""
import argparse
import csv
import json
import os
import sys
from urllib.parse import urlparse

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import sync_playwright

from database import (
    DEFAULT_DB_PATH,
    finish_run,
    get_latest_run_id,
    record_wcag_page_results,
)

# Complete WCAG standards tags: Level A, Level AA, and Level AAA
WCAG_TAGS = [
    "wcag2a", "wcag2aa", "wcag2aaa",
    "wcag21a", "wcag21aa",
    "wcag22a", "wcag22aa", "wcag22aaa",
]

LEVEL_ORDER = {
    "WCAG A": 1,
    "WCAG AA": 2,
    "WCAG AAA": 3,
    "Best Practice": 4,
}

IMPACT_ORDER = {
    "critical": 1,
    "serious": 2,
    "moderate": 3,
    "minor": 4,
}


def classify_wcag_level(tags):
    """
    Classifies a rule into 'WCAG A', 'WCAG AA', or 'WCAG AAA'.
    Precedence: AAA is highest tier requirement, AA is standard, A is baseline.
    """
    tag_set = set(tags)
    if tag_set & {"wcag2aaa", "wcag21aaa", "wcag22aaa"}:
        return "WCAG AAA"
    elif tag_set & {"wcag2aa", "wcag21aa", "wcag22aa"}:
        return "WCAG AA"
    elif tag_set & {"wcag2a", "wcag21a", "wcag22a"}:
        return "WCAG A"
    return "WCAG A"


def format_wcag_criteria(tags):
    """
    Extracts and formats criterion numbers (e.g. 'wcag143' -> '1.4.3', 'wcag1412' -> '1.4.12').
    """
    refs = []
    for t in tags:
        if t.startswith("wcag") and t[4:].isdigit():
            num = t[4:]
            if len(num) == 3:
                refs.append(f"{num[0]}.{num[1]}.{num[2]}")
            elif len(num) == 4:
                refs.append(f"{num[0]}.{num[1]}.{num[2:]}")
            else:
                refs.append(num)
    return ", ".join(sorted(set(refs))) if refs else "N/A"


def load_sitemap_data(path="site_map.json"):
    """Loads pages dictionary from site_map.json."""
    try:
        with open(path, encoding="utf-8") as f:
            pages = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Error: '{path}' not found. Please run crawl_site.py first to generate the sitemap.")
    return pages


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


def run_scan(
    sitemap_path="site_map.json",
    run_id=None,
    db_path=DEFAULT_DB_PATH,
    cookie=None,
    cookies_file=None,
    storage_state=None
):
    pages_data = load_sitemap_data(sitemap_path)
    target_pages = [
        (url, meta) for url, meta in pages_data.items()
        if meta.get("status") == 200 and meta.get("title") is not None
    ]

    if not target_pages:
        print("[!] No crawled pages with HTTP 200 found to scan.")
        return

    if not run_id:
        run_id = get_latest_run_id(db_path)

    print(f"[*] Starting WCAG compliance scan across {len(target_pages)} pages.")
    print("    Target Standards: WCAG Level A, Level AA, Level AAA (2.0 / 2.1 / 2.2)")
    if run_id:
        print(f"    Recording to Database: {db_path} (Run: {run_id})\n")
    else:
        print(f"    Database: {db_path}\n")

    axe = Axe()
    all_results = {}
    detailed_rows = []
    page_summary_rows = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context_kwargs = {}
        if storage_state:
            context_kwargs["storage_state"] = storage_state
        context = browser.new_context(**context_kwargs)

        cookie_list = load_playwright_cookies(
            cookie_str=cookie,
            cookies_file=cookies_file,
            default_url=target_pages[0][0] if target_pages else None
        )
        if cookie_list:
            try:
                context.add_cookies(cookie_list)
                print(f"[*] Injected {len(cookie_list)} session cookies into Playwright context.")
            except Exception as e:
                print(f"[!] Warning: failed to inject cookies: {e}")

        page = context.new_page()

        for i, (url, meta) in enumerate(target_pages, 1):
            title = meta.get("title", "")
            depth = meta.get("depth", 0)
            print(f"[{i}/{len(target_pages)}] Scanning (depth {depth}): {url}")

            try:
                page.goto(url, wait_until="load", timeout=30000)
                result = axe.run(page, options={"runOnly": {"type": "tag", "values": WCAG_TAGS}})
                violations = result.response.get("violations", [])
                incomplete = result.response.get("incomplete", [])
            except Exception as e:
                print(f"  [Error scanning {url}]: {e}")
                all_results[url] = {"error": str(e), "title": title, "depth": depth}
                page_summary_rows.append({
                    "page_url": url,
                    "page_title": title,
                    "depth": depth,
                    "wcag_a_status": "ERROR",
                    "wcag_aa_status": "ERROR",
                    "wcag_aaa_status": "ERROR",
                    "overall_compliance": f"Scan Error: {e}",
                    "total_violations": 0,
                    "critical_count": 0,
                    "serious_count": 0,
                    "moderate_count": 0,
                    "minor_count": 0,
                })
                continue

            # Track violations by level for this page
            page_level_counts = {"WCAG A": 0, "WCAG AA": 0, "WCAG AAA": 0}
            impact_counts = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0}
            page_violations_by_level = {"WCAG A": [], "WCAG AA": [], "WCAG AAA": []}

            for v in violations:
                level = classify_wcag_level(v.get("tags", []))
                criteria = format_wcag_criteria(v.get("tags", []))
                impact = v.get("impact", "moderate")
                nodes = v.get("nodes", [])
                node_count = len(nodes)

                page_level_counts[level] = page_level_counts.get(level, 0) + 1
                if impact in impact_counts:
                    impact_counts[impact] += 1

                # Sample failing DOM target / selector / snippet
                sample_selector = ""
                sample_html = ""
                if nodes:
                    sample_selector = " ".join(nodes[0].get("target", []))
                    sample_html = (nodes[0].get("html") or "").replace("\n", " ").strip()[:150]

                row = {
                    "page_url": url,
                    "page_title": title,
                    "depth": depth,
                    "wcag_level": level,
                    "wcag_criteria": criteria,
                    "impact": impact,
                    "rule_id": v.get("id", ""),
                    "instances": node_count,
                    "description": v.get("help", ""),
                    "failing_selector_sample": sample_selector,
                    "failing_html_sample": sample_html,
                    "help_url": v.get("helpUrl", ""),
                }
                detailed_rows.append(row)
                page_violations_by_level.setdefault(level, []).append(row)

            # Determine overall compliance tier for this page
            a_fail = page_level_counts["WCAG A"] > 0
            aa_fail = page_level_counts["WCAG AA"] > 0
            aaa_fail = page_level_counts["WCAG AAA"] > 0

            if not (a_fail or aa_fail or aaa_fail):
                compliance_str = "WCAG AAA Compliant (Full Pass)"
            elif not (a_fail or aa_fail) and aaa_fail:
                compliance_str = "WCAG AA Compliant (Failed AAA)"
            elif not a_fail and aa_fail:
                compliance_str = "WCAG A Compliant (Failed AA & AAA)"
            else:
                compliance_str = "Non-Compliant (Failed Level A Baseline)"

            page_summary_rows.append({
                "page_url": url,
                "page_title": title,
                "depth": depth,
                "wcag_a_status": "PASS" if not a_fail else f"FAIL ({page_level_counts['WCAG A']} rules)",
                "wcag_aa_status": "PASS" if not aa_fail else f"FAIL ({page_level_counts['WCAG AA']} rules)",
                "wcag_aaa_status": "PASS" if not aaa_fail else f"FAIL ({page_level_counts['WCAG AAA']} rules)",
                "overall_compliance": compliance_str,
                "total_violations": len(violations),
                "critical_count": impact_counts["critical"],
                "serious_count": impact_counts["serious"],
                "moderate_count": impact_counts["moderate"],
                "minor_count": impact_counts["minor"],
            })

            all_results[url] = {
                "title": title,
                "depth": depth,
                "compliance_summary": {
                    "level_a": "PASS" if not a_fail else "FAIL",
                    "level_aa": "PASS" if not aa_fail else "FAIL",
                    "level_aaa": "PASS" if not aaa_fail else "FAIL",
                    "overall": compliance_str,
                },
                "violations_by_level": page_violations_by_level,
                "raw_violations": violations,
                "needs_manual_review": incomplete,
            }

            if run_id:
                try:
                    record_wcag_page_results(
                        run_id=run_id,
                        url=url,
                        title=title,
                        depth=depth,
                        compliance_summary={
                            "level_a": "PASS" if not a_fail else "FAIL",
                            "level_aa": "PASS" if not aa_fail else "FAIL",
                            "level_aaa": "PASS" if not aaa_fail else "FAIL",
                            "overall": compliance_str,
                        },
                        violations=violations,
                        db_path=db_path
                    )
                except Exception as dbe:
                    print(f"  [!] Note: could not write page to database: {dbe}")

        browser.close()

    if run_id:
        try:
            finish_run(run_id, db_path=db_path)
            print(f"[*] Audit run '{run_id}' successfully finalized in database ({db_path}).")
        except Exception as dbe:
            print(f"[!] Warning: failed to finalize run in database: {dbe}")

    # 1. Sort detailed rows hierarchically: Page URL -> WCAG Level (A -> AA -> AAA) -> Impact Severity -> Criteria
    detailed_rows.sort(
        key=lambda r: (
            r["page_url"],
            LEVEL_ORDER.get(r["wcag_level"], 99),
            IMPACT_ORDER.get(r["impact"], 99),
            r["wcag_criteria"],
            r["rule_id"],
        )
    )

    def safe_write_csv(filepath, fieldnames, data_rows):
        def write_data(f):
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(data_rows)

        try:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                write_data(f)
            return filepath
        except PermissionError:
            base, ext = os.path.splitext(filepath)
            fallback = f"{base}_latest{ext}"
            print(f"[!] Warning: '{filepath}' is open in another program (e.g. Excel). Saving to '{fallback}'.")
            with open(fallback, "w", newline="", encoding="utf-8") as f:
                write_data(f)
            return fallback

    # 2. Write Hierarchical Detailed Compliance Report CSV
    compliance_fields = [
        "page_url", "page_title", "depth", "wcag_level", "wcag_criteria",
        "impact", "rule_id", "instances", "description",
        "failing_selector_sample", "failing_html_sample", "help_url"
    ]
    out_rep = safe_write_csv("wcag_compliance_report.csv", compliance_fields, detailed_rows)

    # 3. Write Executive Page-Level Compliance Matrix CSV
    page_fields = [
        "page_url", "page_title", "depth",
        "wcag_a_status", "wcag_aa_status", "wcag_aaa_status",
        "overall_compliance", "total_violations",
        "critical_count", "serious_count", "moderate_count", "minor_count"
    ]
    out_page = safe_write_csv("wcag_page_compliance.csv", page_fields, page_summary_rows)

    # 4. Standard summary CSV (backwards compatibility)
    summary_fields = ["page", "wcag_level", "rule", "impact", "wcag_criteria", "description", "instances", "help_url"]
    legacy_rows = [{
        "page": r["page_url"],
        "wcag_level": r["wcag_level"],
        "rule": r["rule_id"],
        "impact": r["impact"],
        "wcag_criteria": r["wcag_criteria"],
        "description": r["description"],
        "instances": r["instances"],
        "help_url": r["help_url"],
    } for r in detailed_rows]
    out_sum = safe_write_csv("wcag_summary.csv", summary_fields, legacy_rows)

    # 5. Full JSON structure
    with open("wcag_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print("  WCAG COMPLIANCE SCAN COMPLETED SUCCESSFULLY")
    print(f"  Pages Scanned: {len(target_pages)}")
    print(f"  Total Issues Categorized: {len(detailed_rows)}")
    print("=" * 70)
    print("  -> wcag_compliance_report.csv (Hierarchical breakdown labeled WCAG A, AA, AAA)")
    print("  -> wcag_page_compliance.csv   (Page-by-page A / AA / AAA pass/fail matrix)")
    print("  -> wcag_summary.csv           (Standard summary)")
    print("  -> wcag_results.json          (Full element-level JSON)\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Runs axe-core accessibility audits across WCAG A, AA, and AAA standards on site_map.json."
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
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        "--run-id",
        help="Specific crawl run ID to associate results with (defaults to latest)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    storage_state = args.storage_state
    cookies_file = args.cookies_file

    if not storage_state and not cookies_file and not args.cookie:
        if os.path.exists("storage_state.json"):
            storage_state = "storage_state.json"
            print("[*] Auto-detected 'storage_state.json' from previous login.")
        elif os.path.exists("cookies.json"):
            cookies_file = "cookies.json"
            print("[*] Auto-detected 'cookies.json' from previous login.")

    run_scan(
        sitemap_path=args.sitemap,
        run_id=args.run_id,
        db_path=args.db,
        cookie=args.cookie,
        cookies_file=cookies_file,
        storage_state=storage_state
    )
