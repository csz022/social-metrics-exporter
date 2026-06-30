from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from exporter import (
    append_raw_row,
    append_report_row,
    ensure_csv_schema,
    read_terminal_urls,
    read_success_urls,
    REPORT_FIELDNAMES,
    FIELDNAMES,
    write_csv,
    write_failed_csv,
    write_report_csv,
)
from formulas import apply_formulas, apply_report_formulas
from parser import STATUS_POST_NOT_LOADED, empty_row, parse_threads_page
from scraper import ThreadsScraper
from sheet_reader import load_sheet_urls
from social_parser import parse_profile_follower_count, parse_social_page


load_dotenv()
MISSING_PROFILE_CACHE_VALUE = "__missing__"


@dataclass(frozen=True)
class InputItem:
    url: str
    platform: str = "THREADS"
    title: str = ""
    author: str = ""
    published_at: str = ""
    row_number: int = 0


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export public Threads post metrics to CSV.")
    parser.add_argument("--input", default=os.getenv("THREADS_INPUT", "input/urls.txt"), help="Path to URL list, one URL per line.")
    parser.add_argument("--sheet", default=os.getenv("THREADS_SHEET", ""), help="Google Sheet URL or local xlsx file. Uses the 文章標題 hyperlink as URL source.")
    parser.add_argument("--sheet-platforms", default=os.getenv("THREADS_SHEET_PLATFORMS", "THREADS"), help="Comma-separated platforms to read from --sheet: THREADS,IG,FACEBOOK, or ALL.")
    parser.add_argument("--dry-run", action="store_true", help="Load inputs and print URL counts without scraping.")
    parser.add_argument("--output", default=os.getenv("THREADS_OUTPUT", "output/social_metrics.csv"), help="Path to output CSV.")
    parser.add_argument("--headful", action="store_true", default=env_bool("THREADS_HEADFUL", False), help="Show Chromium while scraping.")
    parser.add_argument(
        "--no-debug",
        action="store_true",
        default=not env_bool("THREADS_DEBUG", True),
        help="Do not write raw debug text/html files.",
    )
    parser.add_argument("--delay", type=float, default=env_float("THREADS_DELAY", 2.0), help="Seconds to wait between URLs.")
    parser.add_argument("--retries", type=int, default=env_int("THREADS_RETRIES", 1), help="Retries per URL after a failed load.")
    parser.add_argument("--concurrency", type=int, default=env_int("THREADS_CONCURRENCY", 1), help="Number of pages to scrape in parallel.")
    parser.add_argument("--network-idle-timeout-ms", type=int, default=env_int("THREADS_NETWORK_IDLE_TIMEOUT_MS", 3000), help="Milliseconds to wait for network idle after DOM content loads.")
    parser.add_argument("--profile-search", action="store_true", default=env_bool("THREADS_PROFILE_SEARCH", False), help="When a post URL opens the author's profile, scroll the profile to find the target post id.")
    parser.add_argument("--profile-search-scrolls", type=int, default=env_int("THREADS_PROFILE_SEARCH_SCROLLS", 12), help="Maximum profile scroll attempts per post_not_loaded URL.")
    parser.add_argument("--profile-dir", default=os.getenv("THREADS_PROFILE_DIR") or None, help="Persistent Playwright browser profile directory. Run --login once to create it.")
    parser.add_argument("--no-network-capture", action="store_true", default=not env_bool("THREADS_NETWORK_CAPTURE", True), help="Disable parsing JSON responses received by the loaded Threads page.")
    parser.add_argument("--resume", action="store_true", default=env_bool("THREADS_RESUME", False), help="Skip URLs that are already successful in the output CSV.")
    parser.add_argument("--failed-output", default=os.getenv("THREADS_FAILED_OUTPUT", "output/failed_urls.csv"), help="Path to failed URL CSV.")
    parser.add_argument("--fetch-followers", action="store_true", default=env_bool("THREADS_FETCH_FOLLOWERS", True), help="Fetch IG/Facebook profile pages to fill follower counts when missing.")
    parser.add_argument("--no-fetch-followers", action="store_false", dest="fetch_followers", help="Disable IG/Facebook follower enrichment.")
    parser.add_argument("--profile-cache", default=os.getenv("THREADS_PROFILE_CACHE", ".cache/profile_counts.json"), help="Path to cached profile follower counts.")
    parser.add_argument("--auth-state", default=os.getenv("THREADS_AUTH_STATE", ".auth/threads_state.json"), help="Path to saved login session state.")
    parser.add_argument("--login", action="store_true", help="Open browser for manual login and save auth state, then exit.")
    parser.add_argument(
        "--format",
        choices=("report", "raw"),
        default=os.getenv("THREADS_FORMAT", "report"),
        help="CSV format. report matches the MCD result report columns.",
    )
    args = parser.parse_args()
    if "--input" in sys.argv and "--sheet" not in sys.argv:
        args.sheet = ""
    explicit_login_args = {"--profile-dir", "--auth-state"} & set(sys.argv)
    args.use_login = bool(args.login or env_bool("THREADS_USE_LOGIN", False) or explicit_login_args)
    if not args.use_login:
        args.profile_dir = None
        args.auth_state = ""
    return args


