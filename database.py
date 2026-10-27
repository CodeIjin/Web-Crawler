"""
Database layer for Web Crawler & WCAG Accessibility Audit Suite.
Inspired by Scrapy item pipelines, PySpider database backends, and Crawley ORM storage.

Stores:
  - Crawl runs and execution metadata
  - Discovered pages, response times, HTTP statuses, and content hashes (Nutch-style deduplication)
  - Page link graph (internal and external relationships)
  - Detailed WCAG Level A, Level AA, Level AAA violations and failing DOM elements

Includes automated CSV export functions to download results anytime.
"""
import argparse
import csv
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime

DEFAULT_DB_PATH = "audit_database.db"

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


def get_connection(db_path=DEFAULT_DB_PATH):
    """Establishes SQLite connection with row factory and foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=DEFAULT_DB_PATH):
    """Initializes tables and indexes in the SQLite database."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. Crawl Runs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crawl_runs (
                run_id TEXT PRIMARY KEY,
                start_url TEXT NOT NULL,
                base_domain TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                total_pages_crawled INTEGER DEFAULT 0,
                total_violations_found INTEGER DEFAULT 0,
                compliance_grade TEXT DEFAULT 'Pending'
            )
        """)

        # 2. Crawled Pages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS crawled_pages (
                page_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                url TEXT NOT NULL,
                final_url TEXT,
                depth INTEGER DEFAULT 0,
                http_status TEXT,
                title TEXT,
                content_type TEXT,
                response_time_ms REAL,
                content_hash TEXT,
                found_on TEXT,
                internal_link_count INTEGER DEFAULT 0,
                external_link_count INTEGER DEFAULT 0,
                scanned_for_wcag BOOLEAN DEFAULT 0,
                wcag_a_status TEXT DEFAULT 'Untested',
                wcag_aa_status TEXT DEFAULT 'Untested',
                wcag_aaa_status TEXT DEFAULT 'Untested',
                overall_wcag_status TEXT DEFAULT 'Untested',
                total_violations INTEGER DEFAULT 0,
                FOREIGN KEY (run_id) REFERENCES crawl_runs(run_id) ON DELETE CASCADE,
                UNIQUE(run_id, url)
            )
        """)

        # 3. Page Link Graph table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS page_links (
                link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                target_url TEXT NOT NULL,
                link_type TEXT CHECK(link_type IN ('internal', 'external')),
                FOREIGN KEY (run_id) REFERENCES crawl_runs(run_id) ON DELETE CASCADE
            )
        """)

        # 4. WCAG Violations table (labeled by WCAG A, AA, AAA)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wcag_violations (
                violation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                page_url TEXT NOT NULL,
                page_title TEXT,
                depth INTEGER DEFAULT 0,
                wcag_level TEXT NOT NULL,
                wcag_criteria TEXT NOT NULL,
                impact TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                instances_count INTEGER DEFAULT 1,
                description TEXT,
                failing_selector_sample TEXT,
                failing_html_sample TEXT,
                help_url TEXT,
                FOREIGN KEY (run_id) REFERENCES crawl_runs(run_id) ON DELETE CASCADE
            )
        """)

        # 5. WCAG Failing Element Nodes table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wcag_element_nodes (
                node_id INTEGER PRIMARY KEY AUTOINCREMENT,
                violation_id INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                page_url TEXT NOT NULL,
                target_selector TEXT,
                html_snippet TEXT,
                failure_summary TEXT,
                FOREIGN KEY (violation_id) REFERENCES wcag_violations(violation_id) ON DELETE CASCADE,
                FOREIGN KEY (run_id) REFERENCES crawl_runs(run_id) ON DELETE CASCADE
            )
        """)

        # Performance Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_crawled_pages_url ON crawled_pages(run_id, url)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wcag_violations_level ON wcag_violations(run_id, wcag_level)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wcag_violations_page ON wcag_violations(run_id, page_url)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_page_links_source ON page_links(run_id, source_url)")

        conn.commit()


def start_run(start_url, base_domain, db_path=DEFAULT_DB_PATH):
    """Registers a new crawl & audit run in the database."""
    init_db(db_path)
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    started_at = datetime.now().isoformat()

    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO crawl_runs (run_id, start_url, base_domain, started_at)
            VALUES (?, ?, ?, ?)
        """, (run_id, start_url, base_domain, started_at))
        conn.commit()

    return run_id


