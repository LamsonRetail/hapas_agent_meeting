"""
CRUD hàng đợi job trên SQLite. Máy trạng thái (cửa duyệt đã bỏ 30/07/2026):

    detected → queued → transcribing → recapping → delivered
                  ↑                            ↘ failed  (quá MAX_ATTEMPTS)
                  └── gửi hỏng cho TẤT CẢ: về queued, phát lại từ transcript
                      đã lưu (orchestrator._reuse), không phiên âm lại.

DI SẢN, job mới không bao giờ vào nữa: `awaiting_approval` (db._migrate trả về
queued), `owner_only`, `expired`, `discarded`. Vẫn liệt kê ở TERMINAL và ở các
bảng đếm để job cũ trong DB không bị xử lý lại hay biến mất khỏi báo cáo.

Job là bản ghi bền (không phải RAM) để khôi phục sau khi tiến trình chết.
"""

from __future__ import annotations

import json
import time

from . import db
from .models import Attendee, MeetingMeta

# Trạng thái được coi là "đã xong / không cần xử lý lại" khi quét.
TERMINAL = {"delivered", "owner_only", "expired", "discarded"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _meta_to_json(meta: MeetingMeta) -> str:
    return json.dumps({
        "minute_token": meta.minute_token, "title": meta.title,
        "start": meta.start, "end": meta.end, "duration_sec": meta.duration_sec,
        "owner_open_id": meta.owner_open_id, "owner_name": meta.owner_name,
        "app_link": meta.app_link, "participants_source": meta.participants_source,
        "attendees": [{"open_id": a.open_id, "union_id": a.union_id,
                       "name": a.name} for a in meta.attendees],
    }, ensure_ascii=False)


def meta_to_json(meta: MeetingMeta) -> str:
    """Công khai `_meta_to_json` — `orchestrator._maybe_reresolve` cần nó để
    nhân bản meta (serialize rồi parse lại) trước khi tra lại người dự."""
    return _meta_to_json(meta)


def meta_from_json(s: str) -> MeetingMeta:
    d = json.loads(s)
    m = MeetingMeta(
        minute_token=d["minute_token"], title=d.get("title", ""),
        start=d.get("start"), end=d.get("end"),
        duration_sec=d.get("duration_sec"),
        owner_open_id=d.get("owner_open_id", ""),
        owner_name=d.get("owner_name", ""), app_link=d.get("app_link", ""),
        participants_source=d.get("participants_source", ""),
    )
    m.attendees = [Attendee(**a) for a in d.get("attendees", [])]
    return m


def create(meta: MeetingMeta, status: str = "queued") -> None:
    """Ghi job mới (hoặc bỏ qua nếu đã có)."""
    now = _now_ms()
    with db.tx() as c:
        c.execute(
            """INSERT OR IGNORE INTO jobs
               (minute_token, title, start_ts, owner_open_id, invitee_count,
                meta_json, status, detected_at, queued_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (meta.minute_token, meta.title,
             int(meta.start * 1000) if meta.start else None,
             meta.owner_open_id, meta.invitee_count, _meta_to_json(meta),
             status, now, now),
        )


def set_status(minute_token: str, status: str, *, error: str | None = None,
               **fields) -> None:
    """Đổi trạng thái + set kèm các cột runtime (transcript_path, recap_json,
    audio_seconds, whisper_seconds, approval_msg_id, *_at...)."""
    cols = ["status=?"]
    vals: list = [status]
    if error is not None:
        cols.append("error=?"); vals.append(error)
    for k, v in fields.items():
        cols.append(f"{k}=?"); vals.append(v)
    vals.append(minute_token)
    with db.tx() as c:
        c.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE minute_token=?",
                  vals)


def update_meta(minute_token: str, meta: MeetingMeta) -> None:
    """Ghi đè `meta_json` (+ `invitee_count`) của một job đã tồn tại.

    Chỉ có MỘT chỗ gọi: `orchestrator._maybe_reresolve`. Trước 02/08/2026 không
    có hàm này, và đó chính là vấn đề: `meta` được chốt đúng một lần lúc
    `enqueue_minute` rồi đông cứng. Một cú `LarkError` thoáng qua khi đọc lịch
    là job mang danh sách người dự sai VĨNH VIỄN — không lệnh nào, không vòng
    nào tra lại.

    KHÔNG đụng `status`/`attempts`: đây là sửa DỮ LIỆU của job, không phải một
    bước trong máy trạng thái.
    """
    with db.tx() as c:
        c.execute("UPDATE jobs SET meta_json=?, invitee_count=? "
                  "WHERE minute_token=?",
                  (_meta_to_json(meta), meta.invitee_count, minute_token))


def bump_attempts(minute_token: str) -> int:
    with db.tx() as c:
        c.execute("UPDATE jobs SET attempts=attempts+1 WHERE minute_token=?",
                  (minute_token,))
    row = db.conn().execute(
        "SELECT attempts FROM jobs WHERE minute_token=?", (minute_token,)
    ).fetchone()
    return row["attempts"] if row else 0


def get(minute_token: str) -> dict | None:
    row = db.conn().execute(
        "SELECT * FROM jobs WHERE minute_token=?", (minute_token,)).fetchone()
    return dict(row) if row else None


def unbump_attempts(minute_token: str) -> None:
    """Trả lại một lần thử đã cộng oan (lỗi hạ tầng, không phải lỗi job).

    Vì sao không chỉ đơn giản là "đừng cộng": `process_queue` phải cộng TRƯỚC khi
    làm việc để một job hỏng-liên-tục không quay vòng vô hạn. Chỉ tới lúc bắt
    được `TranscribeUnavailable` mới biết đó là hạ tầng, nên trả lại ở đó.
    """
    with db.tx() as c:
        c.execute("UPDATE jobs SET attempts=MAX(attempts-1, 0) "
                  "WHERE minute_token=?", (minute_token,))


def bump_recap_fails(minute_token: str) -> int:
    """Cộng số lần gọi LLM hỏng LIÊN TIẾP của một job. Trả số mới.

    Đếm RIÊNG khỏi `attempts` có chủ ý: LLM hỏng là lỗi hạ tầng nên không được
    tiêu quota `MAX_ATTEMPTS` (job sẽ `failed` oan), nhưng cũng không được hoãn
    vô hạn — hết `RECAP_MAX_TRIES` thì phát bản không có recap, vì transcript
    vẫn đáng gửi hơn là im lặng mãi.
    """
    with db.tx() as c:
        c.execute("UPDATE jobs SET recap_fails=COALESCE(recap_fails,0)+1 "
                  "WHERE minute_token=?", (minute_token,))
    row = db.conn().execute(
        "SELECT recap_fails FROM jobs WHERE minute_token=?", (minute_token,)
    ).fetchone()
    return (row["recap_fails"] or 0) if row else 0


def reset_recap_fails(minute_token: str) -> None:
    """Recap thành công -> xoá bộ đếm. LIÊN TIẾP nghĩa là phải đặt lại, không
    thì một job phát lại nhiều lần sẽ cộng dồn lỗi của những lần cách xa nhau."""
    with db.tx() as c:
        c.execute("UPDATE jobs SET recap_fails=0 WHERE minute_token=?",
                  (minute_token,))


def all_jobs() -> list[dict]:
    """Mọi job, mới nhất trước. Dùng cho việc quét lại toàn bộ (vd base-sync)."""
    rows = db.conn().execute(
        "SELECT * FROM jobs ORDER BY detected_at DESC").fetchall()
    return [dict(r) for r in rows]


def by_status(*statuses: str) -> list[dict]:
    q = ",".join("?" * len(statuses))
    rows = db.conn().execute(
        f"SELECT * FROM jobs WHERE status IN ({q}) ORDER BY detected_at",
        statuses).fetchall()
    return [dict(r) for r in rows]


def delivery_counts(minute_token: str, kind: str = "recap") -> tuple[int, int]:
    """(số người nhận được, số người gửi hỏng) cho một loại tin của một job.

    Đếm theo NGƯỜI (distinct recipient), không theo dòng: phát lại một job thì
    cùng một người có nhiều dòng, đếm dòng sẽ thổi phồng con số. Một người vừa
    có dòng hỏng vừa có dòng ok thì tính là ĐÃ NHẬN — họ có biên bản trong tay.
    """
    rows = db.conn().execute(
        "SELECT recipient, MAX(ok) AS got FROM deliveries "
        "WHERE minute_token=? AND kind=? GROUP BY recipient",
        (minute_token, kind),
    ).fetchall()
    ok = sum(1 for r in rows if r["got"])
    return ok, len(rows) - ok


def pending_recipients(minute_token: str, kind: str = "recap",
                       max_tries: int = 3) -> list[str]:
    """Người mà MỌI lần gửi `kind` đều hỏng, và chưa quá `max_tries` lần thử.

    Vì sao cần (02/08/2026): `_deliver_now` chỉ giữ job ở `queued` khi gửi hỏng
    cho TẤT CẢ (`failed and not sent`). Hỏng MỘT PHẦN — 3 người nhận được, 1
    người 429 — thì job vẫn thành `delivered` và người đó KHÔNG BAO GIỜ nhận
    được biên bản, dù dòng `ok=0` nằm sẵn trong bảng này. Base và recap đều đã
    có đường vá cho "bước phụ hỏng mà việc chính vẫn xong"; riêng việc PHÁT thì
    chưa, và nó là bước duy nhất người dùng thật sự nhìn thấy.

    `MAX(ok)=0` = chưa lần nào thành công. Có một dòng `ok=1` là thôi, kể cả khi
    trước đó hỏng vài lần — đã tới tay rồi thì không gửi lại (tin trùng).
    """
    rows = db.conn().execute(
        """SELECT recipient, COUNT(*) AS tries
             FROM deliveries
            WHERE minute_token = ? AND kind = ?
         GROUP BY recipient
           HAVING MAX(ok) = 0 AND tries < ?""",
        (minute_token, kind, max_tries)).fetchall()
    return [r["recipient"] for r in rows]


def record_delivery(minute_token: str, recipient: str, kind: str,
                    ok: bool, error: str = "") -> None:
    with db.tx() as c:
        c.execute(
            """INSERT INTO deliveries
               (minute_token, recipient, kind, sent_at, ok, error)
               VALUES (?,?,?,?,?,?)""",
            (minute_token, recipient, kind, _now_ms(), 1 if ok else 0, error),
        )
