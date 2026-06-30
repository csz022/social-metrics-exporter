from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup
from lxml import html as lxml_html

from parser import STATUS_LOGIN_REQUIRED, STATUS_NOT_FOUND, STATUS_PARSE_FAILED, STATUS_SUCCESS, empty_row


def parse_social_page(platform: str, post_url: str, html: str, visible_text: str, network_json_blobs: list[Any] | None = None) -> dict[str, object]:
    row = empty_row(post_url)
    normalized_text = _normalize_text(visible_text)
    lowered = normalized_text.lower()
    platform = platform.upper()

    if _looks_not_found(normalized_text):
        row["status"] = STATUS_NOT_FOUND
        return row
    if _looks_login_required(platform, normalized_text):
        row["status"] = STATUS_LOGIN_REQUIRED
        return row

    soup = BeautifulSoup(html, "lxml")
    dom_root = _safe_lxml_root(html)
    meta_text = _best_meta_text(soup)
    row["username"] = _extract_username(platform, post_url, soup, normalized_text)
    row["text"] = meta_text or _best_visible_text(normalized_text)
    row["created_at"] = _extract_created_at(soup, network_json_blobs or [])

    meta_contents = " ".join(_extract_meta_contents(soup))
    counts_text = "\n".join([meta_contents, normalized_text])
    dom_metrics = _extract_dom_metrics(platform, post_url, dom_root)
    if platform == "IG":
        row["like_count"] = _first_not_none(dom_metrics.get("like_count"), _first_count(counts_text, IG_LIKE_PATTERNS), _json_count(network_json_blobs or [], IG_LIKE_KEYS), 0)
        row["reply_count"] = _first_not_none(dom_metrics.get("reply_count"), _first_count(counts_text, IG_COMMENT_PATTERNS), _json_count(network_json_blobs or [], COMMENT_KEYS), 0)
        row["view_count"] = _first_not_none(dom_metrics.get("view_count"), _first_count(counts_text, VIEW_PATTERNS) if "/reel/" in post_url else None, "N/A")
        if row["view_count"] in (None, 0):
            row["view_count"] = "N/A"
    elif platform == "FACEBOOK":
        row["like_count"] = _first_not_none(dom_metrics.get("like_count"), _first_count(counts_text, FB_REACTION_PATTERNS), _json_count(network_json_blobs or [], FB_REACTION_KEYS), 0)
        row["reply_count"] = _first_not_none(dom_metrics.get("reply_count"), _first_count(counts_text, COMMENT_PATTERNS), _json_count(network_json_blobs or [], COMMENT_KEYS), 0)
        row["repost_count"] = _first_not_none(dom_metrics.get("repost_count"), _first_count(counts_text, SHARE_PATTERNS), _json_count(network_json_blobs or [], SHARE_KEYS), 0)
        row["view_count"] = _first_not_none(dom_metrics.get("view_count"), _first_count(counts_text, VIEW_PATTERNS), _json_count(network_json_blobs or [], VIEW_KEYS), "N/A")

    has_signal = row["text"] != "N/A" or any(row[field] for field in ("like_count", "reply_count", "repost_count", "quote_count"))
    row["status"] = STATUS_SUCCESS if has_signal else STATUS_PARSE_FAILED
    return row


def parse_profile_follower_count(platform: str, html: str, visible_text: str, network_json_blobs: list[Any] | None = None) -> int | None:
    text = "\n".join([_normalize_text(visible_text), _meta_text(html)])
    platform = platform.upper()
    if platform == "IG":
        return _first_count(text, [
            r"([\d,.]+(?:\s*[萬万千億亿KkMmBb])?)\s*(?:位)?(?:粉絲|followers?)",
        ]) or _json_count(network_json_blobs or [], ("follower_count", "followerCount", "edge_followed_by"))
    if platform == "FACEBOOK":
        return _first_count(text, [
            r"([\d,.]+(?:\s*[萬万千億亿KkMmBb])?)\s*(?:位)?(?:粉絲|followers?)",
            r"([\d,.]+(?:\s*[萬万千億亿KkMmBb])?)\s*(?:位|人)?(?:追蹤者|追蹤|following this|follow this|followers?)",
        ]) or _json_count(network_json_blobs or [], ("follower_count", "followerCount", "likers", "page_likers"))
    return None


