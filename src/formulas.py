from __future__ import annotations


def as_int(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        return default
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def apply_formulas(row: dict[str, object]) -> dict[str, object]:
    like_count = as_int(row.get("like_count"))
    reply_count = as_int(row.get("reply_count"))
    repost_count = as_int(row.get("repost_count"))
    quote_count = as_int(row.get("quote_count"))

    row["interaction_total"] = like_count + reply_count + repost_count + quote_count
    row["thread_weight"] = reply_count * 45
    row["like_weight"] = like_count * 30
    row["share_weight"] = (repost_count + quote_count) * 60
    row["value"] = row["thread_weight"] + row["like_weight"] + row["share_weight"]
    return row


def apply_report_formulas(row: dict[str, object]) -> dict[str, object]:
    reply_count = as_int(row.get("討論串總則數"))
    like_count = as_int(row.get("點閱數/按讚數"))
    share_count = as_int(row.get("分享"))
    reach = as_int(row.get("觸及"), default=0)

    row["討論串加權(*45)"] = reply_count * 45
    row["讚數加權(*30)"] = like_count * 30
    row["分享加權(*60)"] = share_count * 60
    row["互動總次數"] = row["討論串加權(*45)"] + row["讚數加權(*30)"] + row["分享加權(*60)"]
    row["互動次數單價"] = row.get("互動次數單價") or 5
    row["觸及單價"] = row.get("觸及單價") or 2.5
    row["VALUE"] = row["互動總次數"] * as_float(row["互動次數單價"])
    if reach:
        row["VALUE"] += reach * float(row["觸及單價"])
    return row
