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

-- Checkpoint sau khi Lark đã đổi authorization code nhưng trước khi user_info/
-- ghi bảng tokens hoàn tất. Code OAuth dùng một lần: nếu máy chết ở khe này mà
-- không checkpoint, retry chỉ nhận "code đã dùng" và mất phiên. Token checkpoint
-- luôn mã hóa Fernet, chỉ ở local, và tự xóa theo nonce.
CREATE TABLE IF NOT EXISTS oauth_exchange_pending (
    state               TEXT PRIMARY KEY,
    access_enc          TEXT NOT NULL,
    refresh_enc         TEXT NOT NULL,
    expires_in          INTEGER NOT NULL,
    refresh_expires_in  INTEGER NOT NULL,
    scopes              TEXT,
    exchanged_at        INTEGER NOT NULL,
    FOREIGN KEY (state) REFERENCES oauth_nonce(nonce) ON DELETE CASCADE
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

-- Chống gửi lặp cho các tin trực tiếp không gắn với một meeting/delivery cụ thể
-- (hiện là list_meetings). Bền qua restart MCP/gateway; fingerprint là SHA-256,
-- không lưu thêm nội dung cuộc họp vào bảng điều khiển.
CREATE TABLE IF NOT EXISTS outbound_dedup (
    channel     TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    reserved_at INTEGER NOT NULL,
    PRIMARY KEY (channel, recipient)
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

-- Người ĐÃ HỎI transcript whisper của một cuộc chưa dịch xong. Vì sao bảng
-- riêng, không phải một cột: nhiều người có thể cùng hỏi một cuộc, "ai cần tự
-- gửi khi dịch xong" là DANH SÁCH. Job đó lên priority=2 (cực cao); phiên âm
-- xong thì `process_queue` gửi transcript cho từng người ở đây rồi xoá.
-- Khoá kép (cuộc, người) để hỏi lại không tạo dòng trùng.
CREATE TABLE IF NOT EXISTS transcript_requests (
    minute_token TEXT,
    requester    TEXT,               -- union_id người hỏi
    name         TEXT,
    requested_at INTEGER,
    PRIMARY KEY (minute_token, requester)
);

-- KÝ ỨC HỘI THOẠI (06/08/2026) — cuộc họp mà một người VỪA nhắc tới khi hỏi bot.
--
-- Vì sao cần một bảng, trong khi Hermes đã có lịch sử phiên: lịch sử đó chết khi
-- phiên tự đóng (im 1 tiếng, hoặc 4h sáng) và khi `hermes gateway restart`. Sau
-- mỗi lần đó, câu tiếp nối kiểu "gửi nguyên văn cuộc đó" mất hết chỗ dựa và bot
-- phải bắt người ta gõ lại cả tên cuộc họp — đúng cái người dùng gọi là "không
-- có ký ức".
--
-- Bảng này KHÔNG phải nguồn quyền và KHÔNG phải nguồn nội dung:
--   * chỉ ghi sau khi `qa._may_see` đã cho qua, nên nó không chứa gì mà chính
--     người đó chưa được xem — ghi vào đây không nới quyền cho ai;
--   * chỉ có token + tiêu đề + mốc thời gian. Không tóm tắt, không nguyên văn.
--     Câu trả lời vẫn phải gọi tool và vẫn qua ACL như trước;
--   * đọc ra để bơm vào prompt, agent dùng nó để HIỂU câu hỏi tiếp nối, không
--     phải để dựng câu trả lời.
-- Khoá kép (người, cuộc) nên nhắc lại một cuộc chỉ đẩy mốc, không sinh dòng mới.
CREATE TABLE IF NOT EXISTS chat_memory (
    union_id     TEXT NOT NULL,
    minute_token TEXT NOT NULL,
    title        TEXT,
    touched_at   INTEGER,
    PRIMARY KEY (union_id, minute_token)
);

-- Thuật ngữ/tên riêng ứng viên cho glossary whisper (part B, 03/08/2026).
-- Hermes trích sau mỗi cuộc -> pending; admin duyệt qua bot -> approved; từ
-- approved được nhồi ĐỘNG vào initial_prompt (KHÔNG ghi vi-prompt.txt server).
-- Khoá theo `term_key` (chữ thường, khử dấu cách) để 'MCP'/'mcp' không tách đôi;
-- `term` giữ chính tả hiển thị. `count` = số cuộc gặp (nguồn lọc nhiễu digest).
CREATE TABLE IF NOT EXISTS glossary_candidates (
    term_key   TEXT PRIMARY KEY,     -- lower(strip(term)) — khoá so khớp
    term       TEXT,                 -- chính tả hiển thị (giữ hoa/thường)
    count      INTEGER DEFAULT 1,    -- số cuộc gặp
    example    TEXT,                 -- TIÊU ĐỀ cuộc gặp gần nhất (không phải câu trích)
    status     TEXT DEFAULT 'pending',   -- pending | approved | rejected
    first_seen INTEGER,
    last_seen  INTEGER
);

-- Xác nhận biên bản của CHỦ cuộc họp (V3 YC1, docs/V3_SPECS.md). Một dòng /
-- cuộc họp. TÁCH khỏi `jobs.status` có chủ ý: whisper vẫn chạy nền theo máy
-- trạng thái cũ, còn đây chỉ trả lời "người dự đã được báo chưa, chủ đã duyệt
-- chưa". state: pending (chờ chủ) | confirmed (chủ duyệt) | auto_published
-- (quá hạn, đã phát kèm nhãn "chưa review" — chủ vẫn duyệt/sửa được sau).
-- `original_recap_json` = bản máy sinh, giữ để đo chất lượng (dashboard YC4).
CREATE TABLE IF NOT EXISTS confirmations (
    minute_token   TEXT PRIMARY KEY,
    owner_union_id TEXT,
    state          TEXT DEFAULT 'pending',
    version        INTEGER DEFAULT 1,     -- tăng mỗi lần nội dung đổi
    released_version INTEGER DEFAULT 0,   -- bản người dự đã nhận (0 = chưa)
    edits          INTEGER DEFAULT 0,     -- số lần chủ sửa
    diff_chars     INTEGER DEFAULT 0,     -- khoảng cách bản máy -> bản chủ chốt
    original_recap_json TEXT,
    sent_at        INTEGER,
    reminded_at    INTEGER,
    responded_at   INTEGER,
    released_at    INTEGER
);

-- Cây tổ chức (V3 YC2), đồng bộ từ Lark Contact bằng tenant token (`org.sync`).
-- Khoá open_id vì `jobs.meta_json` nói chuyện bằng open_id/union_id.
CREATE TABLE IF NOT EXISTS org_edges (
    open_id     TEXT PRIMARY KEY,
    union_id    TEXT,
    name        TEXT,
    leader_open_id TEXT,
    synced_at   INTEGER
);

-- Ai được xem biên bản cuộc nào — VẬT CHẤT HOÁ lúc phát (V3 YC2, chốt
-- 16/09/2026: đổi sếp thì quyền CŨ vẫn giữ). Ghi một lần, KHÔNG thu hồi khi
-- org đổi. `source` = audit tại chỗ: 'attendee' | 'owner' | 'chain:D>E>C' |
-- 'backfill:…'. `qa.viewers_index` cộng bảng này vào luật quyền duy nhất.
CREATE TABLE IF NOT EXISTS note_grants (
    minute_token TEXT NOT NULL,
    open_id      TEXT NOT NULL,
    union_id     TEXT,
    source       TEXT,
    granted_at   INTEGER,
    PRIMARY KEY (minute_token, open_id)
);

-- Folder Drive "space" của từng người (V3 YC2). Tạo bằng danh tính BOT.
CREATE TABLE IF NOT EXISTS drive_spaces (
    open_id      TEXT PRIMARY KEY,
    folder_token TEXT,
    created_at   INTEGER
);

-- File biên bản .md của từng cuộc họp: bản local (nguồn) + bản trên Drive.
-- `shared` = open_id đã được share trên Drive (JSON list) — để chỉ share phần MỚI.
CREATE TABLE IF NOT EXISTS note_files (
    minute_token TEXT PRIMARY KEY,
    local_path   TEXT,
    drive_token  TEXT,
    drive_url    TEXT,
    shared       TEXT DEFAULT '[]',
    updated_at   INTEGER
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
                          ("recap_fails", "INTEGER DEFAULT 0"),
                          # 2=cực cao (có người hỏi), 1=thường (cuộc mới),
                          # 0=backlog (7 ngày lúc enroll). Job cũ mặc định 1.
                          ("priority", "INTEGER DEFAULT 1"),
                          # Sổ sách cache bản chép sẵn của Lark (v2/larktext.py,
                          # 07/08/2026). Chữ THẬT nằm trên đĩa
                          # (`larktext.path_of`), ba cột này chỉ để biết đã lấy
                          # được chưa và lần thử gần nhất lúc nào — nhờ
                          # `lark_tried_at` mà một cuộc không đọc được không bị
                          # nã lại hàng chục lời gọi Lark ở MỖI câu hỏi.
                          ("lark_chars", "INTEGER"),
                          ("lark_at", "INTEGER"),
                          ("lark_tried_at", "INTEGER")):
            if col not in have:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {decl}")
                print(f"[db] thêm cột jobs.{col}")

        # 05/08/2026: thiếu người có token tải recording không phải job hỏng.
        # Park sáu job cũ (và mọi bản cùng hình dạng) để không retry nóng/DM mời
        # OAuth. Khi một người liên quan TỰ nhắn bot + OAuth, jobstore mới đánh
        # thức đúng job của họ về backlog priority 0.
        cur = c.execute(
            "UPDATE jobs SET status='waiting_auth', priority=0 "
            "WHERE status='failed' AND ("
            "error LIKE '%không có người dự nào đã enroll để đọc bản ghi%' OR "
            "(error LIKE '%không ai được phép tải bản ghi này%' AND "
            " error LIKE '%cấp quyền cho hệ thống%'))"
        )
        if cur.rowcount:
            print(f"[db] {cur.rowcount} job thiếu quyền -> waiting_auth "
                  f"(chờ người liên quan tự OAuth)")


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


# ------------------------------------------ chống gửi lặp bền qua process restart


def try_reserve_outbound(channel: str, recipient: str, fingerprint: str,
                         within_seconds: int) -> bool:
    """Đặt chỗ gửi nguyên tử. False nếu cùng nội dung vừa được đặt chỗ/gửi.

    Đặt chỗ trước network call để hai MCP process không cùng vượt qua phép kiểm.
    Caller phải gọi ``release_outbound`` khi gửi hỏng để lần sau được thử lại ngay.
    """
    now = _now_ms()
    cutoff = now - max(0, int(within_seconds)) * 1000
    with tx() as c:
        row = c.execute(
            "SELECT fingerprint, reserved_at FROM outbound_dedup "
            "WHERE channel=? AND recipient=?", (channel, recipient),
        ).fetchone()
        if (row and row["fingerprint"] == fingerprint
                and int(row["reserved_at"] or 0) >= cutoff):
            return False
        c.execute(
            "INSERT INTO outbound_dedup(channel, recipient, fingerprint, reserved_at) "
            "VALUES (?,?,?,?) ON CONFLICT(channel,recipient) DO UPDATE SET "
            "fingerprint=excluded.fingerprint, reserved_at=excluded.reserved_at",
            (channel, recipient, fingerprint, now),
        )
        return True


def release_outbound(channel: str, recipient: str, fingerprint: str) -> None:
    """Bỏ đúng reservation vừa tạo khi network call hỏng; không xoá lượt mới hơn."""
    with tx() as c:
        c.execute(
            "DELETE FROM outbound_dedup WHERE channel=? AND recipient=? "
            "AND fingerprint=?", (channel, recipient, fingerprint),
        )


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
    """Ghi nhận: token người này tìm/đọc được minute đó.

    KHÔNG phải bằng chứng tham dự và không được dùng cho ACL/người nhận. Lark
    Minutes Search có thể trả cả bản ghi được chia sẻ của người khác.

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
    """Người đã enroll từng tìm/đọc được minute này (thứ tự thấy trước)."""
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


# --- Yêu cầu transcript whisper (auto-gửi khi dịch xong) -------------

def add_transcript_request(minute_token: str, requester: str,
                           name: str = "") -> None:
    """Ghi 'người này đã hỏi transcript cuộc này'. Hỏi lại không tạo dòng trùng."""
    with tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO transcript_requests"
            "(minute_token, requester, name, requested_at) VALUES (?,?,?,?)",
            (minute_token, requester, name, _now_ms()),
        )


def transcript_requesters(minute_token: str) -> list[dict]:
    """Người đang chờ transcript của cuộc này (union_id + tên), cũ trước."""
    rows = conn().execute(
        "SELECT requester, name FROM transcript_requests "
        "WHERE minute_token=? ORDER BY requested_at", (minute_token,)).fetchall()
    return [dict(r) for r in rows]


def clear_transcript_requests(minute_token: str) -> None:
    """Đã gửi xong cho mọi người hỏi -> xoá, để lần dịch sau không gửi lại."""
    with tx() as c:
        c.execute("DELETE FROM transcript_requests WHERE minute_token=?",
                  (minute_token,))


# --- Ký ức hội thoại: cuộc họp người này VỪA nhắc tới ----------------
#
# Đọc chú thích bảng `chat_memory` trong _SCHEMA trước khi sửa: đây không phải
# nguồn quyền, không phải nguồn nội dung, và không được biến thành hai thứ đó.

# Ký ức cũ hơn ngần này thì không còn giúp hiểu câu hỏi tiếp nối nữa, chỉ làm
# prompt dài và làm bot nhắc tới một cuộc họp người ta đã quên. Tự dọn lúc ghi,
# không cần cron riêng.
CHAT_MEMORY_TTL_DAYS = 7


def remember_meeting(union_id: str, minute_token: str, title: str = "") -> None:
    """Ghi 'người này vừa nhắc tới cuộc họp đó'. Gọi SAU khi đã kiểm quyền.

    Idempotent theo cặp (người, cuộc): nhắc lại chỉ đẩy `touched_at`, nên bảng
    lớn theo số cuộc họp người ta thật sự hỏi, không theo số tin nhắn.
    """
    union_id = (union_id or "").strip()
    minute_token = (minute_token or "").strip()
    if not union_id or not minute_token:
        return
    now = _now_ms()
    cutoff = now - CHAT_MEMORY_TTL_DAYS * 86_400_000
    with tx() as c:
        c.execute("DELETE FROM chat_memory WHERE touched_at < ?", (cutoff,))
        c.execute(
            "INSERT INTO chat_memory(union_id, minute_token, title, touched_at) "
            "VALUES (?,?,?,?) ON CONFLICT(union_id, minute_token) DO UPDATE SET "
            "  title=COALESCE(NULLIF(excluded.title,''), chat_memory.title), "
            "  touched_at=excluded.touched_at",
            (union_id, minute_token, (title or "").strip(), now),
        )


def recent_meetings(union_id: str, limit: int = 3) -> list[dict]:
    """Cuộc họp người này vừa nhắc tới, mới nhất trước. Bỏ mục đã quá TTL.

    Lọc TTL khi đọc chứ không chỉ dựa vào lần dọn lúc ghi: người ngừng hỏi một
    tháng thì không có lần ghi nào để dọn, và ký ức cũ đó vẫn sẽ được bơm vào
    prompt của lần quay lại.
    """
    union_id = (union_id or "").strip()
    if not union_id:
        return []
    cutoff = _now_ms() - CHAT_MEMORY_TTL_DAYS * 86_400_000
    # Chốt phụ `rowid DESC`: `touched_at` chỉ tới mili-giây, và hai lần ghi
    # trong cùng một lượt xử lý VẪN trùng mốc thật (đã làm selftest chập chờn
    # ngay hôm thêm bảng này). Trùng mốc mà không có chốt phụ thì thứ tự do
    # SQLite tự chọn — tức "cuộc vừa nhắc tới" có thể ra sai, đúng thứ khối ký
    # ức tồn tại để trả lời.
    rows = conn().execute(
        "SELECT minute_token, title, touched_at FROM chat_memory "
        "WHERE union_id=? AND touched_at >= ? "
        "ORDER BY touched_at DESC, rowid DESC LIMIT ?",
        (union_id, cutoff, max(1, int(limit))),
    ).fetchall()
    return [dict(r) for r in rows]


def forget_meetings(union_id: str) -> int:
    """Xoá ký ức của một người (đường vận hành khi họ yêu cầu). Trả số dòng xoá."""
    with tx() as c:
        return c.execute("DELETE FROM chat_memory WHERE union_id=?",
                         (union_id,)).rowcount


# --- Glossary ứng viên (part B: whisper tự cải thiện) ---------------

def _term_key(term: str) -> str:
    """Khoá so khớp: chữ thường + gộp khoảng trắng. 'MCP' và ' mcp ' cùng khoá."""
    return " ".join((term or "").lower().split())


def glossary_add_candidate(term: str, example: str = "") -> None:
    """Ghi/tăng đếm một ứng viên. Gặp lại -> count+1, cập nhật ví dụ + last_seen.

    KHÔNG hồi sinh từ đã `rejected`: admin đã bảo không thì đừng nhét lại vào
    digest. `approved` gặp lại vẫn tăng count (vô hại) nhưng giữ nguyên status.
    """
    term = (term or "").strip()
    key = _term_key(term)
    if not key:
        return
    now = _now_ms()
    with tx() as c:
        row = c.execute(
            "SELECT status FROM glossary_candidates WHERE term_key=?", (key,)
        ).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO glossary_candidates"
                "(term_key, term, count, example, status, first_seen, last_seen)"
                " VALUES (?,?,1,?,'pending',?,?)",
                (key, term, example, now, now))
        elif row["status"] == "rejected":
            return                       # đã bị bỏ: không đếm lại, không nổi lại
        else:
            c.execute(
                "UPDATE glossary_candidates SET count=count+1, "
                "example=COALESCE(NULLIF(?,''), example), last_seen=? "
                "WHERE term_key=?", (example, now, key))


def glossary_list(status: str = "", min_count: int = 0) -> list[dict]:
    """Ứng viên theo status (rỗng = mọi status) và count tối thiểu, nhiều-cuộc trước."""
    q = "SELECT term, count, example, status FROM glossary_candidates WHERE count>=?"
    args: list = [min_count]
    if status:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY count DESC, last_seen DESC"
    return [dict(r) for r in conn().execute(q, args).fetchall()]


def glossary_get(term: str) -> dict | None:
    """Một ứng viên theo khoá so khớp, None nếu không có. Để caller đọc `status`
    TRƯỚC khi đổi (vd: cảnh báo 'từ này trước đã bị bỏ')."""
    key = _term_key(term)
    if not key:
        return None
    row = conn().execute(
        "SELECT term, count, example, status FROM glossary_candidates "
        "WHERE term_key=?", (key,)).fetchone()
    return dict(row) if row else None


def glossary_approved_terms(limit: int = 1000) -> list[str]:
    """Các từ đã duyệt (chính tả hiển thị), nhồi vào prompt whisper."""
    rows = conn().execute(
        "SELECT term FROM glossary_candidates WHERE status='approved' "
        "ORDER BY count DESC, last_seen DESC LIMIT ?", (limit,)).fetchall()
    return [r["term"] for r in rows]


def glossary_set_status(term: str, status: str) -> str:
    """Đổi status theo khoá so khớp. Trả chính tả hiển thị nếu thấy, '' nếu không."""
    key = _term_key(term)
    if not key:
        return ""
    with tx() as c:
        row = c.execute(
            "SELECT term FROM glossary_candidates WHERE term_key=?", (key,)
        ).fetchone()
        if row is None:
            return ""
        c.execute("UPDATE glossary_candidates SET status=? WHERE term_key=?",
                  (status, key))
        return row["term"]
