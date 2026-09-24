"""
Nhật ký hỏi đáp: SQLite local là nguồn bền vững; Base chỉ là mirror opt-in.

Ghi local được hoàn tất trước khi hàm trả về. Ghi Base chạy nền và không được
phép làm hỏng luồng chính. Vì vậy một tiến trình CLI ngắn vẫn không mất bản ghi
local, còn mạng chậm không giữ câu trả lời của chatbot.
"""

from __future__ import annotations

import sys
import time
import threading
from typing import Any

from . import config, db, lark_api


def _write_local(
    now_ms: int,
    name: str,
    union_id: str,
    prompt: str,
    tool_called: str,
    meeting_title: str,
    meeting_status: str,
    response: str,
) -> None:
    """Tạo bảng nếu cần và ghi một event trong cùng giao dịch."""
    with db.tx() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                user_name TEXT,
                union_id TEXT,
                user_prompt TEXT,
                tool_called TEXT,
                meeting_title TEXT,
                meeting_status TEXT,
                response_summary TEXT
            )
        """)
        c.execute("""
            INSERT INTO audit_logs (created_at, user_name, union_id, user_prompt, tool_called, meeting_title, meeting_status, response_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now_ms, name or "", union_id or "", prompt or "", tool_called or "", meeting_title or "", meeting_status or "", response[:2000] if response else "")
        )


def _mirror_base(now_ms: int, name: str, union_id: str, prompt: str,
                 tool_called: str, meeting_title: str, meeting_status: str,
                 response: str) -> None:
    try:
        fields = {
            "Thời gian": now_ms,
            "Người hỏi": name or "",
            "Union ID": union_id or "",
            "Câu hỏi của User": prompt or "",
            "Tool gọi": tool_called or "",
            "Cuộc họp chọn": meeting_title or "",
            "Trạng thái cuộc họp": meeting_status or "",
            "Kết quả / Trả lời": response[:2000] if response else "",
        }
        lark_api.base_record_create(
            config.BITABLE_APP_TOKEN, config.BITABLE_AUDIT_TABLE_ID, fields)
    except Exception as exc:
        print(f"[audit] Lỗi ghi Bitable Base audit: {exc}", file=sys.stderr)


def log_qa_event(
    name: str = "",
    union_id: str = "",
    prompt: str = "",
    tool_called: str = "",
    meeting_title: str = "",
    meeting_status: str = "",
    response: str = ""
) -> None:
    """Ghi local chắc chắn; mirror Base (nếu bật rõ ràng) theo kiểu best-effort."""
    now_ms = int(time.time() * 1000)
    try:
        _write_local(now_ms, name, union_id, prompt, tool_called,
                     meeting_title, meeting_status, response)
    except Exception as exc:
        print(f"[audit] Lỗi ghi SQLite local: {exc}", file=sys.stderr)
    if config.BITABLE_APP_TOKEN and config.BITABLE_AUDIT_TABLE_ID:
        threading.Thread(
            target=_mirror_base,
            args=(now_ms, name, union_id, prompt, tool_called,
                  meeting_title, meeting_status, response),
            daemon=True,
        ).start()
