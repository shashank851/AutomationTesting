"""
Celery task: run_auth_browser

Launches a headful (visible) Playwright browser inside a virtual X display,
exposes it via VNC + noVNC so the user can interact through a normal browser
tab, and saves the session storage state when the user signals "Done".

Infrastructure started per auth session:
  Xvfb :99        — virtual X display
  x11vnc          — VNC server on that display (localhost:5900)
  websockify      — WebSocket bridge + noVNC web files on 0.0.0.0:6080
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
from typing import Optional

import redis
from playwright.sync_api import sync_playwright

from app.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

DISPLAY_NUM   = 99          # Xvfb display number  (:99)
VNC_PORT      = 5900        # x11vnc listens here
NOVNC_PORT    = 6080        # websockify + noVNC web server
VIEWPORT_W    = 1280
VIEWPORT_H    = 900
SESSION_TIMEOUT = 600       # 10 minutes


def _event_channel(run_id: str) -> str:
    return f"run:{run_id}:auth_events"


def _cmd_channel(run_id: str) -> str:
    return f"run:{run_id}:auth_commands"


def _publish(r: redis.Redis, run_id: str, event_type: str, payload: dict) -> None:
    try:
        r.publish(_event_channel(run_id), json.dumps({"event_type": event_type, "payload": payload}))
    except Exception:
        logger.exception("Failed to publish auth event")


def _kill_vnc_stack() -> None:
    """Kill any leftover Xvfb / x11vnc / websockify processes."""
    for name in ("Xvfb", "x11vnc", "websockify"):
        try:
            subprocess.run(["pkill", "-f", name], check=False, capture_output=True)
        except Exception:
            pass
    time.sleep(0.5)


def _start_vnc_stack() -> list[subprocess.Popen]:
    """Start Xvfb → x11vnc → websockify and return the process list."""
    procs: list[subprocess.Popen] = []

    # ── 1. Virtual display ────────────────────────────────────────────────────
    xvfb = subprocess.Popen(
        ["Xvfb", f":{DISPLAY_NUM}", "-screen", "0",
         f"{VIEWPORT_W}x{VIEWPORT_H}x24", "-ac"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(xvfb)
    time.sleep(1.5)   # Xvfb needs a moment before accepting connections

    # ── 2. VNC server ─────────────────────────────────────────────────────────
    x11vnc = subprocess.Popen(
        [
            "x11vnc",
            "-display", f":{DISPLAY_NUM}",
            "-nopw",                # no password (local-only)
            "-listen", "localhost", # only accessible from websockify
            "-forever",             # don't exit after first client disconnect
            "-shared",
            "-quiet",
            "-noncache",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(x11vnc)
    time.sleep(0.8)

    # ── 3. WebSocket bridge + noVNC web files ─────────────────────────────────
    novnc_web = "/usr/share/novnc"
    websockify = subprocess.Popen(
        [
            "websockify",
            "--web", novnc_web,
            str(NOVNC_PORT),
            f"localhost:{VNC_PORT}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    procs.append(websockify)
    time.sleep(0.5)

    return procs


@celery_app.task(
    name="app.workers.auth_tasks.run_auth_browser",
    bind=True,
    max_retries=0,
)
def run_auth_browser(self, run_id: str, target_url: str, auth_state_path: str) -> dict:
    logger.info("Starting auth browser for run_id=%s target=%s", run_id, target_url)

    r = redis.from_url(settings.redis_url, decode_responses=True)
    pubsub = r.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(_cmd_channel(run_id))

    procs: list[subprocess.Popen] = []

    try:
        # Kill any stale VNC stack from a previous session
        _kill_vnc_stack()

        # Start Xvfb + x11vnc + websockify
        procs = _start_vnc_stack()

        # Browser env pointing at the virtual display
        env = os.environ.copy()
        env["DISPLAY"] = f":{DISPLAY_NUM}"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=False,
                env=env,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    f"--window-size={VIEWPORT_W},{VIEWPORT_H}",
                    "--start-maximized",
                ],
            )
            ctx = browser.new_context(
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
            except Exception as exc:
                logger.warning("Initial navigation warning: %s", exc)

            _publish(r, run_id, "browser_ready", {
                "url":       page.url,
                "vnc_port":  NOVNC_PORT,
            })

            # ── Wait for "complete" command ───────────────────────────────────
            deadline = time.time() + SESSION_TIMEOUT

            while time.time() < deadline:
                message = pubsub.get_message(timeout=0.5)
                if message:
                    try:
                        cmd = json.loads(message["data"])
                        if cmd.get("type") == "complete":
                            os.makedirs(os.path.dirname(auth_state_path), exist_ok=True)
                            ctx.storage_state(path=auth_state_path)
                            logger.info("Auth state saved to %s", auth_state_path)
                            _publish(r, run_id, "auth_complete",
                                     {"auth_state_path": auth_state_path})
                            browser.close()
                            pubsub.unsubscribe()
                            return {"status": "completed", "run_id": run_id}
                    except Exception as exc:
                        logger.warning("Command processing error: %s", exc)

            # Timeout
            _publish(r, run_id, "auth_timeout",
                     {"message": "Session timed out after 10 minutes."})
            try:
                browser.close()
            except Exception:
                pass

    except Exception as exc:
        logger.exception("Auth browser task failed for run %s", run_id)
        try:
            _publish(r, run_id, "auth_error", {"message": str(exc)[:200]})
        except Exception:
            pass
        raise

    finally:
        try:
            pubsub.unsubscribe()
        except Exception:
            pass
        # Tear down VNC stack
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        _kill_vnc_stack()

    return {"status": "timeout", "run_id": run_id}
