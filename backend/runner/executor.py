"""
TestRunExecutor — runs all test cases for a single run in two browser phases.

Usage (from a Celery worker or CLI):

    executor = TestRunExecutor(
        run_id=run_id,
        target_url="https://example.com",
        input_file_path="/app/uploads/<id>/file.xlsx",
        output_file_path="/app/results/<id>/file_results.xlsx",
        auth_state_path=None,           # or path to auth_state.json
        timeout_ms=25_000,
        progress_callback=my_fn,        # optional: fn(event_type, payload)
    )
    executor.run()
    results = executor.results         # dict: {test_id: {status, actual, comment}}
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional, Any

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from runner.excel_io import load_test_cases, write_results
from runner.test_registry import TEST_REGISTRY, ANALYTICS_TESTS, _analytics

logger = logging.getLogger(__name__)


class TestRunExecutor:
    def __init__(
        self,
        *,
        run_id: str,
        target_url: str,
        input_file_path: str,
        output_file_path: str,
        auth_state_path: Optional[str] = None,
        timeout_ms: int = 25_000,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.run_id = run_id
        self.target_url = target_url.rstrip("/")
        self.input_file_path = input_file_path
        self.output_file_path = output_file_path
        self.auth_state_path = auth_state_path
        self.timeout_ms = timeout_ms
        self.progress_callback = progress_callback or (lambda _e, _p: None)

        # Results accumulated during run: {test_id: {status, actual, comment}}
        self.results: Dict[str, Dict[str, str]] = {}

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Execute all test phases, write results to output_file_path."""
        test_cases = load_test_cases(self.input_file_path)
        self._emit("status_change", {"status": "running", "total_tests": len(test_cases)})

        with sync_playwright() as pw:
            self._run_anonymous_phase(pw, test_cases)
            self._run_analytics_phase(pw, test_cases)
            self._run_authenticated_phase(pw, test_cases)

        write_results(self.input_file_path, self.output_file_path, self.results)

    # ── Private ───────────────────────────────────────────────────────────────

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            self.progress_callback(event_type, payload)
        except Exception:
            logger.exception("progress_callback raised")

    def _record(self, tid: str, status: str, actual: str, comment: str = "") -> None:
        self.results[tid] = {
            "status":  status,
            "actual":  actual[:400],
            "comment": comment,
        }
        self._emit("test_completed", {
            "test_case_id": tid,
            "status":       status,
            "actual":       actual[:400],
            "comment":      comment,
        })

    def _run_phase(self, page, test_cases: list, requires_auth: bool) -> None:
        for tc in test_cases:
            tid = tc["id"]
            entry = TEST_REGISTRY.get(tid)
            if entry is None:
                continue
            fn, auth = entry
            if auth != requires_auth:
                continue

            try:
                fn(page, tc, self.target_url, self.results)
                # Emit event for the result just written into self.results
                if tid in self.results:
                    r = self.results[tid]
                    self._emit("test_completed", {
                        "test_case_id": tid,
                        "status":       r["status"],
                        "actual":       r["actual"],
                        "comment":      r["comment"],
                    })
            except PWTimeout as exc:
                self.results[tid] = {
                    "status":  "FAIL",
                    "actual":  f"Playwright timeout: {str(exc)[:120]}",
                    "comment": "",
                }
                self._emit("test_completed", {
                    "test_case_id": tid,
                    "status": "FAIL",
                    "actual": self.results[tid]["actual"],
                    "comment": "",
                })
            except Exception as exc:
                self.results[tid] = {
                    "status":  "FAIL",
                    "actual":  f"Unhandled exception: {str(exc)[:120]}",
                    "comment": "",
                }
                self._emit("test_completed", {
                    "test_case_id": tid,
                    "status": "FAIL",
                    "actual": self.results[tid]["actual"],
                    "comment": "",
                })
                logger.exception("Exception in test %s", tid)

    def _run_anonymous_phase(self, pw, test_cases: list) -> None:
        self._emit("status_change", {"phase": "anonymous"})
        browser = pw.chromium.launch(headless=True)
        ctx  = browser.new_context()
        page = ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            self._run_phase(page, test_cases, requires_auth=False)
        finally:
            browser.close()

    def _run_analytics_phase(self, pw, test_cases: list) -> None:
        """Analytics tests are always MANUAL — no browser interaction needed."""
        self._emit("status_change", {"phase": "analytics"})
        browser = pw.chromium.launch(headless=True)
        ctx  = browser.new_context()
        page = ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            for tc in test_cases:
                if tc["id"] in ANALYTICS_TESTS:
                    _analytics(page, tc, self.target_url, self.results,
                               ANALYTICS_TESTS[tc["id"]])
                    r = self.results.get(tc["id"])
                    if r:
                        self._emit("test_completed", {
                            "test_case_id": tc["id"],
                            "status":       r["status"],
                            "actual":       r["actual"],
                            "comment":      r["comment"],
                        })
        finally:
            browser.close()

    def _run_authenticated_phase(self, pw, test_cases: list) -> None:
        self._emit("status_change", {"phase": "authenticated"})

        if not self.auth_state_path:
            # Mark all auth-required tests as SKIP
            auth_ids = {tid for tid, (_, req) in TEST_REGISTRY.items() if req}
            for tc in test_cases:
                if tc["id"] in auth_ids and tc["id"] not in self.results:
                    self.results[tc["id"]] = {
                        "status":  "SKIP",
                        "actual":  "Authenticated session not provided. Upload auth_state.json to run these tests.",
                        "comment": "",
                    }
                    self._emit("test_completed", {
                        "test_case_id": tc["id"],
                        "status": "SKIP",
                        "actual": self.results[tc["id"]]["actual"],
                        "comment": "",
                    })
            return

        browser = pw.chromium.launch(headless=True)
        ctx  = browser.new_context(storage_state=self.auth_state_path)
        page = ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            self._run_phase(page, test_cases, requires_auth=True)
        finally:
            browser.close()
