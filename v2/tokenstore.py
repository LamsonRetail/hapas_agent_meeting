"""
Kho user token trên máy local: lưu mã hóa, tự refresh, xoay vòng.

Token của mỗi người enroll nằm ở đây (SQLite, mã hóa Fernet). Đây là thứ
KHÔNG được để lên Bitable. Mất file này = cả công ty enroll lại.

Điểm dễ sai (V2_ARCHITECTURE §8 #4): refresh token XOAY VÒNG mỗi lần refresh.
Lưu nhầm bản cũ = mất phiên của người đó. Nên get_access_token() luôn ghi
lại cả access LẪN refresh mới ngay sau khi refresh.
"""

from __future__ import annotations

import threading
import time

from . import config, crypto, db, lark_api


class TokenError(RuntimeError):
    pass


# Refresh trước khi access token hết hạn ngần này (giây) để tránh gọi API
# đúng lúc token vừa chết.
_REFRESH_MARGIN = 300

_lock = threading.Lock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def enroll(access_token: str, refresh_token: str,
           expires_in: int, refresh_expires_in: int,
           scopes: str = "") -> dict:
    """Lưu token của người vừa authorize. Trả thông tin user (open_id...).

    Gọi user_info bằng access token để lấy open_id/union_id/name làm khóa.
    """
    info = lark_api.user_info(access_token)
    open_id = info.get("open_id", "")
    if not open_id:
        raise TokenError("không lấy được open_id từ user_info")

    now = _now_ms()
    with _lock, db.tx() as c:
        c.execute(
            """INSERT INTO tokens
               (open_id, union_id, name, access_enc, refresh_enc,
                access_exp, refresh_exp, scopes, status,
                enrolled_at, updated_at, last_used)
               VALUES (?,?,?,?,?,?,?,?, 'active', ?,?,?)
               ON CONFLICT(open_id) DO UPDATE SET
                 union_id=excluded.union_id, name=excluded.name,
                 access_enc=excluded.access_enc, refresh_enc=excluded.refresh_enc,
                 access_exp=excluded.access_exp, refresh_exp=excluded.refresh_exp,
                 scopes=excluded.scopes, status='active',
                 updated_at=excluded.updated_at""",
            (open_id, info.get("union_id", ""), info.get("name", ""),
             crypto.encrypt(access_token), crypto.encrypt(refresh_token),
             now + expires_in * 1000, now + refresh_expires_in * 1000,
             scopes, now, now, now),
        )
    return info


def _save_refreshed(open_id: str, out: dict) -> str:
    """Ghi lại token mới sau khi refresh. Trả access token mới."""
    access = out["access_token"]
    refresh = out.get("refresh_token")           # có thể xoay vòng
    now = _now_ms()
    with db.tx() as c:
        if refresh:
            c.execute(
                """UPDATE tokens SET access_enc=?, refresh_enc=?,
                   access_exp=?, refresh_exp=?, updated_at=?, status='active'
                   WHERE open_id=?""",
                (crypto.encrypt(access), crypto.encrypt(refresh),
                 now + int(out.get("expires_in", 7200)) * 1000,
                 now + int(out.get("refresh_token_expires_in",
                                   30 * 86400)) * 1000,
                 now, open_id),
            )
        else:
            c.execute(
                """UPDATE tokens SET access_enc=?, access_exp=?, updated_at=?,
                   status='active' WHERE open_id=?""",
                (crypto.encrypt(access),
                 now + int(out.get("expires_in", 7200)) * 1000, now, open_id),
            )
    return access


def get_access_token(open_id: str) -> str:
    """Trả access token còn hạn cho open_id, tự refresh nếu cần.

    Ném TokenError nếu người đó chưa enroll, đã revoke, hoặc refresh token
    hết hạn (cần enroll lại).
    """
    with _lock:
        row = db.conn().execute(
            "SELECT * FROM tokens WHERE open_id=?", (open_id,)).fetchone()
        if not row:
            raise TokenError(f"{open_id} chưa enroll")
        if row["status"] == "revoked":
            raise TokenError(f"{open_id} đã bị thu hồi")

        now = _now_ms()
        if now >= row["refresh_exp"]:
            with db.tx() as c:
                c.execute("UPDATE tokens SET status='expired' WHERE open_id=?",
                          (open_id,))
            raise TokenError(f"{open_id} refresh token hết hạn, cần enroll lại")

        # Access còn hạn -> dùng luôn
        if now < row["access_exp"] - _REFRESH_MARGIN * 1000:
            access = crypto.decrypt(row["access_enc"])
        else:
            # Refresh, xoay vòng, lưu lại NGAY
            refresh = crypto.decrypt(row["refresh_enc"])
            out = lark_api.refresh_user_token(refresh)
            access = _save_refreshed(open_id, out)

        with db.tx() as c:
            c.execute("UPDATE tokens SET last_used=? WHERE open_id=?",
                      (now, open_id))
        return access


def list_users(active_only: bool = True) -> list[dict]:
    """Danh sách người đã enroll (không kèm token). Cho poller lặp qua."""
    q = "SELECT open_id, union_id, name, status, access_exp, refresh_exp, " \
        "last_used FROM tokens"
    if active_only:
        q += " WHERE status='active'"
    return [dict(r) for r in db.conn().execute(q).fetchall()]


def revoke(open_id: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE tokens SET status='revoked' WHERE open_id=?",
                  (open_id,))


def auth_report() -> str:
    """Chuỗi in lúc khởi động: ai còn bao lâu, ai cần enroll lại."""
    users = list_users(active_only=False)
    if not users:
        return "  (chưa có ai enroll)"
    now = _now_ms()
    lines = []
    for u in users:
        days = (u["refresh_exp"] - now) / 86_400_000
        flag = "OK" if days > 7 else ("SẮP HẾT" if days > 0 else "HẾT HẠN")
        lines.append(f"  {u['name'] or u['open_id']:<24} "
                     f"{u['status']:<8} refresh còn {days:5.1f} ngày  [{flag}]")
    return "\n".join(lines)
