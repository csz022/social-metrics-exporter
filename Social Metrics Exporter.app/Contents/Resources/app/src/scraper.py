from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Error as AsyncPlaywrightError
from playwright.async_api import TimeoutError as AsyncPlaywrightTimeoutError
from playwright.async_api import async_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


@dataclass(frozen=True)
class ScrapedPage:
    url: str
    final_url: str
    html: str
    visible_text: str
    network_json_blobs: list[Any] | None = None
    status: str = "success"
    error: str = ""
    elapsed_seconds: float = 0.0


class ThreadsScraper:
    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 45_000,
        network_idle_timeout_ms: int = 3_000,
        delay_seconds: float = 2.0,
        retries: int = 1,
        concurrency: int = 1,
        profile_search_scrolls: int = 12,
        profile_dir: str | Path | None = None,
        network_capture: bool = True,
        auth_state_path: str | Path | None = None,
        username: str | None = None,
        password: str | None = None,
        debug_dir: str | Path | None = None,
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.network_idle_timeout_ms = max(0, network_idle_timeout_ms)
        self.delay_seconds = delay_seconds
        self.retries = retries
        self.concurrency = max(1, concurrency)
        self.profile_search_scrolls = max(0, profile_search_scrolls)
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.network_capture = network_capture
        self.auth_state_path = Path(auth_state_path) if auth_state_path else None
        self.username = username or None
        self.password = password or None
        self.debug_dir = Path(debug_dir) if debug_dir else None

    def scrape_many(self, urls: list[str]) -> list[ScrapedPage]:
        if self.concurrency > 1:
            return asyncio.run(self.scrape_many_async(urls))

        with sync_playwright() as p:
            browser = None
            if self._has_persistent_profile():
                context = p.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    headless=self.headless,
                    **self._public_context_options(),
                )
            else:
                browser = p.chromium.launch(headless=self.headless)
                self._ensure_auth_state(browser)
                context = browser.new_context(**self._context_options())
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            results: list[ScrapedPage] = []
            for index, url in enumerate(urls, start=1):
                results.append(self.scrape_one(page, url, index))
                if index < len(urls) and self.delay_seconds > 0:
                    time.sleep(self.delay_seconds)
            context.close()
            if browser:
                browser.close()
            return results

    async def scrape_many_async(self, urls: list[str]) -> list[ScrapedPage]:
        async with async_playwright() as p:
            browser = None
            shared_context = None
            if self._has_persistent_profile():
                shared_context = await p.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    headless=self.headless,
                    **self._public_context_options(),
                )
            else:
                browser = await p.chromium.launch(headless=self.headless)
                await self._ensure_auth_state_async(browser)
            queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
            for index, url in enumerate(urls, start=1):
                queue.put_nowait((index, url))

            results: list[ScrapedPage | None] = [None] * len(urls)
            worker_count = min(self.concurrency, len(urls))
            workers = [
                asyncio.create_task(self._async_worker(browser, queue, results, shared_context))
                for _ in range(worker_count)
            ]
            await queue.join()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            if shared_context:
                await shared_context.close()
            if browser:
                await browser.close()
            return [result for result in results if result is not None]

    async def _async_worker(self, browser, queue, results: list[ScrapedPage | None], shared_context=None) -> None:
        context = shared_context or await browser.new_context(**self._context_options())
        page = await context.new_page()
        page.set_default_timeout(self.timeout_ms)

        try:
            while True:
                index, url = await queue.get()
                try:
                    results[index - 1] = await self.scrape_one_async(page, url, index)
                    if self.delay_seconds > 0:
                        await asyncio.sleep(self.delay_seconds)
                finally:
                    queue.task_done()
        finally:
            await page.close()
            if not shared_context:
                await context.close()

    def scrape_one(self, page, url: str, index: int = 0) -> ScrapedPage:
        last_result: ScrapedPage | None = None
        for attempt in range(1, self.retries + 2):
            last_result = self._scrape_once(page, url, index)
            if last_result.status == "success":
                return last_result
            if attempt <= self.retries:
                time.sleep(max(self.delay_seconds, 1.0))
        return last_result or ScrapedPage(url=url, final_url=url, html="", visible_text="", status="parse_failed")

    def scrape_post_from_profile(self, post_url: str, index: int = 0) -> ScrapedPage:
        profile_url = _profile_url_from_post_url(post_url)
        post_id = _post_id_from_url(post_url)
        if not profile_url or not post_id:
            return ScrapedPage(
                url=post_url,
                final_url=post_url,
                html="",
                visible_text="",
                status="post_not_loaded",
                error="cannot derive profile URL or post id",
            )

        started_at = time.perf_counter()
        with sync_playwright() as p:
            browser = None
            if self._has_persistent_profile():
                context = p.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    headless=self.headless,
                    **self._public_context_options(),
                )
            else:
                browser = p.chromium.launch(headless=self.headless)
                self._ensure_auth_state(browser)
                context = browser.new_context(**self._context_options())
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                page.goto(profile_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=self.network_idle_timeout_ms)
                except PlaywrightTimeoutError:
                    pass

                found_url = self._find_post_link_on_profile(page, post_id)
                for _ in range(self.profile_search_scrolls):
                    if found_url:
                        break
                    page.mouse.wheel(0, 2400)
                    page.wait_for_timeout(1200)
                    found_url = self._find_post_link_on_profile(page, post_id)

                if not found_url:
                    html = page.content()
                    visible_text = page.locator("body").inner_text(timeout=10_000)
                    self._write_debug_files(index, f"{profile_url}#profile_search", html, visible_text)
                    return ScrapedPage(
                        url=post_url,
                        final_url=page.url,
                        html=html,
                        visible_text=visible_text,
                        network_json_blobs=[],
                        status="post_not_loaded",
                        error=f"profile search did not find post id {post_id}",
                        elapsed_seconds=time.perf_counter() - started_at,
                    )

                result = self.scrape_one(page, found_url, index)
                return ScrapedPage(
                    url=post_url,
                    final_url=result.final_url,
                    html=result.html,
                    visible_text=result.visible_text,
                    network_json_blobs=result.network_json_blobs,
                    status=result.status,
                    error=result.error,
                    elapsed_seconds=time.perf_counter() - started_at,
                )
            except PlaywrightTimeoutError as exc:
                return ScrapedPage(
                    url=post_url,
                    final_url=page.url or profile_url,
                    html="",
                    visible_text="",
                    status="timeout",
                    error=str(exc),
                    elapsed_seconds=time.perf_counter() - started_at,
                )
            except PlaywrightError as exc:
                return ScrapedPage(
                    url=post_url,
                    final_url=page.url or profile_url,
                    html="",
                    visible_text="",
                    status="parse_failed",
                    error=str(exc),
                    elapsed_seconds=time.perf_counter() - started_at,
                )
            finally:
                context.close()
                if browser:
                    browser.close()

    def _find_post_link_on_profile(self, page, post_id: str) -> str | None:
        hrefs = page.locator(f"a[href*='/post/{post_id}']")
        count = hrefs.count()
        if count == 0:
            return None
        href = hrefs.first.get_attribute("href")
        if not href:
            return None
        if href.startswith("/"):
            return f"https://www.threads.com{href}"
        return href

    def _scrape_once(self, page, url: str, index: int = 0) -> ScrapedPage:
        started_at = time.perf_counter()
        network_json_blobs: list[Any] = []
        response_handler = self._sync_response_handler(network_json_blobs)
        if response_handler:
            page.on("response", response_handler)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=self.network_idle_timeout_ms)
            except PlaywrightTimeoutError:
                pass
        except PlaywrightTimeoutError as exc:
            self._remove_response_handler(page, response_handler)
            return ScrapedPage(
                url=url,
                final_url=page.url or url,
                html="",
                visible_text="",
                network_json_blobs=network_json_blobs,
                status="timeout",
                error=str(exc),
                elapsed_seconds=time.perf_counter() - started_at,
            )
        except PlaywrightError as exc:
            self._remove_response_handler(page, response_handler)
            return ScrapedPage(
                url=url,
                final_url=page.url or url,
                html="",
                visible_text="",
                network_json_blobs=network_json_blobs,
                status="parse_failed",
                error=str(exc),
                elapsed_seconds=time.perf_counter() - started_at,
            )

        html = page.content()
        visible_text = page.locator("body").inner_text(timeout=10_000)
        self._remove_response_handler(page, response_handler)
        self._write_debug_files(index, url, html, visible_text)
        self._write_network_debug_file(index, url, network_json_blobs)
        return ScrapedPage(
            url=url,
            final_url=page.url,
            html=html,
            visible_text=visible_text,
            network_json_blobs=network_json_blobs,
            elapsed_seconds=time.perf_counter() - started_at,
        )

    async def scrape_one_async(self, page, url: str, index: int = 0) -> ScrapedPage:
        last_result: ScrapedPage | None = None
        for attempt in range(1, self.retries + 2):
            last_result = await self._scrape_once_async(page, url, index)
            if last_result.status == "success":
                return last_result
            if attempt <= self.retries:
                await asyncio.sleep(max(self.delay_seconds, 1.0))
        return last_result or ScrapedPage(url=url, final_url=url, html="", visible_text="", status="parse_failed")

    async def _scrape_once_async(self, page, url: str, index: int = 0) -> ScrapedPage:
        started_at = time.perf_counter()
        network_json_blobs: list[Any] = []
        pending_response_tasks: set[asyncio.Task] = set()
        response_handler = self._async_response_handler(network_json_blobs, pending_response_tasks)
        if response_handler:
            page.on("response", response_handler)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=self.network_idle_timeout_ms)
            except AsyncPlaywrightTimeoutError:
                pass
        except AsyncPlaywrightTimeoutError as exc:
            self._remove_response_handler(page, response_handler)
            await self._drain_response_tasks(pending_response_tasks)
            return ScrapedPage(
                url=url,
                final_url=page.url or url,
                html="",
                visible_text="",
                network_json_blobs=network_json_blobs,
                status="timeout",
                error=str(exc),
                elapsed_seconds=time.perf_counter() - started_at,
            )
        except AsyncPlaywrightError as exc:
            self._remove_response_handler(page, response_handler)
            await self._drain_response_tasks(pending_response_tasks)
            return ScrapedPage(
                url=url,
                final_url=page.url or url,
                html="",
                visible_text="",
                network_json_blobs=network_json_blobs,
                status="parse_failed",
                error=str(exc),
                elapsed_seconds=time.perf_counter() - started_at,
            )

        self._remove_response_handler(page, response_handler)
        await self._drain_response_tasks(pending_response_tasks)
        html = await page.content()
        visible_text = await page.locator("body").inner_text(timeout=10_000)
        self._write_debug_files(index, url, html, visible_text)
        self._write_network_debug_file(index, url, network_json_blobs)
        return ScrapedPage(
            url=url,
            final_url=page.url,
            html=html,
            visible_text=visible_text,
            network_json_blobs=network_json_blobs,
            elapsed_seconds=time.perf_counter() - started_at,
        )

    def _write_debug_files(self, index: int, url: str, html: str, text: str) -> None:
        if not self.debug_dir:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        slug = _safe_slug(url)
        prefix = f"{index:03d}_{slug}"
        (self.debug_dir / f"{prefix}.txt").write_text(text, encoding="utf-8")
        (self.debug_dir / f"{prefix}.html").write_text(html, encoding="utf-8")

    def _write_network_debug_file(self, index: int, url: str, blobs: list[Any]) -> None:
        if not self.debug_dir or not blobs:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        slug = _safe_slug(url)
        prefix = f"{index:03d}_{slug}"
        (self.debug_dir / f"{prefix}.network.json").write_text(
            json.dumps(blobs[:50], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _sync_response_handler(self, blobs: list[Any]):
        if not self.network_capture:
            return None

        def handle_response(response) -> None:
            if not _looks_like_threads_data_response(response.url):
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type and "javascript" not in content_type and "text" not in content_type:
                return
            try:
                parsed = response.json()
            except (PlaywrightError, ValueError):
                try:
                    parsed = _parse_jsonish_response_text(response.text())
                except (PlaywrightError, ValueError):
                    return
            if parsed is not None:
                blobs.append(parsed)

        return handle_response

    def _async_response_handler(self, blobs: list[Any], pending_tasks: set[asyncio.Task]):
        if not self.network_capture:
            return None

        async def capture(response) -> None:
            if not _looks_like_threads_data_response(response.url):
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type and "javascript" not in content_type and "text" not in content_type:
                return
            try:
                parsed = await response.json()
            except (AsyncPlaywrightError, ValueError):
                try:
                    parsed = _parse_jsonish_response_text(await response.text())
                except (AsyncPlaywrightError, ValueError):
                    return
            if parsed is not None:
                blobs.append(parsed)

        def handle_response(response) -> None:
            task = asyncio.create_task(capture(response))
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)

        return handle_response

    async def _drain_response_tasks(self, pending_tasks: set[asyncio.Task]) -> None:
        if not pending_tasks:
            return
        done, pending = await asyncio.wait(pending_tasks, timeout=3)
        for task in done:
            try:
                task.exception()
            except asyncio.CancelledError:
                pass
        for task in pending:
            task.cancel()

    def _remove_response_handler(self, page, handler) -> None:
        if not handler:
            return
        try:
            page.remove_listener("response", handler)
        except Exception:
            pass

    def _context_options(self) -> dict[str, object]:
        options: dict[str, object] = {
            "locale": "zh-TW",
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        if self.auth_state_path and self.auth_state_path.exists():
            options["storage_state"] = str(self.auth_state_path)
        return options

    def auth_mode(self) -> str:
        if self._has_persistent_profile():
            return f"profile:{self.profile_dir}"
        if self.auth_state_path and self.auth_state_path.exists():
            return f"session:{self.auth_state_path}"
        if self.username and self.password:
            return "credentials"
        return "public"

    def save_manual_login_session(self) -> None:
        if self.profile_dir:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    headless=False,
                    **self._public_context_options(),
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.threads.net/", wait_until="domcontentloaded", timeout=self.timeout_ms)
                print("Browser opened with persistent profile. Log in to Threads/Instagram, then return here and press Enter.")
                input()
                context.close()
            print(f"Saved login browser profile to {self.profile_dir}")
            return

        if not self.auth_state_path:
            raise ValueError("auth_state_path is required for login mode")

        self.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(**self._public_context_options())
            page = context.new_page()
            page.goto("https://www.threads.net/", wait_until="domcontentloaded", timeout=self.timeout_ms)
            print("Browser opened. Log in to Threads/Instagram, then return here and press Enter.")
            input()
            context.storage_state(path=str(self.auth_state_path))
            context.close()
            browser.close()
        print(f"Saved login session to {self.auth_state_path}")

    def _ensure_auth_state(self, browser) -> None:
        if not self.auth_state_path or self.auth_state_path.exists():
            return
        if not self.username or not self.password:
            return
        self.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        context = browser.new_context(**self._public_context_options())
        page = context.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            self._login_with_credentials(page)
            context.storage_state(path=str(self.auth_state_path))
        finally:
            context.close()

    async def _ensure_auth_state_async(self, browser) -> None:
        if not self.auth_state_path or self.auth_state_path.exists():
            return
        if not self.username or not self.password:
            return
        self.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
        context = await browser.new_context(**self._public_context_options())
        page = await context.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            await self._login_with_credentials_async(page)
            await context.storage_state(path=str(self.auth_state_path))
        finally:
            await context.close()

    def _login_with_credentials(self, page) -> None:
        page.goto("https://www.threads.net/login", wait_until="domcontentloaded", timeout=self.timeout_ms)
        self._click_continue_with_instagram(page)
        page.locator('input[name="username"], input[autocomplete="username"], input[type="text"]').first.fill(self.username)
        page.locator('input[name="password"], input[autocomplete="current-password"], input[type="password"]').first.fill(self.password)
        page.keyboard.press("Enter")
        page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        try:
            page.wait_for_url(re.compile(r"threads\.(net|com)|instagram\.com"), timeout=15_000)
        except PlaywrightTimeoutError:
            pass

    async def _login_with_credentials_async(self, page) -> None:
        await page.goto("https://www.threads.net/login", wait_until="domcontentloaded", timeout=self.timeout_ms)
        await self._click_continue_with_instagram_async(page)
        await page.locator('input[name="username"], input[autocomplete="username"], input[type="text"]').first.fill(self.username)
        await page.locator('input[name="password"], input[autocomplete="current-password"], input[type="password"]').first.fill(self.password)
        await page.keyboard.press("Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        try:
            await page.wait_for_url(re.compile(r"threads\.(net|com)|instagram\.com"), timeout=15_000)
        except AsyncPlaywrightTimeoutError:
            pass

    def _click_continue_with_instagram(self, page) -> None:
        button = page.get_by_text(re.compile(r"使用 Instagram|Continue with Instagram|Log in with Instagram", re.I)).first
        try:
            button.click(timeout=10_000)
        except PlaywrightError:
            pass

    async def _click_continue_with_instagram_async(self, page) -> None:
        button = page.get_by_text(re.compile(r"使用 Instagram|Continue with Instagram|Log in with Instagram", re.I)).first
        try:
            await button.click(timeout=10_000)
        except AsyncPlaywrightError:
            pass

    def _public_context_options(self) -> dict[str, object]:
        return {
            "locale": "zh-TW",
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }

    def _has_persistent_profile(self) -> bool:
        if not self.profile_dir:
            return False
        return self.profile_dir.exists() and any(self.profile_dir.iterdir())


def _safe_slug(url: str) -> str:
    slug = re.sub(r"^https?://", "", url)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug)
    return slug[:120] or "thread"


def _post_id_from_url(url: str) -> str:
    match = re.search(r"/post/([^/?#]+)", url)
    return match.group(1) if match else ""


def _profile_url_from_post_url(url: str) -> str:
    match = re.search(r"^(https?://www\.threads\.(?:com|net)/(?:@|%40)[^/?#]+)/post/", url)
    return match.group(1) if match else ""


def _looks_like_threads_data_response(url: str) -> bool:
    lowered = url.lower()
    if "threads." not in lowered and "instagram.com" not in lowered:
        return False
    data_markers = (
        "graphql",
        "api",
        "query",
        "relay",
        "ajax",
        "web",
        "__a=",
    )
    return any(marker in lowered for marker in data_markers)


def _parse_jsonish_response_text(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("for (;;);"):
        stripped = stripped.removeprefix("for (;;);").strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    return None