def read_urls(input_path: str | Path) -> list[str]:
    path = Path(input_path)
    urls: list[str] = []
    seen: set[str] = set()

    for line in path.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def load_input_items(args: argparse.Namespace) -> tuple[list[InputItem], str]:
    args.sheet = str(args.sheet or "").strip()
    if args.sheet:
        platforms = {
            platform.strip().upper()
            for platform in str(args.sheet_platforms).split(",")
            if platform.strip()
        }
        platforms = platforms or {"THREADS"}
        if "ALL" in platforms:
            platforms = {"THREADS", "IG", "FACEBOOK"}
        unsupported = platforms - {"THREADS", "IG", "FACEBOOK"}
        if unsupported:
            raise SystemExit(
                "This scraper currently supports THREADS, IG, and FACEBOOK from --sheet. "
                f"Unsupported platform(s): {', '.join(sorted(unsupported))}"
            )
        rows = load_sheet_urls(args.sheet, platforms=platforms)
        return [
            InputItem(
                url=row.url,
                platform=row.platform,
                title=row.title,
                author=row.author,
                published_at=row.published_at,
                row_number=row.row_number,
            )
            for row in rows
        ], f"{args.sheet} ({', '.join(sorted(platforms))})"

    return [InputItem(url=url, platform=platform_from_url(url)) for url in read_urls(args.input)], str(args.input)


