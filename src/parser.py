from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup


STATUS_LOGIN_REQUIRED = "login_required"
STATUS_NOT_FOUND = "not_found"
STATUS_POST_NOT_LOADED = "post_not_loaded"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_SUCCESS = "success"


COUNT_PATTERNS = {
    "like_count": [
        r"([\d,.]+[萬万千KkMm]?)\s*(?:個)?(?:讚|喜歡|likes?|Like)",
    ],
    "reply_count": [
        r"([\d,.]+[萬万千KkMm]?)\s*(?:則|個)?(?:回覆|留言|回應|replies|reply|comments?|comment)",
    ],
    "repost_count": [
        r"([\d,.]+[萬万千KkMm]?)\s*(?:次|則|個)?(?:轉發|轉貼|reposts?|repost|shares?|share)",
    ],
    "quote_count": [
        r"([\d,.]+[萬万千KkMm]?)\s*(?:則|個)?(?:引用|quotes?|quote)",
    ],
    "view_count": [
        r"([\d,.]+[萬万千KkMm]?)\s*(?:次|個)?(?:觀看|瀏覽|views?|view)",
    ],
}


@dataclass(frozen=True)
class ParsedPage:
    row: dict[str, object]
    status: str


def empty_row(post_url: str, status: str = STATUS_PARSE_FAILED) -> dict[str, object]:
    return {
        "post_url": post_url,
        "username": "N/A",
        "text": "N/A",
        "created_at": "N/A",
        "like_count": 0,
        "reply_count": 0,
        "repost_count": 0,
        "quote_count": 0,
        "view_count": "N/A",
        "reach": "N/A",
        "follower_count": "N/A",
        "status": status,
    }


def parse_threads_page(
    post_url: str,
    html: str,
    visible_text: str,
    network_json_blobs: list[Any] | None = None,
) -> ParsedPage:
    row = empty_row(post_url)
    normalized_text = _normalize_text(visible_text)

    page_status = _detect_status(normalized_text)
    if page_status == STATUS_NOT_FOUND:
        row["status"] = page_status
        return ParsedPage(row=row, status=page_status)

    soup = BeautifulSoup(html, "lxml")
    json_blobs = _extract_json_blobs(soup)
    target_network_blobs = _filter_network_json_blobs_for_post(post_url, network_json_blobs or [])
    json_blobs.extend(target_network_blobs)

    row["username"] = _extract_username(post_url, soup, normalized_text, json_blobs)
    row["follower_count"] = _extract_follower_count(normalized_text)
    meta_follower_count = _extract_follower_count(" ".join(_extract_meta_contents(soup)))
    if row["follower_count"] in (None, "N/A") and meta_follower_count is not None:
        row["follower_count"] = meta_follower_count

    if "/post/" in post_url and _og_url_mismatches_post(post_url, soup) and not target_network_blobs:
        row["status"] = STATUS_POST_NOT_LOADED
        return ParsedPage(row=row, status=STATUS_POST_NOT_LOADED)

    visible_post = _extract_visible_post(normalized_text, str(row["username"]))
    candidate_text = visible_post.get("text") or _extract_post_text(soup, normalized_text, json_blobs)
    if _is_threads_marketing_text(candidate_text):
        candidate_text = "N/A"
    if page_status == STATUS_LOGIN_REQUIRED and not visible_post and candidate_text == "N/A":
        row["status"] = STATUS_LOGIN_REQUIRED
        return ParsedPage(row=row, status=STATUS_LOGIN_REQUIRED)
    row["text"] = candidate_text
    row["created_at"] = _extract_created_at(soup, json_blobs)
    if row["created_at"] == "N/A" and visible_post.get("created_at"):
        row["created_at"] = visible_post["created_at"]

    for field, patterns in COUNT_PATTERNS.items():
        parsed = _extract_count(field, patterns, normalized_text, json_blobs, post_url)
        if parsed is not None:
            row[field] = parsed
    for field, value in _extract_visible_metrics(normalized_text, str(row["username"])).items():
        row[field] = value

    if _looks_like_profile_page_without_target_post(normalized_text, str(row["username"]), visible_post) and not target_network_blobs:
        row["status"] = STATUS_POST_NOT_LOADED
        return ParsedPage(row=row, status=STATUS_POST_NOT_LOADED)

    required_signal = row["text"] != "N/A" or any(
        row[field] for field in ("like_count", "reply_count", "repost_count", "quote_count")
    )
    if required_signal:
        row["status"] = STATUS_SUCCESS
    elif page_status == STATUS_LOGIN_REQUIRED:
        row["status"] = STATUS_LOGIN_REQUIRED
    else:
        row["status"] = STATUS_PARSE_FAILED
    return ParsedPage(row=row, status=str(row["status"]))