IG_LIKE_PATTERNS = [
    r"([\d,.]+[萬万千KkMm]?)\s*(?:個)?(?:讚|likes?)",
    r"([\d,.]+[萬万千KkMm]?)\s*likes?",
]
IG_COMMENT_PATTERNS = [
    r"([\d,.]+[萬万千KkMm]?)\s*(?:則|個)?(?:留言|comments?)",
]
FB_REACTION_PATTERNS = [
    r"([\d,.]+[萬万千KkMm]?)\s*(?:個)?(?:讚|心情|likes?|reactions?)",
]
COMMENT_PATTERNS = [
    r"([\d,.]+[萬万千KkMm]?)\s*(?:則|個)?(?:留言|comments?)",
]
SHARE_PATTERNS = [
    r"([\d,.]+[萬万千KkMm]?)\s*(?:次|則|個)?(?:分享|shares?)",
]
VIEW_PATTERNS = [
    r"([\d,.]+[萬万千KkMm]?)\s*(?:次|個)?(?:觀看|瀏覽|views?|plays?)",
]

IG_LIKE_KEYS = ("like_count", "likeCount", "edge_liked_by", "edge_media_preview_like")
FB_REACTION_KEYS = ("reaction_count", "reactionCount", "like_count", "likeCount", "comet_ufi_summary_and_actions_renderer")
COMMENT_KEYS = ("comment_count", "commentCount", "comments_count", "edge_media_to_comment")
SHARE_KEYS = ("share_count", "shareCount", "reshare_count")
VIEW_KEYS = ("view_count", "viewCount", "play_count", "playCount", "video_view_count", "videoViewCount")


def _looks_not_found(text: str) -> bool:
    lowered = text.lower()
    return any(term in text for term in ("找不到此頁面", "內容無法取得", "無法使用此頁面", "目前無法查看此內容")) or any(
        term in lowered
        for term in (
            "page isn't available",
            "page not found",
            "content isn't available",
            "currently unavailable",
        )
    )


def _looks_login_required(platform: str, text: str) -> bool:
    lowered = text.lower()
    if platform == "IG":
        return "log in" in lowered and "instagram" in lowered and len(text) < 1000
    if platform == "FACEBOOK":
        return "log in" in lowered and "facebook" in lowered and len(text) < 1000
    return False


def _extract_dom_metrics(platform: str, post_url: str, root) -> dict[str, int]:
    if root is None:
        return {}
    platform = platform.upper()
    if platform == "FACEBOOK":
        metrics = _extract_metric_from_dom_nodes(
            root,
            "like_count",
            (
                r"(?:所有)?心情[:：]?\s*(\d+(?:\.\d+)?)",
                r"(\d+(?:\.\d+)?)\s*(?:個)?(?:讚|likes?|reactions?)",
            ),
        )
        metrics.update(
            _extract_metric_from_dom_nodes(
                root,
                "reply_count",
                (
                    r"(\d+(?:\.\d+)?)\s*(?:則|個)?(?:留言|comments?)",
                ),
            )
        )
        metrics.update(
            _extract_metric_from_dom_nodes(
                root,
                "repost_count",
                (
                    r"(\d+(?:\.\d+)?)\s*(?:次|則|個)?(?:分享|shares?)",
                ),
            )
        )
        metrics.update(
            _extract_metric_from_dom_nodes(
                root,
                "view_count",
                (
                    r"(\d+(?:\.\d+)?)\s*(?:次|個)?(?:觀看|瀏覽|views?|plays?|曝光|impressions?)",
                ),
            )
        )
        return metrics
    if platform == "IG":
        metrics = _extract_metric_from_dom_nodes(root, "like_count", tuple(IG_LIKE_PATTERNS))
        metrics.update(_extract_metric_from_dom_nodes(root, "reply_count", tuple(IG_COMMENT_PATTERNS)))
        if "/reel/" in post_url:
            metrics.update(_extract_metric_from_dom_nodes(root, "view_count", tuple(VIEW_PATTERNS)))
        return metrics
    return {}


def _extract_metric_from_dom_nodes(root, field: str, patterns: tuple[str, ...]) -> dict[str, int]:
    best_value: int | None = None
    best_rank: tuple[int, int] | None = None
    for node in _iter_candidate_nodes(root):
        text = _node_text(node)
        if not text:
            continue
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            parsed = _parse_human_count(match.group(1))
            if parsed is None:
                continue
            rank = (len(text), -len(match.group(1)))
            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_value = parsed
            break
    if best_value is None:
        return {}
    return {field: best_value}


