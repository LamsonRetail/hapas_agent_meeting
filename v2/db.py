"""
SQLite trên máy local — token, khóa chống trùng, hàng đợi (V2_ARCHITECTURE §5).

Nguyên tắc: Bitable giữ thứ admin cần nhìn; SQLite giữ SECRET và KHÓA.
Không để refresh token trên Bitable (tài liệu chia sẻ, không mã hóa tầng app).

Chống trùng khi nhiều luồng cùng thấy một cuộc họp (§4.1): dùng
`INSERT OR IGNORE` trên khóa chính `minute_token` của bảng minutes_lock.
Ai insert được (rowcount==1) thì xử lý, còn lại bỏ qua. Đây là khóa thật,
thay cho poller_state.json của V1 vốn không an toàn đa luồng.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    open_id      TEXT PRIMARY KEY,
    union_id     TEXT,
    name         TEXT,
    access_enc   TEXT NOT NULL,
    refresh_enc  TEXT NOT NULL,
    access_exp   INTEGER NOT NULL,   -- epoch ms
    refresh_exp  INTEGER NOT NULL,   -- epoch ms
    scopes       TEXT,
    status       TEXT DEFAULT 'active',   -- active | expired | revoked
    enrolled_at  INTEGER,
    updated_at   INTEGER,
    last_used    INTEGER
);

-- Khóa chống trùng: mỗi minute chỉ được một luồng chiếm để xử lý.
CREATE TABLE IF NOT EXISTS minutes_lock (
    minute_token TEXT PRIMARY KEY,
    claimed_at   INTEGER
);

-- Lần ĐẦU TIÊN ta thấy một minute. Dùng để chờ SETTLE_MINUTES trước khi tra
-- người dự: hỏi vc recording quá sớm thì Lark chưa liên kết bản ghi với cuộc
-- họp và trả rỗng -> mất danh sách người được mời (V1 đã gặp).
-- Đếm từ lúc TA thấy, KHÔNG từ giờ Lark trả về: chuỗi mô tả không kèm múi giờ
-- và thực tế lệch ~1 tiếng, so sánh với ngưỡng 3 phút sẽ sai hoàn toàn.
CREATE TABLE IF NOT EXISTS minutes_seen (
    minute_token  TEXT PRIMARY KEY,
    first_seen_at INTEGER
);

-- Ai (trong số người ĐÃ ENROLL) nhìn thấy minute này trong `minutes/search`
-- của chính họ. Thêm 02/08/2026 cho luật "chỉ gửi cho người đã cấp quyền".
--
-- Vì sao đây là nguồn người nhận TỐT NHẤT, tốt hơn cả danh sách lịch:
-- `minutes_list` gọi với `participant_ids:[open_id]`, tức chính LARK khẳng định
-- người này có tham dự. Nó không phụ thuộc cuộc họp có được đặt qua Calendar
-- không, có mời từng người hay mời bằng group chat, hay có khớp được tên/giờ
-- không — đó đúng là bốn chỗ mà chuỗi tra người dự hay sót.
--
-- Và nó cho một BẤT BIẾN dễ kiểm: một minute chỉ vào được hệ thống qua vòng
-- quét của một người vừa tham dự vừa đã cấp quyền, nên người đó LUÔN có mặt ở
-- đây. Tức "phát mà không ai nhận" gần như không xảy ra được nữa.
--
-- Ghi cho MỌI người thấy nó, mỗi vòng quét, KỂ CẢ khi minute đã bị chiếm khóa:
-- người thứ hai thấy cùng cuộc họp không tạo job thứ hai (minutes_lock lo việc
-- đó) nhưng vẫn phải được ghi nhận là người nhận.
CREATE TABLE IF NOT EXISTS minute_viewers (
    minute_token TEXT NOT NULL,
    open_id      TEXT NOT NULL,
    union_id     TEXT,
    name         TEXT,
    seen_at      INTEGER,
    PRIMARY KEY (minute_token, open_id)
);

-- Nonce OAuth một lần dùng (state trong authorize URL). TTL ngắn.
CREATE TABLE IF NOT EXISTS oauth_nonce (
    nonce       TEXT PRIMARY KEY,
    open_id     TEXT,
    expires_at  INTEGER
);

-- Hàng đợi job. status theo máy trạng thái ở V2_ARCHITECTURE §5.
CREATE TABLE IF NOT EXISTS jobs (
    minute_token TEXT PRIMARY KEY,
    title        TEXT,
    start_ts     INTEGER,            -- epoch ms
    end_ts       INTEGER,
    owner_open_id TEXT,
    invitee_count INTEGER DEFAULT 0,
    meta_json    TEXT,               -- MeetingMeta serialize
    status       TEXT DEFAULT 'detected',
    attempts     INTEGER DEFAULT 0,
    recap_fails  INTEGER DEFAULT 0,   -- số lần gọi LLM hỏng LIÊN TIẾP (đếm riêng
                                      -- khỏi attempts: lỗi hạ tầng không tiêu quota)
    error        TEXT,
    transcript_path TEXT,
    recap_json   TEXT,
    approval_msg_id TEXT,            -- DI SẢN: luồng duyệt đã bỏ (xem _migrate)
    bitable_record_id TEXT,          -- record "nội dung đã chốt" trên Base
    detected_at  INTEGER,
    queued_at    INTEGER,
    transcribed_at INTEGER,
    delivered_at INTEGER,
    audio_seconds  REAL,
    whisper_seconds REAL
);

-- Người nhắn bot mà CHƯA enroll: đã mời lúc nào, bằng nonce nào.
-- Vì sao cần bảng riêng: người ta nhắn 5 câu liền thì không được gửi 5 cái link.
-- Khoá theo union_id vì đó là thứ Hermes đưa sang (SessionSource.user_id_alt =
-- union_id của Feishu) và cũng là thứ `tokens` có sẵn -> khớp được không cần
-- tra danh bạ.
CREATE TABLE IF NOT EXISTS enroll_invites (
    union_id   TEXT PRIMARY KEY,
    user_id    TEXT,              -- id kiểu 1fg8g36d, chỉ để đọc log
    name       TEXT,
    nonce      TEXT,
    sent_at    INTEGER,
    times      INTEGER DEFAULT 1
);

-- Mốc "đã báo cái này rồi" của v2/alerts.py. Chống spam: mỗi tình huống chỉ
-- báo MỘT lần cho tới khi tình trạng ĐỔI. Phải bền (không phải RAM) vì vòng
-- `run` bị restart là chuyện thường — mất mốc thì mỗi lần khởi động lại là một
-- cái DM nữa cho cùng một cái hỏng.
-- `value` là DẤU VÂN TAY của tình trạng, không phải nội dung tin: refresh_exp
-- của token, số attempts của job, mốc bắt đầu hỏng của whisper. Vân tay đổi =
-- tình trạng khác = được báo lại.
CREATE TABLE IF NOT EXISTS alert_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at INTEGER
);

-- "Vé phiên" của người đang hỏi bot (v2/askers.py). Vì sao phải có bảng thay vì
-- truyền thẳng union_id: MCP server là MỘT tiến trình dùng chung, lời gọi tool
-- không mang danh tính, nên vé đi đường vòng qua tin nhắn (plugin Hermes chèn) và
-- V2 phải tra lại được vé đó. Ngẫu nhiên + hết hạn = người khác không mượn được.
CREATE TABLE IF NOT EXISTS qa_sessions (
    token      TEXT PRIMARY KEY,
    union_id   TEXT,
    open_id    TEXT,
    name       TEXT,
    expires_at INTEGER
);

-- Ghi từng lần gửi để truy 'ai nhận gì, lúc nào'.
CREATE TABLE IF NOT EXISTS deliveries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    minute_token TEXT,
    recipient    TEXT,               -- union_id
    kind         TEXT,               -- full | recap
    sent_at      INTEGER,
    ok           INTEGER,
    error        TEXT
);
"""

