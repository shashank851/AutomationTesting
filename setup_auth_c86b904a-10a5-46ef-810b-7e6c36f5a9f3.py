#!/usr/bin/env python3
"""
Auth setup script — generated for run c86b904a-10a5-46ef-810b-7e6c36f5a9f3
Target: https://hub.morpheus.skyfall.ai

Steps:
  1. pip install playwright
  2. playwright install chromium
  3. python setup_auth_c86b904a-10a5-46ef-810b-7e6c36f5a9f3.py
  4. Log in when the browser opens, then close the browser window.
  5. Upload the saved auth_state.json back to the platform.
"""
import os, subprocess, sys

TARGET_URL = "https://hub.morpheus.skyfall.ai"
AUTH_STATE_FILE = "auth_state.json"

print("\n" + "=" * 60)
print("  UI TEST PLATFORM — Authentication Setup")
print(f"  Target: {TARGET_URL}")
print("=" * 60)
print()
print("  A browser window will open. Log in, then close it.")
print()

result = subprocess.run([
    sys.executable, "-m", "playwright", "open",
    "--save-storage", AUTH_STATE_FILE,
    TARGET_URL
])

if result.returncode != 0 or not os.path.exists(AUTH_STATE_FILE):
    print("\n  ERROR: Browser closed without saving a session.")
    sys.exit(1)

size = os.path.getsize(AUTH_STATE_FILE)
if size < 500:
    print(f"\n  ERROR: auth_state.json is too small ({size} bytes). Login may not have completed.")
    sys.exit(1)

print(f"\n  SUCCESS: auth_state.json saved ({size:,} bytes)")
print("\n  Next step: upload auth_state.json to the platform.")
