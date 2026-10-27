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
        │       Stage 2: Automatic WCAG Compliance Scan       │
        │  - Automatically triggered upon crawl completion    │
        │  - Headless Chromium browser via Playwright         │
        │  - Evaluates live JS-rendered DOM with axe-core     │
        │  - Checks WCAG 2.0 / 2.1 / 2.2: Levels A, AA, AAA   │
        └──────────────────────────┬──────────────────────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
        `wcag_compliance_report.csv`    `wcag_page_compliance.csv`
       (Hierarchical A / AA / AAA)     (Page-level pass/fail matrix)
                     │
                     ▼
             `wcag_results.json`
          (Full element DOM/CSS/HTML)
```

---

## Features

### Stage 1: Full-Site Crawler (`crawl_site.py`)
- **Complete Website Traversal**: Starts on the homepage, follows every internal link, and traverses all subdomains (e.g., `en.wikipedia.org`, `fr.wikipedia.org`, `blog.example.com`).
- **Base Domain Discovery**: Automatically detects root domains (handling two-part TLDs like `.co.uk`, `.org.in`, `.com.au`).
- **Automatic Audit Chain**: Once crawling finishes, the crawler automatically runs Stage 2 (WCAG compliance scan) on all discovered pages (bypassable via `--no-scan`).
- **RFC 9309 `robots.txt` Compliance & Bypass**: Caches `robots.txt` per host; provides `--ignore-robots` for crawling private authenticated routes (`/auth`, `/profile`, `/dashboard`).
- **Authenticated & Login-Protected Crawling**: Supports session cookies (`--cookie`, `--cookies-file`), custom auth headers (`--header`), or interactive one-time browser login (`--login`).
- **Session Logout Protection**: Automatically detects and skips `/logout`, `/signout`, and account deletion links so your authenticated crawl never terminates prematurely.
- **Automatic Session Persistence**: Captures sessions to `cookies.json` and `storage_state.json` and automatically reuses them on future crawl and audit runs.
- **URL Normalization**: Strips `#fragments` and tracking query parameters (`utm_source`, `utm_medium`, `fbclid`, `gclid`, etc.) to eliminate duplicate crawl loops.
- **Sitemap Support**: Automatically inspects `sitemap.xml` and sitemap indexes to find unlinked orphan pages.
- **Flexible Controls**: Configurable request delay, optional page limit caps, or `--unlimited` traversal mode.

### Stage 2: WCAG Accessibility Scanner (`wcag_scan.py`)
- **Full Standard Coverage (A, AA, AAA)**: Audits against Deque's `axe-core` rule engine for WCAG 2.0, 2.1, and 2.2 across **Level A**, **Level AA**, and **Level AAA** standards.
- **Hierarchical CSV Reporting**:
  - `wcag_compliance_report.csv`: Organized hierarchically by URL -> WCAG Standard (`WCAG A`, `WCAG AA`, `WCAG AAA`) -> Severity Impact -> Criterion. Includes failing element selectors and HTML snippets.
  - `wcag_page_compliance.csv`: Executive summary matrix indicating Pass/Fail status for Level A, Level AA, and Level AAA per URL, plus total violations and impact breakdown.
- **JavaScript & SPA Execution**: Uses a real headless Chromium instance so dynamic components, single-page applications, and interactive menus are rendered accurately.
- **Diagnostic JSON**: `wcag_results.json` contains full violation trees with exact CSS selectors, HTML snippets, and manual review flags.

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

#### Example 5: Authenticated Crawling (Login-Protected Websites)

To crawl private sections of a site (e.g., user profiles, member areas, or `/auth` routes), use one of the following methods:

##### Method 1: Using Cookies from Your Regular Browser (Recommended for Protected Sites)
Sites with aggressive anti-bot protections or Google OAuth (like `mkissa.to`) often block automated test browsers from opening sign-in popups. The simplest, 100% reliable method is to grab your session cookie from your normal browser:

1. Open your regular Google Chrome browser and log into the site.
2. Press **`F12`** to open DevTools, select the **Console** tab, and run:
   ```javascript
   document.cookie
   ```
