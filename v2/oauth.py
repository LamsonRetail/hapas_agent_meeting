"""
Luồng enroll qua OAuth: sinh nonce -> authorize URL -> nhận code -> đổi token.

state trong authorize URL PHẢI là nonce một lần dùng, KHÔNG phải open_id trần
(V2_ARCHITECTURE §4[0]): dùng open_id trần thì ai đoán được cũng gọi được
callback. Nonce buộc callback phải khớp đúng phiên enroll vừa mở.

Module này không quan tâm code tới bằng đường nào (callback local, hộp thư
Vercel, hay tunnel) — nơi nào nhận được code + state thì gọi complete().
"""

from __future__ import annotations

import secrets
import time

from . import config, db, lark_api, tokenstore


def _now_ms() -> int:
    return int(time.time() * 1000)


def start(open_id: str = "") -> tuple[str, str]:
    """Mở một phiên enroll. Trả (authorize_url, nonce).

    open_id để trống được (enroll người lạ mới nhắn bot chưa biết open_id);
    khi đó chỉ ràng buộc bằng nonce.
    """
    nonce = secrets.token_urlsafe(24)
    with db.tx() as c:
        c.execute("DELETE FROM oauth_nonce WHERE expires_at < ?", (_now_ms(),))
        c.execute(
            "INSERT INTO oauth_nonce(nonce, open_id, expires_at) VALUES (?,?,?)",
            (nonce, open_id, _now_ms() + config.OAUTH_NONCE_TTL * 1000),
        )
    return lark_api.authorize_url(nonce), nonce


def complete(code: str, state: str) -> dict:
    """Hoàn tất enroll: kiểm nonce, đổi code lấy token, lưu. Trả user info.

    Ném RuntimeError nếu nonce sai/hết hạn (chống callback giả mạo).
    """
    row = db.conn().execute(
        "SELECT open_id, expires_at FROM oauth_nonce WHERE nonce=?", (state,)
    ).fetchone()
    if not row:
        raise RuntimeError("state không hợp lệ (nonce không tồn tại)")
    if _now_ms() > row["expires_at"]:
        with db.tx() as c:
            c.execute("DELETE FROM oauth_nonce WHERE nonce=?", (state,))
        raise RuntimeError("phiên enroll đã hết hạn, bấm lại link")

    out = lark_api.exchange_code(code)
    access = out["access_token"]
    refresh = out.get("refresh_token")
    if not refresh:
        raise RuntimeError(
            "Lark không trả refresh_token. Gần như chắc chắn thiếu scope "
            "'offline_access' trong OAUTH_SCOPES (v2/.env). Thêm vào, chạy lại "
            "`python -m v2 enroll-url`, bấm Đồng ý lần nữa rồi complete.")
    info = tokenstore.enroll(
        access, refresh,
        int(out.get("expires_in", 7200)),
        int(out.get("refresh_token_expires_in", 30 * 86400)),
        scopes=out.get("scope", ""),
    )

    with db.tx() as c:
        c.execute("DELETE FROM oauth_nonce WHERE nonce=?", (state,))
    return info


# =====================================================================
#  Kéo code từ hộp thư Vercel (enroll tự phục vụ)
# =====================================================================
#
# Chiều gọi vẫn là LOCAL -> VERCEL, Vercel không bao giờ gọi vào máy này
# (V2_ARCHITECTURE §3). Vercel giữ {code,state} trong Blob với TTL ngắn; máy local
# là chỗ duy nhất có app_secret nên cũng là chỗ duy nhất đổi được code lấy token.
# Đọc xong thì Vercel xoá luôn (code của Lark dùng-một-lần, đọc hai lần vô nghĩa).


def poll_pending(*, notify: bool = True) -> list[dict]:
    """Lấy các code đang chờ ở Vercel rồi enroll. Trả danh sách người vừa xong.

    Lỗi mạng KHÔNG ném ra ngoài: hàm này chạy trong vòng `run`, một cú Vercel
    502 không được phép làm chết orchestrator.
    """
    import httpx

    if not (config.OAUTH_PULL_URL and config.STATUS_PUSH_SECRET):
        return []

    try:
        resp = httpx.get(
            config.OAUTH_PULL_URL,
            headers={"Authorization": f"Bearer {config.STATUS_PUSH_SECRET}"},
            timeout=config.STATUS_PUSH_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"[enroll] hộp thư trả {resp.status_code}: {resp.text[:120]}")
            return []
        items = (resp.json() or {}).get("pending") or []
    except Exception as exc:      # noqa: BLE001 — mạng hỏng không được làm chết vòng run
        print(f"[enroll] không đọc được hộp thư: {exc}")
        return []

    done: list[dict] = []
    for it in items:
        code, state = it.get("code"), it.get("state")
        if not (code and state):
            continue
        try:
            info = complete(code, state)
        except Exception as exc:      # noqa: BLE001
            # Nonce hết hạn / code đã dùng: bỏ qua, không chặn người kế tiếp.
            print(f"[enroll] state={state[:8]}… bỏ qua: {exc}")
            continue

        name = info.get("name") or info.get("open_id")
        union_id = info.get("union_id", "")
        print(f"[enroll] ✓ {name} đã cấp quyền")
        if union_id:
            from . import gate
            gate.clear_invite(union_id)
            if notify:
                try:
                    lark_api.im_send_text(
                        union_id,
                        f"Xong rồi {name}! Giờ bạn hỏi về biên bản họp được.",
                        id_type="union_id",
                    )
                except lark_api.LarkError as exc:
                    print(f"[enroll] không nhắn được cho {name}: {exc}")
        done.append(info)
    return done
