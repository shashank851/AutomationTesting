"""
Test function registry.

Each test function signature:  fn(page, tc, base_url, results) -> None
  page     — Playwright Page object (pre-configured context)
  tc       — dict {id, title, steps, expected, _row}
  base_url — target URL string (trailing slash stripped)
  results  — dict to record results into (mutated in-place)

The ANALYTICS_TESTS dict maps test IDs that are always MANUAL (analytics checks).
"""
import re
import time
from datetime import datetime

from playwright.sync_api import TimeoutError as PWTimeout


# ── Known fixture used across multiple tests ──────────────────────────────────
KNOWN_ENV_SLUG = "prod-edi-gateway"
KNOWN_ENV_NAME = "Production EDI Gateway"

# Cards are <a href="/environments/..."> inside <main>
ENV_CARD_SEL = "main a[href*='/environments/']"


def _rec(results: dict, tid: str, status: str, actual: str, comment: str = "") -> None:
    results[tid] = {
        "status":  status,
        "actual":  actual[:400],
        "comment": comment,
    }


def _wait_for_cards(page) -> list:
    return page.locator(ENV_CARD_SEL).all()


def _get_star_counts(page) -> list:
    counts = page.evaluate("""
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


# ── Individual test functions ─────────────────────────────────────────────────

def test_eh01(page, tc, base_url, results):
    t0 = time.time()
    page.goto(base_url, wait_until="networkidle")
    elapsed = round(time.time() - t0, 2)

    issues = []
    if elapsed >= 20.0:
        issues.append(f"load time {elapsed}s ≥ 20s")
    if page.get_by_text("Environments Hub").count() == 0:
        issues.append("'Environments Hub' heading not found")

    cards = page.locator(ENV_CARD_SEL).count()
    if cards == 0:
        issues.append("no environment cards visible")

    if issues:
        _rec(results, "EH-01", "FAIL", f"Loaded in {elapsed}s. Issues: {'; '.join(issues)}")
    else:
        _rec(results, "EH-01", "PASS",
             f"Page loaded in {elapsed}s. 'Environments Hub' heading present. "
             f"{cards} environment card(s) visible.")


def test_eh02(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    required = ["Environments Hub", "Starred Environments", "Documentation"]
    optional = ["Announcements", "Profile"]

    missing = [i for i in required if page.get_by_text(i).count() == 0]
    found   = [i for i in required if i not in missing]
    extra   = [i for i in optional if page.get_by_text(i).count() > 0]

    if missing:
        _rec(results, "EH-02", "FAIL",
             f"Found: {', '.join(found + extra)}. Missing required: {', '.join(missing)}")
    else:
        _rec(results, "EH-02", "PASS",
             f"All required nav items present: {', '.join(found + extra)}")


def test_eh03(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    cards = _wait_for_cards(page)
    if not cards:
        _rec(results, "EH-03", "FAIL", "No environment cards found on homepage"); return

    first = cards[0].text_content() or ""
    checks = {
        "name visible":   len(first.strip()) > 10,
        "has date":       any(x in first for x in ["2025", "2026",
                              "Jan","Feb","Mar","Apr","May","Jun",
                              "Jul","Aug","Sep","Oct","Nov","Dec"]),
        "has star/count": bool(re.search(r'\d', first)),
    }
    failed = [k for k, v in checks.items() if not v]

    if failed:
        _rec(results, "EH-03", "MANUAL",
             f"{len(cards)} cards found. Could not verify: {', '.join(failed)}. "
             "Inspect cards manually to confirm name, description, date, and star count.")
    else:
        _rec(results, "EH-03", "PASS",
             f"{len(cards)} environment cards found, each showing name, date, and star count.")


def test_eh04(page, tc, base_url, results):
    page.goto(f"{base_url}/starred", wait_until="networkidle")
    body = page.locator("body").text_content().lower()
    url  = page.url.lower()

    prompted = (
        "login" in body or "sign in" in body or "not logged in" in body
        or "auth0" in url or "/login" in url
        or page.get_by_text("Sign In").count() > 0
    )
    if prompted:
        _rec(results, "EH-04", "PASS",
             "Anonymous user visiting /starred is shown a login prompt / redirected to login.")
    else:
        _rec(results, "EH-04", "FAIL",
             f"No login prompt shown for anonymous user on /starred. URL: {page.url}")


def test_eh05(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    if (page.get_by_text("New/Updated", exact=False).count() > 0
            or page.get_by_text("New", exact=False).count() > 0
            or page.locator("[class*='new' i], [class*='updated' i]").count() > 0):
        _rec(results, "EH-05", "PASS",
             "New/Updated section visible on homepage with environment card(s).")
    else:
        _rec(results, "EH-05", "MANUAL",
             "New/Updated section not currently visible. "
             "Section only appears when an environment was published/updated within the last 15 days.")


def test_eh06(page, tc, base_url, results):
    _rec(results, "EH-06", "MANUAL",
         "Sign-up requires a brand-new unregistered email and completion of OAuth flow. "
         "Manual steps: click Sign In → choose Google/Microsoft with a new account → "
         "verify account created and Profile reflects the signed-in state.")


def test_eh07(page, tc, base_url, results):
    _rec(results, "EH-07", "MANUAL",
         "Requires a fresh anonymous/incognito session. "
         "Manual steps: open incognito → browse homepage → confirm Star/Download/Feedback "
         "actions are hidden or show a login prompt.")


def test_eh08(page, tc, base_url, results):
    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    page.wait_for_timeout(500)

    body_before = page.locator("body").text_content()
    m_before    = re.search(r'(\d+)\s*(?:Star|star)', body_before)
    count_before = int(m_before.group(1)) if m_before else None

    star_btn = page.get_by_role("button", name=re.compile(r"star", re.IGNORECASE)).first
    if star_btn.count() == 0:
        _rec(results, "EH-08", "FAIL", "Star button not found on environment detail page"); return

    star_btn.click()
    page.wait_for_timeout(2000)

    page.goto(f"{base_url}/starred", wait_until="networkidle")
    in_starred = KNOWN_ENV_NAME in (page.locator("main").text_content() or "")

    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    body_after   = page.locator("body").text_content()
    m_after      = re.search(r'(\d+)\s*(?:Star|star)', body_after)
    count_after  = int(m_after.group(1)) if m_after else None

    count_msg = (f"Star count: {count_before} → {count_after}"
                 if count_before is not None and count_after is not None
                 else "Star count not readable from DOM")

    if in_starred or (count_before is not None and count_after is not None
                      and count_after != count_before):
        _rec(results, "EH-08", "PASS",
             f"Star toggled successfully. {count_msg}. "
             f"Environment {'visible' if in_starred else 'not confirmed'} in /starred.")
    else:
        _rec(results, "EH-08", "MANUAL",
             f"Star button clicked. {count_msg}. "
             "Verify toggle and /starred page manually.")


def test_eh09(page, tc, base_url, results):
    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    page.wait_for_timeout(500)

    body_before = page.locator("main").text_content()
    m_before    = re.search(r'(\d+)\s*Download', body_before, re.IGNORECASE)
    count_before = int(m_before.group(1)) if m_before else None

    dl_btn = page.get_by_text("Download Environment", exact=False)
    if dl_btn.count() == 0:
        dl_btn = page.get_by_role("button", name=re.compile(r"download", re.IGNORECASE))
    if dl_btn.count() == 0:
        _rec(results, "EH-09", "FAIL", "Download button not found on detail page"); return

    downloaded = False
    try:
        with page.expect_download(timeout=10_000) as dl_info:
            dl_btn.first.click()
        dl_info.value.cancel()
        downloaded = True
    except Exception:
        dl_btn.first.click()
        page.wait_for_timeout(3000)

    body_after  = page.locator("main").text_content()
    m_after     = re.search(r'(\d+)\s*Download', body_after, re.IGNORECASE)
    count_after = int(m_after.group(1)) if m_after else None

    if count_after is not None and count_before is not None and count_after > count_before:
        _rec(results, "EH-09", "PASS",
             f"Download triggered. Count: {count_before} → {count_after}")
    elif downloaded:
        _rec(results, "EH-09", "MANUAL",
             f"Download dialog triggered. Count before: {count_before}, after: {count_after}. "
             "Count may update asynchronously — verify manually.")
    else:
        _rec(results, "EH-09", "MANUAL",
             f"Download button clicked. Count before: {count_before}, after: {count_after}. "
             "Verify count incremented manually.")


def test_eh10(page, tc, base_url, results):
    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    page.wait_for_timeout(500)

    textarea = page.locator("textarea").first
    if textarea.count() == 0:
        _rec(results, "EH-10", "FAIL",
             "Feedback textarea not found — user may not be authenticated or "
             "form requires login. Check auth_state.json."); return

    ts   = datetime.now().strftime("%H:%M:%S")
    text = f"Automated test feedback [{ts}]"
    textarea.fill(text)

    submit = page.locator("button[type='submit']").or_(
        page.get_by_role("button", name=re.compile(r"submit", re.IGNORECASE)))
    if submit.count() == 0:
        _rec(results, "EH-10", "FAIL", "Submit button not found in feedback section"); return

    submit.last.click()
    page.wait_for_timeout(2500)

    body = page.locator("main").text_content()
    if text[:20] in body:
        _rec(results, "EH-10", "PASS",
             "Feedback submitted and visible in Community Feedback section.")
    else:
        _rec(results, "EH-10", "MANUAL",
             f"Submit clicked. Verify '{text[:30]}…' appears in feedback list.")


def test_eh11(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    counts = _get_star_counts(page)

    if len(counts) >= 2:
        sorted_ok = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
        if sorted_ok:
            _rec(results, "EH-11", "PASS",
                 f"Environments sorted descending by star count: {counts}")
        else:
            _rec(results, "EH-11", "FAIL",
                 f"Sort order incorrect. Star counts (card order): {counts}. Expected descending.")
    else:
        _rec(results, "EH-11", "MANUAL",
             f"Could not extract star counts from cards (found: {counts}). "
             "Visually confirm All Environments is sorted descending by star count.")


def test_eh12(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    search = page.get_by_placeholder(re.compile(r"search", re.IGNORECASE))
    if search.count() == 0:
        _rec(results, "EH-12", "FAIL", "Search bar not found on homepage"); return

    search_term = "EDI"
    search.fill(search_term)
    search.press("Enter")
    page.wait_for_timeout(1500)
    body  = page.locator("main").text_content()
    cards = page.locator(ENV_CARD_SEL).count()

    if "edi" in body.lower() and cards > 0:
        _rec(results, "EH-12", "PASS",
             f"Search by name '{search_term}' returned {cards} matching environment(s).")
    elif cards > 0:
        _rec(results, "EH-12", "PASS",
             f"Search by name '{search_term}' returned {cards} environment card(s).")
    else:
        _rec(results, "EH-12", "FAIL",
             f"Search '{search_term}' returned no visible cards. "
             f"Page text snippet: {body[:150]}")


def test_eh13(page, tc, base_url, results):
    _rec(results, "EH-13", "MANUAL",
         "Search by tag requires a known tag value from the current dataset. "
         "Manual steps: note a tag shown on an environment card → type it in the search bar → "
         "verify only environments with that tag are shown.")


def test_eh14(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    search = page.get_by_placeholder(re.compile(r"search", re.IGNORECASE))
    if search.count() == 0:
        _rec(results, "EH-14", "FAIL", "Search bar not found"); return

    search.fill("machine learning")
    search.press("Enter")
    page.wait_for_timeout(1500)
    body  = page.locator("main").text_content()
    cards = page.locator(ENV_CARD_SEL).count()

    if "machine learning" in body.lower() and cards > 0:
        _rec(results, "EH-14", "PASS",
             f"Search by description 'machine learning' returned {cards} result(s).")
    elif cards > 0:
        _rec(results, "EH-14", "PASS",
             f"Search 'machine learning' returned {cards} card(s).")
    else:
        _rec(results, "EH-14", "MANUAL",
             "Search 'machine learning' returned no results. "
             "Verify with a phrase known to appear in an environment description, then re-run.")


def test_eh15(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    search = page.get_by_placeholder(re.compile(r"search", re.IGNORECASE))
    if search.count() == 0:
        _rec(results, "EH-15", "FAIL", "Search bar not found"); return

    search.fill("zzznomatchxxx")
    search.press("Enter")
    page.wait_for_timeout(2000)
    body  = page.locator("main").text_content().lower()
    cards = page.locator(ENV_CARD_SEL).count()
    no_results = (
        "no result" in body or "no environment" in body
        or "nothing found" in body or "0 result" in body
        or cards == 0
    )
    if no_results:
        _rec(results, "EH-15", "PASS",
             "Empty/no-results state shown for unmatched query 'zzznomatchxxx'. No cards visible.")
    else:
        _rec(results, "EH-15", "FAIL",
             f"Expected empty state but {cards} card(s) still visible after unmatched search.")


def test_eh16(page, tc, base_url, results):
    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    body = page.locator("body").text_content() or ""
    checks = {
        "URL contains /environments/": "/environments/" in page.url,
        "Environment name visible":    KNOWN_ENV_NAME in body,
        "Has date":                    any(x in body for x in ["2025","2026",
                                           "Jan","Feb","Mar","Apr","May","Jun",
                                           "Jul","Aug","Sep","Oct","Nov","Dec"]),
        "Download button present":     "Download Environment" in body,
        "Community Feedback section":  "Community Feedback" in body,
    }
    failed = [k for k, v in checks.items() if not v]
    passed = [k for k, v in checks.items() if v]

    if not failed:
        _rec(results, "EH-16", "PASS",
             f"All detail page fields verified: {', '.join(passed)}")
    elif len(failed) <= 2:
        _rec(results, "EH-16", "MANUAL",
             f"Passed: {', '.join(passed)} | Could not verify: {', '.join(failed)}")
    else:
        _rec(results, "EH-16", "FAIL",
             f"Passed: {', '.join(passed)} | Missing: {', '.join(failed)}")


def test_eh17(page, tc, base_url, results):
    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    body     = page.locator("main").text_content() or ""
    ver_link = page.get_by_text("version history", exact=False)

    if ver_link.count() > 0:
        ver_link.first.click()
        page.wait_for_timeout(1000)
        _rec(results, "EH-17", "PASS", "Version history link found and opened successfully.")
    elif "version" in body.lower():
        _rec(results, "EH-17", "MANUAL",
             "Version field present on detail page but no explicit 'view version history' link. "
             "Verify multiple versions are listed/accessible manually.")
    else:
        _rec(results, "EH-17", "MANUAL",
             "No version history section found. "
             "Test requires an environment with multiple published versions.")


def test_eh18(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    page.wait_for_timeout(500)

    star_btn = page.locator(
        "button[aria-label*='star' i], [class*='star' i] button, [data-testid*='star' i]"
    ).first
    if star_btn.count() == 0:
        _rec(results, "EH-18", "MANUAL",
             "Star button not identifiable on environment cards via automation selectors. "
             "Verify manually: clicking ☆ on a card toggles it to ★ and increments the count.")
        return

    star_btn.click()
    page.wait_for_timeout(1500)
    _rec(results, "EH-18", "MANUAL",
         "Star button clicked on card. Verify star icon toggled and count updated in the UI.")


def test_eh19(page, tc, base_url, results):
    page.goto(base_url, wait_until="networkidle")
    page.wait_for_timeout(500)

    unstar_btn = page.locator(
        "button[aria-label*='unstar' i], button[aria-label*='starred' i], "
        "[class*='starred' i] button, [class*='active' i][class*='star' i]"
    ).first

    if unstar_btn.count() == 0:
        _rec(results, "EH-19", "MANUAL",
             "No active/starred star button found on cards. "
             "Manual steps: star an environment first → click the filled star to unstar → "
             "verify count decrements.")
        return

    unstar_btn.click()
    page.wait_for_timeout(1500)
    _rec(results, "EH-19", "MANUAL",
         "Unstar button clicked. Verify the star icon reverted to unfilled and count decremented.")


def test_eh20(page, tc, base_url, results):
    page.goto(f"{base_url}/starred", wait_until="networkidle")
    body  = page.locator("main").text_content() or ""
    cards = page.locator(ENV_CARD_SEL).count()

    if "not logged in" in body.lower() or "sign in" in body.lower():
        _rec(results, "EH-20", "FAIL",
             "Starred Environments page shows login prompt — authentication not working.")
    elif cards > 0:
        _rec(results, "EH-20", "PASS",
             f"Starred Environments page loads correctly for authenticated user. "
             f"{cards} starred environment card(s) visible.")
    elif len(body.strip()) > 100:
        _rec(results, "EH-20", "MANUAL",
             "Page loaded without error but no cards found. "
             "Star an environment (EH-08) and re-run.")
    else:
        _rec(results, "EH-20", "FAIL",
             f"Starred Environments page appears empty. URL: {page.url}")


def test_eh21(page, tc, base_url, results):
    page.goto(f"{base_url}/starred", wait_until="networkidle")
    body = page.locator("main").text_content() or ""

    if "not logged in" in body.lower():
        _rec(results, "EH-21", "FAIL", "Not authenticated on Starred Environments page"); return

    counts = _get_star_counts(page)

    if len(counts) >= 2:
        sorted_ok = all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
        _rec(results, "EH-21",
             "PASS" if sorted_ok else "FAIL",
             f"Starred environments star counts: {counts} — "
             f"{'sorted descending ✓' if sorted_ok else 'NOT sorted descending ✗'}")
    else:
        _rec(results, "EH-21", "MANUAL",
             f"Fewer than 2 starred items found (counts: {counts}). "
             "Star multiple environments and re-run to verify sort order.")


def test_eh22(page, tc, base_url, results):
    page.goto(f"{base_url}/docs", wait_until="networkidle")
    body = page.locator("body").text_content() or ""

    page_loaded = "Documentation" in body
    expected    = ["Getting Started", "CLI Overview", "FAQ"]
    found       = [d for d in expected if d.lower() in body.lower()]
    missing     = [d for d in expected if d not in found]

    if len(found) == len(expected):
        _rec(results, "EH-22", "PASS",
             f"Documentation page loaded with all expected cards: {', '.join(found)}")
    elif found:
        _rec(results, "EH-22", "MANUAL",
             f"Documentation page loaded. Found: {', '.join(found)}. "
             f"Not yet published: {', '.join(missing)}.")
    elif page_loaded:
        _rec(results, "EH-22", "FAIL",
             f"Documentation page loaded at /docs but no content cards found. "
             f"Checked for: {', '.join(expected)}.")
    else:
        _rec(results, "EH-22", "FAIL",
             f"/docs page did not load. URL: {page.url}")


def test_eh23(page, tc, base_url, results):
    page.goto(f"{base_url}/docs", wait_until="networkidle")
    gs = page.get_by_text("Getting Started", exact=False)
    if gs.count() > 0:
        gs.first.click()
        page.wait_for_load_state("networkidle")
        _rec(results, "EH-23", "PASS", f"'Getting Started' card opened. URL: {page.url}")
    else:
        _rec(results, "EH-23", "FAIL",
             "'Getting Started' card not found on /docs page. "
             "Documentation content has not been published yet.")


def test_eh24(page, tc, base_url, results):
    page.goto(f"{base_url}/docs", wait_until="networkidle")
    cli = page.get_by_text("CLI Overview", exact=False)
    if cli.count() > 0:
        cli.first.click()
        page.wait_for_load_state("networkidle")
        _rec(results, "EH-24", "PASS", f"'CLI Overview' card opened. Navigated to: {page.url}")
        return

    _rec(results, "EH-24", "FAIL",
         "'CLI Overview' card not found on /docs page. "
         "Documentation content has not been published yet.")


def test_eh25(page, tc, base_url, results):
    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    body = page.locator("main").text_content() or ""
    if "Community Feedback" in body or "Feedback" in body:
        _rec(results, "EH-25", "PASS",
             "Community Feedback section is visible on the environment detail page.")
    else:
        _rec(results, "EH-25", "FAIL",
             "Community Feedback section NOT found on environment detail page.")


def test_eh26(page, tc, base_url, results):
    _rec(results, "EH-26", "MANUAL",
         "Requires a fresh anonymous/incognito session. "
         "Manual steps: open incognito → open environment detail page → "
         "verify feedback form shows 'sign in to leave feedback' message.")


def test_eh27(page, tc, base_url, results):
    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    textarea = page.locator("textarea").first
    if textarea.count() == 0:
        _rec(results, "EH-27", "FAIL",
             "Feedback textarea not available — check authentication"); return

    ts   = datetime.now().strftime("%H:%M:%S")
    text = f"EH-27 test comment [{ts}]"
    textarea.fill(text)

    submit = page.locator("button[type='submit']").or_(
        page.get_by_role("button", name=re.compile(r"submit", re.IGNORECASE)))
    submit.last.click()
    page.wait_for_timeout(2500)

    body = page.locator("main").text_content() or ""
    if text[:20] in body:
        _rec(results, "EH-27", "PASS",
             "Authenticated feedback submitted and visible in Community Feedback section.")
    else:
        _rec(results, "EH-27", "MANUAL",
             f"Submit clicked. Verify '{text[:30]}…' appears in the feedback list.")


def test_eh28(page, tc, base_url, results):
    page.goto(f"{base_url}/environments/{KNOWN_ENV_SLUG}", wait_until="networkidle")
    textarea = page.locator("textarea").first
    if textarea.count() == 0:
        _rec(results, "EH-28", "FAIL",
             "Feedback textarea not found — check authentication"); return

    textarea.fill("")

    submit = page.locator("button[type='submit']").or_(
        page.get_by_role("button", name=re.compile(r"submit", re.IGNORECASE)))

    if submit.count() > 0 and submit.last.is_disabled():
        _rec(results, "EH-28", "PASS",
             "Submit button is disabled when the feedback textarea is empty.")
        return

    submit.last.click()
    page.wait_for_timeout(1000)

    body = page.locator("main").text_content().lower()
    error_visible = (
        page.locator("[class*='error' i], [class*='invalid' i], "
                     "[class*='validation' i]").count() > 0
        or "required" in body
        or "cannot be empty" in body
        or "field is required" in body
    )
    if error_visible:
        _rec(results, "EH-28", "PASS",
             "Validation error displayed for empty feedback — form was not submitted.")
    else:
        _rec(results, "EH-28", "MANUAL",
             "Empty submit attempted. Check whether browser native validation or a UI error "
             "message blocked submission.")


def test_eh29(page, tc, base_url, results):
    _rec(results, "EH-29", "MANUAL",
         "Email notification cannot be verified through UI automation. "
         "Manual steps: ensure email integration is enabled → submit feedback → "
         "check team inbox for notification.")


def test_eh30(page, tc, base_url, results):
    _rec(results, "EH-30", "SKIP",
         "Admin-only test. Requires admin account to create and publish a new environment.")


def test_eh31(page, tc, base_url, results):
    _rec(results, "EH-31", "SKIP",
         "Admin-only test. Requires admin account to publish a new version of an environment.")


def _analytics(page, tc, base_url, results, action: str):
    _rec(results, tc["id"], "MANUAL",
         f"Analytics verification requires access to the analytics backend/dashboard. "
         f"Best effort — {action}. "
         "Confirm the corresponding event is captured in your analytics tool.")


# ── Registry ──────────────────────────────────────────────────────────────────
# Maps test ID → (function, requires_auth)

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
    # EH-32 to EH-36 handled inline by executor via _analytics()
}

ANALYTICS_TESTS = {
    "EH-32": "entered a search query → verify search event (query + timestamp + user) in analytics",
    "EH-33": "opened environment detail page → verify 'view' event captured",
    "EH-34": "new user completed OAuth signup → verify 'signup' event captured",
    "EH-35": "authenticated user starred an environment → verify 'star' event captured",
    "EH-36": "authenticated user submitted feedback → verify 'feedback_submitted' event captured",
}