def _detect_status(text: str) -> str:
    lowered = text.lower()
    if any(needle in text for needle in ("找不到此頁面", "無法使用此頁面", "內容無法取得", "頁面真的走丟了")):
        return STATUS_NOT_FOUND
    if any(needle in lowered for needle in ("page isn't available", "page not found", "content isn't available")):
        return STATUS_NOT_FOUND
    if any(needle in text for needle in ("登入", "註冊", "繼續使用 Threads")) and "threads" in lowered:
        return STATUS_LOGIN_REQUIRED
    if any(needle in lowered for needle in ("log in", "sign up", "continue with instagram")):
        return STATUS_LOGIN_REQUIRED
    return STATUS_SUCCESS


def _extract_username(
    post_url: str, soup: BeautifulSoup, text: str, json_blobs: list[Any]
) -> str:
    url_match = re.search(r"threads\.(?:com|net)/(?:@|%40)([^/?#]+)/post/", post_url)
    if url_match:
        return f"@{url_match.group(1)}"
    url_match = re.search(r"threads\.(?:com|net)/(?:@|%40)([^/?#]+)", post_url)
    if url_match:
        return f"@{url_match.group(1)}"

    for meta_selector in (
        {"property": "og:title"},
        {"name": "twitter:title"},
        {"property": "al:ios:url"},
    ):
        tag = soup.find("meta", attrs=meta_selector)
        content = tag.get("content", "") if tag else ""
        match = re.search(r"@([\w.]+)", content)
        if match:
            return f"@{match.group(1)}"

    for value in _walk_json_values(json_blobs):
        if isinstance(value, str):
            match = re.search(r"@([\w.]+)", value)
            if match:
                return f"@{match.group(1)}"

    match = re.search(r"@([\w.]+)", text)
    return f"@{match.group(1)}" if match else "N/A"


def _extract_visible_post(text: str, username: str) -> dict[str, str]:
    if username == "N/A":
        return {}

    bare_username = username.removeprefix("@")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line != bare_username:
            continue

        date_index = _visible_date_index(lines, index)
        if date_index is None:
            continue

        content_lines: list[str] = []
        for content_line in lines[date_index + 1 :]:
            if content_line in ("翻譯", "Translate"):
                break
            if _is_metric_line(content_line):
                break
            content_lines.append(content_line)

        content = " ".join(content_lines).strip()
        if content:
            return {"created_at": lines[date_index], "text": content[:2000]}
    return {}


def _extract_visible_metrics(text: str, username: str) -> dict[str, int]:
    if username == "N/A":
        return {}

    bare_username = username.removeprefix("@")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line != bare_username:
            continue
        date_index = _visible_date_index(lines, index)
        if date_index is None:
            continue

        metrics: list[int] = []
        saw_translation_marker = False
        for metric_line in lines[date_index + 1 :]:
            if metric_line in ("翻譯", "Translate"):
                saw_translation_marker = True
                continue
            if not saw_translation_marker:
                continue
            if _is_related_threads_boundary(metric_line):
                break
            if not _is_metric_line(metric_line):
                if metrics:
                    break
                continue
            parsed = _parse_human_count(metric_line)
            if parsed is not None:
                metrics.append(parsed)
            if len(metrics) >= 4:
                break

        if len(metrics) < 4:
            metrics = []

        result: dict[str, int] = {}
        if len(metrics) >= 1:
            result["like_count"] = metrics[0]
        if len(metrics) >= 2:
            result["reply_count"] = metrics[1]
        if len(metrics) >= 3:
            result["repost_count"] = metrics[2]
        if len(metrics) >= 4:
            result["quote_count"] = metrics[3]

        view_count = _extract_visible_view_count(lines[: date_index + 1])
        if view_count is not None:
            result["view_count"] = view_count
        return result
    return {}


def _is_related_threads_boundary(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"相關串文", "related threads", "related posts"}


def _visible_date_index(lines: list[str], username_index: int) -> int | None:
    for candidate_index in (username_index + 1, username_index + 2):
        if candidate_index < len(lines) and _looks_like_date(lines[candidate_index]):
            return candidate_index
    return None


def _extract_visible_view_count(lines: list[str]) -> int | None:
    for line in lines:
        if "瀏覽" in line or re.search(r"\bviews?\b", line, flags=re.IGNORECASE):
            parsed = _parse_human_count(line)
            if parsed is not None:
                return parsed
    return None


