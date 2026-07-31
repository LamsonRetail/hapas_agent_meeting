"""
Thẻ Lark cho biên bản đã phát.

Chỉ còn MỘT loại thẻ: recap gửi cho mọi người dự. Thẻ duyệt (4 nút, đường
card.action.trigger) đã bỏ 30/07/2026 — họp xong là phát ngay, không ai duyệt.
Thẻ ở đây KHÔNG có nút, nên không cần đường callback nào.
"""

from __future__ import annotations

from typing import Any

from .models import MeetingMeta, Recap


def _fmt_time_range(meta: MeetingMeta) -> str:
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=7))          # hiển thị +07 (V2_LONGTERM §4.3)
    if not meta.start:
        return ""
    lo = datetime.fromtimestamp(meta.start, tz).strftime("%H:%M %d/%m")
    if meta.duration_sec:
        hi = datetime.fromtimestamp(
            meta.start + meta.duration_sec, tz).strftime("%H:%M")
        return f"{lo}–{hi}"
    return lo


def recap_card(meta: MeetingMeta, recap: Recap) -> dict[str, Any]:
    """Thẻ recap phát cho người nhận."""
    when = _fmt_time_range(meta)
    elements: list[dict[str, Any]] = []
    if when:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"🕐 {when}"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                    "content": recap.to_markdown()[:4000]}})
    if meta.app_link:
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"[Xem trên Lark Minutes]({meta.app_link})"}})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green",
                   "title": {"tag": "plain_text",
                             "content": f"Biên bản: {meta.title}"[:100]}},
        "elements": elements,
    }
