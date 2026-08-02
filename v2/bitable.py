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

from . import config, jobstore, lark_api
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

STATUS_OPTIONS = [ST_SENT, ST_NO_RECAP, ST_FAILED, ST_NO_CONSENT]

# Tên field là KHÓA khi ghi record (base/v3 nhận map tên -> giá trị). Đổi tên
# field trên UI Base = code ghi hỏng. Muốn đổi nhãn thì đổi cả hai chỗ.
F_TITLE = "Cuộc họp"
F_WHEN = "Thời gian họp"
F_STATUS = "Trạng thái"
F_SUMMARY = "Tóm tắt"
F_DECISIONS = "Quyết định"
F_ACTIONS = "Việc cần làm"
F_RECIPIENTS = "Số người nhận"
F_SOURCE = "Nguồn người nhận"
F_APPROVER = "Người chốt"
F_APPROVED_AT = "Chốt lúc"
F_LINK = "Link Minutes"
F_TOKEN = "minute_token"

# Thêm 31/07/2026 theo yêu cầu user: theo dõi cụ thể ai/cái gì/mất bao lâu.
F_OWNER = "Chủ cuộc họp"          # tên người sở hữu minute (Lark trả owner_id)
F_OWNER_UID = "user_id chủ"       # user_id ngắn, tra chéo được với hệ thống khác
F_MEMBERS = "Người dự"            # mỗi dòng: "Tên — user_id"
F_TRANSCRIPT = "File transcript"  # attachment, bấm xem ngay trên Base
F_WHISPER_SEC = "Whisper (giây)"
F_AUDIO_SEC = "Audio (giây)"
F_WHISPER_X = "Tốc độ whisper"    # whisper/audio, vd "0.59x realtime"

TABLE_NAME = "Biên bản"

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
    {"name": F_APPROVER, "type": "text"},
    {"name": F_APPROVED_AT, "type": "datetime"},
    {"name": F_LINK, "type": "text"},
    {"name": F_TOKEN, "type": "text"},
    {"name": F_OWNER, "type": "text"},
    {"name": F_OWNER_UID, "type": "text"},
    {"name": F_MEMBERS, "type": "text"},
    {"name": F_TRANSCRIPT, "type": "attachment"},
    {"name": F_WHISPER_SEC, "type": "number"},
    {"name": F_AUDIO_SEC, "type": "number"},
    {"name": F_WHISPER_X, "type": "text"},
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