_local = threading.local()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30,
                           isolation_level=None)   # autocommit; ta tự BEGIN
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def conn() -> sqlite3.Connection:
    """Kết nối theo-thread (SQLite không cho chia sẻ conn giữa thread)."""
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect()
        _local.conn = c
    return c


def init() -> None:
    conn().executescript(_SCHEMA)
    _migrate()


def _migrate() -> None:
    """Vá dữ liệu cũ sang mô hình hiện tại. Phải idempotent (chạy mỗi lần init).

    30/07/2026 — bỏ cửa duyệt: không còn ai gửi thẻ duyệt nữa nên job đang
    `awaiting_approval` sẽ nằm đó vĩnh viễn. Trả chúng về `queued`;
    `process_queue` thấy đã có transcript+recap thì phát luôn, không phiên âm lại.
    Cột `approval_msg_id` giữ nguyên (SQLite bỏ cột rất phiền, và nó vô hại).

    CREATE TABLE IF NOT EXISTS không thêm cột vào bảng đã tồn tại — cột mới phải
    ALTER TABLE ở đây, nếu không DB cũ sẽ thiếu cột và mọi câu ghi đều lỗi.
    """
    with tx() as c:
        cur = c.execute("UPDATE jobs SET status='queued' "
                        "WHERE status='awaiting_approval'")
        if cur.rowcount:
            print(f"[db] {cur.rowcount} job đang chờ duyệt -> queued "
                  f"(cửa duyệt đã bỏ)")

        have = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        for col, decl in (("bitable_record_id", "TEXT"),
                          ("recap_fails", "INTEGER DEFAULT 0")):
            if col not in have:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")
                print(f"[db] thêm cột jobs.{col}")


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Giao dịch. IMMEDIATE để chiếm khóa ghi ngay, tránh race đọc-rồi-ghi."""
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
    except Exception:
        c.execute("ROLLBACK")
        raise
    else:
        c.execute("COMMIT")


# --------------------------------------------------- chống trùng minute


def try_claim_minute(minute_token: str) -> bool:
    """Chiếm quyền xử lý một minute. True nếu ta là người chiếm được.

    Đây là khóa thật thay cho seen_tokens của V1: an toàn cả khi có nhiều
    luồng/tiến trình cùng quét (một cuộc họp N người xuất hiện trong Minutes
    của cả N người).
    """
    with tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO minutes_lock(minute_token, claimed_at) "
            "VALUES (?, ?)",
            (minute_token, _now_ms()),
        )
        return cur.rowcount == 1


def release_minute(minute_token: str) -> None:
    """Nhả khóa để xử lý lại (vd retry sau khi hỏng)."""
    with tx() as c:
        c.execute("DELETE FROM minutes_lock WHERE minute_token=?",
                  (minute_token,))


def note_seen(minute_token: str) -> int:
    """Ghi mốc lần đầu thấy minute. Trả mốc CŨ nếu đã từng thấy (ms)."""
    now = _now_ms()
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO minutes_seen"
                  "(minute_token, first_seen_at) VALUES (?, ?)",
                  (minute_token, now))
    row = conn().execute(
        "SELECT first_seen_at FROM minutes_seen WHERE minute_token=?",
        (minute_token,)).fetchone()
    return row["first_seen_at"] if row else now


def clear_seen(minute_token: str) -> None:
    """Bỏ mốc chờ sau khi đã tạo job (jobs/minutes_lock giữ vai trò đó rồi)."""
    with tx() as c:
        c.execute("DELETE FROM minutes_seen WHERE minute_token=?",
                  (minute_token,))


def note_viewer(minute_token: str, open_id: str, union_id: str = "",
                name: str = "") -> None:
    """Ghi nhận: người đã enroll này nhìn thấy minute đó trong Minutes của họ.

    Idempotent (`INSERT OR IGNORE` trên khóa kép) — gọi mỗi vòng quét là bình
    thường. Giữ `seen_at` của LẦN ĐẦU, không cập nhật: nó trả lời "từ bao giờ ta
    biết người này có dự", câu hỏi hữu ích khi truy vì sao ai đó nhận/không nhận.
    """
    if not minute_token or not open_id:
        return
    with tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO minute_viewers"
            "(minute_token, open_id, union_id, name, seen_at) VALUES (?,?,?,?,?)",
            (minute_token, open_id, union_id, name, _now_ms()),
        )


def viewers_of(minute_token: str) -> list[dict]:
    """Người đã enroll mà Lark báo là có dự cuộc họp này (thứ tự thấy trước)."""
    rows = conn().execute(
        "SELECT open_id, union_id, name, seen_at FROM minute_viewers "
        "WHERE minute_token=? ORDER BY seen_at", (minute_token,)).fetchall()
    return [dict(r) for r in rows]


def viewers_all() -> dict[str, list[dict]]:
    """Như `viewers_of` nhưng cho MỌI minute, trong MỘT lời gọi.

    Có riêng hàm này vì `qa.viewers_index()` lặp qua toàn bộ `jobs`, và nó chạy
    HAI lần cho mỗi câu hỏi của bot (`_only_visible` và `pending_split` đều dựng
    lại chỉ mục). Gọi `viewers_of` trong vòng lặp là N+1 query — 5 cuộc họp thì
    không thấy gì, 500 cuộc là 2.000 query cho một tin nhắn.
    """
    out: dict[str, list[dict]] = {}
    for r in conn().execute(
            "SELECT minute_token, open_id, union_id, name, seen_at "
            "FROM minute_viewers ORDER BY seen_at").fetchall():
        out.setdefault(r["minute_token"], []).append(dict(r))
    return out


def is_claimed(minute_token: str) -> bool:
    row = conn().execute(
        "SELECT 1 FROM minutes_lock WHERE minute_token=?", (minute_token,)
    ).fetchone()
    return row is not None
