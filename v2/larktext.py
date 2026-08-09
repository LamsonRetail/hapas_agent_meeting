"""Bản chép sẵn của Lark cho một cuộc họp — đọc được NGAY, không phải chờ whisper.

Vì sao module này tồn tại (user báo 07/08/2026, kèm hai ảnh chụp chat): người
dùng bấm vào link Lark Minutes thì thấy **đủ chữ**, nhưng hỏi bot thì bot nói
"chưa có biên bản — đang phiên âm" rồi từ chối phân tích. Với họ đó là bot mù:
dữ liệu bày ra trước mắt mà hệ thống bảo không có.

Nguyên nhân không phải quyền, mà là bot chưa từng ĐI LẤY. `lark_api.
minutes_transcript()` nằm trong repo từ đầu và chỉ được gọi đúng MỘT chỗ —
`orchestrator._notify_minute`, lúc cuộc họp vừa được phát hiện, bằng ĐÚNG MỘT
token (người tình cờ tìm ra minute). Token đó thường không phải chủ bản ghi nên
lời gọi hỏng, `minute_text` thành rỗng, và từ đó về sau không ai hỏi lại nữa.

Đo thật ngày 07/08/2026 trên chính DB này (chỉ đếm ký tự, không in nội dung):

| Nhóm job | Đọc được bản Lark |
|---|---|
| 12 cuộc gần nhất ĐÃ có whisper | **12/12** — từ 188 tới 124.915 ký tự |
| 27 cuộc `waiting_auth` | 0/27 — chủ bản ghi chưa kết nối |
| 6 cuộc `failed` (chủ đã kết nối) | 6/6 |

Kết luận rút ra, và là toàn bộ lý lẽ của module này: **hễ chủ bản ghi đã kết
nối thì bản chép của Lark đọc được ngay khi họp xong** — tức trong suốt khoảng
whisper đang chạy (vài phút tới ~15 phút cho cuộc dài), thứ bot cần để trả lời
đã nằm sẵn ở đó. Cuộc `waiting_auth` thì vẫn tắc, đúng như §11c.2 của
`docs/CURRENT_CONTEXT.md`: không có đường vòng kỹ thuật nào, và module này
KHÔNG cố mở đường đó.

Ranh giới phải giữ:

* **Không phải cửa quyền.** Module này không hỏi ai được xem gì. Caller
  (`qa`, `sendfile`) phải qua `qa._may_see` TRƯỚC khi gọi vào đây. Một luật
  quyền thứ hai song song là thứ §7 cấm.
* **Không thay bản nguyên văn whisper.** Bản Lark không có tên người dự nhồi
  vào prompt, không có glossary đã duyệt, và không nằm trên đĩa nhà mình. Nó là
  bản ĐỌC TẠM để trả lời ngay; bản chuẩn vẫn là whisper. `qa` nói rõ điều đó
  cho người dùng thay vì lặng lẽ tráo hai thứ.
* **Chỉ đọc.** Không enqueue, không đổi status, không gửi tin. Cột duy nhất ghi
  vào `jobs` là ba cột cache của chính module này.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import config, db, lark_api, tokenstore
from .models import MeetingMeta

# Lấy hỏng thì bao lâu mới thử lại. Bản chép của Lark KHÔNG đổi sau khi cuộc họp
# kết thúc, nên lấy được một lần là xong vĩnh viễn — chỉ nhánh HỎNG mới cần nhịp
# thử lại. 30 phút là để bắt đúng ca hay gặp nhất: chủ bản ghi vừa kết nối xong,
# hoặc Lark chưa dựng xong bản chép ngay sau khi họp tan.
RETRY_AFTER_S = 30 * 60

# Cuộc dài 2 tiếng cho ra ~125.000 ký tự (đo thật). Đổ ngần đó vào prompt agent
# là vừa tốn vừa vô ích, nên `qa` cắt phần y như nguyên văn whisper. Hằng số này
# chỉ chặn ca bệnh lý (Lark trả một file khổng lồ) để khỏi ôm hết vào RAM.
MAX_CHARS = 400_000


class LarkTextError(RuntimeError):
    """Không lấy được bản chép của Lark. Caller quyết cách nói với người dùng."""


def path_of(minute_token: str) -> Path:
    """Chỗ nằm của bản Lark trên đĩa. TÍNH được, không phải đi tìm."""
    return config.TRANSCRIPT_DIR / f"lark-{minute_token}.txt"


def cached(minute_token: str) -> str:
    """Bản đã tải về, "" nếu chưa có. KHÔNG chạm mạng.

    Đọc thẳng từ đĩa chứ không tin cột `lark_chars`: file mới là dữ liệu, cột
    chỉ là sổ sách. Xoá file để buộc tải lại là một thao tác vận hành hợp lệ và
    nó phải có tác dụng ngay.
    """
    p = path_of(minute_token)
    try:
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[larktext] đọc cache {p.name} hỏng: {exc}")
        return ""


def _note(minute_token: str, chars: int, ok: bool) -> None:
    """Ghi sổ lần thử vào `jobs`. Hỏng thì im — sổ sách không được làm hỏng việc."""
    try:
        with db.tx() as c:
            c.execute(
                "UPDATE jobs SET lark_chars=?, lark_at=?, lark_tried_at=? "
                "WHERE minute_token=?",
                (chars if ok else None, int(time.time() * 1000) if ok else None,
                 int(time.time() * 1000), minute_token),
            )
    except Exception as exc:                       # noqa: BLE001 — xem docstring
        print(f"[larktext] ghi sổ {minute_token} hỏng (bỏ qua): {exc}")


def _tried_recently(minute_token: str) -> bool:
    """Vừa thử và hỏng trong `RETRY_AFTER_S` gần đây chưa.

    Chống việc mỗi câu hỏi của người dùng lại nã một loạt lời gọi Lark cho một
    cuộc mà ta đã biết là không đọc được. Cuộc `waiting_auth` có tới 21 ứng
    viên token — không có phanh này thì một câu hỏi là 21 lời gọi mạng, và câu
    trả lời cho người dùng chậm đi vài giây mà chẳng đổi kết quả.
    """
    try:
        row = db.conn().execute(
            "SELECT lark_tried_at FROM jobs WHERE minute_token=?",
            (minute_token,)).fetchone()
    except Exception:                              # noqa: BLE001
        return False
    at = (row["lark_tried_at"] if row else None) or 0
    return bool(at) and (time.time() * 1000 - at) < RETRY_AFTER_S * 1000


def fetch(meta: MeetingMeta) -> str:
    """Tải bản chép của Lark, THỬ TỪNG token ứng viên tới khi có ai đọc được.

    Ném `LarkTextError` khi không ai đọc được. Trả "" thì không bao giờ xảy ra:
    Lark trả chuỗi rỗng cũng bị coi là không đọc được, vì một bản rỗng và một
    lần gọi hỏng có cùng hậu quả với người đang hỏi.

    Dùng LẠI `pipeline._reader_candidates` chứ không tự dựng danh sách riêng.
    Quyền đọc bản chép và quyền tải bản ghi là cùng một trần quyền của Lark
    (§11c.2: cả hai đều `2091005` với người không phải chủ), nên hai danh sách
    lệch nhau chỉ tạo ra hai kiểu "vì sao cuộc này đọc được mà cuộc kia không"
    phải chẩn riêng.
    """
    from . import pipeline                          # vòng import: pipeline -> qa -> đây
    token = meta.minute_token
    codes: list[str] = []
    for oid in pipeline._reader_candidates(meta):
        try:
            access = tokenstore.get_access_token(oid)
        except tokenstore.TokenError:
            codes.append("chưa kết nối")
            continue
        try:
            text = lark_api.minutes_transcript(access, token)
        except lark_api.LarkError as exc:
            codes.append(str(exc.code))
            continue
        except Exception as exc:                    # noqa: BLE001 — mạng/5xx
            codes.append(type(exc).__name__)
            continue
        text = (text or "").strip()
        if not text:
            codes.append("rỗng")
            continue
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
        try:
            path_of(token).write_text(text, encoding="utf-8")
        except OSError as exc:
            # Ghi cache hỏng KHÔNG được làm hỏng câu trả lời: ta đã có chữ trong
            # tay, chỉ là lần sau phải tải lại.
            print(f"[larktext] lưu cache {token} hỏng (bỏ qua): {exc}")
        _note(token, len(text), ok=True)
        print(f"[larktext] {token}: {len(text)} ký tự (mượn quyền {oid[:12]}…)")
        return text
    _note(token, 0, ok=False)
    raise LarkTextError(
        f"không token nào đọc được bản chép của Lark cho {token} "
        f"({len(codes)} ứng viên: {', '.join(codes[:6]) or 'không có ai'})")


def get(row: dict[str, Any], *, allow_network: bool = True) -> str:
    """Bản chép của Lark cho một job. "" nếu không có và không lấy được.

    KHÔNG ném: đây là đường phục vụ câu hỏi của người dùng, và thiếu bản Lark
    chỉ có nghĩa là câu trả lời nghèo hơn, không phải câu trả lời hỏng.

    `allow_network=False` để nơi nào chỉ muốn biết "đã có sẵn chưa" (vd dựng
    danh sách hàng chục cuộc) không kéo theo hàng chục lời gọi mạng.
    """
    token = str(row.get("minute_token") or "")
    if not token:
        return ""
    if (hit := cached(token)):
        return hit
    if not allow_network or _tried_recently(token):
        return ""
    try:
        meta = _meta_of(row)
    except Exception as exc:                        # noqa: BLE001
        print(f"[larktext] {token}: meta hỏng, bỏ qua ({exc})")
        return ""
    try:
        return fetch(meta)
    except LarkTextError as exc:
        print(f"[larktext] {exc}")
        return ""
    except Exception as exc:                        # noqa: BLE001 — xem docstring
        print(f"[larktext] {token} lấy hỏng (bỏ qua): {exc}")
        return ""


def _meta_of(row: dict[str, Any]) -> MeetingMeta:
    from . import jobstore
    return jobstore.meta_from_json(row["meta_json"])


def have(minute_token: str) -> bool:
    """Đã có bản Lark trên đĩa chưa. Không chạm mạng — dùng để gắn nhãn danh sách."""
    return bool(cached(minute_token))
