"""
"Nội dung đã chốt" trên Lark Base — mỗi cuộc họp MỘT record.

Vì sao Base chứ không phải Doc (quyết định của user, 30/07/2026): cần trạng
thái draft/final tra được bằng máy, cần biết ai chốt lúc nào, và đây là nền cho
bot Q&A sau này — bot đọc Base để trả lời "họp X chốt gì", chứ không phải đọc
lại transcript.

Vòng đời một record: phát biên bản xong -> tạo record (write_draft). Hết. Không
còn cửa duyệt nên không còn bước nào sau đó.

Cột `Trạng thái` nói về VIỆC PHÁT, không phải về việc duyệt (đổi 31/07/2026,
user chọn phương án (a) của Việc 5b). Trước đó nó là `draft`/`final`: cửa duyệt
đã bỏ 31/07 nên không còn đường nào flip sang `final`, tức mọi record ở `draft`
VĨNH VIỄN — một cột chỉ có một giá trị thì không phải trạng thái, chỉ là chỗ để
người sau đọc sai ("chưa xong à?"). Nay nó đọc từ bảng `deliveries` và trả lời
đúng câu người ta thật sự hỏi: biên bản này có tới tay ai không.

record_id giữ trong SQLite (cột jobs.bitable_record_id), KHÔNG tra lại bằng
cách search Base: search theo minute_token là thêm một lời gọi có thể sai, còn
SQLite vốn đã là nguồn sự thật của job.

Base/table KHÔNG tự sinh: chạy `python -m v2 base-init` để lấy 3 lệnh lark-cli
chạy một lần, rồi dán token vào .env. Lý do không cho bot tự tạo nằm ở docstring
`setup_commands()` — đọc trước khi định "cải tiến" chỗ này.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import config, db, jobstore, lark_api
from .models import MeetingMeta, Recap

# Giá trị cột `Trạng thái` — về VIỆC PHÁT (xem docstring module).
ST_SENT = "đã phát"          # ít nhất một người nhận được tóm tắt
ST_FAILED = "phát hỏng"      # đã THỬ gửi mà không ai nhận được
ST_NO_RECAP = "không có recap"   # có người nhận, nhưng tóm tắt rỗng
# Thêm 02/08/2026 cùng luật "chỉ gửi cho người đã cấp quyền": không ai trong
# cuộc họp đã enroll nên KHÔNG THỬ gửi cho ai. Tách khỏi `phát hỏng` vì hai cái
# đòi hai hành động khác hẳn — `phát hỏng` là đi sửa lỗi kỹ thuật, còn cái này
# là đi mời người ta cấp quyền. Gộp chung thì mỗi cuộc họp của phòng chưa dùng
# hệ thống lại hiện lên như một sự cố.
ST_NO_CONSENT = "chưa ai cấp quyền"
# Thêm 03/08/2026 (mô hình kéo): đã phiên âm whisper XONG nhưng CỐ Ý giữ, chờ
# người dự nhắn 'gửi transcript [tên]' mới gửi. KHÔNG có dòng recap-delivery nào
# (held không broadcast) nên nếu không tách riêng, `delivery_status` sẽ nhầm nó
# là `chưa ai cấp quyền` — sai hẳn: người dự VẪN được báo Minute+tóm tắt lúc họp
# xong, chỉ là bản nguyên văn thì chờ hỏi.
ST_HELD = "chờ hỏi transcript"

STATUS_OPTIONS = [ST_SENT, ST_NO_RECAP, ST_FAILED, ST_NO_CONSENT, ST_HELD]

# Tên field là KHÓA khi ghi record (base/v3 nhận map tên -> giá trị). Đổi tên
# field trên UI Base = code ghi hỏng. Muốn đổi nhãn thì đổi cả hai chỗ.
F_TITLE = "Cuộc họp"
F_WHEN = "Thời gian họp"
F_STATUS = "Tình trạng gửi"
F_SUMMARY = "Tóm tắt"
F_DECISIONS = "Quyết định"
F_ACTIONS = "Việc cần làm"
F_RECIPIENTS = "Số người nhận"
F_SOURCE = "Nguồn người nhận"
F_LINK = "Link Minutes"
F_TOKEN = "minute_token"

# --- GƯƠNG của bảng `jobs` (03/08/2026, user yêu cầu quản trị trên Base) ------
#
# Trước đó Base chỉ có góc nhìn "biên bản đã phát": record CHỈ được tạo cho job
# `delivered`/`held`, và cột `Trạng thái` nói về việc GỬI. Nhìn vào Base không
# trả lời được ba câu người vận hành hỏi nhiều nhất — cuộc này đang ở bước nào,
# hỏng vì cái gì, thử mấy lần rồi. Cuộc hỏng thì còn không có dòng nào trên Base
# (hai cuộc HAPAS thiếu quyền tải là ví dụ: mất tăm, phải mở SQLite mới thấy).
#
# Nay mỗi job trong `jobs` = một record, và các cột dưới đây soi thẳng từ cột
# cùng tên trong `db.py`. `Tình trạng gửi` (cũ tên `Trạng thái`) vẫn giữ nguyên
# nghĩa CŨ và vẫn hữu ích — hai câu hỏi khác nhau, để hai cột.
F_JOB_STATUS = "Tình trạng xử lý"    # jobs.status, dịch sang chữ người đọc được
F_ERROR = "Lỗi gần nhất"             # jobs.error
F_ATTEMPTS = "Số lần thử"            # jobs.attempts
F_INVITEES = "Số người được mời"     # jobs.invitee_count (KHÁC `Số người nhận`)
F_DETECTED = "Phát hiện lúc"         # jobs.detected_at
F_TRANSCRIBED = "Dịch xong lúc"      # jobs.transcribed_at
F_DELIVERED = "Gửi lúc"              # jobs.delivered_at

# Thêm 31/07/2026 theo yêu cầu user: theo dõi cụ thể ai/cái gì/mất bao lâu.
F_OWNER = "Chủ cuộc họp"          # tên người sở hữu minute (Lark trả owner_id)
F_OWNER_UID = "user_id chủ"       # user_id ngắn, tra chéo được với hệ thống khác
F_MEMBERS = "Người dự"            # mỗi dòng: "Tên — user_id"
F_TRANSCRIPT = "File transcript"  # attachment, bấm xem ngay trên Base
F_WHISPER_SEC = "Whisper (giây)"
F_AUDIO_SEC = "Audio (giây)"
F_WHISPER_X = "Tốc độ whisper"    # whisper/audio, vd "0.59x realtime"
# V3 (28/09/2026): Base là INDEX; file chi tiết .md nằm trên Drive (docs/V3_SPECS.md).
F_CONFIRM = "Xác nhận"            # chủ trì đã duyệt / chưa review / chờ duyệt
F_NOTE = "File biên bản"          # link file .md trên Drive

TABLE_NAME = "Biên bản"

# jobs.status -> chữ hiện trên Base. Người vận hành đọc Base, không đọc code:
# `held` hay `recapping` không nói gì với họ. Giữ ĐỦ mọi giá trị `status` có thể
# có (kể cả di sản) — thiếu một cái là ô select rỗng và không ai biết vì sao.
JOB_STATUS_LABEL = {
    "detected": "mới phát hiện",
    "queued": "đang chờ dịch",
    "transcribing": "đang phiên âm",
    "recapping": "đang tóm tắt",
    "waiting_auth": "chờ người có quyền tự xác thực",
    "held": "đã dịch xong — chờ người hỏi",
    "delivered": "đã gửi",
    "failed": "HỎNG — không tự chạy lại",
    "discarded": "bỏ qua",
    "awaiting_approval": "di sản: cửa duyệt cũ",
    "owner_only": "di sản: chỉ gửi chủ cuộc",
    "expired": "di sản: quá hạn duyệt",
}
JOB_STATUS_OPTIONS = list(dict.fromkeys(JOB_STATUS_LABEL.values()))

# Schema tạo lần đầu. `text` chứa được cả đoạn dài, nên tóm tắt/quyết định/việc
# cần làm đều là text nhiều dòng thay vì bảng con — bot Q&A đọc text dễ hơn.
SCHEMA: list[dict[str, Any]] = [
    {"name": F_TITLE, "type": "text"},
    {"name": F_WHEN, "type": "datetime"},
    {"name": F_STATUS, "type": "select", "multiple": False,
     "options": [{"name": s} for s in STATUS_OPTIONS]},
    {"name": F_SUMMARY, "type": "text"},
    {"name": F_DECISIONS, "type": "text"},
    {"name": F_ACTIONS, "type": "text"},
    {"name": F_RECIPIENTS, "type": "number"},
    {"name": F_SOURCE, "type": "text"},
    {"name": F_LINK, "type": "text"},
    {"name": F_TOKEN, "type": "text"},
    {"name": F_JOB_STATUS, "type": "select", "multiple": False,
     "options": [{"name": s} for s in JOB_STATUS_OPTIONS]},
    {"name": F_ERROR, "type": "text"},
    {"name": F_ATTEMPTS, "type": "number"},
    {"name": F_INVITEES, "type": "number"},
    {"name": F_DETECTED, "type": "datetime"},
    {"name": F_TRANSCRIBED, "type": "datetime"},
    {"name": F_DELIVERED, "type": "datetime"},
    {"name": F_OWNER, "type": "text"},
    {"name": F_OWNER_UID, "type": "text"},
    {"name": F_MEMBERS, "type": "text"},
    {"name": F_TRANSCRIPT, "type": "attachment"},
    {"name": F_WHISPER_SEC, "type": "number"},
    {"name": F_AUDIO_SEC, "type": "number"},
    {"name": F_WHISPER_X, "type": "text"},
    {"name": F_CONFIRM, "type": "text"},
    {"name": F_NOTE, "type": "text"},
]


def ensure_fields() -> list[str]:
    """Thêm field còn thiếu vào table ĐANG TỒN TẠI. Trả danh sách vừa thêm.

    Vì sao cần: `SCHEMA` chỉ được dùng lúc `base-init` tạo table lần đầu. Table
    đã tạo từ 30/07 rồi, nên thêm cột mới phải gọi field-create — giống hệt bẫy
    `CREATE TABLE IF NOT EXISTS` không thêm cột trong db.py.

    Idempotent: chạy bao nhiêu lần cũng được, chỉ thêm cái chưa có (so theo TÊN).
    """
    if not enabled():
        return []
    migrate_fields()                      # đổi tên cột cũ TRƯỚC khi so theo tên
    have = {f.get("name") for f in lark_api.base_fields(
        config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID)}
    added = []
    for spec in SCHEMA:
        if spec["name"] in have:
            continue
        try:
            lark_api.base_field_create(
                config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID, spec)
        except lark_api.LarkError as exc:
            print(f"[base] thêm field {spec['name']!r} hỏng: {exc}")
            continue
        added.append(spec["name"])
        print(f"[base] đã thêm field {spec['name']!r} ({spec['type']})")
    return added


# Cột cũ đổi tên (03/08/2026) — PHẢI chạy TRƯỚC `ensure_fields`, nếu không nó
# so theo tên và tạo thêm một cột mới nằm cạnh cột cũ cùng ý nghĩa.
_RENAMES: list[tuple[str, str, str]] = [
    # (tên CŨ trên Base, tên MỚI, type)
    ("Trạng thái", F_STATUS, "select"),
    # Hai cột dưới là di sản cửa duyệt đã bỏ 31/07: KHÔNG ai ghi, đo 03/08 là
    # 0/6 record có dữ liệu. Đổi tên để TÁI DÙNG chỗ trống thay vì xoá — xoá
    # field là thao tác không hoàn tác được, còn đổi tên thì giữ nguyên field_id
    # và mọi view/filter đang trỏ vào nó.
    ("Người chốt", F_ERROR, "text"),
    ("Chốt lúc", F_TRANSCRIBED, "datetime"),
]


def migrate_fields() -> list[str]:
    """Đổi tên các cột cũ sang tên mới. Trả danh sách đã đổi. Idempotent.

    Chỉ đổi khi tên CŨ còn tồn tại và tên MỚI chưa có — chạy lần hai là no-op.
    """
    if not enabled():
        return []
    flds = lark_api.base_fields(config.BITABLE_APP_TOKEN,
                                config.BITABLE_TABLE_ID)
    by_name = {f.get("name"): f for f in flds}
    done: list[str] = []
    for old, new, ftype in _RENAMES:
        if new in by_name or old not in by_name:
            continue
        spec: dict[str, Any] = {"name": new, "type": ftype}
        if ftype == "select":
            spec["multiple"] = False
            spec["options"] = [{"name": s} for s in STATUS_OPTIONS]
        # Lark chặn nhịp `OpenAPIUpdateField` rất gắt (800004135 — đo 03/08/2026:
        # đổi 3 cột liên tiếp thì cột 2 và 3 bị chặn). Thử lại có giãn cách; hết
        # lượt thì bỏ, `ensure_fields` sẽ tạo cột mới bên cạnh và cột cũ nằm lại
        # rỗng — xấu chứ không mất gì.
        for wait in (0, 3, 8):
            if wait:
                time.sleep(wait)
            try:
                lark_api.base_field_update(
                    config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID,
                    by_name[old]["id"], spec)
            except lark_api.LarkError as exc:
                if "800004135" not in str(exc):
                    print(f"[base] đổi tên cột {old!r} -> {new!r} hỏng: {exc}")
                    break
                last = exc
                continue
            done.append(f"{old} -> {new}")
            print(f"[base] đã đổi tên cột {old!r} -> {new!r}")
            break
        else:
            print(f"[base] đổi tên cột {old!r} -> {new!r} bị chặn nhịp: {last}")
    return done


def ensure_status_options() -> bool:
    """Đồng bộ bộ option của HAI cột select (`Tình trạng gửi`, `Tình trạng xử lý`).

    Cần riêng một hàm vì `ensure_fields()` chỉ THÊM field còn thiếu — field đã
    tồn tại thì nó không đụng, nên Base tạo trước 31/07/2026 vẫn giữ option
    `draft`/`final` và ghi giá trị mới sẽ hỏng.

    ⚠️ Gửi `options` là THAY THẾ cả bộ: record đang giữ giá trị bị bỏ khỏi danh
    sách sẽ mất ô. Đó là lý do `sync_tracking` đổ lại cột này cho mọi record —
    chạy `python -m v2 base-sync` ngay sau khi đổi.
    """
    if not enabled():
        return False
    flds = lark_api.base_fields(config.BITABLE_APP_TOKEN,
                                config.BITABLE_TABLE_ID)
    changed = False
    for fname, want in ((F_STATUS, STATUS_OPTIONS),
                        (F_JOB_STATUS, JOB_STATUS_OPTIONS)):
        fld = next((f for f in flds if f.get("name") == fname), None)
        if not fld:
            continue                      # chưa có cột -> ensure_fields tạo đúng
        have = [o.get("name") for o in (fld.get("options") or [])]
        if have == want:
            continue
        spec = {"name": fname, "type": "select", "multiple": False,
                "options": [{"name": s} for s in want]}
        try:
            lark_api.base_field_update(config.BITABLE_APP_TOKEN,
                                       config.BITABLE_TABLE_ID, fld["id"], spec)
        except lark_api.LarkError as exc:
            print(f"[base] đổi option cột {fname!r} hỏng: {exc}")
            continue
        changed = True
        print(f"[base] cột {fname!r}: {have} -> {want} "
              f"(chạy `python -m v2 base-sync` để đổ lại giá trị)")
    return changed


def delivery_status(minute_token: str, recap: Recap | None = None) -> str:
    """Trạng thái PHÁT của một cuộc họp, đọc từ bảng `deliveries`.

    Thứ tự xét có chủ ý: "không ai nhận được" là câu hỏi cấp bách hơn "tóm tắt
    có rỗng không", nên nó xét trước.

    Phân biệt "đã THỬ gửi mà hỏng" với "không có ai để gửi" (02/08/2026): dấu
    hiệu là bảng `deliveries` KHÔNG có dòng nào cho cuộc họp này — tức chưa lần
    nào chạm tới Lark, nên không thể là lỗi kỹ thuật. Từ khi có luật "chỉ gửi
    cho người đã cấp quyền", lý do gần như luôn là chưa ai trong cuộc họp
    enroll. Gộp hai cái vào `phát hỏng` thì mỗi cuộc họp của phòng chưa dùng hệ
    thống lại hiện lên như một sự cố, và người ta thôi đọc cột này.

    Xét `jobs.status` TRƯỚC nhánh đếm `deliveries` (mô hình kéo, 03/08/2026):
    job `held` cũng có 0 dòng `deliveries` (không broadcast) nên rơi thẳng vào
    `chưa ai cấp quyền` — SAI, người dự vẫn được báo Minute+tóm tắt, chỉ giữ bản
    nguyên văn chờ hỏi. Đây đúng là ca mà docstring cũ đã dặn: "Nếu sau này có ai
    cho job [không-delivered] lên Base thì phải xét `jobs.status` trước". Nay
    `_base_record_held` + `retry_missing_records` cho cả `held` lên Base thật.
    """
    if (jobstore.get(minute_token) or {}).get("status") == "held":
        return ST_HELD
    ok, failed = jobstore.delivery_counts(minute_token, "recap")
    if ok == 0:
        return ST_FAILED if failed else ST_NO_CONSENT
    if recap is None:
        recap = _recap_from_db(minute_token)
    empty = recap is None or (not (recap.summary or "").strip()
                              and not recap.decisions
                              and not recap.action_items)
    return ST_NO_RECAP if empty else ST_SENT


def _recap_from_db(minute_token: str) -> Recap | None:
    """Recap đã lưu của một job (cho `base-sync`, nơi không có sẵn object)."""
    import json
    row = jobstore.get(minute_token) or {}
    if not row.get("recap_json"):
        return None
    try:
        d = json.loads(row["recap_json"])
    except (ValueError, TypeError):
        return None
    from .models import ActionItem
    return Recap(summary=d.get("summary", ""),
                 decisions=d.get("decisions", []),
                 action_items=[ActionItem(**a) for a in d.get("action_items", [])])


_fields_checked = False


def _ensure_fields_once() -> None:
    """Bổ cột còn thiếu, MỘT lần mỗi tiến trình.

    Vì sao gọi từ `write_draft`: table đã tồn tại từ trước nên `SCHEMA` (chỉ dùng
    lúc `base-init`) không tự thêm cột mới. Nếu không có bước này thì máy mới /
    Base cũ sẽ ghi record thiếu ô theo dõi mà không báo gì. Một lần/tiến trình =
    thêm đúng 1 lời gọi GET, không đáng kể so với cả pipeline.
    """
    global _fields_checked
    if _fields_checked:
        return
    _fields_checked = True
    try:
        ensure_fields()
    except lark_api.LarkError as exc:
        print(f"[base] không kiểm được field ({exc}) — vẫn ghi record")
    # Bộ option của cột select PHẢI kiểm ở đây nữa, không chỉ trong
    # `sync_tracking` (thêm 02/08/2026). `ensure_fields` chỉ THÊM field còn
    # thiếu, không đụng field đã có — nên khi `STATUS_OPTIONS` có thêm giá trị
    # mới (`chưa ai cấp quyền`), Base vẫn giữ bộ 3 option cũ và record ĐẦU TIÊN
    # dùng giá trị mới sẽ ghi hỏng. Đúng cái bẫy đã trả giá với `draft`/`final`,
    # chỉ khác là lần này đường ghi tự động chạm vào trước khi ai kịp chạy
    # `base-sync`. Ở đây CHỈ THÊM option (3 -> 4), không bỏ cái nào, nên không
    # record nào mất ô — cảnh báo "gửi options là THAY THẾ" không áp dụng.
    try:
        ensure_status_options()
    except lark_api.LarkError as exc:
        print(f"[base] không kiểm được option cột {F_STATUS!r} ({exc})")


def sync_tracking(only_token: str = "") -> int:
    """Đổ lại các ô theo dõi cho record ĐÃ có trên Base. Trả số record đã sửa.

    Dùng khi: vừa thêm field mới (ensure_fields) và muốn record cũ cũng có dữ
    liệu, hoặc lần ghi đầu bị hỏng một ô nào đó (upload lỗi, mất mạng).
    Idempotent — ghi lại cùng giá trị thì Base không đổi gì.
    """
    if not enabled():
        print("[base] đang TẮT (thiếu BITABLE_*)")
        return 0
    ensure_fields()
    # Phải đứng TRƯỚC vòng ghi: đổi option xong thì record đang giữ giá trị cũ
    # (`draft`) mất ô, và chính vòng dưới đây là chỗ đổ lại giá trị mới.
    ensure_status_options()

    # Ô nào đã có file rồi thì ĐỪNG upload lại: mỗi lần upload sinh file_token
    # mới, ô chỉ giữ cái mới nhất còn bản cũ thành rác trong Base. Chạy
    # base-sync nhiều lần là bình thường, nên phải chống ở đây.
    have_file: set[str] = set()
    try:
        for rec in lark_api.base_records_all(config.BITABLE_APP_TOKEN,
                                             config.BITABLE_TABLE_ID):
            # base/v3 trả record PHẲNG, id ở `_record_id` (không phải `fields`).
            if rec.get(F_TRANSCRIPT):
                have_file.add(rec.get("_record_id") or "")
    except lark_api.LarkError as exc:
        print(f"[base] không đọc được record cũ ({exc}) — sẽ upload lại file")

    n = 0
    for row in jobstore.all_jobs():
        rid = row.get("bitable_record_id")
        if not rid:
            continue
        if only_token and row["minute_token"] != only_token:
            continue
        meta = jobstore.meta_from_json(row["meta_json"])
        vals = _tracking_fields(meta, skip_file=rid in have_file)
        vals.update(_job_fields(row))     # cột gương của `jobs`
        # Đổ lại cột `Tình trạng gửi`. Chỉ cho job đã chạy xong: job
        # `queued`/`failed` chưa phát gì thì ghi "phát hỏng" là nói dối — nó
        # chưa tới lượt. (`held` có nghĩa riêng, xem `delivery_status`.)
        if row.get("status") in ("delivered", "held"):
            vals[F_STATUS] = delivery_status(row["minute_token"])
        if not vals:
            print(f"[base] {meta.title!r}: không có dữ liệu theo dõi nào")
            continue
        try:
            lark_api.base_record_update(
                config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID, rid, vals)
        except lark_api.LarkError as exc:
            print(f"[base] cập nhật {meta.title!r} hỏng: {exc}")
            continue
        n += 1
        print(f"[base] ✓ {meta.title!r} -> {', '.join(sorted(vals))}")
    return n


def _job_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Các ô SOI THẲNG từ một dòng `jobs` (rẻ: không gọi API nào).

    Tách khỏi `_tracking_fields` vì hai thứ khác hẳn về giá: cái kia tra danh bạ
    và upload file (chậm, hay hỏng), cái này chỉ đọc SQLite. Nhờ vậy vòng `run`
    đổ được trạng thái lên Base mỗi lượt mà không tốn gì.

    `error` cắt còn 500 ký tự: ô text của Base chứa được dài hơn, nhưng cột lỗi
    dài làm hỏng cả bảng khi nhìn, mà phần đầu của lỗi mới là phần nói nguyên
    nhân. Muốn xem đủ thì mở SQLite.
    """
    out: dict[str, Any] = {
        F_ATTEMPTS: int(row.get("attempts") or 0),
        F_INVITEES: int(row.get("invitee_count") or 0),
        # Ghi CHUỖI RỖNG khi hết lỗi, không bỏ qua ô: job hỏng rồi chạy lại
        # thành công mà ô lỗi vẫn còn chữ cũ thì người đọc Base tưởng vẫn đang
        # hỏng. Ô này phải nói về TRẠNG THÁI HIỆN TẠI.
        F_ERROR: (row.get("error") or "")[:500],
    }
    st = row.get("status") or ""
    if st:
        # Giá trị lạ (status mới thêm mà quên khai) thì ghi thẳng chữ thô: option
        # select không khớp sẽ bị Base từ chối, và như vậy còn hơn im lặng bỏ ô.
        out[F_JOB_STATUS] = JOB_STATUS_LABEL.get(st, st)
    for fname, col in ((F_DETECTED, "detected_at"),
                       (F_TRANSCRIBED, "transcribed_at"),
                       (F_DELIVERED, "delivered_at")):
        v = row.get(col)
        if v:
            out[fname] = int(v)           # datetime của Base nhận epoch ms
    # V3: trạng thái duyệt của chủ + link file biên bản trên Drive (chỉ đọc SQLite).
    token = row.get("minute_token") or ""
    c = db.conn().execute("SELECT state FROM confirmations WHERE minute_token=?",
                          (token,)).fetchone()
    if c:
        out[F_CONFIRM] = CONFIRM_LABEL.get(c["state"], c["state"])
    n = db.conn().execute("SELECT drive_url FROM note_files WHERE minute_token=?",
                          (token,)).fetchone()
    if n and n["drive_url"]:
        out[F_NOTE] = n["drive_url"]
    return out


