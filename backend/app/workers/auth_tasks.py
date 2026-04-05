"""
Celery task: run_auth_browser

Launches a headless Playwright browser pointed at the target URL,
streams JPEG screenshots to Redis pub/sub, and forwards user interactions
(clicks, keystrokes) received on a separate Redis command channel.

When the user clicks "Done", the session's storage state is saved to
auth_state.json and an auth_complete event is published.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Optional

import redis
from playwright.sync_api import sync_playwright

from app.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

VIEWPORT_W = 1280
VIEWPORT_H = 800
SCREENSHOT_INTERVAL = 0.4   # seconds between frames
SESSION_TIMEOUT     = 300   # 5 minutes max


def _event_channel(run_id: str) -> str:
    return f"run:{run_id}:auth_events"


def _cmd_channel(run_id: str) -> str:
    return f"run:{run_id}:auth_commands"


def _publish(r: redis.Redis, run_id: str, event_type: str, payload: dict) -> None:
    try:
        r.publish(_event_channel(run_id), json.dumps({"event_type": event_type, "payload": payload}))
    except Exception:
        logger.exception("Failed to publish auth event")


@celery_app.task(
    name="app.workers.auth_tasks.run_auth_browser",
    bind=True,
    max_retries=0,
)
def run_auth_browser(self, run_id: str, target_url: str, auth_state_path: str) -> dict:
    logger.info("Starting auth browser for run_id=%s target=%s", run_id, target_url)

    r = redis.from_url(settings.redis_url, decode_responses=False)
    r_text = redis.from_url(settings.redis_url, decode_responses=True)

    pubsub = r_text.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(_cmd_channel(run_id))

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
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

            # Navigate to target
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=20_000)
            except Exception as exc:
                logger.warning("Initial navigation warning: %s", exc)

            _publish(r_text, run_id, "browser_ready", {"url": page.url})

            deadline = time.time() + SESSION_TIMEOUT
            last_screenshot_time = 0.0

            while time.time() < deadline:
                # ── Process pending commands ──────────────────────────────────
                message = pubsub.get_message(timeout=0.05)
                if message:
                    try:
                        cmd = json.loads(message["data"])
                        cmd_type = cmd.get("type")

                        if cmd_type == "click":
                            page.mouse.click(float(cmd["x"]), float(cmd["y"]))
                            time.sleep(0.15)

                        elif cmd_type == "type":
                            page.keyboard.type(str(cmd["text"]))

                        elif cmd_type == "key":
                            page.keyboard.press(str(cmd["key"]))

                        elif cmd_type == "scroll":
                            page.mouse.wheel(0, float(cmd.get("delta", 100)))

                        elif cmd_type == "navigate":
                            page.goto(str(cmd["url"]), wait_until="domcontentloaded", timeout=15_000)

                        elif cmd_type == "complete":
                            # User confirmed login — save session and finish
                            ctx.storage_state(path=auth_state_path)
                            logger.info("Auth state saved to %s", auth_state_path)
                            _publish(r_text, run_id, "auth_complete",
                                     {"auth_state_path": auth_state_path})
                            browser.close()
                            pubsub.unsubscribe()
                            return {"status": "completed", "run_id": run_id}

                    except Exception as exc:
                        logger.warning("Command processing error: %s", exc)

                # ── Stream screenshot ─────────────────────────────────────────
                now = time.time()
                if now - last_screenshot_time >= SCREENSHOT_INTERVAL:
                    try:
                        # Wait for any pending navigation
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=500)
                        except Exception:
                            pass

                        shot = page.screenshot(type="jpeg", quality=65, full_page=False)
                        b64  = base64.b64encode(shot).decode()
                        _publish(r_text, run_id, "screenshot", {
                            "image": b64,
                            "url":   page.url,
                        })
                        last_screenshot_time = now
                    except Exception as exc:
                        logger.warning("Screenshot error: %s", exc)

            # Timeout — clean up without saving
            _publish(r_text, run_id, "auth_timeout", {"message": "Session timed out after 5 minutes."})
            try:
                browser.close()
            except Exception:
                pass

    except Exception as exc:
        logger.exception("Auth browser task failed for run %s", run_id)
        try:
            _publish(r_text, run_id, "auth_error", {"message": str(exc)[:200]})
        except Exception:
            pass
        raise
    finally:
        try:
            pubsub.unsubscribe()
        except Exception:
            pass

    return {"status": "timeout", "run_id": run_id}