def main() -> int:
    args = parse_args()
    scraper = ThreadsScraper(
        headless=not args.headful,
        network_idle_timeout_ms=args.network_idle_timeout_ms,
        delay_seconds=args.delay,
        retries=args.retries,
        concurrency=args.concurrency,
        profile_search_scrolls=args.profile_search_scrolls,
        profile_dir=args.profile_dir,
        network_capture=not args.no_network_capture,
        auth_state_path=args.auth_state,
        username=(os.getenv("THREADS_USERNAME") or None) if args.use_login else None,
        password=(os.getenv("THREADS_PASSWORD") or None) if args.use_login else None,
    )

    if args.login:
        scraper.save_manual_login_session()
        return 0

    print("Resolving input source...", flush=True)
    if args.sheet:
        print("Reading Google Sheet export...", flush=True)
    elif args.input:
        print("Reading URL list...", flush=True)
    input_items, input_label = load_input_items(args)
    if not input_items:
        raise SystemExit(f"No URLs found in {input_label}")
    print(f"Input ready: {len(input_items)} URL(s)", flush=True)
    if args.dry_run:
        print(f"Loaded {len(input_items)} URL(s) from {input_label}")
        for item in input_items[:20]:
            print(f"{item.platform}\t{item.url}")
        if len(input_items) > 20:
            print(f"... {len(input_items) - 20} more URL(s)")
        return 0
    urls = [item.url for item in input_items]
    item_by_url = {item.url: item for item in input_items}

    expected_fields = FIELDNAMES if args.format == "raw" else REPORT_FIELDNAMES
    backup_path = ensure_csv_schema(args.output, expected_fields)
    if backup_path:
        print(f"Output schema changed. Backed up old CSV to {backup_path}")
    if not args.resume:
        if args.format == "raw":
            write_csv([], args.output)
        else:
            write_report_csv([], args.output)
        write_failed_csv([], args.failed_output)

    skipped = 0
    if args.resume:
        success_urls = read_terminal_urls(args.output, report_format=args.format == "report")
        before = len(urls)
        urls = [url for url in urls if url not in success_urls]
        skipped = before - len(urls)
        if not urls:
            print(f"All URLs already succeeded in {args.output}. Skipped {skipped} URL(s).")
            return 0

    debug_dir = None if args.no_debug else Path(args.output).parent / "debug"
    profile_cache_path = Path(args.profile_cache) if args.profile_cache else None
    profile_cache = load_profile_cache(profile_cache_path)
    scraper = ThreadsScraper(
        headless=not args.headful,
        network_idle_timeout_ms=args.network_idle_timeout_ms,
        delay_seconds=args.delay,
        retries=args.retries,
        concurrency=args.concurrency,
        profile_search_scrolls=args.profile_search_scrolls,
        profile_dir=args.profile_dir,
        network_capture=not args.no_network_capture,
        auth_state_path=args.auth_state,
        username=(os.getenv("THREADS_USERNAME") or None) if args.use_login else None,
        password=(os.getenv("THREADS_PASSWORD") or None) if args.use_login else None,
        debug_dir=debug_dir,
    )

    raw_rows: list[dict[str, object]] = []
    report_rows: list[dict[str, object]] = []
    failed_rows: list[dict[str, object]] = []
    print(f"Loaded {len(urls)} URL(s) from {input_label}", flush=True)
    if skipped:
        print(f"Resume: skipped {skipped} already-successful URL(s)", flush=True)
    print(f"Auth mode: {scraper.auth_mode()}", flush=True)
    started_at = time.perf_counter()
    auth_mode = scraper.auth_mode()
    for index, scraped in enumerate(scraper.scrape_many(urls), start=1):
        item = item_by_url.get(scraped.url, InputItem(url=scraped.url))
        row = row_from_scraped(scraped, item.platform)
        apply_sheet_metadata(row, item)
        fallback_reason = scraped.error
        elapsed_seconds = scraped.elapsed_seconds
        if item.platform == "THREADS" and row["status"] == STATUS_POST_NOT_LOADED:
            alternate_url = alternate_threads_domain(scraped.url)
            if alternate_url:
                alternate = scraper.scrape_many([alternate_url])[0]
                elapsed_seconds += alternate.elapsed_seconds
                alternate_row = row_from_scraped(alternate, item.platform)
                apply_sheet_metadata(alternate_row, item)
                if alternate_row["status"] == "success":
                    alternate_row["post_url"] = scraped.url
                    row = alternate_row
                else:
                    fallback_reason = (
                        f"target post did not load; alternate {alternate_url} "
                        f"returned {alternate_row['status']}"
                    )
            if row["status"] == STATUS_POST_NOT_LOADED and args.profile_search:
                profile_result = scraper.scrape_post_from_profile(scraped.url, index)
                elapsed_seconds += profile_result.elapsed_seconds
                profile_row = row_from_scraped(profile_result, item.platform)
                apply_sheet_metadata(profile_row, item)
                if profile_row["status"] == "success":
                    profile_row["post_url"] = scraped.url
                    row = profile_row
                    fallback_reason = profile_result.error
                else:
                    fallback_reason = profile_result.error or fallback_reason
        row = apply_formulas(row)
        raw_rows.append(row)
        report_row = apply_report_formulas(to_report_row(index, row, auth_mode=auth_mode, platform=item.platform))
        report_rows.append(report_row)
        if row["status"] != "success":
            failed_rows.append({"post_url": scraped.url, "status": row["status"], "reason": fallback_reason})
        print(f"[{index}/{len(urls)}] [{row['status']}] {elapsed_seconds:.2f}s {scraped.url}", flush=True)

    if args.fetch_followers:
        enrich_social_followers(raw_rows, report_rows, input_items, scraper, profile_cache=profile_cache)
        save_profile_cache(profile_cache_path, profile_cache)

    for row, report_row in zip(raw_rows, report_rows):
        if args.format == "raw":
            append_raw_row(row, args.output)
        else:
            append_report_row(report_row, args.output)

    if args.format == "raw":
        row_count = len(raw_rows)
    else:
        row_count = len(report_rows)
    write_failed_csv(failed_rows, args.failed_output)
    elapsed = time.perf_counter() - started_at
    average = elapsed / row_count if row_count else 0
    print(f"Done. Wrote {row_count} rows to {args.output}")
    print(f"Failed URLs: {len(failed_rows)} rows to {args.failed_output}")
    print(f"Elapsed: {elapsed:.2f}s total, {average:.2f}s per URL")
    return 0


def row_from_scraped(scraped, platform: str = "THREADS") -> dict[str, object]:
    if scraped.status != "success":
        return empty_row(scraped.url, status=scraped.status)
    if platform.upper() != "THREADS":
        return parse_social_page(
            platform,
            scraped.url,
            scraped.html,
            scraped.visible_text,
            scraped.network_json_blobs or [],
        )
    return parse_threads_page(
        scraped.url,
        scraped.html,
        scraped.visible_text,
        scraped.network_json_blobs or [],
    ).row