def record_page(run_id, page_record, db_path=DEFAULT_DB_PATH):
    """
    Saves a crawled page and its link relationships into the database in real time.
    Computes a content hash for duplicate content identification.
    """
    url = page_record.get("url")
    final_url = page_record.get("final_url", url)
    depth = page_record.get("depth", 0)
    status = str(page_record.get("status", ""))
    title = page_record.get("title", "")
    ctype = page_record.get("content_type", "text/html")
    resp_time = page_record.get("response_time_ms", 0.0)
    found_on = page_record.get("found_on")
    internal_links = page_record.get("internal_links", [])
    external_links = page_record.get("external_links", [])

    raw_body = page_record.get("body_sample", "")
    content_hash = hashlib.sha256(raw_body.encode("utf-8")).hexdigest() if raw_body else None

    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO crawled_pages (
                run_id, url, final_url, depth, http_status, title,
                content_type, response_time_ms, content_hash, found_on,
                internal_link_count, external_link_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, url) DO UPDATE SET
                final_url = excluded.final_url,
                http_status = excluded.http_status,
                title = excluded.title,
                content_hash = excluded.content_hash,
                internal_link_count = excluded.internal_link_count,
                external_link_count = excluded.external_link_count
        """, (
            run_id, url, final_url, depth, status, title,
            ctype, resp_time, content_hash, found_on,
            len(internal_links), len(external_links)
        ))

        # Record Link graph
        link_rows = [
            (run_id, url, target, "internal") for target in internal_links
        ] + [
            (run_id, url, target, "external") for target in external_links
        ]
        if link_rows:
            cursor.executemany("""
                INSERT INTO page_links (run_id, source_url, target_url, link_type)
                VALUES (?, ?, ?, ?)
            """, link_rows)

        conn.commit()


def record_wcag_page_results(run_id, url, title, depth, compliance_summary, violations, db_path=DEFAULT_DB_PATH):
    """
    Stores WCAG evaluation results for a page:
      - Updates crawled_pages with Level A, AA, AAA pass/fail metrics
      - Inserts individual violations tagged with WCAG A, AA, AAA
      - Inserts individual failing DOM nodes
    """
    a_status = compliance_summary.get("level_a", "Untested")
    aa_status = compliance_summary.get("level_aa", "Untested")
    aaa_status = compliance_summary.get("level_aaa", "Untested")
    overall = compliance_summary.get("overall", "Non-Compliant")
    total_violations = len(violations)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE crawled_pages SET
                scanned_for_wcag = 1,
                wcag_a_status = ?,
                wcag_aa_status = ?,
                wcag_aaa_status = ?,
                overall_wcag_status = ?,
                total_violations = ?
            WHERE run_id = ? AND url = ?
        """, (a_status, aa_status, aaa_status, overall, total_violations, run_id, url))

        for v in violations:
            tags = v.get("tags", [])
            tag_set = set(tags)
            if tag_set & {"wcag2aaa", "wcag21aaa", "wcag22aaa"}:
                level = "WCAG AAA"
            elif tag_set & {"wcag2aa", "wcag21aa", "wcag22aa"}:
                level = "WCAG AA"
            elif tag_set & {"wcag2a", "wcag21a", "wcag22a"}:
                level = "WCAG A"
            else:
                level = "WCAG A"

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
            criteria_str = ", ".join(sorted(set(refs))) if refs else "N/A"

            impact = v.get("impact", "moderate")
            rule_id = v.get("id", "")
            description = v.get("help", "")
            help_url = v.get("helpUrl", "")
            nodes = v.get("nodes", [])

            sample_selector = " ".join(nodes[0].get("target", [])) if nodes else ""
            sample_html = (nodes[0].get("html") or "").replace("\n", " ").strip()[:150] if nodes else ""

            cursor.execute("""
                INSERT INTO wcag_violations (
                    run_id, page_url, page_title, depth, wcag_level,
                    wcag_criteria, impact, rule_id, instances_count,
                    description, failing_selector_sample, failing_html_sample, help_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, url, title, depth, level,
                criteria_str, impact, rule_id, len(nodes),
                description, sample_selector, sample_html, help_url
            ))
            violation_id = cursor.lastrowid

            node_records = []
            for n in nodes:
                target_str = " ".join(n.get("target", []))
                html_str = (n.get("html") or "").replace("\n", " ").strip()
                summary_msgs = [m.get("message", "") for m in (n.get("all", []) + n.get("any", []) + n.get("none", []))]
                fail_summary = "; ".join([s for s in summary_msgs if s])[:300]
                node_records.append((violation_id, run_id, url, target_str, html_str, fail_summary))

            if node_records:
                cursor.executemany("""
                    INSERT INTO wcag_element_nodes (
                        violation_id, run_id, page_url, target_selector, html_snippet, failure_summary
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, node_records)

        conn.commit()


def finish_run(run_id, db_path=DEFAULT_DB_PATH):
    """Finalizes run statistics and computes overall compliance grade."""
    finished_at = datetime.now().isoformat()

    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM crawled_pages WHERE run_id = ?", (run_id,))
        page_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM wcag_violations WHERE run_id = ?", (run_id,))
        violation_count = cursor.fetchone()[0]

        cursor.execute("""
            SELECT wcag_level, COUNT(*) FROM wcag_violations
            WHERE run_id = ? GROUP BY wcag_level
        """, (run_id,))
        level_counts = dict(cursor.fetchall())

        if violation_count == 0:
            grade = "WCAG AAA Compliant (Full Pass)"
        elif "WCAG A" not in level_counts and "WCAG AA" not in level_counts:
            grade = "WCAG AA Compliant (Failed AAA)"
        elif "WCAG A" not in level_counts:
            grade = "WCAG A Compliant (Failed AA & AAA)"
        else:
            grade = f"Non-Compliant ({level_counts.get('WCAG A', 0)} Level A issues)"

        cursor.execute("""
            UPDATE crawl_runs SET
                finished_at = ?,
                total_pages_crawled = ?,
                total_violations_found = ?,
                compliance_grade = ?
            WHERE run_id = ?
        """, (finished_at, page_count, violation_count, grade, run_id))

        conn.commit()


def get_latest_run_id(db_path=DEFAULT_DB_PATH):
    """Returns the most recent run_id in the database."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT run_id FROM crawl_runs ORDER BY started_at DESC LIMIT 1")
        row = cursor.fetchone()
        return row[0] if row else None


def list_runs(db_path=DEFAULT_DB_PATH):
    """Returns all recorded crawl runs."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT run_id, start_url, base_domain, started_at, finished_at,
                   total_pages_crawled, total_violations_found, compliance_grade
            FROM crawl_runs ORDER BY started_at DESC
        """)
        return [dict(r) for r in cursor.fetchall()]


def export_to_csv(
    db_path=DEFAULT_DB_PATH,
    run_id=None,
    out_compliance="wcag_compliance_report.csv",
    out_pages="wcag_page_compliance.csv",
    out_elements="wcag_failing_elements.csv"
):
    """
    Downloads/exports full audit data from the SQLite database into downloadable CSV files.
    If run_id is None, exports the most recent run.
    """
    init_db(db_path)
    if not run_id:
        run_id = get_latest_run_id(db_path)
        if not run_id:
            print("[!] No runs found in database to export.")
            return

    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. Export Hierarchical WCAG Compliance Report CSV
        cursor.execute("""
            SELECT page_url, page_title, depth, wcag_level, wcag_criteria,
                   impact, rule_id, instances_count AS instances, description,
                   failing_selector_sample, failing_html_sample, help_url
            FROM wcag_violations
            WHERE run_id = ?
            ORDER BY page_url,
                     CASE wcag_level
                         WHEN 'WCAG A' THEN 1
                         WHEN 'WCAG AA' THEN 2
                         WHEN 'WCAG AAA' THEN 3
                         ELSE 4
                     END,
                     CASE impact
                         WHEN 'critical' THEN 1
                         WHEN 'serious' THEN 2
                         WHEN 'moderate' THEN 3
                         WHEN 'minor' THEN 4
                         ELSE 5
                     END,
                     wcag_criteria, rule_id
        """, (run_id,))
        violations = [dict(r) for r in cursor.fetchall()]

        def safe_write(filepath, fieldnames, rows):
            try:
                with open(filepath, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                return filepath
            except PermissionError:
                base, ext = os.path.splitext(filepath)
                fallback = f"{base}_latest{ext}"
                print(f"[!] Warning: '{filepath}' is open in another program. Saving to '{fallback}'.")
                with open(fallback, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                return fallback

        comp_fields = [
            "page_url", "page_title", "depth", "wcag_level", "wcag_criteria",
            "impact", "rule_id", "instances", "description",
            "failing_selector_sample", "failing_html_sample", "help_url"
        ]
        f1 = safe_write(out_compliance, comp_fields, violations)

        # 2. Export Page Compliance Matrix CSV
        cursor.execute("""
            SELECT p.url AS page_url, p.title AS page_title, p.depth,
                   p.wcag_a_status, p.wcag_aa_status, p.wcag_aaa_status,
                   p.overall_wcag_status AS overall_compliance,
                   p.total_violations,
                   COALESCE(SUM(CASE WHEN v.impact = 'critical' THEN 1 ELSE 0 END), 0) AS critical_count,
                   COALESCE(SUM(CASE WHEN v.impact = 'serious' THEN 1 ELSE 0 END), 0) AS serious_count,
                   COALESCE(SUM(CASE WHEN v.impact = 'moderate' THEN 1 ELSE 0 END), 0) AS moderate_count,
                   COALESCE(SUM(CASE WHEN v.impact = 'minor' THEN 1 ELSE 0 END), 0) AS minor_count
            FROM crawled_pages p
            LEFT JOIN wcag_violations v ON p.run_id = v.run_id AND p.url = v.page_url
            WHERE p.run_id = ?
            GROUP BY p.url
            ORDER BY p.depth, p.url
        """, (run_id,))
        page_rows = [dict(r) for r in cursor.fetchall()]

        page_fields = [
            "page_url", "page_title", "depth",
            "wcag_a_status", "wcag_aa_status", "wcag_aaa_status",
            "overall_compliance", "total_violations",
            "critical_count", "serious_count", "moderate_count", "minor_count"
        ]
        f2 = safe_write(out_pages, page_fields, page_rows)

        # 3. Export Detailed Element Nodes CSV
        cursor.execute("""
            SELECT e.page_url, v.wcag_level, v.wcag_criteria, v.impact, v.rule_id,
                   e.target_selector, e.html_snippet, e.failure_summary
            FROM wcag_element_nodes e
            JOIN wcag_violations v ON e.violation_id = v.violation_id
            WHERE e.run_id = ?
            ORDER BY e.page_url, v.wcag_level, v.impact
        """, (run_id,))
        element_rows = [dict(r) for r in cursor.fetchall()]

        elem_fields = [
            "page_url", "wcag_level", "wcag_criteria", "impact", "rule_id",
            "target_selector", "html_snippet", "failure_summary"
        ]
        f3 = safe_write(out_elements, elem_fields, element_rows)

        print("\n" + "=" * 70)
        print(f"  EXPORT COMPLETED FOR RUN: {run_id}")
        print("=" * 70)
        print(f"  -> {f1} (Hierarchical breakdown labeled WCAG A, AA, AAA)")
        print(f"  -> {f2} (Page-by-page compliance matrix)")
        print(f"  -> {f3} (Developer element-level selector/HTML remediation list)")
        print("=" * 70 + "\n")


def print_summary(run_id=None, db_path=DEFAULT_DB_PATH):
    """Displays an executive summary of an audit run."""
    init_db(db_path)
    if not run_id:
        run_id = get_latest_run_id(db_path)
        if not run_id:
            print("[!] No runs recorded in database.")
            return

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM crawl_runs WHERE run_id = ?", (run_id,))
        run = cursor.fetchone()
        if not run:
            print(f"[!] Run '{run_id}' not found.")
            return

        cursor.execute("""
            SELECT wcag_level, COUNT(*), SUM(instances_count)
            FROM wcag_violations WHERE run_id = ?
            GROUP BY wcag_level
            ORDER BY CASE wcag_level WHEN 'WCAG A' THEN 1 WHEN 'WCAG AA' THEN 2 ELSE 3 END
        """, (run_id,))
        level_stats = cursor.fetchall()

        cursor.execute("""
            SELECT rule_id, wcag_level, impact, COUNT(*), SUM(instances_count)
            FROM wcag_violations WHERE run_id = ?
            GROUP BY rule_id ORDER BY SUM(instances_count) DESC LIMIT 5
        """, (run_id,))
        top_violations = cursor.fetchall()

        print("\n" + "=" * 70)
        print(f"  AUDIT RUN SUMMARY: {run['run_id']}")
        print(f"  Target Website: {run['start_url']} ({run['base_domain']})")
        print(f"  Started: {run['started_at']} | Finished: {run['finished_at']}")
        print(f"  Overall Grade: {run['compliance_grade']}")
        print(f"  Total Pages Crawled: {run['total_pages_crawled']}")
        print(f"  Total Violations Logged: {run['total_violations_found']}")
        print("-" * 70)
        print("  WCAG Standards Breakdown:")
        for lvl, rule_cnt, inst_cnt in level_stats:
            print(f"    - {lvl:<10}: {rule_cnt} failing rule(s), {inst_cnt} element instance(s)")
        print("-" * 70)
        print("  Top 5 Most Frequent Violations:")
        for rule, lvl, imp, r_cnt, i_cnt in top_violations:
            print(f"    * [{lvl}] [{imp.upper():<8}] {rule:<25}: {i_cnt} instance(s)")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Database manager and CSV exporter for Web Crawler & WCAG Audit Suite.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--export-csv", action="store_true", help="Export audit results to downloadable CSV files.")
    parser.add_argument("--list-runs", action="store_true", help="List all audit runs stored in the database.")
    parser.add_argument("--summary", nargs="?", const="", help="Show summary for a run (defaults to latest).")
    parser.add_argument("--run-id", help="Specify a specific run_id to export or inspect.")
    args = parser.parse_args()

    if args.list_runs:
        runs = list_runs(args.db)
        print("\n" + "=" * 80)
        print("  RECORDED AUDIT RUNS IN DATABASE")
        print("=" * 80)
        for r in runs:
            print(f"  ID: {r['run_id']:<24} | Pages: {r['total_pages_crawled']:<4} | Issues: {r['total_violations_found']:<5} | Grade: {r['compliance_grade']}")
            print(f"      URL: {r['start_url']} ({r['started_at']})")
        print("=" * 80 + "\n")
    elif args.summary is not None:
        target_run = args.run_id or (args.summary if args.summary else None)
        print_summary(target_run, db_path=args.db)
    elif args.export_csv:
        export_to_csv(db_path=args.db, run_id=args.run_id)
    else:
        export_to_csv(db_path=args.db, run_id=args.run_id)