CONFIRM_LABEL = {"pending": "chờ chủ trì duyệt", "confirmed": "chủ trì đã duyệt",
                 "auto_published": "chưa được chủ trì review"}


def _meta_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Ô HIỂN THỊ soi từ `meta_json`. Rẻ như `_job_fields` — chỉ parse JSON.

    Vì sao phải có (đo 05/08/2026): `sync_jobs` trước đây chỉ đổ `_job_fields`,
    nên bốn ô dưới đây được ghi ĐÚNG MỘT LẦN lúc `write_draft` tạo record rồi
    đóng băng vĩnh viễn. Ba hậu quả đã đo được trên Base thật:

      - Bản vá ACL đổi `participants_source` của Workforce Buổi 4 từ
        `calendar[near5m]` sang `calendar[verified]…+vc29` trong SQLite, còn Base
        vẫn hiện "khớp theo GIỜ (lệch 5m)". Đó chính là ô người vận hành nhìn để
        trả lời "vì sao người này nhận được biên bản" — nó đang nói dối.
      - Tên cuộc họp còn nguyên escape HTML (`Review HRIS &amp; feedback`) dù
        `meetings.build_meta` đã gỡ từ 04/08. `qa._title()` chỉ gỡ lúc HIỂN THỊ
        trong bot, nên người mở thẳng Base vẫn đọc chuỗi hỏng.
      - Cuộc họp được tra lại người dự (`_maybe_reresolve`) đổi cả tên sự kiện
        lẫn link Minutes mà Base không hay biết.

    KHÔNG đưa `F_WHEN` vào `_DIFF_FIELDS` (xem chú thích ở đó): Base trả datetime
    về dạng chuỗi còn ta gửi epoch ms, so thẳng thì lần nào cũng "lệch". Nó vẫn
    được ghi — đi ké mỗi khi một ô khác kích hoạt lượt đẩy.
    """
    out: dict[str, Any] = {}
    try:
        meta = jobstore.meta_from_json(row["meta_json"])
    except (ValueError, KeyError, TypeError):
        return out                        # job cũ méo: để `_job_fields` lo phần còn lại
    from . import meetings
    out[F_TITLE] = meta.title or "(không tiêu đề)"
    out[F_SOURCE] = meetings.explain_source(meta.participants_source)
    out[F_LINK] = meta.app_link or ""
    if meta.start:
        out[F_WHEN] = int(meta.start * 1000)
    return out


def _tracking_fields(meta: MeetingMeta, *,
                     skip_file: bool = False) -> dict[str, Any]:
    """Các ô theo dõi: chủ họp, người dự, thời gian whisper, file transcript.

    Mỗi phần bọc try riêng: thiếu quyền danh bạ hay upload hỏng thì vẫn ghi được
    những ô còn lại. Ghi Base là việc PHỤ, không được làm job thành failed.
    """
    out: dict[str, Any] = {}
    row = jobstore.get(meta.minute_token) or {}

    # --- ai chủ, ai dự (tra danh bạ để có user_id) ---
    # meta.attendees là list DATACLASS `Attendee`, không phải dict — .open_id,
    # đừng .get().
    try:
        att = meta.attendees or []
        ids = [a.open_id for a in att if a.open_id]
        owner_oid = meta.owner_open_id or ""
        who = lark_api.contact_batch(
            list(dict.fromkeys([*ids, owner_oid])) if owner_oid else ids)
        if owner_oid and owner_oid in who:
            out[F_OWNER] = who[owner_oid]["name"] or meta.owner_name
            out[F_OWNER_UID] = who[owner_oid]["user_id"]
        elif meta.owner_name:
            out[F_OWNER] = meta.owner_name
        lines = []
        for a in att:
            info = who.get(a.open_id) or {}
            name = info.get("name") or a.name or a.open_id
            uid = info.get("user_id") or ""
            lines.append(f"{name} — {uid}" if uid else name)
        if lines:
            out[F_MEMBERS] = "\n".join(lines)
    except lark_api.LarkError as exc:
        print(f"[base] tra danh bạ hỏng ({exc}) — bỏ qua ô chủ/người dự")

    # --- thời gian whisper ---
    ws = row.get("whisper_seconds") or 0
    aud = row.get("audio_seconds") or 0
    if ws:
        out[F_WHISPER_SEC] = round(float(ws), 1)
    if aud:
        out[F_AUDIO_SEC] = round(float(aud), 1)
    if ws and aud:
        out[F_WHISPER_X] = f"{float(ws) / float(aud):.2f}x realtime"

    # --- file transcript (attachment) ---
    # Ưu tiên bản người đọc được (`pipeline.doc_path` TÍNH ra đường dẫn, không
    # glob mò); không có thì đính kèm chính file .json trong DB.
    #
    # BA nấc, và nấc giữa mới thêm 03/08/2026: đuôi file đổi .txt -> .docx hôm
    # đó, nên mọi cuộc họp CŨ chỉ có bản .txt trên đĩa. Thiếu nấc `legacy` thì
    # các đường ghi Base MUỘN (sync_tracking, retry_missing_records) tụt thẳng
    # xuống .json, và người mở ô file nhận một cục JSON thay vì biên bản —
    # hỏng im lặng, vì record vẫn ghi thành công.
    # `is_file()` chứ KHÔNG `exists()` ở cả ba nấc (sửa 03/08/2026): job chưa
    # dịch có `transcript_path` RỖNG, mà `Path("")` là `Path(".")` — thư mục
    # hiện hành, và `.exists()` của nó là True. Trước đây không lộ vì Base chỉ
    # nhận job đã phát (luôn có file); từ khi Base nhận cả job `queued` thì nấc
    # cuối trỏ vào "." và `read_bytes()` ném PermissionError, làm CHẾT nguyên
    # lượt `base-sync` chứ không chỉ mất một ô.
    # `_usable`: phải là FILE và phải CÓ BYTE. File 0 byte bị Lark trả
    # `1061002 params error` — và vì nấc chọn cũ chỉ hỏi "có tồn tại không",
    # một bản .txt rỗng của cuộc họp cũ làm cả ô file hỏng VĨNH VIỄN, lượt đồng
    # bộ nào cũng thử lại rồi lại trượt (đo 04/08/2026: cuộc 'test' 27/07).
    def _usable(p: Path | None) -> bool:
        return bool(p) and p.is_file() and p.stat().st_size > 0

    from . import pipeline
    target = None if skip_file else pipeline.doc_path(meta)
    if not _usable(target):
        legacy = pipeline.legacy_txt_path(meta) if not skip_file else None
        target = legacy if _usable(legacy) else None
    if target is None and not skip_file:
        raw_s = (row.get("transcript_path") or "").strip()
        raw = Path(raw_s) if raw_s else None
        target = raw if _usable(raw) else None
    if target:
        last: Exception | None = None
        # `or [""]`: không lấy được token của ai thì VẪN thử một lần bằng tenant
        # token — với tenant nào có scope `docs:document.media:upload` ở app
        # level thì nó chạy. Bỏ nhánh này là mất luôn đường upload ở môi trường
        # chưa ai enroll (và làm chết một phép kiểm cũ).
        for tok in (_upload_tokens(meta) or [""]):
            try:
                ft = lark_api.base_media_upload(
                    target, config.BITABLE_APP_TOKEN, tok)
            except lark_api.LarkError as exc:
                last = exc
                continue                  # người này không ghi được Base -> thử người kế
            if ft:
                out[F_TRANSCRIPT] = [{"file_token": ft}]
                break
        else:
            print(f"[base] upload transcript hỏng, đã thử hết người ({last}) "
                  f"— bỏ qua ô file")
    return out


def _upload_tokens(meta: MeetingMeta) -> list[str]:
    """access_token NGƯỜI DÙNG để upload attachment, theo thứ tự nên thử.

    ⚠️ ADMIN ĐỨNG TRƯỚC, không phải chủ bản ghi (sửa 04/08/2026) — đây là hai
    MIỀN QUYỀN khác nhau và trước đó bị lẫn:
      * tải bản ghi TỪ Lark: chủ bản ghi là người chắc chắn có quyền
        (`pipeline._reader_candidates` — giữ nguyên, đúng cho việc đó);
      * upload file VÀO Base: người phải có quyền GHI chính cái Base này, tức
        chủ Base — hôm nay là admin.
    Đo 04/08/2026 trên cùng một file: token của admin upload OK, token của Chi
    và Thiện đều `1061004 forbidden` (Base chỉ chia sẻ tường minh cho admin).
    Hậu quả của thứ tự cũ: cuộc họp do NGƯỜI KHÁC chủ trì thì ô 'File
    transcript' trên Base rỗng vĩnh viễn, mà record vẫn ghi thành công nên
    không có gì báo động.

    Trả DANH SÁCH chứ không phải một token: caller thử lần lượt tới khi được.
    Rỗng = chưa ai enroll — lúc đó `base_media_upload` rơi về tenant token và
    (với app này) sẽ hỏng, nhưng chỉ mất ô file chứ không mất record.
    """
    from . import tokenstore
    out: list[str] = []
    for oid in _upload_candidates(meta):
        try:
            out.append(tokenstore.get_access_token(oid))
        except Exception:                  # noqa: BLE001 — chưa enroll/token hỏng
            continue
    return out


def _upload_candidates(meta: MeetingMeta) -> list[str]:
    """open_id theo thứ tự nên thử upload — tách riêng để kiểm được thứ tự mà
    không cần token thật."""
    from . import pipeline, tokenstore
    users = tokenstore.list_users(active_only=True)
    admins = [u["open_id"] for u in users
              if u.get("union_id") in config.QA_ADMIN_UNION_IDS]
    order: list[str] = []
    for oid in [*admins, *pipeline._reader_candidates(meta),
                *[u["open_id"] for u in users]]:
        if oid and oid not in order:
            order.append(oid)
    return order


def enabled() -> bool:
    return bool(config.BITABLE_APP_TOKEN and config.BITABLE_TABLE_ID)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bullets(lines: list[str]) -> str:
    """Danh sách -> text nhiều dòng. Rỗng thì để trống, KHÔNG ghi '(không có)':
    ô trống lọc/đếm được trên Base, còn chuỗi giả thì không."""
    return "\n".join(f"• {s.strip()}" for s in lines if s and s.strip())


def _action_lines(recap: Recap) -> str:
    out = []
    for a in recap.action_items:
        bits = [a.task.strip()]
        if a.owner:
            bits.append(f"— {a.owner}")
        if a.due:
            bits.append(f"(hạn {a.due})")
        out.append(" ".join(bits))
    return _bullets(out)


# ------------------------------------------------------------------ ghi


def write_draft(meta: MeetingMeta, recap: Recap, n_recipients: int) -> str:
    """Tạo record cho một cuộc họp. Trả record_id ("" nếu tắt/hỏng).

    Tên hàm giữ nguyên `write_draft` dù không còn `draft`: đổi tên là đụng vào
    `orchestrator._deliver_now` và mọi tài liệu đang trỏ tới nó, không đáng.

    Ghi record là việc PHỤ: biên bản đã tới tay người dự rồi. Hỏng thì log và
    đi tiếp, không được làm job thành failed.
    """
    if not enabled():
        return ""
    _ensure_fields_once()
    row = jobstore.get(meta.minute_token) or {}
    if row.get("bitable_record_id"):
        return row["bitable_record_id"]          # đã ghi, đừng tạo trùng

    # Chưa có id trong DB KHÔNG có nghĩa là Base chưa có record: tạo xong mà
    # chết trước khi lưu id là đủ để lần sau ghi trùng (đã xảy ra thật khi parse
    # record_id sai — ra 2 record cho cùng một cuộc họp). Tra Base trước.
    try:
        found = lark_api.base_record_find(
            config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID,
            F_TOKEN, meta.minute_token)
    except lark_api.LarkError as exc:
        print(f"[base] tra record cũ hỏng ({exc}) — vẫn ghi mới")
        found = ""
    if found:
        jobstore.set_status(meta.minute_token, row.get("status") or "delivered",
                            bitable_record_id=found)
        print(f"[base] record đã có trên Base, gắn lại id: {found}")
        return found

    from . import meetings
    fields: dict[str, Any] = {
        F_TITLE: meta.title,
        F_SUMMARY: recap.summary.strip(),
        F_DECISIONS: _bullets(recap.decisions),
        F_ACTIONS: _action_lines(recap),
        F_RECIPIENTS: n_recipients,
        F_SOURCE: meetings.explain_source(meta.participants_source),
        F_LINK: meta.app_link or "",
        F_TOKEN: meta.minute_token,
    }
    if meta.start:
        fields[F_WHEN] = int(meta.start * 1000)   # datetime nhận epoch ms
    # `Tình trạng gửi` CHỈ có nghĩa với job đã chạy xong. Từ 03/08/2026 Base
    # nhận record của MỌI job (kể cả `queued`/`failed`), mà `delivery_status`
    # của một job chưa tới lượt sẽ ra `chưa ai cấp quyền` — nói dối. Để TRỐNG
    # thì đúng: chưa gửi thì chưa có gì để nói. Cột `Tình trạng xử lý` mới là
    # chỗ trả lời "cuộc này đang ở đâu".
    if (row.get("status") or "") in ("delivered", "held"):
        fields[F_STATUS] = delivery_status(meta.minute_token, recap)
    fields.update(_job_fields(row))
    fields.update(_tracking_fields(meta))

    try:
        rid = lark_api.base_record_create(
            config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID, fields)
    except lark_api.LarkError as exc:
        print(f"[base] ghi record hỏng ({exc}) — biên bản vẫn đã phát")
        return ""
    jobstore.set_status(meta.minute_token, row.get("status") or "delivered",
                        bitable_record_id=rid)
    print(f"[base] đã ghi record ({fields.get(F_STATUS) or fields.get(F_JOB_STATUS)}"
          f"): {rid}")
    return rid


def update_recap(minute_token: str, recap: Recap) -> bool:
    """Đổ lại ba ô nội dung + `Trạng thái` cho record ĐÃ có. True nếu đã ghi.

    Chỉ cho `orchestrator._backfill_recaps`: một cuộc họp lỡ phát với tóm tắt
    rỗng (LLM chết quá `RECAP_MAX_TRIES` vòng) thì record trên Base cũng rỗng
    theo, và Base chính là thứ bot đọc — nên tới khi ô này được vá thì người
    dùng hỏi vẫn nhận được "không có tóm tắt", dù transcript vẫn còn nguyên.

    KHÔNG đụng các ô theo dõi (`_tracking_fields`): file transcript đã upload
    rồi, ghi lại là sinh `file_token` mới và bỏ bản cũ thành rác trong Base —
    đúng cái bẫy `sync_tracking` đã phải chống.
    """
    if not enabled():
        return False
    row = jobstore.get(minute_token) or {}
    rid = row.get("bitable_record_id")
    if not rid:
        return False                          # `retry_missing_records` lo ca này
    vals = {
        F_SUMMARY: (recap.summary or "").strip(),
        F_DECISIONS: _bullets(recap.decisions),
        F_ACTIONS: _action_lines(recap),
        F_STATUS: delivery_status(minute_token, recap),
    }
    try:
        lark_api.base_record_update(
            config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID, rid, vals)
    except lark_api.LarkError as exc:
        print(f"[base] cập nhật tóm tắt {minute_token} hỏng: {exc}")
        return False
    print(f"[base] đã cập nhật tóm tắt cho {minute_token} ({vals[F_STATUS]})")
    return True


# Các ô đem ra SO khi quyết định có ghi lại record không. Cố ý KHÔNG có ô
# datetime: Base trả chúng về dạng chuỗi '2026-07-29 09:41:23' còn ta gửi đi là
# epoch ms, so thẳng thì lần nào cũng "lệch" -> ghi lại cả bảng mỗi 5 phút. Ba
# mốc thời gian chỉ được đặt đúng lúc `status` đổi, mà `status` thì có trong
# danh sách này — nên chúng vẫn lên Base, chỉ là đi ké. `Thời gian họp`
# (`F_WHEN`) cũng vậy: `_meta_fields` luôn gửi nó, chỉ không đem ra so.
#
# Thêm HAI ô hiển thị 05/08/2026 (xem `_meta_fields`): thiếu chúng thì tên cuộc
# họp và nguồn người nhận chỉ đúng vào ĐÚNG LÚC record được tạo. Đo thật lúc
# thêm: 6/24 record đang lệch, trong đó Workforce Buổi 4 vẫn khoe "khớp theo GIỜ
# (lệch 5m)" hai ngày sau khi ACL của nó được vá thành verified.
#
# `F_LINK` CỐ Ý KHÔNG có mặt, dù `_meta_fields` vẫn gửi nó. Lý do đo được: Base
# tự chuẩn hoá ô url, và 1/24 record đang lưu dạng markdown
# `[https://…](https://…)` trong khi ta gửi URL trần. Đưa nó vào đây thì record
# đó lệch VĨNH VIỄN -> ghi lại mỗi 5 phút, mãi mãi — đúng cái bẫy mà chú thích
# datetime ngay trên vừa cảnh báo. Nó vẫn được sửa, chỉ là đi ké lượt đẩy do ô
# khác kích hoạt.
_DIFF_FIELDS = (F_JOB_STATUS, F_ERROR, F_ATTEMPTS, F_INVITEES,
                F_TITLE, F_SOURCE, F_CONFIRM, F_NOTE)


def _needs_push(rec: dict[str, Any], want: dict[str, Any]) -> bool:
    """Record trên Base có lệch với dữ liệu muốn ghi không.

    Vì sao không chỉ so mỗi `Tình trạng xử lý` (sửa 03/08/2026, ngay sau khi
    dựng gương): một job đang thử lại đứng yên ở `queued` trong khi `attempts`
    và `error` đổi mỗi vòng — đúng lúc người vận hành nhìn Base để hiểu chuyện
    gì đang xảy ra thì nó lại là thứ KHÔNG được cập nhật.
    """
    for k in _DIFF_FIELDS:
        if k not in want:
            continue
        cur, new = rec.get(k), want[k]
        if isinstance(new, (int, float)):
            if float(cur or 0) != float(new or 0):
                return True
        elif str(cur or "").strip() != str(new or "").strip():
            return True
    return False


def sync_jobs() -> int:
    """Đổ cột GƯƠNG (`jobs`) lên Base cho record đã có. Trả số record vừa sửa.

    RẺ, gọi được mỗi vòng `run`: một lời gọi đọc cả bảng, rồi CHỈ ghi những
    record có `Tình trạng xử lý` lệch với SQLite. Không tra danh bạ, không upload
    file — phần đắt đó nằm ở `sync_tracking` (chạy tay qua `base-sync`).

    Vì sao lọc chứ không ghi hết: 16 record x mỗi 5 phút là 190 lời gọi/giờ cho
    việc không đổi gì. Nhưng lọc phải so ĐỦ các ô hay đổi — xem `_needs_push`.
    """
    if not enabled():
        return 0
    try:
        recs = {r.get(F_TOKEN): r for r in lark_api.base_records_all(
            config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID)}
    except lark_api.LarkError as exc:
        print(f"[base] không đọc được record để đồng bộ ({exc})")
        return 0
    n = 0
    for row in jobstore.all_jobs():
        rec = recs.get(row["minute_token"])
        rid = (rec or {}).get("_record_id") or row.get("bitable_record_id")
        if not rid:
            continue                      # chưa có record -> retry_missing_records lo
        want = _job_fields(row)
        want.update(_meta_fields(row))
        # Ô ĐẮT (file transcript, số giây whisper, người dự) chỉ được điền lúc
        # TẠO record. Từ 03/08/2026 record được tạo ngay khi job còn `queued` —
        # lúc đó chưa có transcript, mà `write_draft` ở bước `held` thì thoát
        # sớm vì record đã tồn tại ⇒ ô file TRỐNG VĨNH VIỄN, chỉ `base-sync`
        # chạy tay mới vá. Đúng triệu chứng đã gặp: hai cuộc vừa dịch xong,
        # .docx nằm sẵn trên đĩa mà ô 'File transcript' trên Base rỗng.
        cur = rec or {}
        has_file = bool(cur.get(F_TRANSCRIPT))
        has_metrics = bool(cur.get(F_WHISPER_SEC))
        lacks = ((row.get("transcript_path") and not has_file)
                 or (row.get("whisper_seconds") and not has_metrics))
        # Danh sách người dự đã đổi so với Base (thêm 05/08/2026). Tra lại người
        # dự và các bản vá ACL đều ghi đè `meta.attendees`, mà ô `Người dự` /
        # `Chủ cuộc họp` nằm trong `_tracking_fields` — phần ĐẮT, trước đây chỉ
        # được chạm khi thiếu FILE. Nên một cuộc họp bị gỡ 22 attendee suy đoán
        # vẫn khoe đủ 22 người trên Base, vĩnh viễn.
        #
        # So bằng `Số người được mời` chứ không so từng tên: nó là con số duy
        # nhất phản ánh `len(meta.attendees)` mà không tốn lời gọi API nào, và
        # nó đã nằm sẵn trong `_DIFF_FIELDS`. Đổi người mà giữ nguyên SỐ người
        # thì lượt này bỏ sót — chấp nhận, vì cái giá của phép so đầy đủ là một
        # cú tra danh bạ cho MỌI record ở MỌI vòng.
        stale_members = (rec is not None
                         and float(cur.get(F_INVITEES) or 0)
                         != float(want.get(F_INVITEES) or 0))
        if rec is not None and not lacks and not stale_members \
                and not _needs_push(rec, want):
            continue                      # không có gì đổi -> khỏi ghi
        if lacks or stale_members:
            # Chỉ chạm phần đắt khi THIẾU thật: mỗi lần là một cú tra danh bạ +
            # một cú upload. Bỏ qua upload nếu ô đã có file HOẶC nếu ta vào đây
            # chỉ vì danh sách người dự đổi — upload lại chỉ sinh file_token mới
            # và biến bản cũ thành rác trong Base.
            try:
                meta = jobstore.meta_from_json(row["meta_json"])
                want.update(_tracking_fields(
                    meta, skip_file=has_file or not lacks))
            except Exception as exc:      # noqa: BLE001 — ô phụ, đừng chặn ô chính
                print(f"[base] {row['minute_token']} lấy ô theo dõi hỏng "
                      f"(vẫn ghi trạng thái): {exc}")
        if (row.get("status") or "") in ("delivered", "held"):
            want[F_STATUS] = delivery_status(row["minute_token"])
        try:
            lark_api.base_record_update(config.BITABLE_APP_TOKEN,
                                        config.BITABLE_TABLE_ID, rid, want)
        except lark_api.LarkError as exc:
            print(f"[base] đồng bộ {row['minute_token']} hỏng: {exc}")
            continue
        n += 1
    if n:
        print(f"[base] đồng bộ trạng thái {n} record")
    return n


def retry_missing_records() -> int:
    """Ghi record cho job chưa có trên Base. Trả số record vừa tạo.

    Từ 03/08/2026 quét MỌI job, không chỉ `delivered`/`held` (user yêu cầu quản
    trị trên Base): cuộc `queued` chờ dịch và cuộc `failed` vì thiếu quyền tải
    trước đây không có dòng nào trên Base, nên nhìn Base tưởng chúng không tồn
    tại — phải mở SQLite mới thấy. Nay Base là GƯƠNG của bảng `jobs`.

    Vì sao phải có (sửa 31/07/2026): `write_draft` cố ý chỉ log rồi đi tiếp khi
    ghi Base hỏng — biên bản đã tới tay người dự rồi, không được làm job
    `failed`. Nhưng sau đó KHÔNG có đường vá nào, kể cả bằng tay: `sync_tracking`
    bỏ qua mọi job không có `bitable_record_id` (nó chỉ đổ lại ô cho record đã
    tồn tại), và không lệnh nào tạo record thiếu. Hậu quả: một cú mất mạng lúc
    ghi Base là cuộc họp đó vĩnh viễn không lên Base, còn bot thì báo "CHƯA CÓ
    BIÊN BẢN" mãi mãi (`qa.pending_meetings` xét đúng cột này) dù đã phát xong.

    Quét cả `held` (sửa 03/08/2026 — mô hình kéo): cuộc `held` đã phiên âm xong
    và `_base_record_held` đã cố ghi record, nhưng ghi hỏng thì cùng một lỗ —
    không có `bitable_record_id`, mất trong `list_meetings`/`search_meetings`, và
    KHÔNG được nhánh `delivered` này vá vì held không bao giờ thành delivered.
    `write_draft` với `n_recipients=0` khớp đúng cái `_base_record_held` ghi.

    Gọi từ hai chỗ: cuối mỗi `process_queue` thật, và đầu `base-sync` (người
    chạy tay cũng phải vá được, không chỉ vòng `run`).

    Idempotent: `write_draft` tự tra Base theo `minute_token` trước khi tạo, nên
    chạy lại nhiều lần không sinh record trùng.
    """
    if not enabled():
        return 0
    n = 0
    for row in jobstore.all_jobs():
        if row.get("bitable_record_id"):
            continue
        token = row["minute_token"]
        try:
            meta = jobstore.meta_from_json(row["meta_json"])
        except (ValueError, KeyError, TypeError) as exc:
            print(f"[base] {token} meta_json méo, bỏ qua: {exc}")
            continue
        # Recap đọc từ DB; không có thì vẫn ghi record với recap rỗng —
        # `delivery_status` sẽ tự cho ra `không có recap`, và có record trống
        # còn hơn không có gì (bot mới thôi nói "chưa có biên bản").
        recap = _recap_from_db(token) or Recap(summary="")
        n_recip, _ = jobstore.delivery_counts(token, "recap")
        print(f"[base] {token} ({row.get('status')}) chưa có record trên Base "
              f"-> ghi")
        if write_draft(meta, recap, n_recip):
            n += 1
    return n


# `mark_final()` và lệnh `python -m v2 base-final` ĐÃ BỎ (31/07/2026, Việc 5b).
# Chúng chỉ tồn tại để flip `draft` -> `final`, mà `final` không còn là một giá
# trị hợp lệ của cột `Trạng thái` nữa — giữ lại thì lệnh đó ghi một option không
# tồn tại vào Base. Cửa duyệt đã bỏ từ 30/07 nên cũng không còn ai "chốt" cái gì.
# Hai cột `Người chốt` / `Chốt lúc` đã được TÁI DÙNG 03/08/2026: đổi tên thành
# `Lỗi gần nhất` / `Dịch xong lúc` (xem `_RENAMES`). Chúng rỗng 0/6 record nên
# không mất gì, và đổi tên giữ nguyên field_id — mọi view/filter cũ vẫn trỏ đúng.


# ------------------------------------------------------- khởi tạo một lần


def setup_commands() -> str:
    """In hai lệnh lark-cli để tạo Base một lần. KHÔNG tự tạo bằng bot.

    Vì sao không để V2 tự tạo (đã thử và thất bại 30/07/2026): app V2 tạo Base
    được, nhưng KHÔNG chia sẻ được cho người dùng — `drive_member_add` trả 99991672
    "cần scope drive:drive / bitable:app / docs:permission.member:create". Base do
    bot tạo thì bot là chủ, người dùng không mở được, và cũng không ai xóa được
    nữa (cả bot lẫn user đều bị từ chối). Nói cách khác: bot tạo = tạo rác vĩnh viễn.

    Đường đúng: lark-cli tạo bằng danh tính USER (Base thuộc về người đó, hiện
    trong Drive của họ), rồi thêm app V2 làm collaborator full_access. Sau đó app
    ghi được vì quyền Base ở tầng app vốn đã có.
    """
    import json
    schema = json.dumps(SCHEMA, ensure_ascii=False)
    return (
        "# 1) Tạo Base bằng danh tính USER (bạn là chủ sở hữu):\n"
        f"lark-cli base +base-create --name \"V2 — Biên bản họp\" \\\n"
        f"  --table-name \"{TABLE_NAME}\" --fields '{schema}' \\\n"
        "  --time-zone Asia/Bangkok --as user --json\n"
        "#    -> lấy base_token trong data.base.base_token\n"
        "#    (Asia/Ho_Chi_Minh bị API từ chối, dùng Asia/Bangkok — cùng +07)\n\n"
        "# 2) Cho app V2 quyền ghi vào Base đó:\n"
        f"lark-cli drive +member-add --token <BASE_TOKEN> --type bitable \\\n"
        f"  --member-type appid --member-id {config.APP_ID} \\\n"
        "  --perm full_access --as user --yes\n\n"
        "# 3) Lấy table_id rồi dán cả hai vào v2/.env:\n"
        "lark-cli base +table-list --base-token <BASE_TOKEN> --as user --json\n"
        "#    BITABLE_APP_TOKEN=<BASE_TOKEN>\n"
        "#    BITABLE_TABLE_ID=<TABLE_ID>\n"
    )


def check() -> tuple[bool, str]:
    """Kiểm app V2 thật sự đọc được table đã cấu hình. (ok, mô tả)."""
    if not enabled():
        return False, "chưa đặt BITABLE_APP_TOKEN/BITABLE_TABLE_ID"
    try:
        tables = lark_api.base_tables(config.BITABLE_APP_TOKEN)
    except lark_api.LarkError as exc:
        return False, f"không đọc được Base ({exc})"
    hit = next((t for t in tables
                if t["table_id"] == config.BITABLE_TABLE_ID), None)
    if not hit:
        names = ", ".join(f"{t.get('name')}={t['table_id']}" for t in tables)
        return False, (f"Base không có table {config.BITABLE_TABLE_ID}; "
                       f"đang có: {names or '(rỗng)'}")

    # Thiếu cột thì ghi record vẫn chạy nhưng dữ liệu theo dõi im lặng rơi mất —
    # nói ra ở doctor thay vì để người ta tự phát hiện khi mở Base.
    try:
        have = {f.get("name") for f in lark_api.base_fields(
            config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID)}
        missing = [s["name"] for s in SCHEMA if s["name"] not in have]
    except lark_api.LarkError:
        missing = []
    tail = (f" — THIẾU {len(missing)} cột: {', '.join(missing)}"
            f" (chạy `python -m v2 base-sync`)" if missing else "")
    return not missing, f"table '{hit.get('name')}' ({hit['table_id']}){tail}"