def apply_sheet_metadata(row: dict[str, object], item: InputItem) -> None:
    if item.platform.upper() in {"IG", "FACEBOOK"} and item.author:
        row["username"] = item.author
    if row.get("text") in (None, "", "N/A") and item.title:
        row["text"] = item.title
    if row.get("created_at") in (None, "", "N/A") and item.published_at:
        row["created_at"] = item.published_at


def alternate_threads_domain(url: str) -> str | None:
    if "://www.threads.com/" in url:
        return url.replace("://www.threads.com/", "://www.threads.net/", 1)
    if "://www.threads.net/" in url:
        return url.replace("://www.threads.net/", "://www.threads.com/", 1)
    return None


def platform_from_url(url: str) -> str:
    lowered = url.lower()
    if "instagram." in lowered:
        return "IG"
    if "facebook." in lowered or "fb.watch" in lowered or "fb.com" in lowered:
        return "FACEBOOK"
    return "THREADS"


def to_report_row(index: int, row: dict[str, object], *, auth_mode: str, platform: str = "THREADS") -> dict[str, object]:
    username = str(row.get("username") or "N/A")
    title = str(row.get("text") or "N/A")
    share_count = 0 if platform.upper() == "IG" else int(row.get("repost_count") or 0) + int(row.get("quote_count") or 0)
    platform_label = {"THREADS": "Threads", "IG": "Instagram", "FACEBOOK": "Facebook"}.get(platform.upper(), platform)

    reach = row.get("reach") or "N/A"
    if reach != "N/A":
        reach_status = "found"
    elif auth_mode.startswith("session"):
        reach_status = "unavailable_after_login"
    elif auth_mode.startswith("profile"):
        reach_status = "unavailable_after_login"
    elif auth_mode == "credentials":
        reach_status = "unavailable_with_credentials"
    else:
        reach_status = "public_unavailable"

    return {
        "序號": index,
        "發布時間": row.get("created_at") or "N/A",
        "FB": f"{platform_label} > {username}" if username != "N/A" else platform_label,
        "fb標題": title,
        "網站": platform_label,
        "頻道": username,
        "fb標題_2": title,
        "討論串總則數": row.get("reply_count") or 0,
        "點閱數/按讚數": row.get("like_count") or 0,
        "瀏覽數": row.get("view_count") or "N/A",
        "分享": share_count,
        "網址": row.get("post_url") or "",
        "粉絲團追蹤人數": (row.get("follower_count") or "N/A") if platform.upper() in {"IG", "FACEBOOK"} else "N/A",
        "觸及": reach,
        "status": row.get("status") or "",
        "reach_status": reach_status,
    }


def enrich_followers(
    raw_rows: list[dict[str, object]],
    report_rows: list[dict[str, object]],
    scraper: ThreadsScraper,
    *,
    auth_mode: str,
    profile_cache: dict[str, object],
) -> None:
    for raw_row, report_row in zip(raw_rows, report_rows):
        username = str(raw_row.get("username") or "N/A")
        if username == "N/A":
            continue
        cache_key = profile_cache_key("THREADS", username)
        cached = profile_cache.get(cache_key)
        if cached is not None and cached != MISSING_PROFILE_CACHE_VALUE and raw_row.get("follower_count") in (None, "", "N/A"):
            raw_row["follower_count"] = cached
            report_row["粉絲團追蹤人數"] = cached
            apply_report_formulas(report_row)

    missing_usernames = sorted(
        {
            str(row.get("username"))
            for row in raw_rows
            if "threads." in str(row.get("post_url") or "").lower()
            and row.get("username") not in (None, "", "N/A")
            and row.get("follower_count") in (None, "", "N/A")
            and profile_cache.get(profile_cache_key("THREADS", str(row.get("username")))) != MISSING_PROFILE_CACHE_VALUE
        }
    )
    if not missing_usernames:
        return

    profile_urls = [f"https://www.threads.com/{username}" for username in missing_usernames]
    print(f"Fetching follower counts for {len(profile_urls)} profile(s)", flush=True)
    follower_counts: dict[str, object] = {}

    for scraped in scraper.scrape_many(profile_urls):
        parsed = parse_threads_page(
            scraped.url,
            scraped.html,
            scraped.visible_text,
            scraped.network_json_blobs or [],
        ).row
        username = str(parsed.get("username") or "N/A")
        follower_count = parsed.get("follower_count")
        if username != "N/A" and follower_count not in (None, "", "N/A"):
            follower_counts[username] = follower_count
            profile_cache[profile_cache_key("THREADS", username)] = follower_count

    for username in missing_usernames:
        profile_cache.setdefault(profile_cache_key("THREADS", username), MISSING_PROFILE_CACHE_VALUE)

    if not follower_counts:
        return

    for raw_row, report_row in zip(raw_rows, report_rows):
        username = str(raw_row.get("username") or "N/A")
        if username in follower_counts and raw_row.get("follower_count") in (None, "", "N/A"):
            raw_row["follower_count"] = follower_counts[username]
            report_row["粉絲團追蹤人數"] = follower_counts[username]
            apply_report_formulas(report_row)