def ensure_status_options() -> bool:
    """Đổi bộ option của cột `Trạng thái` sang bộ mới. True nếu vừa đổi.

    Cần riêng một hàm vì `ensure_fields()` chỉ THÊM field còn thiếu — field đã
    tồn tại thì nó không đụng, nên Base tạo trước 31/07/2026 vẫn giữ option
    `draft`/`final` và ghi giá trị mới sẽ hỏng.

    ⚠️ Gửi `options` là THAY THẾ cả bộ: record đang giữ `draft` sẽ mất giá trị ô
    sau khi đổi. Đó là lý do `sync_tracking` đổ lại cột này cho mọi record —
    chạy `python -m v2 base-sync` ngay sau khi đổi.
    """
    if not enabled():
        return False
    fld = next((f for f in lark_api.base_fields(
        config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID)
        if f.get("name") == F_STATUS), None)
    if not fld:
        return False                      # chưa có cột -> ensure_fields tạo đúng
    have = [o.get("name") for o in (fld.get("options") or [])]
    if have == STATUS_OPTIONS:
        return False
    spec = {"name": F_STATUS, "type": "select", "multiple": False,
            "options": [{"name": s} for s in STATUS_OPTIONS]}
    try:
        lark_api.base_field_update(config.BITABLE_APP_TOKEN,
                                   config.BITABLE_TABLE_ID, fld["id"], spec)
    except lark_api.LarkError as exc:
        print(f"[base] đổi option cột {F_STATUS!r} hỏng: {exc}")
        return False
    print(f"[base] cột {F_STATUS!r}: {have} -> {STATUS_OPTIONS} "
          f"(chạy `python -m v2 base-sync` để đổ lại giá trị)")
    return True


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

    Suy luận đó chỉ đúng vì hàm này CHỈ được gọi cho job `delivered`
    (`write_draft` từ `_deliver_now`, `retry_missing_records` lọc
    `by_status("delivered")`, `sync_tracking` chỉ đụng record đã có). Job
    `failed` cũng có 0 dòng `deliveries` nhưng vì lý do khác hẳn — nó không bao
    giờ tới đây. Nếu sau này có ai cho job `failed` lên Base thì phải xét
    `jobs.status` trước, đừng để nó nhận nhãn này.
    """
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
        # Đổ lại cột `Trạng thái` theo nghĩa MỚI (việc phát). Chỉ cho job đã
        # chạy xong: job `queued`/`failed` chưa phát gì thì ghi "phát hỏng" là
        # nói dối — nó chưa tới lượt.
        if row.get("status") == "delivered":
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
    # Ưu tiên bản .txt người đọc được (`pipeline.txt_path` TÍNH ra đường dẫn,
    # không glob mò); không có thì đính kèm chính file .json trong DB.
    from . import pipeline
    target = None if skip_file else pipeline.txt_path(meta)
    if target and not target.exists():
        raw = Path(row.get("transcript_path") or "")
        target = raw if raw.exists() else None
    if target:
        try:
            ft = lark_api.base_media_upload(
                target, config.BITABLE_APP_TOKEN, _user_token(meta))
            if ft:
                out[F_TRANSCRIPT] = [{"file_token": ft}]
        except lark_api.LarkError as exc:
            print(f"[base] upload transcript hỏng ({exc}) — bỏ qua ô file")
    return out


def _user_token(meta: MeetingMeta) -> str:
    """access_token của NGƯỜI DÙNG để upload attachment (xem base_media_upload).

    Thứ tự ưu tiên: chủ bản ghi -> người dự -> người đã enroll mà Lark báo có dự
    -> bất kỳ ai đã enroll. Trả "" nếu không ai enroll — lúc đó upload sẽ thử
    bằng tenant token và (với app này) sẽ hỏng, nhưng chỉ mất ô file chứ không
    mất record.

    Dùng chung danh sách ứng viên với `pipeline._reader_candidates` (sửa
    02/08/2026). Trước đó chỉ thử `owner_open_id` rồi rơi thẳng xuống "người
    enroll ĐẦU TIÊN bất kỳ" — một người có thể chẳng liên quan gì tới cuộc họp
    này. Nhánh đó nay bị chạm thường xuyên hơn: từ khi `build_meta` tra ra CHỦ
    THẬT (§25), `owner_open_id` không còn luôn là người đã enroll nữa.
    """
    from . import pipeline, tokenstore
    for oid in pipeline._reader_candidates(meta):
        try:
            return tokenstore.get_access_token(oid)
        except Exception:                  # noqa: BLE001 — chưa enroll/token hỏng
            continue
    try:
        users = tokenstore.list_users(active_only=True)
        return tokenstore.get_access_token(users[0]["open_id"]) if users else ""
    except Exception:                      # noqa: BLE001
        return ""


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
        F_STATUS: delivery_status(meta.minute_token, recap),
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
    fields.update(_tracking_fields(meta))

    try:
        rid = lark_api.base_record_create(
            config.BITABLE_APP_TOKEN, config.BITABLE_TABLE_ID, fields)
    except lark_api.LarkError as exc:
        print(f"[base] ghi record hỏng ({exc}) — biên bản vẫn đã phát")
        return ""
    jobstore.set_status(meta.minute_token, row.get("status") or "delivered",
                        bitable_record_id=rid)
    print(f"[base] đã ghi record ({fields[F_STATUS]}): {rid}")
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


def retry_missing_records() -> int:
    """Ghi record cho job ĐÃ phát mà trên Base chưa có. Trả số record vừa tạo.

    Vì sao phải có (sửa 31/07/2026): `write_draft` cố ý chỉ log rồi đi tiếp khi
    ghi Base hỏng — biên bản đã tới tay người dự rồi, không được làm job
    `failed`. Nhưng sau đó KHÔNG có đường vá nào, kể cả bằng tay: `sync_tracking`
    bỏ qua mọi job không có `bitable_record_id` (nó chỉ đổ lại ô cho record đã
    tồn tại), và không lệnh nào tạo record thiếu. Hậu quả: một cú mất mạng lúc
    ghi Base là cuộc họp đó vĩnh viễn không lên Base, còn bot thì báo "CHƯA CÓ
    BIÊN BẢN" mãi mãi (`qa.pending_meetings` xét đúng cột này) dù đã phát xong.

    Gọi từ hai chỗ: cuối mỗi `process_queue` thật, và đầu `base-sync` (người
    chạy tay cũng phải vá được, không chỉ vòng `run`).

    Idempotent: `write_draft` tự tra Base theo `minute_token` trước khi tạo, nên
    chạy lại nhiều lần không sinh record trùng.
    """
    if not enabled():
        return 0
    n = 0
    for row in jobstore.by_status("delivered"):
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
        print(f"[base] {token} đã phát mà CHƯA có record trên Base -> ghi lại")
        if write_draft(meta, recap, n_recip):
            n += 1
    return n


# `mark_final()` và lệnh `python -m v2 base-final` ĐÃ BỎ (31/07/2026, Việc 5b).
# Chúng chỉ tồn tại để flip `draft` -> `final`, mà `final` không còn là một giá
# trị hợp lệ của cột `Trạng thái` nữa — giữ lại thì lệnh đó ghi một option không
# tồn tại vào Base. Cửa duyệt đã bỏ từ 30/07 nên cũng không còn ai "chốt" cái gì.
# Hai cột `Người chốt` / `Chốt lúc` trên Base nay không ai ghi; để trống vô hại,
# muốn dọn thì xoá tay trên UI Base.


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