def _extract_meta_contents(soup: BeautifulSoup) -> list[str]:
    contents: list[str] = []
    for tag in soup.find_all("meta"):
        content = tag.get("content")
        if content:
            contents.append(str(content))
    return contents


def _og_url_mismatches_post(post_url: str, soup: BeautifulSoup) -> bool:
    tag = soup.find("meta", attrs={"property": "og:url"})
    og_url = tag.get("content", "") if tag else ""
    if not og_url:
        return False

    expected = _extract_post_id(post_url)
    actual = _extract_post_id(og_url)
    if not actual:
        return True
    return expected != actual


def _extract_post_id(url: str) -> str:
    match = re.search(r"/post/([^/?#]+)", url)
    return match.group(1) if match else ""


def _extract_follower_count(text: str) -> int | None:
    for line in text.splitlines():
        normalized = line.strip().replace("\xa0", " ")
        match = re.search(r"([\d,.]+(?:\s*[萬万千億亿KkMmBb])?)\s*位粉絲", normalized)
        if match:
            return _parse_human_count(match.group(1))
        match = re.search(r"([\d,.]+(?:\s*[萬万千億亿KkMmBb])?)\s*followers?", normalized, flags=re.IGNORECASE)
        if match:
            return _parse_human_count(match.group(1))
    return None


def _looks_like_profile_page_without_target_post(
    text: str,
    username: str,
    visible_post: dict[str, str],
) -> bool:
    if username == "N/A" or visible_post:
        return False

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    profile_markers = {"提及", "串文", "回覆", "影音內容", "轉發"}
    marker_count = sum(1 for marker in profile_markers if marker in lines[:30])
    if marker_count >= 2:
        return True
    if "此個人檔案不公開。" in text or "This profile is private" in text:
        return True
    return False


def _looks_like_date(value: str) -> bool:
    normalized = value.strip()
    return bool(
        re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", normalized)
        or re.match(r"^\d+\s*(?:秒|分鐘|小時|天|週|周|個月|年)$", normalized)
        or re.match(r"^\d+\s*(?:s|sec|secs|min|mins|h|hr|hrs|d|day|days|w|week|weeks|mo|month|months|y|yr|yrs|year|years)$", normalized, flags=re.IGNORECASE)
    )


def _is_metric_line(value: str) -> bool:
    return bool(re.match(r"^[\d,.]+\s*[萬万千KkMm]?$", value.replace("\xa0", " ")))


def _extract_post_text(soup: BeautifulSoup, text: str, json_blobs: list[Any]) -> str:
    for attrs in ({"property": "og:description"}, {"name": "description"}, {"name": "twitter:description"}):
        tag = soup.find("meta", attrs=attrs)
        content = _clean_post_text(tag.get("content", "")) if tag else ""
        if content:
            return content

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    content_lines = [
        line for line in lines if _looks_like_post_text(line) and not re.match(r"^[\d,.萬万千KkMm\s]+$", line)
    ]
    return max(content_lines, key=len)[:2000] if content_lines else "N/A"


def _extract_created_at(soup: BeautifulSoup, json_blobs: list[Any]) -> str:
    time_tag = soup.find("time")
    if time_tag:
        return time_tag.get("datetime") or time_tag.get_text(strip=True) or "N/A"

    for value in _walk_json_values(json_blobs):
        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}T", value):
            return value
    return "N/A"


def _extract_count(
    field: str,
    patterns: list[str],
    text: str,
    json_blobs: list[Any],
    post_url: str = "",
) -> int | None:
    post_id = _extract_post_id(post_url)
    if post_id:
        return _extract_target_json_count(field, post_id, json_blobs)

    json_count = _extract_json_count(field, json_blobs)
    if json_count is not None:
        return json_count

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _parse_human_count(match.group(1))
    return None


def _extract_json_count(field: str, json_blobs: list[Any]) -> int | None:
    key_hints = _json_count_key_hints()
    for obj in _walk_json_objects(json_blobs):
        for key in key_hints[field]:
            value = obj.get(key)
            if isinstance(value, (int, float, str)):
                parsed = _parse_human_count(value)
                if parsed is not None:
                    return parsed
    return None


def _extract_target_json_count(field: str, post_id: str, json_blobs: list[Any]) -> int | None:
    key_hints = _json_count_key_hints()
    for obj in _walk_json_objects(json_blobs):
        if not _json_object_matches_post_id(obj, post_id):
            continue
        for scoped_obj in _walk_json_objects([obj]):
            for key in key_hints[field]:
                value = scoped_obj.get(key)
                if isinstance(value, (int, float, str)):
                    parsed = _parse_human_count(value)
                    if parsed is not None:
                        return parsed
    return None