3. Copy the returned string and pass it to the crawler with `--ignore-robots`:
   ```bash
   python crawl_site.py https://mkissa.to/ 50 --cookie "session_token=abc123xyz..." --ignore-robots
   ```
   *(Or save the cookie string into a `cookies.json` file once, and the crawler will auto-detect and reuse it on all future runs).*

##### Method 2: One-Time Interactive Browser Login (`--login`)
Launches a real Chrome window via Playwright with automation masking:

```bash
python crawl_site.py https://example.com 100 --login
```
* **Interactive Terminal Prompt:** Complete your login in the browser and press **`[ENTER]`** in your terminal. If the site's anti-bot blocks OAuth popups, the prompt also allows you to simply paste your cookie string directly.
* **Auto-Session Reuse:** Automatically saves credentials to `cookies.json` and `storage_state.json`.
* **Future Runs:** On all future runs, you don't need `--login`; the crawler and scanner auto-detect and reuse the saved session without asking:
  ```bash
  python crawl_site.py https://example.com 100
  ```

##### Method 3: Custom Headers & Bearer Tokens (`--header`)
For APIs or single-page apps using JWT or API keys:
```bash
python crawl_site.py https://example.com 100 --header "Authorization: Bearer <your_token>" --ignore-robots
```

> **Why `--ignore-robots`?** Websites routinely disallow `/auth/`, `/profile`, and `/dashboard` in their `robots.txt` to keep public search engines out. Passing `--ignore-robots` tells the crawler to traverse these authenticated internal routes.
>
> **Safety Guard:** The crawler automatically detects and skips `/logout`, `/signout`, and account deletion links to prevent your session from being terminated during the crawl.

#### Outputs Generated by Stage 1:
- **`site_map.json`**: Hierarchical JSON dictionary mapping every page to its HTTP status, depth, title, parent page, internal links, and external links.
- **`pages.csv`**: Flat spreadsheet listing all discovered pages with their internal/external link counts.

---

### Stage 2: Run WCAG Accessibility Audit

Once `site_map.json` has been generated, run the accessibility auditor:

```bash
python wcag_scan.py
```
*(If you used `--login` in Stage 1, `wcag_scan.py` automatically detects and reuses `storage_state.json` to audit the authenticated pages with no flags needed).*

To scan from a custom JSON file:
```bash
python wcag_scan.py path/to/custom_site_map.json
```

#### Outputs Generated by Stage 2:
- **`wcag_compliance_report.csv`** *(Hierarchical Detailed Report)*:
  Every accessibility violation organized hierarchically:
  - **Grouped by URL**
  - **Sub-grouped by WCAG Standard**: Explicitly labeled as `WCAG A`, `WCAG AA`, or `WCAG AAA`
  - **Sorted by Impact Severity**: `critical` -> `serious` -> `moderate` -> `minor`
  - **Columns**: `page_url`, `page_title`, `depth`, `wcag_level` (`WCAG A`, `WCAG AA`, `WCAG AAA`), `wcag_criteria` (e.g. `1.4.1`, `1.4.3`, `1.4.6`), `impact`, `rule_id`, `instances`, `description`, `failing_selector_sample`, `failing_html_sample`, `help_url`.
- **`wcag_page_compliance.csv`** *(Executive Page Compliance Matrix)*:
  Summary table evaluating compliance per URL:
  - `page_url`, `page_title`, `depth`
  - `wcag_a_status`: `PASS` or `FAIL (N rules)`
  - `wcag_aa_status`: `PASS` or `FAIL (N rules)`
  - `wcag_aaa_status`: `PASS` or `FAIL (N rules)`
  - `overall_compliance`: `WCAG AAA Compliant`, `WCAG AA Compliant`, `WCAG A Compliant`, or `Non-Compliant`
  - `total_violations`, `critical_count`, `serious_count`, `moderate_count`, `minor_count`
- **`wcag_summary.csv`**: Standard violation table with `wcag_level` included.
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
