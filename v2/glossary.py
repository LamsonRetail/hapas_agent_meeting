"""
Duyệt glossary whisper QUA BOT — đường GHI, admin-only (part B, 03/08/2026).

Vì sao module riêng (không đặt trong `qa.py`): `qa.py` giữ bất biến "chỉ đọc"
(V2_HANDOFF §5.3). Cùng lý do `sendfile.py` / `tasks.py` tồn tại riêng — mọi cửa
GHI nằm NGOÀI `qa.py`, để ranh giới "dữ liệu họp chảy vào prompt agent" không bao
giờ chạm cửa ghi.

Cưỡng chế bằng CODE, KHÔNG bằng mô tả tool:
 1. CHỈ admin (`QA_ADMIN_UNION_IDS`) duyệt/bỏ được. Nội dung họp lái được agent,
    nên một tool "sửa từ điển phiên âm" mà ai gọi cũng được là lỗ thật: từ đã
    duyệt đi vào `initial_prompt` của MỌI cuộc sau — ảnh hưởng cả hệ thống, và
    một từ sai được duyệt sẽ khoá cứng lỗi (bias whisper về chữ sai).
 2. Chỉ ĐỔI STATUS ứng viên ĐÃ CÓ trong bảng; không tạo từ tuỳ ý từ tham số tool
    — giảm sức nhét rác qua agent.
"""

from __future__ import annotations

from typing import Any

from . import askers, db

NOT_ADMIN = (
    "Chỉ admin mới duyệt được từ điển phiên âm. Nếu bạn là admin mà bị chặn, "
    "kiểm QA_ADMIN_UNION_IDS trong v2/.env."
)


def _is_admin(who: dict[str, Any] | None) -> bool:
    if not who:
        return False
    return bool(who.get("admin")) or askers.is_admin(
        who.get("union_id", ""), who.get("open_id", ""))


def pending(who: dict[str, Any] | None, *, min_count: int = 0) -> str:
    """Liệt kê thuật ngữ đang chờ duyệt. Admin-only."""
    if not _is_admin(who):
        return NOT_ADMIN
    rows = db.glossary_list(status="pending", min_count=min_count)
    if not rows:
        return "Không có thuật ngữ nào đang chờ duyệt."
    lines = [
        f"- {r['term']}  (gặp {r['count']} cuộc)"
        + (f" — vd: {r['example'][:60]}" if r.get("example") else "")
        for r in rows
    ]
    return ("Thuật ngữ đang chờ duyệt (nhắn 'duyệt <từ>' hoặc 'bỏ <từ>', "
            "nhiều từ cách nhau dấu phẩy):\n" + "\n".join(lines))


def approve(who: dict[str, Any] | None, terms: list[str] | str) -> str:
    return _apply(who, terms, "approved", "duyệt")


def reject(who: dict[str, Any] | None, terms: list[str] | str) -> str:
    return _apply(who, terms, "rejected", "bỏ")


def digest_body(min_count: int) -> str:
    """Nội dung digest tuần: thuật ngữ chờ duyệt gặp >= min_count cuộc. '' nếu không có."""
    rows = db.glossary_list(status="pending", min_count=min_count)
    if not rows:
        return ""
    lines = [f"• {r['term']} (gặp {r['count']} cuộc)" for r in rows]
    return ("📚 Thuật ngữ mới cho từ điển phiên âm — cần bạn duyệt:\n"
            + "\n".join(lines)
            + "\n\nNhắn mình 'duyệt <từ>' để đưa vào (whisper viết đúng các từ "
              "này ở cuộc sau), hoặc 'bỏ <từ>' nếu sai. Nhiều từ cách dấu phẩy.")


def send_digest(min_count: int, recipients: list[str]) -> int:
    """DM digest cho từng admin. Trả số người gửi được; 0 nếu không có gì để gửi."""
    from . import lark_api
    body = digest_body(min_count)
    if not body or not recipients:
        return 0
    ok = 0
    for uid in recipients:
        try:
            lark_api.im_send_text(uid, body, id_type="union_id")
            ok += 1
        except lark_api.LarkError as exc:
            print(f"[glossary] gửi digest cho {uid} hỏng: {exc}")
    return ok


def _apply(who: dict[str, Any] | None, terms: list[str] | str,
           status: str, verb: str) -> str:
    if not _is_admin(who):
        return NOT_ADMIN
    if isinstance(terms, str):
        terms = [terms]
    terms = [t.strip() for t in (terms or []) if t and t.strip()]
    if not terms:
        return f"Cần nêu từ cần {verb}, ví dụ: '{verb} MCP, Anthropic'."
    done: list[str] = []
    miss: list[str] = []
    for t in terms:
        got = db.glossary_set_status(t, status)
        (done if got else miss).append(got or t)
    parts: list[str] = []
    if done:
        extra = (" Cuộc họp sau whisper sẽ ưu tiên viết đúng các từ này."
                 if status == "approved" else "")
        parts.append(f"Đã {verb}: {', '.join(done)}.{extra}")
    if miss:
        parts.append(f"Không thấy trong danh sách chờ (bỏ qua): {', '.join(miss)}.")
    return " ".join(parts)
