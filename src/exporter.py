from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable


FIELDNAMES = [
    "post_url",
    "username",
    "text",
    "created_at",
    "like_count",
    "reply_count",
    "repost_count",
    "quote_count",
    "view_count",
    "reach",
    "follower_count",
    "status",
]

REPORT_FIELDNAMES = [
    "網址",
    "fb標題",
    "討論串總則數",
    "點閱數/按讚數",
    "瀏覽數",
    "分享",
    "粉絲團追蹤人數",
    "觸及",
]

FAILED_FIELDNAMES = [
    "post_url",
    "status",
    "reason",
]


def write_csv(rows: Iterable[dict[str, object]], output_path: str | Path) -> None:
    _write_csv(rows, output_path, FIELDNAMES)


def write_report_csv(rows: Iterable[dict[str, object]], output_path: str | Path) -> None:
    _write_csv(rows, output_path, REPORT_FIELDNAMES)


def write_failed_csv(rows: Iterable[dict[str, object]], output_path: str | Path) -> None:
    _write_csv(rows, output_path, FAILED_FIELDNAMES)


def append_csv_row(row: dict[str, object], output_path: str | Path, fieldnames: list[str]) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_csv_schema(path, fieldnames)
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fieldnames})


def append_report_row(row: dict[str, object], output_path: str | Path) -> None:
    append_csv_row(row, output_path, REPORT_FIELDNAMES)


def append_raw_row(row: dict[str, object], output_path: str | Path) -> None:
    append_csv_row(row, output_path, FIELDNAMES)


def read_success_urls(output_path: str | Path, *, report_format: bool) -> set[str]:
    path = Path(output_path)
    if not path.exists() or path.stat().st_size == 0:
        return set()

    url_field = "網址" if report_format else "post_url"
    success_urls: set[str] = set()
    with path.open(newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if report_format and "status" not in (reader.fieldnames or []) and row.get(url_field):
                success_urls.add(str(row[url_field]))
                continue
            if row.get("status") == "success" and row.get(url_field):
                success_urls.add(str(row[url_field]))
    return success_urls


def read_terminal_urls(output_path: str | Path, *, report_format: bool) -> set[str]:
    path = Path(output_path)
    if not path.exists() or path.stat().st_size == 0:
        return set()

    url_field = "網址" if report_format else "post_url"
    terminal_statuses = {"success", "post_not_loaded", "not_found"}
    urls: set[str] = set()
    with path.open(newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if report_format and "status" not in (reader.fieldnames or []) and row.get(url_field):
                urls.add(str(row[url_field]))
                continue
            if row.get("status") in terminal_statuses and row.get(url_field):
                urls.add(str(row[url_field]))
    return urls


def ensure_csv_schema(output_path: str | Path, fieldnames: list[str]) -> Path | None:
    path = Path(output_path)
    if not path.exists() or path.stat().st_size == 0:
        return None

    with path.open(newline="", encoding="utf-8-sig") as csvfile:
        reader = csv.reader(csvfile)
        try:
            header = next(reader)
        except StopIteration:
            return None

    if header == fieldnames:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.stem}.schema_mismatch_{timestamp}{path.suffix}")
    path.rename(backup_path)
    return backup_path


def _write_csv(
    rows: Iterable[dict[str, object]],
    output_path: str | Path,
    fieldnames: list[str],
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
