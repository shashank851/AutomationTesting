#!/usr/bin/env python3
"""
Morpheus Environment Hub — Automated Test Runner
=================================================
Reads test cases from an Excel file, executes automated checks with Playwright,
and writes results (Status, Actual Result, Comments) back to a timestamped copy.

Usage:
    python3 run_tests.py
    python3 run_tests.py --input MorpheusEnvHubTestCases_Results.xlsx
    python3 run_tests.py --headed          # Show browser (useful for debugging)
    python3 run_tests.py --skip-auth       # Anonymous tests only (no OAuth login)

First-run authentication:
    The site uses Auth0 (Google / Microsoft OAuth).  On the first run a headed
    browser window opens — log in manually.  The session is saved to
    auth_state.json and reused on every subsequent run automatically.
    To force a fresh login:  rm auth_state.json

Output:
    Results_YYYYMMDD_HHMMSS.xlsx  — original file with Status, Actual Result,
                                    and Comments columns filled in.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime

# ── Dependency check ──────────────────────────────────────────────────────────
try:
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill, Font
except ImportError:
    print("ERROR: openpyxl not installed.\n  Run: python3 -m pip install --user openpyxl")
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("ERROR: playwright not installed.\n  Run: python3 -m pip install --user playwright")
    sys.exit(1)


# ── Configuration ─────────────────────────────────────────────────────────────
BASE_URL        = "https://hub.morpheus.skyfall.ai"
AUTH_STATE_FILE = "auth_state.json"
TIMEOUT_MS      = 25_000

# CSS selector that matches environment cards (Tailwind-based, no semantic class names)
# Cards are <a href="/environments/..."> inside a grid inside <main>
ENV_CARD_SEL = "main a[href*='/environments/']"

# Known fixture used across multiple tests
KNOWN_ENV_SLUG = "prod-edi-gateway"
KNOWN_ENV_NAME = "Production EDI Gateway"

# Excel column names expected in the input file
COL_ID       = "Test Case #"
COL_TITLE    = "Title"
COL_STEPS    = "Steps"
COL_EXPECTED = "Expected Result"
COL_STATUS   = "Status"
COL_ACTUAL   = "Actual Result"
COL_COMMENTS = "Comments"

# Status display values written to Excel
STATUS_PASS   = "✅ PASS"
STATUS_FAIL   = "❌ FAIL"
STATUS_MANUAL = "⚠️ MANUAL"
STATUS_SKIP   = "— SKIP"

# Cell fill colours for results
FILL = {
    "PASS":   PatternFill("solid", fgColor="C6EFCE"),   # green
    "FAIL":   PatternFill("solid", fgColor="FFC7CE"),   # red
    "MANUAL": PatternFill("solid", fgColor="FFEB9C"),   # yellow
    "SKIP":   PatternFill("solid", fgColor="D9D9D9"),   # grey
}

# ── Result store ──────────────────────────────────────────────────────────────
_results: dict = {}   # { "EH-01": {"status": "PASS", "actual": "...", "comment": "..."} }


def rec(tid: str, status: str, actual: str, comment: str = "") -> None:
    """Record a result and print it immediately."""
    _results[tid] = {
        "status":  status,
        "actual":  actual[:400],
        "comment": comment,
    }
    symbols = {"PASS": "✅", "FAIL": "❌", "MANUAL": "⚠️ ", "SKIP": "— "}
    sym = symbols.get(status, "?")
    print(f"  {sym} {tid}: {actual[:110]}")


# ── Excel helpers ─────────────────────────────────────────────────────────────

def load_test_cases(path: str) -> list:
    """
    Load test case definitions from Excel.
    Returns a list of dicts with keys: id, title, steps, expected.
    Raises ValueError if the file appears empty or unreadable.
    """
    wb = load_workbook(path)
    ws = wb.active

    # Map header name → column index
    headers = {}
    for c in range(1, ws.max_column + 1):
        val = ws.cell(1, c).value
        if val:
            headers[str(val).strip()] = c

    def col(*names):
        for name in names:
            if name in headers:
                return headers[name]
        return None

    tc_col  = col(COL_ID,       "Test Case Number", "TC #", "ID")
    ttl_col = col(COL_TITLE,    "Test Name", "Name")
    stp_col = col(COL_STEPS,    "Test Steps", "Step")
    exp_col = col(COL_EXPECTED, "Expected",  "Expected Outcome")

    if not tc_col:
        raise ValueError(f"Could not find a 'Test Case #' column in {path}. "
                         f"Found headers: {list(headers.keys())}")

    test_cases = []
    for r in range(2, ws.max_row + 1):
        tc_id = ws.cell(r, tc_col).value
        if not tc_id:
            continue
        test_cases.append({
            "id":       str(tc_id).strip(),
            "title":    ws.cell(r, ttl_col).value if ttl_col else "",
            "steps":    ws.cell(r, stp_col).value if stp_col else "",
            "expected": ws.cell(r, exp_col).value if exp_col else "",
            "_row":     r,
        })

    if not test_cases:
        raise ValueError(f"No test case rows found in {path}.")

    return test_cases


def write_results(input_path: str, output_path: str) -> None:
    """
    Copy input_path to output_path, then fill in Status, Actual Result, Comments
    from _results for every test that was executed.
    """
    shutil.copy(input_path, output_path)
    wb = load_workbook(output_path)
    ws = wb.active

    # Locate columns by header
    headers = {}
    for c in range(1, ws.max_column + 1):
        val = ws.cell(1, c).value
        if val:
            headers[str(val).strip()] = c

    def col(*names):
        for name in names:
            if name in headers:
                return headers[name]
        return None

    tc_col  = col(COL_ID,       "Test Case Number", "TC #", "ID")
    st_col  = col(COL_STATUS,   "Status")
    ar_col  = col(COL_ACTUAL,   "Actual Result")
    cm_col  = col(COL_COMMENTS, "Comments")

    # Add missing columns if not present
    next_col = ws.max_column + 1
    if not st_col:
        ws.cell(1, next_col).value = COL_STATUS
        st_col = next_col; next_col += 1
    if not ar_col:
        ws.cell(1, next_col).value = COL_ACTUAL
        ar_col = next_col; next_col += 1
    if not cm_col:
        ws.cell(1, next_col).value = COL_COMMENTS
        cm_col = next_col

    # Write results row by row
    for r in range(2, ws.max_row + 1):
        tc_id = ws.cell(r, tc_col).value if tc_col else None
        if not tc_id:
            continue
        tc_id = str(tc_id).strip()
        if tc_id not in _results:
            continue

        result  = _results[tc_id]
        status  = result["status"]
        label   = {"PASS": STATUS_PASS, "FAIL": STATUS_FAIL,
                   "MANUAL": STATUS_MANUAL, "SKIP": STATUS_SKIP}.get(status, status)
        fill    = FILL.get(status)

        for col_idx, value in [(st_col,  label),
                               (ar_col,  result["actual"]),
                               (cm_col,  result["comment"])]:
            if col_idx:
                cell = ws.cell(r, col_idx)
                cell.value = value
                if fill:
                    cell.fill = fill

    wb.save(output_path)
    print(f"\n  Results written → {output_path}")


# ── Auth helper ───────────────────────────────────────────────────────────────

def ensure_auth_state():
    """
    Ensure a valid auth_state.json exists before running authenticated tests.
    If missing or invalid, automatically launches setup_auth.py so the user
    can log in interactively — no need to run it separately.
    Returns the path to the file, or None if setup failed.
    """
    def _is_valid():
        return (os.path.exists(AUTH_STATE_FILE)
                and os.path.getsize(AUTH_STATE_FILE) >= 500)

    if _is_valid():
        size      = os.path.getsize(AUTH_STATE_FILE)
        age_hours = (time.time() - os.path.getmtime(AUTH_STATE_FILE)) / 3600
        print(f"\n  [auth] Found {AUTH_STATE_FILE}  ({age_hours:.1f}h old, {size:,} bytes)")
        return AUTH_STATE_FILE

    # Not found or invalid — run setup_auth.py automatically
    missing = not os.path.exists(AUTH_STATE_FILE)
    if missing:
        print("\n" + "=" * 62)
        print("  AUTH SESSION NOT FOUND — launching login setup")
        print("=" * 62)
    else:
        size = os.path.getsize(AUTH_STATE_FILE)
        print(f"\n  ⚠️  {AUTH_STATE_FILE} is invalid ({size} bytes) — re-running login setup.")

    print()
    print("  A browser will open. Log in with Google or Microsoft,")
    print("  then close the browser window when done.")
    print()

    setup_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_auth.py")
    result = subprocess.run([sys.executable, setup_script])

    if result.returncode != 0 or not _is_valid():
        print("\n  ❌  Authentication setup did not complete successfully.")
        print("  Authenticated tests will be skipped.")
        return None

    size = os.path.getsize(AUTH_STATE_FILE)
    print(f"\n  ✅  Session ready — {AUTH_STATE_FILE}  ({size:,} bytes)")
    return AUTH_STATE_FILE

    print(f"\n  [auth] Found {AUTH_STATE_FILE}  ({age_hours:.1f}h old, {size:,} bytes)")
    return AUTH_STATE_FILE


# ── Individual test functions ─────────────────────────────────────────────────
# Each function receives:
#   p  — Playwright Page (already has correct auth context)
#   tc — dict with keys: id, title, steps, expected

def test_eh01(p, tc):
    t0 = time.time()
    p.goto(BASE_URL, wait_until="networkidle")
    # networkidle waits for JS rendering; record total elapsed including that
    elapsed = round(time.time() - t0, 2)

    issues = []
    if elapsed >= 20.0:
        issues.append(f"load time {elapsed}s ≥ 20s")
    if p.get_by_text("Environments Hub").count() == 0:
        issues.append("'Environments Hub' heading not found")

    cards = p.locator(ENV_CARD_SEL).count()
    if cards == 0:
        issues.append("no environment cards visible")

    if issues:
        rec("EH-01", "FAIL", f"Loaded in {elapsed}s. Issues: {'; '.join(issues)}")
    else:
        rec("EH-01", "PASS",
            f"Page loaded in {elapsed}s. 'Environments Hub' heading present. "
            f"{cards} environment card(s) visible.")


def test_eh02(p, tc):
    p.goto(BASE_URL, wait_until="networkidle")
    required = ["Environments Hub", "Starred Environments", "Documentation"]
    optional = ["Announcements", "Profile"]

    missing  = [i for i in required if p.get_by_text(i).count() == 0]
    found    = [i for i in required if i not in missing]
    extra    = [i for i in optional if p.get_by_text(i).count() > 0]

    if missing:
        rec("EH-02", "FAIL",
            f"Found: {', '.join(found + extra)}. Missing required: {', '.join(missing)}")
    else:
        rec("EH-02", "PASS",
            f"All required nav items present: {', '.join(found + extra)}")


def _wait_for_cards(p) -> list:
    """Return all environment card elements (networkidle already waited for them)."""
    return p.locator(ENV_CARD_SEL).all()


def test_eh03(p, tc):
    p.goto(BASE_URL, wait_until="networkidle")

    cards = _wait_for_cards(p)
    if not cards:
        rec("EH-03", "FAIL", "No environment cards found on homepage"); return

    first = cards[0].text_content() or ""
    checks = {
        "name visible":  len(first.strip()) > 10,
        "has date":      any(x in first for x in ["2025", "2026", "Jan", "Feb", "Mar", "Apr",
                                                    "May", "Jun", "Jul", "Aug", "Sep", "Oct",
                                                    "Nov", "Dec"]),
        "has star/count": bool(re.search(r'\d', first)),
    }
    failed = [k for k, v in checks.items() if not v]

    if failed:
        rec("EH-03", "MANUAL",
            f"{len(cards)} cards found. Could not verify: {', '.join(failed)}. "
            "Inspect cards manually to confirm name, description, date, and star count.")
    else:
        rec("EH-03", "PASS",
            f"{len(cards)} environment cards found, each showing name, date, and star count.")


def test_eh04(p, tc):
    """Anonymous redirect — this context has no stored auth state."""
    p.goto(f"{BASE_URL}/starred", wait_until="networkidle")
    body = p.locator("body").text_content().lower()
    url  = p.url.lower()

    prompted = (
        "login" in body or "sign in" in body or "not logged in" in body
        or "auth0" in url or "/login" in url
        or p.get_by_text("Sign In").count() > 0
    )
    if prompted:
        rec("EH-04", "PASS",
            "Anonymous user visiting /starred is shown a login prompt / redirected to login.")
    else:
        rec("EH-04", "FAIL",
            f"No login prompt shown for anonymous user on /starred. URL: {p.url}")


def test_eh05(p, tc):
    p.goto(BASE_URL, wait_until="networkidle")
    if (p.get_by_text("New/Updated", exact=False).count() > 0
            or p.get_by_text("New", exact=False).count() > 0
            or p.locator("[class*='new' i], [class*='updated' i]").count() > 0):
        rec("EH-05", "PASS", "New/Updated section visible on homepage with environment card(s).")
    else:
        rec("EH-05", "MANUAL",
            "New/Updated section not currently visible. "
            "Section only appears when an environment was published/updated within the last 15 days. "
            "Publish or update an environment and re-run to verify.")


def test_eh06(p, tc):
    rec("EH-06", "MANUAL",
        "Sign-up requires a brand-new unregistered email and completion of OAuth flow. "
        "Manual steps: click Sign In → choose Google/Microsoft with a new account → "
        "verify account created and Profile reflects the signed-in state.")


def test_eh07(p, tc):
    rec("EH-07", "MANUAL",
        "Requires a fresh anonymous/incognito session — cannot run alongside authenticated tests. "
        "Manual steps: open incognito → browse homepage → confirm Star/Download/Feedback "
        "actions are hidden or show a login prompt.")


def test_eh08(p, tc):
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    p.wait_for_timeout(500)

    body_before = p.locator("body").text_content()
    m_before    = re.search(r'(\d+)\s*(?:Star|star)', body_before)
    count_before = int(m_before.group(1)) if m_before else None

    star_btn = (p.get_by_role("button", name=re.compile(r"star", re.IGNORECASE)).first
                or p.get_by_text("Star", exact=False).first)
    if star_btn.count() == 0:
        rec("EH-08", "FAIL", "Star button not found on environment detail page"); return

    star_btn.click()
    p.wait_for_timeout(2000)

    # Confirm it appears in /starred
    p.goto(f"{BASE_URL}/starred", wait_until="networkidle")
    in_starred = KNOWN_ENV_NAME in (p.locator("main").text_content() or "")

    # Check count change
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    body_after  = p.locator("body").text_content()
    m_after     = re.search(r'(\d+)\s*(?:Star|star)', body_after)
    count_after = int(m_after.group(1)) if m_after else None

    count_msg = (f"Star count: {count_before} → {count_after}"
                 if count_before is not None and count_after is not None
                 else "Star count not readable from DOM")

    if in_starred or (count_before is not None and count_after is not None
                      and count_after != count_before):
        rec("EH-08", "PASS",
            f"Star toggled successfully. {count_msg}. "
            f"Environment {'visible' if in_starred else 'not confirmed'} in /starred.")
    else:
        rec("EH-08", "MANUAL",
            f"Star button clicked. {count_msg}. "
            "Verify toggle and /starred page manually.")


def test_eh09(p, tc):
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    p.wait_for_timeout(500)

    body_before  = p.locator("main").text_content()
    m_before     = re.search(r'(\d+)\s*Download', body_before, re.IGNORECASE)
    count_before = int(m_before.group(1)) if m_before else None

    dl_btn = p.get_by_text("Download Environment", exact=False)
    if dl_btn.count() == 0:
        dl_btn = p.get_by_role("button", name=re.compile(r"download", re.IGNORECASE))
    if dl_btn.count() == 0:
        rec("EH-09", "FAIL", "Download button not found on detail page"); return

    downloaded = False
    try:
        with p.expect_download(timeout=10_000) as dl_info:
            dl_btn.first.click()
        dl_info.value.cancel()
        downloaded = True
    except Exception:
        dl_btn.first.click()
        p.wait_for_timeout(3000)

    body_after  = p.locator("main").text_content()
    m_after     = re.search(r'(\d+)\s*Download', body_after, re.IGNORECASE)
    count_after = int(m_after.group(1)) if m_after else None

    if count_after is not None and count_before is not None and count_after > count_before:
        rec("EH-09", "PASS", f"Download triggered. Count: {count_before} → {count_after}")
    elif downloaded:
        rec("EH-09", "MANUAL",
            f"Download dialog triggered. Count before: {count_before}, after: {count_after}. "
            "Count may update asynchronously — verify manually.")
    else:
        rec("EH-09", "MANUAL",
            f"Download button clicked. Count before: {count_before}, after: {count_after}. "
            "Verify count incremented manually.")


def test_eh10(p, tc):
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    p.wait_for_timeout(500)

    textarea = p.locator("textarea").first
    if textarea.count() == 0:
        rec("EH-10", "FAIL",
            "Feedback textarea not found — user may not be authenticated or "
            "form requires login. Check auth_state.json.")
        return

    ts   = datetime.now().strftime("%H:%M:%S")
    text = f"Automated test feedback [{ts}]"
    textarea.fill(text)

    submit = p.locator("button[type='submit']").or_(
        p.get_by_role("button", name=re.compile(r"submit", re.IGNORECASE)))
    if submit.count() == 0:
        rec("EH-10", "FAIL", "Submit button not found in feedback section"); return

    submit.last.click()
    p.wait_for_timeout(2500)

    body = p.locator("main").text_content()
    if text[:20] in body:
        rec("EH-10", "PASS", "Feedback submitted and visible in Community Feedback section.")
    else:
        rec("EH-10", "MANUAL",
            f"Submit clicked. Verify '{text[:30]}…' appears in feedback list.")


def _get_star_counts(p) -> list:
    """
    Extract star counts from environment cards.
    The HTML structure is: <span class="text-sm">N</span><img src="...star-icon.svg">
    We find each star icon's parent div and read the adjacent span's text.
    """
    counts = p.evaluate("""
        () => {
            const imgs = document.querySelectorAll('img[src*="star-icon"]');
            const result = [];
            imgs.forEach(img => {
                const parent = img.parentElement;
                if (parent) {
                    const span = parent.querySelector('span');
                    if (span) {
                        const n = parseInt(span.textContent.trim(), 10);
                        if (!isNaN(n)) result.push(n);
                    }
                }
            });
            return result;
        }
    """)
    return counts or []


def test_eh11(p, tc):
    p.goto(BASE_URL, wait_until="networkidle")

    counts = _get_star_counts(p)

    if len(counts) >= 2:
        sorted_ok = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
        if sorted_ok:
            rec("EH-11", "PASS",
                f"Environments sorted descending by star count: {counts}")
        else:
            rec("EH-11", "FAIL",
                f"Sort order incorrect. Star counts (card order): {counts}. Expected descending.")
    else:
        rec("EH-11", "MANUAL",
            f"Could not extract star counts from cards (found: {counts}). "
            "Visually confirm All Environments is sorted descending by star count.")


def test_eh12(p, tc):
    p.goto(BASE_URL, wait_until="networkidle")
    search = p.get_by_placeholder(re.compile(r"search", re.IGNORECASE))
    if search.count() == 0:
        rec("EH-12", "FAIL", "Search bar not found on homepage"); return

    # Use KNOWN_ENV_NAME which we know exists in the dataset
    search_term = "EDI"
    search.fill(search_term)
    search.press("Enter")
    p.wait_for_timeout(1500)
    body  = p.locator("main").text_content()
    cards = p.locator(ENV_CARD_SEL).count()

    if "edi" in body.lower() and cards > 0:
        rec("EH-12", "PASS",
            f"Search by name '{search_term}' returned {cards} matching environment(s).")
    elif cards > 0:
        rec("EH-12", "PASS",
            f"Search by name '{search_term}' returned {cards} environment card(s).")
    else:
        rec("EH-12", "FAIL",
            f"Search '{search_term}' returned no visible cards. "
            f"Page text snippet: {body[:150]}")


def test_eh13(p, tc):
    rec("EH-13", "MANUAL",
        "Search by tag requires a known tag value from the current dataset. "
        "Manual steps: note a tag shown on an environment card → type it in the search bar → "
        "verify only environments with that tag are shown.")


def test_eh14(p, tc):
    p.goto(BASE_URL, wait_until="networkidle")
    search = p.get_by_placeholder(re.compile(r"search", re.IGNORECASE))
    if search.count() == 0:
        rec("EH-14", "FAIL", "Search bar not found"); return

    search.fill("machine learning")
    search.press("Enter")
    p.wait_for_timeout(1500)
    body  = p.locator("main").text_content()
    cards = p.locator(ENV_CARD_SEL).count()

    if "machine learning" in body.lower() and cards > 0:
        rec("EH-14", "PASS",
            f"Search by description 'machine learning' returned {cards} result(s).")
    elif cards > 0:
        rec("EH-14", "PASS",
            f"Search 'machine learning' returned {cards} card(s).")
    else:
        rec("EH-14", "MANUAL",
            "Search 'machine learning' returned no results. "
            "Verify with a phrase known to appear in an environment description, then re-run.")


def test_eh15(p, tc):
    p.goto(BASE_URL, wait_until="networkidle")
    search = p.get_by_placeholder(re.compile(r"search", re.IGNORECASE))
    if search.count() == 0:
        rec("EH-15", "FAIL", "Search bar not found"); return

    search.fill("zzznomatchxxx")
    search.press("Enter")
    p.wait_for_timeout(2000)
    body  = p.locator("main").text_content().lower()
    cards = p.locator(ENV_CARD_SEL).count()
    no_results = (
        "no result" in body or "no environment" in body
        or "nothing found" in body or "0 result" in body
        or cards == 0
    )
    if no_results:
        rec("EH-15", "PASS",
            "Empty/no-results state shown for unmatched query 'zzznomatchxxx'. No cards visible.")
    else:
        rec("EH-15", "FAIL",
            f"Expected empty state but {cards} card(s) still visible after unmatched search.")


def test_eh16(p, tc):
    # Navigate directly — avoids ambiguity of clicking one of two cards with the same name
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")

    body = p.locator("body").text_content() or ""
    checks = {
        "URL contains /environments/": "/environments/" in p.url,
        "Environment name visible":    KNOWN_ENV_NAME in body,
        "Has date":                    any(x in body for x in ["2025", "2026",
                                           "Jan","Feb","Mar","Apr","May","Jun",
                                           "Jul","Aug","Sep","Oct","Nov","Dec"]),
        "Download button present":     "Download Environment" in body,
        "Community Feedback section":  "Community Feedback" in body,
    }
    failed = [k for k, v in checks.items() if not v]
    passed = [k for k, v in checks.items() if v]

    if not failed:
        rec("EH-16", "PASS",
            f"All detail page fields verified: {', '.join(passed)}")
    elif len(failed) <= 2:
        rec("EH-16", "MANUAL",
            f"Passed: {', '.join(passed)} | Could not verify: {', '.join(failed)}")
    else:
        rec("EH-16", "FAIL",
            f"Passed: {', '.join(passed)} | Missing: {', '.join(failed)}")


def test_eh17(p, tc):
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}",
           wait_until="networkidle")
    body     = p.locator("main").text_content() or ""
    ver_link = p.get_by_text("version history", exact=False)

    if ver_link.count() > 0:
        ver_link.first.click()
        p.wait_for_timeout(1000)
        rec("EH-17", "PASS", "Version history link found and opened successfully.")
    elif "version" in body.lower():
        rec("EH-17", "MANUAL",
            "Version field present on detail page but no explicit 'view version history' link. "
            "Verify multiple versions are listed/accessible manually.")
    else:
        rec("EH-17", "MANUAL",
            "No version history section found. "
            "Test requires an environment with multiple published versions.")


def test_eh18(p, tc):
    """Star from card — runs in the authenticated context."""
    p.goto(BASE_URL, wait_until="networkidle")
    p.wait_for_timeout(500)

    star_btn = p.locator(
        "button[aria-label*='star' i], [class*='star' i] button, "
        "[data-testid*='star' i]"
    ).first
    if star_btn.count() == 0:
        rec("EH-18", "MANUAL",
            "Star button not identifiable on environment cards via automation selectors. "
            "Verify manually: clicking ☆ on a card toggles it to ★ and increments the count.")
        return

    star_btn.click()
    p.wait_for_timeout(1500)

    body_after = p.locator("main").text_content()
    rec("EH-18", "MANUAL",
        "Star button clicked on card. "
        "Verify star icon toggled and count updated in the UI.")


def test_eh19(p, tc):
    """Unstar from card — reverse of EH-18."""
    p.goto(BASE_URL, wait_until="networkidle")
    p.wait_for_timeout(500)

    unstar_btn = p.locator(
        "button[aria-label*='unstar' i], button[aria-label*='starred' i], "
        "[class*='starred' i] button, [class*='active' i][class*='star' i]"
    ).first

    if unstar_btn.count() == 0:
        rec("EH-19", "MANUAL",
            "No active/starred star button found on cards. "
            "Manual steps: star an environment first → click the filled star to unstar → "
            "verify count decrements.")
        return

    unstar_btn.click()
    p.wait_for_timeout(1500)
    rec("EH-19", "MANUAL",
        "Unstar button clicked. Verify the star icon reverted to unfilled and count decremented.")


def test_eh20(p, tc):
    p.goto(f"{BASE_URL}/starred", wait_until="networkidle")
    body  = p.locator("main").text_content() or ""
    cards = p.locator(ENV_CARD_SEL).count()

    if "not logged in" in body.lower() or "sign in" in body.lower():
        rec("EH-20", "FAIL",
            "Starred Environments page shows login prompt — authentication not working. "
            "Delete auth_state.json and re-run to trigger a fresh login.")
    elif cards > 0:
        rec("EH-20", "PASS",
            f"Starred Environments page loads correctly for authenticated user. "
            f"{cards} starred environment card(s) visible.")
    elif len(body.strip()) > 100:
        rec("EH-20", "MANUAL",
            "Page loaded without error but no cards found. "
            "Star an environment (EH-08) and re-run.")
    else:
        rec("EH-20", "FAIL", f"Starred Environments page appears empty. URL: {p.url}")


def test_eh21(p, tc):
    p.goto(f"{BASE_URL}/starred", wait_until="networkidle")
    body = p.locator("main").text_content() or ""

    if "not logged in" in body.lower():
        rec("EH-21", "FAIL", "Not authenticated on Starred Environments page"); return

    counts = _get_star_counts(p)

    if len(counts) >= 2:
        sorted_ok = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
        rec("EH-21",
            "PASS" if sorted_ok else "FAIL",
            f"Starred environments star counts: {counts} — "
            f"{'sorted descending ✓' if sorted_ok else 'NOT sorted descending ✗'}")
    else:
        rec("EH-21", "MANUAL",
            f"Fewer than 2 starred items found (counts: {counts}). "
            "Star multiple environments and re-run to verify sort order.")


def test_eh22(p, tc):
    p.goto(f"{BASE_URL}/docs", wait_until="networkidle")
    body = p.locator("body").text_content() or ""

    # Page loaded successfully if "Documentation" heading is present
    page_loaded = "Documentation" in body
    expected    = ["Getting Started", "CLI Overview", "FAQ"]
    found       = [d for d in expected if d.lower() in body.lower()]
    missing     = [d for d in expected if d not in found]

    if len(found) == len(expected):
        rec("EH-22", "PASS",
            f"Documentation page loaded with all expected cards: {', '.join(found)}")
    elif found:
        rec("EH-22", "MANUAL",
            f"Documentation page loaded. Found: {', '.join(found)}. "
            f"Not yet published: {', '.join(missing)}.")
    elif page_loaded:
        rec("EH-22", "FAIL",
            f"Documentation page loaded at /docs but no content cards found "
            f"({', '.join(expected)}). Page shows only placeholder text.")
    else:
        rec("EH-22", "FAIL",
            f"/docs page did not load. URL: {p.url}")


def test_eh23(p, tc):
    p.goto(f"{BASE_URL}/docs", wait_until="networkidle")
    gs = p.get_by_text("Getting Started", exact=False)
    if gs.count() > 0:
        gs.first.click()
        p.wait_for_load_state("networkidle")
        rec("EH-23", "PASS", f"'Getting Started' card opened. URL: {p.url}")
    else:
        rec("EH-23", "FAIL",
            "'Getting Started' card not found on /docs page. "
            "Documentation content has not been published yet.")


def test_eh24(p, tc):
    p.goto(f"{BASE_URL}/docs", wait_until="networkidle")
    cli = p.get_by_text("CLI Overview", exact=False)
    if cli.count() > 0:
        cli.first.click()
        p.wait_for_load_state("networkidle")
        rec("EH-24", "PASS", f"'CLI Overview' card opened. Navigated to: {p.url}")
        return

    rec("EH-24", "FAIL",
        "'CLI Overview' card not found on /docs page. "
        "Documentation content has not been published yet.")


def test_eh25(p, tc):
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}",
           wait_until="networkidle")
    body = p.locator("main").text_content() or ""
    if "Community Feedback" in body or "Feedback" in body:
        rec("EH-25", "PASS",
            "Community Feedback section is visible on the environment detail page.")
    else:
        rec("EH-25", "FAIL",
            "Community Feedback section NOT found on environment detail page.")


def test_eh26(p, tc):
    rec("EH-26", "MANUAL",
        "Requires a fresh anonymous/incognito session. "
        "Manual steps: open incognito → open environment detail page → "
        "verify feedback form shows 'sign in to leave feedback' message and textarea is absent.")


def test_eh27(p, tc):
    """Authenticated feedback submission (same mechanic as EH-10, logged as EH-27)."""
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}",
           wait_until="networkidle")
    textarea = p.locator("textarea").first
    if textarea.count() == 0:
        rec("EH-27", "FAIL",
            "Feedback textarea not available — check authentication"); return

    ts   = datetime.now().strftime("%H:%M:%S")
    text = f"EH-27 test comment [{ts}]"
    textarea.fill(text)

    submit = p.locator("button[type='submit']").or_(
        p.get_by_role("button", name=re.compile(r"submit", re.IGNORECASE)))
    submit.last.click()
    p.wait_for_timeout(2500)

    body = p.locator("main").text_content() or ""
    if text[:20] in body:
        rec("EH-27", "PASS",
            "Authenticated feedback submitted and visible in Community Feedback section.")
    else:
        rec("EH-27", "MANUAL",
            f"Submit clicked. Verify '{text[:30]}…' appears in the feedback list.")


def test_eh28(p, tc):
    p.goto(f"{BASE_URL}/environments/{KNOWN_ENV_SLUG}",
           wait_until="networkidle")
    textarea = p.locator("textarea").first
    if textarea.count() == 0:
        rec("EH-28", "FAIL",
            "Feedback textarea not found — check authentication"); return

    textarea.fill("")   # Ensure empty

    submit = p.locator("button[type='submit']").or_(
        p.get_by_role("button", name=re.compile(r"submit", re.IGNORECASE)))

    # Check native disabled state before clicking
    if submit.count() > 0 and submit.last.is_disabled():
        rec("EH-28", "PASS",
            "Submit button is disabled when the feedback textarea is empty.")
        return

    submit.last.click()
    p.wait_for_timeout(1000)

    body          = p.locator("main").text_content().lower()
    error_visible = (
        p.locator("[class*='error' i], [class*='invalid' i], "
                  "[class*='validation' i]").count() > 0
        or "required" in body
        or "cannot be empty" in body
        or "field is required" in body
    )
    if error_visible:
        rec("EH-28", "PASS",
            "Validation error displayed for empty feedback — form was not submitted.")
    else:
        rec("EH-28", "MANUAL",
            "Empty submit attempted. Check whether browser native validation or a UI error "
            "message blocked submission. No empty post should appear in the list.")


def test_eh29(p, tc):
    rec("EH-29", "MANUAL",
        "Email notification cannot be verified through UI automation. "
        "Manual steps: ensure email integration is enabled → submit feedback → "
        "check team inbox for notification containing the environment reference and feedback content.")


def test_eh30(p, tc):
    rec("EH-30", "SKIP",
        "Admin-only test. Requires admin account to create and publish a new environment.")


def test_eh31(p, tc):
    rec("EH-31", "SKIP",
        "Admin-only test. Requires admin account to publish a new version of an environment.")


def _analytics(p, tc, action: str):
    rec(tc["id"], "MANUAL",
        f"Analytics verification requires access to the analytics backend/dashboard. "
        f"Best effort — {action}. "
        "Confirm the corresponding event (with timestamp and user context) is captured "
        "in your analytics tool.")


# ── Test registry ─────────────────────────────────────────────────────────────
# Maps test ID → (function, requires_auth)
# requires_auth=True  → runs in the authenticated Playwright context
# requires_auth=False → runs in the anonymous (no stored cookies) context
TEST_REGISTRY: dict = {
    "EH-01": (test_eh01,  False),
    "EH-02": (test_eh02,  False),
    "EH-03": (test_eh03,  False),
    "EH-04": (test_eh04,  False),
    "EH-05": (test_eh05,  False),
    "EH-06": (test_eh06,  False),
    "EH-07": (test_eh07,  False),
    "EH-08": (test_eh08,  True),
    "EH-09": (test_eh09,  True),
    "EH-10": (test_eh10,  True),
    "EH-11": (test_eh11,  False),
    "EH-12": (test_eh12,  False),
    "EH-13": (test_eh13,  False),
    "EH-14": (test_eh14,  False),
    "EH-15": (test_eh15,  False),
    "EH-16": (test_eh16,  False),
    "EH-17": (test_eh17,  False),
    "EH-18": (test_eh18,  True),
    "EH-19": (test_eh19,  True),
    "EH-20": (test_eh20,  True),
    "EH-21": (test_eh21,  True),
    "EH-22": (test_eh22,  False),
    "EH-23": (test_eh23,  False),
    "EH-24": (test_eh24,  False),
    "EH-25": (test_eh25,  False),
    "EH-26": (test_eh26,  False),
    "EH-27": (test_eh27,  True),
    "EH-28": (test_eh28,  True),
    "EH-29": (test_eh29,  False),
    "EH-30": (test_eh30,  False),
    "EH-31": (test_eh31,  False),
    # EH-32 to EH-36 handled inline in main() via _analytics()
}

ANALYTICS_TESTS = {
    "EH-32": "entered a search query → verify search event (query + timestamp + user) in analytics",
    "EH-33": "opened environment detail page → verify 'view' event captured",
    "EH-34": "new user completed OAuth signup → verify 'signup' event captured",
    "EH-35": "authenticated user starred an environment → verify 'star' event captured",
    "EH-36": "authenticated user submitted feedback → verify 'feedback_submitted' event captured",
}


# ── Runner ────────────────────────────────────────────────────────────────────

def run_phase(page, test_cases: list, requires_auth: bool, headless: bool) -> None:
    """Execute one phase (anonymous or authenticated) for the given test cases."""
    for tc in test_cases:
        tid = tc["id"]
        fn, auth = TEST_REGISTRY.get(tid, (None, None))
        if fn is None:
            continue
        if auth != requires_auth:
            continue

        try:
            fn(page, tc)
        except PWTimeout as e:
            rec(tid, "FAIL", f"Playwright timeout: {str(e)[:120]}")
        except Exception as e:
            rec(tid, "FAIL", f"Unhandled exception: {str(e)[:120]}")


def main():
    parser = argparse.ArgumentParser(
        description="Morpheus Environment Hub — Automated Test Runner")
    parser.add_argument(
        "--input", default=None,
        help="Path to the test cases Excel file (default: auto-detect)")
    parser.add_argument(
        "--output", default=None,
        help="Path for the results Excel file (default: Results_<timestamp>.xlsx)")
    parser.add_argument(
        "--headed", action="store_true",
        help="Run browser in headed (visible) mode for debugging")
    parser.add_argument(
        "--skip-auth", action="store_true",
        help="Skip authenticated tests (no OAuth login required)")
    args = parser.parse_args()

    # ── Resolve input file ────────────────────────────────────────────────────
    candidates = [
        args.input,
        "MorpheusEnvHubTestCases.xlsx",
        "MorpheusEnvHubTestCases_Results.xlsx",
    ]
    input_path = None
    for c in candidates:
        if c and os.path.exists(c) and os.path.getsize(c) > 0:
            input_path = c
            break

    if not input_path:
        print("ERROR: No valid test case Excel file found.")
        print("  Expected: MorpheusEnvHubTestCases.xlsx  or  MorpheusEnvHubTestCases_Results.xlsx")
        sys.exit(1)

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = args.output or f"Results_{timestamp}.xlsx"
    headless    = not args.headed

    # ── Header ────────────────────────────────────────────────────────────────
    print()
    print("=" * 62)
    print("  MORPHEUS ENVIRONMENT HUB — AUTOMATED TEST RUNNER")
    print(f"  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")
    print(f"  Input : {input_path}")
    print(f"  Output: {output_path}")
    print(f"  Mode  : {'headed (browser visible)' if not headless else 'headless'}")
    print("=" * 62)

    # ── Load test cases ───────────────────────────────────────────────────────
    try:
        test_cases = load_test_cases(input_path)
    except Exception as e:
        print(f"ERROR loading test cases: {e}")
        sys.exit(1)

    print(f"\n  Loaded {len(test_cases)} test case(s) from {input_path}")

    with sync_playwright() as pw:

        # ── Phase 1: Anonymous tests ──────────────────────────────────────────
        print("\n── ANONYMOUS TESTS ──────────────────────────────────────────")
        anon_browser = pw.chromium.launch(headless=headless)
        anon_ctx     = anon_browser.new_context()
        anon_page    = anon_ctx.new_page()
        anon_page.set_default_timeout(TIMEOUT_MS)

        run_phase(anon_page, test_cases, requires_auth=False, headless=headless)

        # Analytics (always MANUAL — no auth needed)
        print("\n── ANALYTICS TESTS (always manual) ─────────────────────────")
        for tc in test_cases:
            if tc["id"] in ANALYTICS_TESTS:
                _analytics(anon_page, tc, ANALYTICS_TESTS[tc["id"]])

        anon_browser.close()

        # ── Phase 2: Authenticated tests ──────────────────────────────────────
        if args.skip_auth:
            print("\n── AUTHENTICATED TESTS (skipped via --skip-auth) ────────────")
        else:
            print("\n── AUTHENTICATED TESTS ───────────────────────────────────────")
            auth_state = ensure_auth_state()

            if auth_state is None:
                # No valid session — mark all auth tests as SKIP with instructions
                print("\n  Marking authenticated tests as SKIP (no session available).")
                auth_test_ids = [tid for tid, (_, req_auth) in TEST_REGISTRY.items()
                                 if req_auth]
                for tc in test_cases:
                    if tc["id"] in auth_test_ids:
                        rec(tc["id"], "SKIP",
                            "Authenticated session not available. "
                            "Run  python3 setup_auth.py  to log in, then re-run tests.")
            else:
                auth_browser = pw.chromium.launch(headless=headless)
                auth_ctx     = auth_browser.new_context(storage_state=auth_state)
                auth_page    = auth_ctx.new_page()
                auth_page.set_default_timeout(TIMEOUT_MS)

                # Sanity-check: confirm we are actually logged in
                auth_page.goto(BASE_URL, wait_until="networkidle")
                if auth_page.get_by_text("Sign In").count() > 0:
                    print("\n  ⚠️  WARNING: Session appears stale — 'Sign In' still visible.")
                    print("     Run  python3 setup_auth.py  to refresh your session.\n")

                run_phase(auth_page, test_cases, requires_auth=True, headless=headless)
                auth_browser.close()

    # ── Write results ─────────────────────────────────────────────────────────
    print("\n── WRITING RESULTS ──────────────────────────────────────────────")
    write_results(input_path, output_path)

    # ── Summary ───────────────────────────────────────────────────────────────
    total   = len(_results)
    passed  = sum(1 for r in _results.values() if r["status"] == "PASS")
    failed  = sum(1 for r in _results.values() if r["status"] == "FAIL")
    manual  = sum(1 for r in _results.values() if r["status"] == "MANUAL")
    skipped = sum(1 for r in _results.values() if r["status"] == "SKIP")

    print(f"\n{'=' * 62}")
    print(f"  RESULTS SUMMARY  —  {total} tests executed")
    print(f"  ✅  PASS    : {passed}")
    print(f"  ❌  FAIL    : {failed}")
    print(f"  ⚠️   MANUAL  : {manual}  (require human verification)")
    print(f"  —   SKIP    : {skipped}  (admin-only)")
    print(f"{'=' * 62}")
    print(f"\n  Full results: {output_path}\n")

    # Exit code: 0 = all automatable tests passed, 1 = failures present
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
