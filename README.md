# Full-Site Web Crawler & WCAG Accessibility Audit Suite

A robust, two-stage Python pipeline that crawls an entire website (including all subdomains), maps its complete link structure, and executes automated **WCAG 2.0 / 2.1 / 2.2 (Level A & AA)** accessibility scans using **Playwright** and **axe-core**.

---

## Architecture Overview

```
                      ┌─────────────────────────┐
                      │    Target Website URL   │
                      └────────────┬────────────┘
                                   │
                                   ▼
        ┌─────────────────────────────────────────────────────┐
        │               Stage 1: crawl_site.py                │
        │  - BFS queue with depth tracking                    │
        │  - Traverses homepage -> internal links -> subdomains│
        │  - RFC 9309 robots.txt cached per host              │
        │  - URL normalization (strips UTM/tracking params)    │
        │  - Sitemap.xml orphan page discovery                │
        └──────────────────────────┬──────────────────────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
             `site_map.json`                `pages.csv`
       (Full link graph & hierarchy)    (Spreadsheet report)
                     │
                     ▼
        ┌─────────────────────────────────────────────────────┐
        │                Stage 2: wcag_scan.py                │
        │  - Headless Chromium browser via Playwright         │
        │  - Evaluates live JS-rendered DOM with axe-core     │
        │  - Checks WCAG 2.0 / 2.1 / 2.2 (Levels A & AA)      │
        └──────────────────────────┬──────────────────────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
            `wcag_summary.csv`           `wcag_results.json`
          (Issues by criterion/page)   (Full element CSS/HTML)
```

---

## Features

### Stage 1: Full-Site Crawler (`crawl_site.py`)
- **Complete Website Traversal**: Starts on the homepage, follows every internal link, and traverses all subdomains (e.g., `en.wikipedia.org`, `fr.wikipedia.org`, `blog.example.com`).
- **Base Domain Discovery**: Automatically detects root domains (handling two-part TLDs like `.co.uk`, `.org.in`, `.com.au`).
- **RFC 9309 `robots.txt` Compliance**: Caches `robots.txt` per host so each subdomain's rules are respected independently without blocking on 404s.
- **URL Normalization**: Strips `#fragments` and tracking query parameters (`utm_source`, `utm_medium`, `fbclid`, `gclid`, etc.) to eliminate duplicate crawl loops.
- **Sitemap Support**: Automatically inspects `sitemap.xml` and sitemap indexes to find unlinked orphan pages.
- **Flexible Controls**: Configurable request delay, optional page limit caps, or `--unlimited` traversal mode.

### Stage 2: WCAG Accessibility Scanner (`wcag_scan.py`)
- **JavaScript Execution**: Uses a real headless Chromium instance so Single Page Applications (SPAs) and dynamic components are rendered accurately.
- **Industry Standard Rules**: Audits against Deque's `axe-core` rule engine for WCAG criteria:
  - `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`
- **Actionable Outputs**:
  - `wcag_summary.csv`: Filterable table with page URL, rule ID, impact level (`critical`, `serious`, `moderate`, `minor`), WCAG criterion (e.g. `wcag143`), failure count, and fix documentation URLs.
  - `wcag_results.json`: Full violation breakdown including exact failing DOM selectors, HTML snippets, and recommendations.

### Starter Crawler (`crawler.py`)
- A minimal, single-file BFS crawler with politeness limits, ideal for quick tests and learning crawler fundamentals.

---

## Quick Start & Installation

### 1. Prerequisites
- Python 3.10+ (compatible with Python 3.11, 3.12, 3.13, 3.14)
- Git

### 2. Clone the Repository
```bash
git clone https://github.com/angshumanroy20/Web-Crawler.git
cd Web-Crawler
```

### 3. Create and Activate Virtual Environment

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
*(If PowerShell restricts scripts, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first).*

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Install Dependencies & Playwright Browsers
```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Step-by-Step Usage Guide

### Stage 1: Crawl the Website

#### Example 1: Crawl with a Page Limit (Recommended for First Run)
Traverse the site up to 50 pages:
```bash
python crawl_site.py https://example.com 50
```

#### Example 2: Crawl the Entire Website Recursively (No Limit)
Traverse until every link on the site and all its subdomains is visited:
```bash
python crawl_site.py https://example.com --unlimited
```
*(Or specify `0`: `python crawl_site.py https://example.com 0`)*

#### Example 3: Adjust Politeness Delay
Add a delay between requests (default is `0.5` seconds):
```bash
python crawl_site.py https://example.com 100 --delay 1.0
```

#### Example 4: Restrict to Exact Domain (Exclude Other Subdomains)
Only crawl pages on the exact host provided (e.g. stay strictly on `blog.example.com`):
```bash
python crawl_site.py https://blog.example.com 100 --exact-domain
```

#### Outputs Generated by Stage 1:
- **`site_map.json`**: Hierarchical JSON dictionary mapping every page to its HTTP status, depth, title, parent page, internal links, and external links.
- **`pages.csv`**: Flat spreadsheet listing all discovered pages with their internal/external link counts.

---

### Stage 2: Run WCAG Accessibility Audit

Once `site_map.json` has been generated, run the accessibility auditor:

```bash
python wcag_scan.py
```

To scan from a custom JSON file:
```bash
python wcag_scan.py path/to/custom_site_map.json
```

#### Outputs Generated by Stage 2:
- **`wcag_summary.csv`**: One row per violation per page with columns:
  - `page`: Page URL
  - `rule`: axe-core rule (e.g. `color-contrast`, `image-alt`, `label`)
  - `impact`: Severity (`critical`, `serious`, `moderate`, `minor`)
  - `wcag_criteria`: WCAG rule reference (e.g. `wcag143`, `wcag111`, `wcag412`)
  - `description`: Plain-English summary of the issue
  - `instances`: Total failing elements on that page
  - `help_url`: Deque University guidance link explaining how to resolve it
- **`wcag_results.json`**: Complete structural JSON containing exact CSS selectors, HTML code snippets, and items needing manual review.

---

## Using the Minimal Starter Crawler (`crawler.py`)

For a lightweight single-domain crawl:
```bash
python crawler.py https://example.com 25
```

---

## Automated vs. Manual Testing Considerations

Automated tools catch approximately 30% to 50% of WCAG issues. They reliably detect:
- Missing image alt attributes (`wcag111`)
- Insufficient text color contrast ratios (`wcag143`)
- Missing or malformed form inputs & labels (`wcag412`)
- Incorrect ARIA roles, states, and attributes
- Empty links and buttons

**Items Requiring Manual Verification:**
- Whether image alternative text is semantically meaningful.
- Keyboard navigation order and focus traps.
- Screen reader audio accuracy.
- Items tagged in `wcag_results.json` under `"needs_manual_review"`.

---

## License

MIT License. Free for personal and commercial audits.