def enrich_social_followers(
    raw_rows: list[dict[str, object]],
    report_rows: list[dict[str, object]],
    input_items: list[InputItem],
    scraper: ThreadsScraper,
    profile_cache: dict[str, object],
) -> None:
    profile_by_url: dict[str, tuple[str, str]] = {}
    for raw_row, report_row, item in zip(raw_rows, report_rows, input_items):
        profile_url = _social_profile_url(item)
        if not profile_url:
            continue
        cache_key = profile_cache_key(item.platform, profile_url)
        cached = profile_cache.get(cache_key)
        if cached is not None and cached != MISSING_PROFILE_CACHE_VALUE and raw_row.get("follower_count") in (None, "", "N/A"):
            raw_row["follower_count"] = cached
            report_row["粉絲團追蹤人數"] = cached
            apply_report_formulas(report_row)
        if cached in (None, MISSING_PROFILE_CACHE_VALUE) and raw_row.get("follower_count") in (None, "", "N/A"):
            profile_by_url[profile_url] = (item.platform, item.author)

    if not profile_by_url:
        return
    print(f"Fetching social follower counts for {len(profile_by_url)} profile(s)", flush=True)
    follower_counts: dict[tuple[str, str], object] = {}
    profile_urls = list(profile_by_url)
    for scraped in scraper.scrape_many(profile_urls):
        platform, author = profile_by_url[scraped.url]
        follower_count = parse_profile_follower_count(
            platform,
            scraped.html,
            scraped.visible_text,
            scraped.network_json_blobs or [],
        )
        if follower_count is not None:
            follower_counts[(platform.upper(), author)] = follower_count
            profile_cache[profile_cache_key(platform, scraped.url)] = follower_count
        else:
            profile_cache[profile_cache_key(platform, scraped.url)] = MISSING_PROFILE_CACHE_VALUE

    for raw_row, report_row, item in zip(raw_rows, report_rows, input_items):
        key = (item.platform.upper(), item.author)
        if key in follower_counts and raw_row.get("follower_count") in (None, "", "N/A"):
            raw_row["follower_count"] = follower_counts[key]
            report_row["粉絲團追蹤人數"] = follower_counts[key]
            apply_report_formulas(report_row)


def load_profile_cache(cache_path: Path | None) -> dict[str, object]:
    if not cache_path or not cache_path.exists():
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cache: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(key, str) and value not in (None, ""):
            cache[key] = value
    return cache


def save_profile_cache(cache_path: Path | None, cache: dict[str, object]) -> None:
    if not cache_path or not cache:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def profile_cache_key(platform: str, identifier: str) -> str:
    return f"{platform.upper()}:{identifier.strip().lower()}"


def _social_profile_url(item: InputItem) -> str:
    if item.platform.upper() == "IG":
        handle = item.author.strip().lstrip("@")
        return f"https://www.instagram.com/{handle}/" if handle else ""
    if item.platform.upper() == "FACEBOOK":
        return facebook_profile_url(item.url)
    return ""


def facebook_profile_url(post_url: str) -> str:
    marker = "facebook.com/"
    if marker not in post_url:
        return ""
    tail = post_url.split(marker, 1)[1].split("?", 1)[0].split("#", 1)[0].strip("/")
    if "_" in tail:
        page_id = tail.split("_", 1)[0]
        return f"https://www.facebook.com/{page_id}"
    first_segment = tail.split("/", 1)[0]
    return f"https://www.facebook.com/{first_segment}" if first_segment else ""


if __name__ == "__main__":
    raise SystemExit(main())