def _iter_candidate_nodes(root):
    for xpath in (
        "//*[@aria-label]",
        "//*[@role='button']",
        "//*[self::span or self::div or self::a or self::button]",
    ):
        for node in root.xpath(xpath):
            yield node


def _node_text(node) -> str:
    text = node.get("aria-label") or node.get("title") or " ".join(node.xpath(".//text()"))
    return _normalize_text(text)


def _safe_lxml_root(html: str):
    try:
        return lxml_html.fromstring(html)
    except Exception:
        return None


def _best_meta_text(soup: BeautifulSoup) -> str:
    for attrs in ({"property": "og:description"}, {"name": "description"}, {"property": "og:title"}):
        tag = soup.find("meta", attrs=attrs)
        content = _clean_text(tag.get("content", "")) if tag else ""
        if content and not _is_boilerplate(content):
            return content[:2000]
    return "N/A"


def _best_visible_text(text: str) -> str:
    candidates = [line for line in text.splitlines() if len(line) >= 12 and not _is_boilerplate(line)]
    return max(candidates, key=len)[:2000] if candidates else "N/A"


def _extract_username(platform: str, post_url: str, soup: BeautifulSoup, text: str) -> str:
    if platform == "IG":
        title = _meta(soup, {"property": "og:title"}) or _meta(soup, {"name": "twitter:title"})
        title_match = re.search(r"(?:comments?\s+-\s+)?([\w.]+)\s+(?:於|on)\s+", title, flags=re.IGNORECASE)
        if title_match:
            return f"@{title_match.group(1)}"
        user_match = re.search(r"@([\w.]+)", title or text)
        if user_match:
            return f"@{user_match.group(1)}"
        match = re.search(r"instagram\.com/(?:p|reel)/([^/?#]+)", post_url)
        return f"IG:{match.group(1)}" if match else "IG"
    if platform == "FACEBOOK":
        title = _meta(soup, {"property": "og:title"}) or _meta(soup, {"name": "twitter:title"})
        if title:
            return _clean_text(title)[:120]
        return "Facebook"
    return "N/A"


def _extract_created_at(soup: BeautifulSoup, blobs: list[Any]) -> str:
    for selector in ({"property": "article:published_time"}, {"property": "og:updated_time"}):
        content = _meta(soup, selector)
        if content:
            return content
    for value in _walk_json_values(blobs):
        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}T", value):
            return value
    return "N/A"


def _first_count(text: str, patterns: list[str]) -> int | None:
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            parsed = _parse_human_count(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _json_count(blobs: list[Any], keys: tuple[str, ...]) -> int | None:
    for obj in _walk_json_objects(blobs):
        for key in keys:
            value = obj.get(key)
            parsed = _parse_json_count_value(value)
            if parsed is not None:
                return parsed
    return None


def _parse_json_count_value(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("count", "total_count", "value"):
            if key in value:
                parsed = _parse_json_count_value(value[key])
                if parsed is not None:
                    return parsed
        return None
    return _parse_human_count(value)


def _parse_human_count(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    raw = str(value).replace(",", "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)\s*([萬万千億亿KkMmBb]?)", raw)
    if not match:
        return None
    number = float(match.group(1))
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
    }.get(match.group(2), 1)
    return int(number * multiplier)


def _meta(soup: BeautifulSoup, attrs: dict[str, str]) -> str:
    tag = soup.find("meta", attrs=attrs)
    return str(tag.get("content", "")).strip() if tag else ""


def _extract_meta_contents(soup: BeautifulSoup) -> list[str]:
    return [str(tag.get("content")) for tag in soup.find_all("meta") if tag.get("content")]


def _meta_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    return " ".join(_extract_meta_contents(soup))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().strip('"')


def _is_boilerplate(value: str) -> bool:
    lowered = value.lower().strip()
    blocked = (
        "instagram",
        "facebook",
        "log in",
        "sign up",
        "create an account",
        "see posts, photos and more",
    )
    return lowered in blocked or any(phrase in lowered for phrase in ("log in to", "create an account or log in"))


def _normalize_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


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