def _json_count_key_hints() -> dict[str, tuple[str, ...]]:
    return {
        "like_count": ("like_count", "likeCount", "likes_count", "likesCount"),
        "reply_count": ("direct_reply_count", "reply_count", "replyCount", "replies_count", "comment_count", "commentCount"),
        "repost_count": ("repost_count", "repostCount", "reshare_count", "share_count", "shareCount"),
        "quote_count": ("quote_count", "quoteCount"),
        "view_count": ("view_count", "viewCount", "play_count", "impression_count"),
    }


def _json_object_matches_post_id(obj: dict[str, Any], post_id: str) -> bool:
    for key in ("code", "shortcode", "pk", "id"):
        value = obj.get(key)
        if isinstance(value, str) and value == post_id:
            return True

    for key in ("url", "permalink", "canonical_url", "share_url"):
        value = obj.get(key)
        if isinstance(value, str) and f"/post/{post_id}" in value:
            return True

    return False


def _extract_json_blobs(soup: BeautifulSoup) -> list[Any]:
    blobs: list[Any] = []
    for script in soup.find_all("script"):
        content = script.string or script.get_text()
        if not content:
            continue
        stripped = content.strip()
        if not stripped:
            continue
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                blobs.append(json.loads(stripped))
            except json.JSONDecodeError:
                pass
    return blobs


def _filter_network_json_blobs_for_post(post_url: str, blobs: list[Any]) -> list[Any]:
    if "/post/" not in post_url:
        return blobs

    post_id = _extract_post_id(post_url)
    if not post_id:
        return []

    return [blob for blob in blobs if _json_contains_text(blob, post_id)]


def _json_contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_json_contains_text(child, needle) for child in value.values())
    if isinstance(value, list):
        return any(_json_contains_text(child, needle) for child in value)
    if isinstance(value, str):
        return needle in value
    return False


def _walk_json_values(values: list[Any]):
    for value in values:
        if isinstance(value, dict):
            for child in value.values():
                yield from _walk_json_values([child])
        elif isinstance(value, list):
            for child in value:
                yield from _walk_json_values([child])
        else:
            yield value


def _walk_json_objects(values: list[Any]):
    for value in values:
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from _walk_json_objects([child])
        elif isinstance(value, list):
            for child in value:
                yield from _walk_json_objects([child])


def _parse_human_count(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)

    raw = str(value).strip().replace(",", "")
    match = re.search(r"([\d.]+)\s*([萬万千億亿KkMmBb]?)", raw)
    if not match:
        return None

    number = float(match.group(1))
    unit = match.group(2)
    multiplier = {
        "千": 1_000,
        "K": 1_000,
        "k": 1_000,
        "萬": 10_000,
        "万": 10_000,
        "億": 100_000_000,
        "亿": 100_000_000,
        "M": 1_000_000,
        "m": 1_000_000,
        "B": 1_000_000_000,
        "b": 1_000_000_000,
    }.get(unit, 1)
    return int(number * multiplier)


def _clean_post_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(r"^.+? on Threads:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" \"'")


def _looks_like_post_text(value: str) -> bool:
    if len(value) < 8:
        return False
    if _is_threads_marketing_text(value):
        return False
    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        return False
    if stripped.startswith(("http://", "https://")):
        return False
    if any(token in stripped for token in ("rti/web_rs_transport", "__bbox", "RelayPrefetchedStreamCache")):
        return False
    if any(token in stripped for token in ("Mozilla/5.0", "AppleWebKit/", "Chrome/", "Safari/")):
        return False
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", stripped):
        return False
    if len(stripped) > 80 and not re.search(r"\s", stripped):
        return False
    lowered = value.lower()
    blocked = ("threads", "log in", "sign up", "instagram", "javascript")
    if any(blocked_term == lowered for blocked_term in blocked):
        return False
    return True


def _is_threads_marketing_text(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value).strip().lower()
    if not normalized or normalized == "n/a":
        return False
    blocked_phrases = (
        "加入 threads 即可",
        "使用你的 instagram 登入",
        "透過 threads 暢所欲言",
        "join threads to",
        "log in to threads",
        "log in with instagram",
        "continue with instagram",
        "share ideas, ask questions",
    )
    return any(phrase in normalized for phrase in blocked_phrases)


def _normalize_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())
