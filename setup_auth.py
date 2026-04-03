#!/usr/bin/env python3
"""
Morpheus Environment Hub — One-Time Authentication Setup
=========================================================
Uses Playwright's built-in 'open' command to capture a real browser session.
This is the standard industry approach for automating OAuth / SSO flows.

Usage:
    python3 setup_auth.py

Steps:
    1. A visible Chromium browser opens on the Morpheus Hub.
    2. Click Sign In and complete the Google / Microsoft login.
    3. Once you are back on the Hub homepage — close the browser tab/window.
    4. auth_state.json is saved automatically when the browser closes.
    5. Run:  python3 run_tests.py

To refresh an expired session, run this script again.
"""

import subprocess
import sys
import os

AUTH_STATE_FILE = "auth_state.json"
BASE_URL        = "https://hub.morpheus.skyfall.ai"


def main():
    print()
    print("=" * 62)
    print("  MORPHEUS HUB — AUTHENTICATION SETUP")
    print("=" * 62)
    print()
    print("  A Chromium browser will open now.")
    print()
    print("  1. Click 'Sign In' in the browser.")
    print("  2. Complete the Google / Microsoft OAuth login.")
    print("  3. Wait until you are back on the Hub homepage.")
    print("  4. Close the browser window (Cmd+W or close the tab).")
    print()
    print("  Your session will be saved automatically on close.")
    print("=" * 62)
    print()

    # Use Playwright's own CLI: opens a real browser, saves storage on close.
    # This is the recommended approach for OAuth/SSO in Playwright docs.
    result = subprocess.run(
        [
            sys.executable, "-m", "playwright", "open",
            "--save-storage", AUTH_STATE_FILE,
            BASE_URL,
        ],
        # Inherit stdio so the browser can open in the current display session
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    if result.returncode != 0:
        print(f"\n  ❌  playwright open exited with code {result.returncode}.")
        print("  Make sure Chromium is installed:")
        print("    python3 -m playwright install chromium")
        sys.exit(1)

    # Verify the saved file looks valid
    if not os.path.exists(AUTH_STATE_FILE):
        print(f"\n  ❌  {AUTH_STATE_FILE} was not created.")
        sys.exit(1)

    size = os.path.getsize(AUTH_STATE_FILE)
    if size < 500:
        print(f"\n  ⚠️  {AUTH_STATE_FILE} is only {size} bytes — login may not have completed.")
        print("  Run this script again and make sure you are fully logged in")
        print("  before closing the browser.")
        sys.exit(1)

    print()
    print(f"  ✅  Session saved → {AUTH_STATE_FILE}  ({size:,} bytes)")
    print()
    print("  Run the full test suite:")
    print("    python3 run_tests.py")
    print()


if __name__ == "__main__":
    main()
