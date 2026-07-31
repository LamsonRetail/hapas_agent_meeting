"""
Cửa vào cho bot hỏi đáp: người chưa enroll thì tự bấm link OAuth, xong mới hỏi.

Ai gọi module này: plugin Hermes `v2-enroll-gate` (chạy hook
`pre_gateway_dispatch`, TRƯỚC cửa auth của Hermes) gọi qua `v2-gate.bat` rồi đọc
JSON ở stdout. Vì sao qua subprocess chứ không import: Hermes chạy Python 3.11
của uv, V2 chạy Python 3.12 với bộ thư viện riêng — nhập chéo là mời lỗi phụ
thuộc. Tiến trình tách nhau, giao tiếp bằng JSON một dòng.

Ba trạng thái trả về:
    {"decision": "allow"}                  đã enroll -> cho vào agent
    {"decision": "invite", ...}            chưa, vừa gửi link
    {"decision": "wait", ...}              chưa, link còn hiệu lực -> im lặng

KHÔNG bao giờ trả "allow" khi không chắc: hàm này là cửa duy nhất (user chốt
31/07/2026), nên nghi ngờ thì đóng.

Khớp danh tính bằng `union_id`. Hermes đưa sang `SessionSource.user_id_alt` —
tài liệu của nó ghi rõ đó là union_id của Feishu — và `tokens` cũng lưu
`union_id`, nên không phải tra danh bạ. `user_id` (kiểu `1fg8g36d`) chỉ để in log:
bảng `tokens` KHÔNG có nó, đừng dùng để so.
"""

from __future__ import annotations

import time
from typing import Any

from . import db, lark_api, oauth

# Nhắc lại link sau bao lâu. KHÔNG dùng TTL nonce (nay 24h — xem config): dùng
# chung thì người mất link phải chờ hết 24h mới được cái mới. Link cũ vẫn sống
# song song, và cả hai đều enroll được nên không sao.
from .config import ENROLL_REINVITE_MINUTES

_INVITE_TEXT = (
    "Chào {name}!\n\n"
    "Trước khi hỏi về biên bản họp, bạn cần cho phép hệ thống đọc biên bản "
    "cuộc họp của bạn một lần. Bấm link dưới rồi chọn Đồng ý:\n\n{link}\n\n"
    "Xong bạn nhắn lại là mình trả lời được ngay."
)

_WAIT_TEXT = (
    "Link cấp quyền mình vừa gửi vẫn còn hiệu lực — bạn bấm vào đó và chọn "
    "Đồng ý nhé. Chưa có quyền thì mình chưa đọc được biên bản của bạn."
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _enrolled(union_id: str) -> dict | None:
    row = db.conn().execute(
        "SELECT open_id, name, status FROM tokens WHERE union_id=?",
        (union_id,),
    ).fetchone()
    if row and row["status"] == "active":
        return {"open_id": row["open_id"], "name": row["name"]}
    return None


def check(union_id: str, user_id: str = "", name: str = "",
          *, send: bool = True) -> dict[str, Any]:
    """Quyết định cho vào hay không. `send=False` để thử mà không nhắn ai."""
    if not union_id:
        # Không có union_id thì không định danh được -> đóng.
        return {"decision": "wait", "reason": "thiếu union_id"}

    who = _enrolled(union_id)
    if who:
        return {"decision": "allow", "open_id": who["open_id"],
                "name": who["name"]}

    inv = db.conn().execute(
        "SELECT nonce, sent_at, times FROM enroll_invites WHERE union_id=?",
        (union_id,),
    ).fetchone()
    if inv and _now_ms() - int(inv["sent_at"] or 0) < ENROLL_REINVITE_MINUTES * 60_000:
        if send:
            try:
                lark_api.im_send_text(union_id, _WAIT_TEXT, id_type="union_id")
            except lark_api.LarkError as exc:
                print(f"[gate] không nhắc được {union_id}: {exc}")
        return {"decision": "wait", "nonce": inv["nonce"],
                "times": inv["times"]}

    link, nonce = oauth.start()          # open_id trống: chỉ ràng buộc bằng nonce
    if send:
        try:
            lark_api.im_send_text(
                union_id,
                _INVITE_TEXT.format(name=name or "bạn", link=link),
                id_type="union_id",
            )
        except lark_api.LarkError as exc:
            # Gửi hỏng thì ĐỪNG ghi invite, để lần sau thử lại.
            print(f"[gate] không gửi được link cho {union_id}: {exc}")
            return {"decision": "wait", "reason": f"gửi link hỏng: {exc.code}"}

    with db.tx() as c:
        c.execute(
            "INSERT INTO enroll_invites(union_id,user_id,name,nonce,sent_at,times) "
            "VALUES (?,?,?,?,?,1) "
            "ON CONFLICT(union_id) DO UPDATE SET "
            "  user_id=excluded.user_id, name=excluded.name, "
            "  nonce=excluded.nonce, sent_at=excluded.sent_at, "
            "  times=enroll_invites.times+1",
            (union_id, user_id, name, nonce, _now_ms()),
        )
    return {"decision": "invite", "nonce": nonce, "link": link}


def clear_invite(union_id: str) -> None:
    """Xoá lời mời sau khi người đó enroll xong."""
    with db.tx() as c:
        c.execute("DELETE FROM enroll_invites WHERE union_id=?", (union_id,))


def pending_invites() -> list[dict]:
    rows = db.conn().execute(
        "SELECT union_id, user_id, name, sent_at, times FROM enroll_invites "
        "ORDER BY sent_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]
