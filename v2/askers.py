"""Ai đang hỏi bot — cấp và giải "vé phiên" (asker token).

Vì sao cần cả một module (đo 31/07/2026, xem V2_MAINTENANCE §20): MCP server là
MỘT tiến trình dùng chung cho cả tenant, Hermes spawn nó một lần với env tĩnh
(`tools/mcp_tool.py`), nên lời gọi tool KHÔNG mang theo danh tính người hỏi. Mà
`qa.py` phải biết người hỏi, không thì ai enroll cũng đọc được biên bản của mọi
người.

Cách KHÔNG dùng được (đã đo, đừng thử lại): để `gate` ghi "người hỏi hiện tại"
vào DB rồi MCP đọc ra. Hermes serialize theo **session**, không phải toàn cục
(`gateway/run.py` — "Per-SESSION_ID turn lease"), nên hai người nhắn cùng lúc là
hai session chạy song song: B nhắn lúc agent của A đang chạy thì lời gọi tool
sau đó của A đọc ra danh tính B, và đó đúng là rò dữ liệu — im lặng.

Cách đang dùng: `gate` cấp một vé ngẫu nhiên buộc với `union_id`, plugin Hermes
chèn vé vào tin nhắn bằng hook `pre_gateway_dispatch` (`action: "rewrite"`), agent
truyền lại qua tham số `asker_token` của tool. Người khác KHÔNG đoán được vé của
ai, và prompt injection từ nội dung họp cũng không bịa ra được vé hợp lệ — kẻ xấu
chỉ dùng được vé của CHÍNH họ, tức đúng bằng quyền họ vốn có.

Điểm yếu đã biết: phụ thuộc LLM chịu truyền tham số. Quên thì bị từ chối
(fail-closed, không rò gì) nhưng người dùng thấy bot vô dụng. Vì vậy `qa.py`
nhận **người hỏi đã giải** chứ không nhận vé — đổi cơ chế cấp danh tính về sau
không phải viết lại tầng lọc.
"""

from __future__ import annotations

import secrets
import time

from . import config, db


def _now_ms() -> int:
    return int(time.time() * 1000)


def is_admin(union_id: str = "", open_id: str = "") -> bool:
    """Người này được xem TẤT CẢ biên bản không.

    Danh sách ở `QA_ADMIN_UNION_IDS` (mặc định = `ALERT_UNION_IDS`: hôm nay người
    nhận cảnh báo và người chẩn lỗi là cùng một người). Trống = KHÔNG ai là admin
    — đúng hướng fail-closed, không phải "trống thì mở cho tất cả".
    """
    return bool(union_id) and union_id in config.QA_ADMIN_UNION_IDS


def issue(union_id: str, open_id: str = "", name: str = "",
          *, min_ttl_s: int = 120) -> str:
    """Cấp (hoặc gia hạn) vé phiên cho một người. Trả token.

    DÙNG LẠI vé còn sống của cùng người thay vì cấp mới mỗi tin: một cuộc hội
    thoại nhiều lượt thì vé giữ nguyên, nên agent lấy lại được vé từ lượt trước
    trong ngữ cảnh nếu tin mới nhất bị nó bỏ sót. Và bảng không phình theo số tin.

    Mỗi lần gọi đều đẩy `expires_at` ra xa: hội thoại dài hơn TTL vẫn không rơi
    giữa đường.
    """
    now = _now_ms()
    ttl = config.QA_TOKEN_TTL * 1000
    with db.tx() as c:
        c.execute("DELETE FROM qa_sessions WHERE expires_at < ?", (now,))
    row = db.conn().execute(
        "SELECT token FROM qa_sessions WHERE union_id=? AND expires_at > ? "
        "ORDER BY expires_at DESC LIMIT 1",
        (union_id, now + min_ttl_s * 1000),
    ).fetchone() if union_id else None

    token = row["token"] if row else secrets.token_urlsafe(12)
    with db.tx() as c:
        c.execute(
            "INSERT INTO qa_sessions(token, union_id, open_id, name, expires_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET open_id=excluded.open_id, "
            "  name=excluded.name, expires_at=excluded.expires_at",
            (token, union_id, open_id, name, now + ttl),
        )
    return token


def resolve(token: str) -> dict | None:
    """Vé -> người hỏi, hoặc None nếu vé sai/hết hạn.

    None KHÔNG được hiểu là "cho xem hết". Caller phải từ chối — xem
    `qa.NO_ASKER`.
    """
    token = (token or "").strip()
    if not token:
        return None
    row = db.conn().execute(
        "SELECT union_id, open_id, name, expires_at FROM qa_sessions WHERE token=?",
        (token,),
    ).fetchone()
    if not row or _now_ms() > int(row["expires_at"] or 0):
        return None
    return who(row["union_id"], row["open_id"], row["name"] or "")


def who(union_id: str = "", open_id: str = "", name: str = "") -> dict:
    """Dựng người hỏi từ id đã biết (đường `v2 ask --as`, không qua vé)."""
    return {"union_id": union_id or "", "open_id": open_id or "",
            "name": name or (union_id or open_id or "(không rõ)"),
            "admin": is_admin(union_id, open_id)}


def admin_view(label: str = "(quyền admin)") -> dict:
    """Người hỏi giả có quyền xem hết — CHỈ dùng cho đường terminal.

    Không bao giờ được dựng từ dữ liệu đến từ Lark: đây là cửa sau cho người
    ngồi trước máy, và người đó đã có `sqlite3` trong tay rồi.

    `see_all` tách khỏi `admin` (04/08/2026, user chốt): trước đây hai thứ này
    là MỘT cờ, nên một người là admin trong Lark hỏi bot "liệt kê cuộc họp của
    tôi" thì `_may_see` cho qua hết và bot trả lời "hệ thống tìm thấy 22 cuộc
    họp gắn với tài khoản của bạn" — 22 là toàn bộ cuộc họp của cả công ty.
    Câu đó vừa sai vừa làm admin tưởng mình dự những cuộc chưa từng dự.

    Nay: `admin` = được duyệt từ điển phiên âm (glossary). `see_all` = xem được
    cuộc họp của người khác, và CHỈ đường terminal có nó.
    """
    return {"union_id": "", "open_id": "", "name": label,
            "admin": True, "see_all": True}


def find_enrolled(needle: str) -> dict | None:
    """Tra người đã enroll theo union_id HOẶC open_id (cho `v2 ask --as`)."""
    row = db.conn().execute(
        "SELECT union_id, open_id, name FROM tokens "
        "WHERE union_id=? OR open_id=?", (needle, needle),
    ).fetchone()
    if not row:
        return None
    return who(row["union_id"], row["open_id"], row["name"] or "")
