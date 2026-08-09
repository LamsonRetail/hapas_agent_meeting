"""
Orchestrator V2 — một process, một máy (V2_ARCHITECTURE §3).

Hai việc:
  1. scan_once()      polling: quét minute mới của từng người enroll -> job
  2. process_queue()  xử lý job queued: transcribe + recap -> PHÁT NGAY

KHÔNG có cửa duyệt (bỏ 30/07/2026 theo yêu cầu): họp xong là mọi người trong
sự kiện lịch của host nhận cả tóm tắt lẫn transcript. Hệ quả phải biết: không
còn ai xem `participants_source` trước khi phát, nên chuỗi tra người dự ở
meetings.py PHẢI giữ nguyên tính fail-closed — thà không gửi còn hơn gửi cho
người không dự.

Polling là NGUỒN SỰ THẬT (§14): hỏi trạng thái hiện tại, không quan tâm bỏ
lỡ event nào. WebSocket (ws_listener.py) chỉ là đường nhanh gọi enqueue sớm.

Chống trùng bằng khóa thật db.try_claim_minute() — an toàn cả khi event và
polling cùng thấy một cuộc họp.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from . import (alerts, backup, cards, config, db, jobstore, lark_api, meetings,
               pipeline, summarize, tokenstore, transcribe, whisper_supervisor)
from .models import MeetingMeta


def _now_ms() -> int:
    return int(time.time() * 1000)


def _explicit_viewer(meta: MeetingMeta, open_id: str = "",
                     union_id: str = "") -> bool:
    """True khi metadata job tự chứng minh người này thuộc cuộc họp.

    `minutes/search(participant_ids=...)` KHÔNG phải bằng chứng tham dự: đo thật
    05/08/2026 cho thấy nó vẫn trả bản ghi được chia sẻ/đọc được của người khác.
    Chỉ owner thật từ `minutes_get.owner_id` hoặc attendee đã resolve từ Calendar
    / VC mới được dùng cho ACL, gửi tự động và welcome.
    """
    ids = {x for x in (open_id, union_id) if x}
    if not ids:
        return False
    trusted = {meta.owner_open_id} if meta.owner_open_id else set()
    # Metadata cũ `calendar[near…]` được dựng chỉ vì gần giờ và đã ghép nhầm
    # sự kiện thật. Owner từ minutes_get vẫn dùng được; attendee của nguồn này
    # thì không. Tìm chuỗi con vì row bị cách ly mang tiền tố
    # `unsafe_near_rejected:` — near khi đó không còn ở đầu chuỗi.
    if "calendar[near" not in (meta.participants_source or ""):
        trusted |= {x for a in meta.attendees
                    for x in (a.open_id, a.union_id) if x}
    return bool(ids & trusted)


def _verified_job_viewer(minute_token: str, open_id: str = "",
                         union_id: str = "") -> MeetingMeta | None:
    """Trả metadata khi job xác nhận viewer; dữ liệu thiếu/méo thì đóng."""
    row = jobstore.get(minute_token)
    if not row:
        return None
    try:
        meta = jobstore.meta_from_json(row["meta_json"])
    except Exception:                       # job cũ méo: không đoán quyền
        return None
    return meta if _explicit_viewer(meta, open_id, union_id) else None


def _note_verified_viewer(minute_token: str, open_id: str, union_id: str = "",
                          name: str = "") -> MeetingMeta | None:
    """Chỉ ghi dấu reader sau khi metadata độc lập xác nhận quan hệ cuộc họp."""
    meta = _verified_job_viewer(minute_token, open_id, union_id)
    if meta is not None:
        db.note_viewer(minute_token, open_id, union_id, name)
    return meta


# =====================================================================
#  1. Phát hiện (polling) + enqueue
# =====================================================================

def enqueue_minute(reader_open_id: str, minute_token: str,
                   raw_item: dict | None = None, *,
                   priority: int = 1, notify: bool = True) -> bool:
    """Dựng job cho một minute nếu chưa ai xử lý. True nếu vừa tạo job.

    Dùng chung cho cả polling và event. reader_open_id là người mà ta có
    token để đọc minute (thường là người mà minute xuất hiện trong danh sách).

    priority: 1 = cuộc mới (mặc định), 0 = backlog lúc enroll.
    notify:   gửi thẻ "họp xong" (Minute+tóm tắt) hay không. Backlog thì TẮT —
              cuộc cũ đã liệt kê trong tin chào, đừng báo "họp xong" lần nữa.
    """
    if not db.try_claim_minute(minute_token):
        return False                     # người/luồng khác đã chiếm
    try:
        token = tokenstore.get_access_token(reader_open_id)
        meta = meetings.build_meta(token, minute_token, raw_item)
        meetings.resolve_participants(token, meta)

        if not meta.attendees and config.FALLBACK_TO_OWNER:
            # Chỉ fallback khi API đã xác minh người phát hiện là CHỦ THẬT.
            # Thiếu owner tuyệt đối không được lấy reader thay vào: reader có
            # thể chỉ là người được share bản ghi, và fallback đó từng biến họ
            # thành owner + người nhận biên bản của người khác.
            u = tokenstore.list_users(active_only=False)
            info = next((x for x in u if x["open_id"] == reader_open_id), None)
            if (meta.owner_open_id == reader_open_id
                    and info and info.get("union_id")):
                from .models import Attendee
                meta.attendees = [Attendee(open_id=reader_open_id,
                                           union_id=info["union_id"],
                                           name=info.get("name", ""))]
                meta.participants_source += " -> fallback:owner"
            elif not meta.owner_open_id:
                print(f"[enqueue] {minute_token}: không rõ owner; KHÔNG fallback "
                      f"sang reader {reader_open_id[:12]}…")

        jobstore.create(meta, status="queued", priority=priority)
        print(f"[enqueue] {meta.title}  ({minute_token})  "
              f"{meta.invitee_count} người [{meta.participants_source}] "
              f"prio={priority}")
        # Báo NGAY khi họp xong (mô hình kéo): gửi tóm tắt Minute Lark + lời
        # mời transcript. Whisper chạy nền -> held, chờ người dự tự hỏi. Không
        # được làm hỏng enqueue: báo hỏng thì job vẫn còn, vòng sau vẫn dịch.
        if notify:
            try:
                _notify_minute(meta)
            except Exception as exc:          # noqa: BLE001
                print(f"[notify] {minute_token} báo hỏng (bỏ qua): {exc}")
        return True
    except Exception as exc:             # noqa: BLE001
        # Nhả khóa để vòng sau thử lại; không để job kẹt vì lỗi tạm thời.
        db.release_minute(minute_token)
        print(f"[enqueue] {minute_token} hỏng, nhả khóa: {exc}")
        return False


def _notify_minute(meta: MeetingMeta) -> None:
    """Báo NGAY khi họp xong: tóm tắt Minute Lark + link + lời mời transcript.

    Gửi cho người dự đã enroll (`_recipients`). KÉO: KHÔNG kèm transcript
    whisper (đang chạy nền -> held, chờ hỏi). Gate theo SEND_MODE — dry-run thì
    chỉ in. Lấy Minute hỏng thì vẫn gửi, chỉ thiếu phần tóm tắt.

    KHÔNG còn nhận `access_token` (07/08/2026): token của người phát hiện ra
    cuộc họp gần như không bao giờ là token đọc được bản chép. Việc chọn token
    nay là của `larktext`, chỗ duy nhất biết cả thang ứng viên.
    """
    recips, _ = _recipients(meta)
    if not recips:
        return
    # THỬ TỪNG token ứng viên, không chỉ token của người tình cờ phát hiện ra
    # cuộc họp (sửa 07/08/2026). Bản cũ gọi thẳng `minutes_transcript` bằng
    # `access_token` — token của reader — mà quyền đọc bản chép thì gắn với CHỦ
    # bản ghi. Nên với phần lớn cuộc họp, lời gọi đó hỏng, `minute_text` thành
    # rỗng, thẻ "Họp xong" đi ra không có tóm tắt, và không ai hỏi lại lần nào
    # nữa. Đó chính là gốc của "vào Lark thì có đủ text mà bot bảo không đọc
    # được". `larktext` dùng đúng thang ứng viên của `download_recording` và
    # lưu lại kết quả, nên câu hỏi đầu tiên của người dùng cũng dùng lại được.
    try:
        from . import larktext
        minute_text = larktext.fetch(meta)
    except Exception as exc:                  # noqa: BLE001 — báo vẫn phải gửi
        minute_text = ""
        print(f"[notify] {meta.minute_token} không lấy được Minute Lark "
              f"(gửi không kèm tóm tắt): {exc}")
    recap = summarize.recap_from_text(minute_text, meta.title)
    # Lưu tóm tắt Minute vào job để bước `held` ghi Base dùng lại (khỏi gọi LLM
    # lần hai). Giữ nguyên status 'queued' — chỉ ghi thêm recap_json.
    try:
        import json as _json
        jobstore.set_status(meta.minute_token, "queued", recap_json=_json.dumps(
            {"summary": recap.summary, "decisions": recap.decisions,
             "action_items": [a.__dict__ for a in recap.action_items]},
            ensure_ascii=False))
    except Exception as exc:                  # noqa: BLE001 — lưu recap là phụ
        print(f"[notify] {meta.minute_token} lưu recap_json hỏng (bỏ qua): {exc}")
    card = cards.minute_notice_card(meta, recap)
    if not config.SEND_MODE:
        print(f"[notify] (dry-run) {meta.minute_token} -> báo Minute+tóm tắt "
              f"cho {len(recips)} người")
        return
    ok = 0
    for rid in recips:
        try:
            lark_api.im_send_card(rid, card, id_type="union_id",
                                  uuid_key=f"notice-{meta.minute_token}-{rid}"[:50])
            ok += 1
        except lark_api.LarkError as exc:
            print(f"[notify] gửi báo cho {rid} hỏng: {exc}")
    print(f"[notify] {meta.minute_token} -> báo Minute+tóm tắt: "
          f"{ok}/{len(recips)} người")


ENROLL_FAST_POLL_S = 5


def _sleep_with_fast_enroll(total: float) -> None:
    """Ngủ hết `total` giây, nhưng kéo hộp thư OAuth mỗi 5 giây KHI có người
    đang cấp quyền dở.

    Vì sao (user chốt 05/08/2026): luồng thật là "bấm nút → đồng ý → nhận ngay
    danh sách 7 ngày". Vòng run ngủ `POLL_INTERVAL` = 300 giây, nên trước đây
    người dùng bấm xong ngồi nhìn khung chat trống tới 5 phút và tưởng hỏng.

    Chỉ poll nhanh khi `has_live_nonce()` — không có ai enroll dở thì đây vẫn là
    một giấc ngủ dài, không tốn thêm lời gọi mạng nào. Nonce hết hạn là tự về
    nhịp cũ, nên một người bỏ dở giữa chừng không làm hệ thống poll mãi mãi.

    KHÔNG được ném: đây nằm trong vòng lặp chính, một lỗi hộp thư không được
    làm chết orchestrator.

    `oauth` phải import CỤC BỘ: module này không import nó ở cấp file (vòng
    `run` cũng làm vậy, dòng ~1234) vì `oauth` import ngược lại `orchestrator`.
    Bản đầu của hàm này quên mất và `try` nuốt luôn `NameError`, nên poll nhanh
    chết âm thầm — log chỉ có một dòng "không kiểm được nonce" mỗi 5 phút.
    """
    from . import oauth
    end = time.monotonic() + max(0.0, total)
    while True:
        remain = end - time.monotonic()
        if remain <= 0:
            return
        try:
            fast = oauth.has_live_nonce()
        except Exception as exc:              # noqa: BLE001 — xem docstring
            print(f"[enroll] không kiểm được nonce (ngủ bình thường): {exc}")
            fast = False
        if not fast:
            time.sleep(remain)
            return
        time.sleep(min(ENROLL_FAST_POLL_S, remain))
        if time.monotonic() >= end:
            return
        try:
            oauth.poll_pending()
        except Exception as exc:              # noqa: BLE001 — xem docstring
            print(f"[enroll] kéo nhanh hộp thư hỏng (bỏ qua): {exc}")


def welcome_and_backlog(info: dict) -> None:
    """Sau khi một người enroll: chào + liệt kê cuộc họp gần đây của họ + tạo
    backlog (priority 0) để dịch dần + `note_viewer` để họ pull được.

    KÉO: KHÔNG tự gửi transcript nào. Người mới nhắn 'gửi transcript [tên]' thì
    `sendfile` nâng cực cao + tự gửi. Gọi từ `oauth.poll_pending` (đường tự phục
    vụ — người vừa nhắn bot, nhắn lại cho họ là đúng). KHÔNG được ném: enroll đã
    xong rồi, một phép quét hỏng không được làm hỏng việc đó.

    HAI CỬA SỔ KHÁC NHAU, đừng gộp (06/08/2026):

      `ENROLL_BACKFILL_DAYS` — quét lùi bao nhiêu ngày để TẠO BACKLOG.
      `LOOKBACK_DAYS`        — liệt kê bao nhiêu ngày trong THẺ CHÀO.

    Vì sao phải tách: người dùng muốn ai kết nối cũng có sẵn 90 ngày cuộc họp
    cũ, nhưng một thẻ chào liệt kê 90 ngày là vài chục dòng ngay ở tin nhắn đầu
    tiên — không ai đọc. Nạp nền thì im lặng và có ích; liệt kê hết thì ồn và
    vô dụng. Backlog vẫn `priority=0` + `notify=False` nên cuộc mới luôn chen
    trước và không ai bị nhắn về cuộc họp từ tháng 3.

    Mặc định `ENROLL_BACKFILL_DAYS = LOOKBACK_DAYS`, tức KHÔNG đổi hành vi cho
    tới khi người vận hành tự đặt trong `.env`. Đọc `docs/CURRENT_CONTEXT.md`
    §11c trước khi nới: nới lên 90 làm mỗi người mới nạp thêm vài chục job vào
    một máy phiên âm bằng CPU.
    """
    union_id = info.get("union_id", "")
    open_id = info.get("open_id", "")
    name = info.get("name") or open_id or "bạn"
    if not union_id:
        return
    titles: list[str] = []
    # Chỉ tính lần một: mốc phân giới cho THẺ CHÀO. Cuộc cũ hơn mốc này vẫn
    # được tạo backlog, chỉ không xuất hiện trong tin nhắn chào.
    now_ms = _now_ms()
    welcome_from_ms = now_ms - config.LOOKBACK_DAYS * 86_400_000
    days = max(config.ENROLL_BACKFILL_DAYS, config.LOOKBACK_DAYS)
    try:
        token = tokenstore.get_access_token(open_id)
        items = lark_api.minutes_list(
            token, now_ms - days * 86_400_000, now_ms, open_id)
    except Exception as exc:                  # noqa: BLE001 — xem docstring
        items = []
        print(f"[welcome] {name}: không quét được minute {days} ngày: {exc}")
    for it in items:
        mt = it.get("token") or it.get("minute_token")
        if not mt:
            continue
        # Tạo backlog nếu chưa có job: priority 0, KHÔNG báo "họp xong".
        if not (db.is_claimed(mt) or jobstore.get(mt)):
            try:
                enqueue_minute(open_id, mt, it, priority=0, notify=False)
            except Exception as exc:          # noqa: BLE001
                print(f"[welcome] backlog {mt} hỏng (bỏ qua): {exc}")

        # Search hit chỉ là ứng viên phát hiện. Chỉ đưa vào welcome và cấp quyền
        # pull nếu job metadata xác nhận owner/attendee thật.
        meta = _note_verified_viewer(mt, open_id, union_id, name)
        if meta is None:
            print(f"[welcome] {name}: bỏ {mt} khỏi danh sách — search thấy nhưng "
                  "metadata không xác nhận owner/attendee")
            continue
        # Cuộc cũ hơn cửa sổ thẻ chào: đã tạo backlog ở trên rồi, chỉ không nêu
        # ra ở tin nhắn đầu. `meta.start` là giây; không có giờ thì coi như cũ
        # và im lặng — thà thiếu một dòng còn hơn nói sai "trong 7 ngày qua".
        if (meta.start or 0) * 1000 < welcome_from_ms:
            continue
        titles.append(meta.title or mt)

    # `oauth.complete()` đã thử đánh thức theo owner/attendees có sẵn. Quét trên
    # vừa ghi lại viewer đã được metadata xác nhận, nên thử LẠI ở đây để cứu
    # job liên quan. Idempotent; không gửi transcript và
    # tuyệt đối không tự gửi link OAuth cho ai.
    try:
        released = jobstore.release_waiting_auth(open_id, union_id)
        if released:
            print(f"[welcome] {name}: {len(released)} job chờ quyền -> backlog")
    except Exception as exc:                  # noqa: BLE001 — chào hỏng không phá enroll
        print(f"[welcome] không đánh thức được job chờ quyền cho {name}: {exc}")

    # Danh sách dựng bằng CHÍNH bộ dựng của bot (`qa.list_text`) chứ không tự
    # ghép tên nữa (05/08/2026). Lý do: bản cũ chỉ có bullet tên cuộc, không
    # link Minutes, không nói có bản gỡ băng — trong khi hỏi bot thì lại thấy
    # đủ. Hai định dạng cho cùng một dữ liệu là hai chỗ phải nhớ sửa, và đã
    # lệch thật. Dùng chung thì chân trang "có bản gỡ băng đầy đủ" tự có mặt.
    from . import askers, qa
    body = ""
    if titles:
        try:
            who = askers.find_enrolled(open_id)
            if who:
                # PHẢI truyền `since` (sửa 05/08/2026): `list_text` không lọc
                # thời gian thì trả về MỌI cuộc họp người đó được xem, trong khi
                # câu ngay bên trên nói "7 ngày qua". Đo thật: thẻ chào ghi 7
                # cuộc, gồm cả cuộc ngày 27/07 — tức 9 ngày trước.
                since = datetime.fromtimestamp(
                    (_now_ms() - config.LOOKBACK_DAYS * 86_400_000) / 1000
                ).strftime("%Y-%m-%d")
                body = qa.list_text(who, since=since)
        except Exception as exc:              # noqa: BLE001 — chào hỏng không phá enroll
            print(f"[welcome] {name}: dựng danh sách hỏng, dùng bản rút gọn: {exc}")
        if not body:
            shown = "\n".join(f"• {t}" for t in titles[:15])
            more = f"\n…và {len(titles) - 15} cuộc nữa" if len(titles) > 15 else ""
            body = (f"Mình thấy bạn có {len(titles)} cuộc họp trong 7 ngày "
                    f"qua:\n\n{shown}{more}")
        body += ("\n\nTừ giờ họp xong mình tự gửi Minute + tóm tắt cho bạn.")
    else:
        body = ("Hiện chưa thấy cuộc họp nào của bạn trong 7 ngày qua. Từ giờ "
                "họp xong mình sẽ tự gửi Minute + tóm tắt cho bạn.")

    if not config.SEND_MODE:
        print(f"[welcome] (dry-run) chào {name}: {len(titles)} cuộc, backlog đã tạo")
        return

    try:
        from . import cards
        lark_api.im_send_card(union_id, cards.welcome_card(name, body),
                              id_type="union_id")
    except lark_api.LarkError as exc:
        print(f"[welcome] thẻ chào hỏng ({exc}) — gửi lại bằng text")
        try:
            lark_api.im_send_text(union_id, f"Xong rồi {name}!\n\n{body}",
                                  id_type="union_id")
        except lark_api.LarkError as exc2:
            print(f"[welcome] không nhắn được cho {name}: {exc2}")


def backfill_missing(days: int, *, dry_run: bool = True,
                     priority: int = 0) -> list[dict]:
    """Nạp bù cuộc họp CŨ HƠN cửa sổ quét thường. Trả danh sách đã/sẽ nạp.

    Vì sao cần một lệnh riêng (đo 05/08/2026): `scan_once` chỉ nhìn lùi
    `LOOKBACK_DAYS` (=7) mỗi vòng, và đó là TOÀN BỘ khả năng bắt bù của hệ thống
    — `config.LOOKBACK_DAYS` đã ghi rõ hệ quả. Hệ thống bắt đầu chạy ~30/07 nên
    mọi cuộc họp trước 23/07 chưa bao giờ lọt vào tầm nhìn và sẽ không bao giờ
    lọt nữa. Đo thật trên tenant này: Lark có 71 minute trong 180 ngày mà chính
    nó xếp một người đã enroll là người dự, DB chỉ có 23 — thiếu 48 cuộc, trải
    từ 05/03 tới 28/07, gồm cả HỌP BOD và Review Q2/Chiến lược Q3.

    KHÔNG nới `LOOKBACK_DAYS` để chữa việc này. Nới là mỗi vòng quét, mãi mãi,
    kéo về 150 item/người cho một việc chỉ cần làm một lần — và tệ hơn: vòng
    quét gọi `enqueue_minute` với `notify=True`, tức mọi người dự đã enroll sẽ
    nhận thẻ "họp xong" cho những cuộc từ tháng 3.

    Ba ràng buộc, cưỡng chế bằng code:

     1. `notify=False` LUÔN LUÔN, không phụ thuộc `SEND_MODE`. Đây là nạp bù dữ
        liệu cũ, không phải phát hiện cuộc họp mới — không tin nào được gửi.
     2. `priority=0` (backlog): cuộc họp MỚI và người đang hỏi transcript vẫn
        chen lên trước, `_process_queue` chỉ chạy 1 backlog mỗi vòng.
     3. Nguồn là `minutes_list(..., participant_open_id=oid)` — bản lọc THEO
        NGƯỜI. Không dùng nhánh không-lọc (`SCAN_ALL_VISIBLE`): nó kéo cả cuộc
        của phòng khác mà không ai trong hệ thống dự (đã bật rồi tắt 04/08).

    Quyền: giống hệt `welcome_and_backlog` — `_note_verified_viewer` chỉ ghi dấu
    khi metadata job tự xác nhận owner/attendee, nên nạp bù KHÔNG nới ACL cho ai.
    """
    users = tokenstore.list_users(active_only=True)
    if not users:
        print("[backfill] chưa có ai enroll — không có token nào để đọc Lark")
        return []

    end = _now_ms()
    start = end - max(1, int(days)) * 86_400_000
    found: dict[str, dict] = {}
    for u in users:
        oid = u["open_id"]
        try:
            token = tokenstore.get_access_token(oid)
        except Exception as exc:              # noqa: BLE001 — một token chập không làm mù cả lượt
            print(f"[backfill] {u['name'] or oid}: bỏ qua ({exc})")
            continue
        for it in lark_api.minutes_list(token, start, end, oid):
            mt = it.get("token") or it.get("minute_token")
            if not mt:
                continue
            rec = found.setdefault(mt, {"minute_token": mt, "item": it,
                                        "readers": []})
            rec["readers"].append(u)

    todo = [r for mt, r in found.items()
            if not (db.is_claimed(mt) or jobstore.get(mt))]
    print(f"[backfill] {days} ngày: Lark có {len(found)} minute, "
          f"DB đã có {len(found) - len(todo)}, cần nạp {len(todo)}")
    if dry_run:
        for r in todo:
            it = r["item"]
            title = (it.get("topic") or it.get("title")
                     or it.get("display_info") or "").split("\n", 1)[0].strip()
            import html as _html
            print(f"  (thử khô) {r['minute_token']}  {_html.unescape(title)[:56]}")
        return todo

    done: list[dict] = []
    for r in todo:
        mt = r["minute_token"]
        # Mượn token của TỪNG người thấy minute này, theo thứ tự, tới khi có ai
        # dựng được job. Chủ bản ghi là người chắc chắn đọc được nhất nhưng ta
        # chưa biết đó là ai trước khi `build_meta` chạy — nên cứ thử lần lượt.
        for u in r["readers"]:
            try:
                ok = enqueue_minute(u["open_id"], mt, r["item"],
                                    priority=priority, notify=False)
            except Exception as exc:          # noqa: BLE001 — một cuộc hỏng không dừng cả lượt
                print(f"[backfill] {mt} mượn {u['open_id'][:12]}… hỏng: {exc}")
                continue
            if not ok:
                continue
            _note_verified_viewer(mt, u["open_id"], u.get("union_id", ""),
                                  u.get("name", ""))
            done.append(r)
            break
        else:
            print(f"[backfill] {mt}: không người nào dựng được job — bỏ qua")
    print(f"[backfill] đã nạp {len(done)}/{len(todo)} job (priority={priority}, "
          f"KHÔNG gửi tin cho ai)")
    return done


def scan_once() -> int:
    """Quét minute mới cho mọi người enroll. Trả số job mới tạo."""
    users = tokenstore.list_users(active_only=True)
    if not users:
        print("[scan] chưa có ai enroll — bỏ qua")
        return 0

    end = _now_ms()
    start = end - config.LOOKBACK_DAYS * 86_400_000
    created = 0
    for u in users:
        oid = u["open_id"]
        try:
            token = tokenstore.get_access_token(oid)
        except tokenstore.TokenError as exc:
            print(f"[scan] {u['name'] or oid}: {exc}")
            continue
        except Exception as exc:         # noqa: BLE001 — xem dưới
            # KHÔNG chỉ bắt TokenError (sửa 02/08/2026): `get_access_token` gọi
            # `refresh_user_token` khi access sắp hết hạn, và lời gọi đó là POST
            # nên KHÔNG có retry — một cú 429/5xx/mạng chớp là `LarkError` bay
            # thẳng ra khỏi vòng `for u in users`, tức những người CÒN LẠI không
            # được quét gì trong vòng đó. Một người có token chập không được
            # phép làm mù cả hệ thống.
            print(f"[scan] {u['name'] or oid}: lỗi khi lấy token, bỏ qua người "
                  f"này trong vòng này: {exc}")
            continue
        # HAI NGUỒN, cố ý tách bạch:
        #  (1) lọc theo chính người này -> ứng viên mà tài khoản này tìm thấy.
        #      Nó KHÔNG tự chứng minh họ có dự; Search vẫn có thể trả bản ghi
        #      được share của người khác. Quyền chỉ được ghi sau khi job metadata
        #      xác nhận owner/attendee.
        #  (2) không lọc người -> mọi minute token này MỞ XEM ĐƯỢC. Chỉ để NẠP.
        #      Cần vì Lark không xếp người ta vào `participant_ids` của mọi bản
        #      ghi họ thực sự dự: đo trên tài khoản BOD ra 5 so với 7, hai cuộc
        #      chênh đều có ghi hình và người đó khẳng định có ngồi họp. Trước
        #      đây chúng rơi khỏi hệ thống không để lại dấu vết nào.
        # Gộp nguồn (2) mà VẪN note_viewer là mở toang quyền: người chỉ được
        # chia sẻ bản ghi sẽ thành "người dự" và kéo được biên bản. Đừng gộp.
        items = lark_api.minutes_list(token, start, end, oid)
        mine = {it.get("token") or it.get("minute_token") for it in items}
        if config.SCAN_ALL_VISIBLE:
            extra = [it for it in lark_api.minutes_list(token, start, end, "")
                     if (it.get("token") or it.get("minute_token")) not in mine]
            if extra:
                print(f"[scan] {u['name'] or oid}: +{len(extra)} minute chỉ MỞ "
                      f"XEM ĐƯỢC (không nằm trong danh sách người dự của Lark)")
            items = items + extra
        for it in items:
            mt = it.get("token") or it.get("minute_token")
            if not mt:
                continue
            if mt not in mine:
                # Nguồn (2): nạp thôi, KHÔNG ghi là người xem.
                if not (db.is_claimed(mt) or jobstore.get(mt)):
                    waited = (_now_ms() - db.note_seen(mt)) / 60_000
                    if waited >= config.SETTLE_MINUTES and enqueue_minute(oid, mt, it):
                        created += 1
                        db.clear_seen(mt)
                continue
            if db.is_claimed(mt) or jobstore.get(mt):
                _note_verified_viewer(mt, oid, u.get("union_id", ""),
                                      u.get("name", ""))
                continue
            # Chờ Lark liên kết bản ghi với cuộc họp trước khi tra người dự;
            # hỏi sớm thì vc recording trả rỗng -> mất danh sách người được
            # mời và im lặng rơi về gửi-cho-một-người. Xem db.minutes_seen.
            waited_min = (_now_ms() - db.note_seen(mt)) / 60_000
            if waited_min < config.SETTLE_MINUTES:
                print(f"[scan] {mt} mới thấy {waited_min:.1f}/"
                      f"{config.SETTLE_MINUTES} phút — chờ Lark xử lý xong")
                continue
            if enqueue_minute(oid, mt, it):
                created += 1
                db.clear_seen(mt)
                _note_verified_viewer(mt, oid, u.get("union_id", ""),
                                      u.get("name", ""))
    print(f"[{datetime.now():%H:%M:%S}] scan xong, {created} job mới")
    return created


# =====================================================================
#  2. Xử lý hàng đợi
# =====================================================================

# Chỉ MỘT lượt xử lý hàng đợi tại một thời điểm trong cùng tiến trình.
#
# Vì sao cần (02/08/2026): `ws_listener` chạy trong THREAD NỀN và gọi thẳng
# `process_queue()`, trong khi vòng `run()` ở thread chính cũng gọi. Khoá
# `try_claim_minute` chỉ bảo vệ lúc TẠO job, không bảo vệ lúc XỬ LÝ — nên hai
# lượt chồng nhau có thể phiên âm và PHÁT CÙNG MỘT BIÊN BẢN HAI LẦN cho tất cả
# người dự. Hôm nay chưa xảy ra vì sổ tay dặn đừng bật `run --ws` (lý do khác:
# tranh WebSocket với Hermes) — nhưng "đừng bật" là loại dặn dò sẽ bị quên, và
# hậu quả thì nhìn thấy ngay trong chat của mọi người.
#
# Bỏ lượt chứ không xếp hàng: hai lượt liên tiếp không làm được gì hơn một lượt,
# vì lượt đang chạy sẽ quét lại cả hàng đợi ở cuối.
_queue_lock = threading.Lock()


def process_queue(dry_run: bool | None = None) -> None:
    """Xử lý job queued -> transcribe + recap -> phát ngay cho người dự."""
    if not _queue_lock.acquire(blocking=False):
        print("[queue] đã có một lượt xử lý đang chạy — bỏ lượt này")
        return
    try:
        _process_queue(dry_run)
    finally:
        _queue_lock.release()


def _process_queue(dry_run: bool | None = None) -> None:
    dry_run = (not config.SEND_MODE) if dry_run is None else dry_run
    backlog_done = 0
    for row in jobstore.active_by_priority():
        token = row["minute_token"]
        priority = int(row["priority"]) if row["priority"] is not None else 1
        # Backlog (priority 0) chỉ chạy 1 job/vòng: whisper CPU không cắt ngang
        # được, nhưng đừng ôm cả loạt backlog trong một vòng — để cuộc mới (1) và
        # cực cao (2), vốn đã đứng trước nhờ active_by_priority, luôn chạy trước.
        if priority <= 0:
            if backlog_done >= 1:
                continue
            backlog_done += 1
        if dry_run:
            # Dry-run KHÔNG được tiêu quota thử lại, và KHÔNG được ghi gì.
            # `process` (không --send) là lệnh CHẨN ĐOÁN — người ta chạy nó vài
            # lần để xem sẽ gửi cho ai. Trước 31/07/2026 nó cộng `attempts` y
            # như lần chạy thật: hai lần dry-run + một lần gửi = +3, đủ để một
            # job đang ở 3 lần bị đánh `failed` VĨNH VIỄN trước khi kịp gửi.
            # Đã mất một job thật đúng như vậy.
            attempts = int(row["attempts"] or 0)
            if attempts >= config.MAX_ATTEMPTS:
                print(f"[queue] {token} (dry-run) attempts={attempts}/"
                      f"{config.MAX_ATTEMPTS} — lần GỬI THẬT tới sẽ bị bỏ")
        else:
            attempts = jobstore.bump_attempts(token)
            if attempts > config.MAX_ATTEMPTS:
                # GIỮ lỗi thật, chỉ thêm ghi chú vào trước. Trước 31/07/2026 dòng
                # này ghi đè `error` bằng "quá N lần thử", tức xoá luôn nguyên
                # nhân — và đó chính là câu mà DM cảnh báo in ra ở dòng "Lỗi
                # cuối". Đã trả giá thật: job HRIS `failed` mà không còn cách nào
                # biết nó hỏng vì tải media, ffmpeg, hay gì khác.
                why = (row["error"] or "").strip() or "(không ghi lại được)"
                jobstore.set_status(
                    token, "failed",
                    error=f"quá {config.MAX_ATTEMPTS} lần thử — lỗi cuối: {why}")
                print(f"[queue] {token} bỏ sau {attempts} lần thử: {why}")
                continue

        meta = jobstore.meta_from_json(row["meta_json"])
        done = _reuse(token, row)
        if done:
            t, recap = done
            if recap is not None:
                print(f"[queue] {token} đã có transcript+recap -> phát luôn, "
                      f"không phiên âm lại")
        else:
            try:
                t, _ = pipeline.run_transcription(meta)
                recap = None
            except pipeline.WaitingForAuth as exc:
                # Chưa có token của người có thể tải: park, KHÔNG retry mỗi vòng,
                # KHÔNG alert/DM mời OAuth. Chỉ `oauth.complete()` của chính một
                # người liên quan mới trả job về backlog.
                if dry_run:
                    print(f"[queue] {token} (dry-run) đang CHỜ người có quyền tự "
                          f"OAuth: {exc}")
                    continue
                jobstore.set_status(token, "waiting_auth", error=str(exc), priority=0)
                print(f"[queue] {token} -> waiting_auth (không tự gửi link, không "
                      f"retry nóng): {exc}")
                continue
            except pipeline.MediaDenied as exc:
                # Ngược hẳn với hai nhánh dưới: cái này KHÔNG tự khỏi. Không ai
                # được phép tải bản ghi thì vòng sau, và vòng sau nữa, vẫn vậy.
                # Đốt 5 lần thử trong 25 phút rồi báo "quá 5 lần thử" là xoá mất
                # nguyên nhân — đã trả giá thật với job HRIS 31/07, và phải mở
                # lại bằng tay mới biết là 2091005.
                # PHẢI bắt TRƯỚC `except Exception`: MediaDenied là PipelineError.
                if dry_run:
                    print(f"[queue] {token} (dry-run) KHÔNG tải được bản ghi "
                          f"(quyền) — lần chạy THẬT sẽ đánh failed ngay: {exc}")
                    continue
                jobstore.set_status(token, "failed", error=str(exc))
                print(f"[queue] {token} KHÔNG tải được bản ghi (quyền) — "
                      f"đánh failed NGAY, không thử lại: {exc}")
                continue
            except pipeline.EmptyTranscript as exc:
                # Cùng lý lẽ với MediaDenied ngay trên: KHÔNG tự khỏi, nên dừng
                # NGAY thay vì đốt 5 lần thử rồi báo "quá 5 lần thử". Khác chỗ
                # quan trọng nhất so với hành vi cũ: trước 02/08/2026 nhánh này
                # không tồn tại và job đi tiếp thành `delivered` — người dự nhận
                # một thẻ tóm tắt "Bản ghi trống" mà không có transcript, còn
                # mọi bảng trạng thái đều báo thành công.
                # `failed` là thứ `alerts._check_failed_jobs` nhìn thấy -> có DM.
                if dry_run:
                    print(f"[queue] {token} (dry-run) transcript RỖNG — lần chạy "
                          f"THẬT sẽ đánh failed ngay: {exc}")
                    continue
                jobstore.set_status(token, "failed", error=str(exc))
                print(f"[queue] {token} transcript RỖNG — đánh failed NGAY, "
                      f"không thử lại: {exc}")
                continue
            except transcribe.TranscribeUnavailable as exc:
                # HẠ TẦNG hỏng (whisper tắt/treo), không phải lỗi cuộc họp này.
                # Trả lại lần thử, nếu không thì vòng `run` 5 phút/lần sẽ đốt hết
                # MAX_ATTEMPTS trong ~25 phút và job `failed` VĨNH VIỄN dù nội
                # dung không sai gì — mất luôn biên bản của cuộc họp đó, im lặng.
                if dry_run:
                    print(f"[queue] {token} (dry-run) whisper KHÔNG dùng được: "
                          f"{exc}")
                    continue
                jobstore.unbump_attempts(token)
                jobstore.set_status(token, "queued", error=str(exc))
                print(f"[queue] {token} whisper KHÔNG dùng được — "
                      f"KHÔNG tính lần thử (attempts giữ {attempts - 1}): {exc}")
                continue
            except Exception as exc:     # noqa: BLE001
                if dry_run:
                    print(f"[queue] {token} (dry-run) transcription hỏng: {exc}")
                    continue
                jobstore.set_status(token, "queued", error=str(exc))
                print(f"[queue] {token} transcription hỏng "
                      f"(thử {attempts}/{config.MAX_ATTEMPTS}): {exc}")
                continue

        # ĐÃ CÓ transcript. Rẽ theo priority (mô hình KÉO, 03/08/2026):
        #   cực cao (2) = có người HỎI  -> gửi transcript cho đúng họ
        #   thường (1) / backlog (0)    -> GIỮ (held), chờ người dự tự hỏi
        # Không còn broadcast recap+transcript cho mọi người dự nữa.
        _ = recap
        if priority >= 2:
            _deliver_requested(meta, t, dry_run=dry_run)
        elif dry_run:
            print(f"[queue] (dry-run) {token} -> sẽ GIỮ (held), chờ người hỏi")
        else:
            # TÓM TẮT LẠI TỪ NGUYÊN VĂN trước khi ghi Base (user chốt
            # 05/08/2026). Trước đây nhánh này không gọi LLM lần nào:
            # `_base_record_held` dùng lại `recap_json` mà `_notify_minute` đã
            # ghi từ VĂN BẢN MINUTE CỦA LARK. Tức mọi cuộc đi đường kéo có
            # transcript whisper nằm trên đĩa nhưng tóm tắt hiển thị khắp nơi
            # (Base, get_meeting, câu kèm file) lại phân tích từ bản Lark —
            # bản kém chi tiết hơn hẳn. Người dùng gọi đúng tên vấn đề này.
            #
            # `_recap_step` trả None = LLM chớp tắt, job giữ `queued` để vòng
            # sau làm lại. Không được set `held` lúc đó: set rồi thì không còn
            # đường nào quay lại làm tóm tắt, và cuộc họp kẹt vĩnh viễn với bản
            # tóm tắt từ Minute — đúng cái đang đi sửa.
            if _recap_step(meta, t, attempts, dry_run=dry_run) is None:
                continue
            # Viết .docx để đính vào Base; set held; ghi record Base (nguồn bot
            # Q&A đọc — không có thì 'gửi transcript [tên]' tra không ra cuộc).
            try:
                pipeline.write_doc(t, meta)
            except Exception as exc:      # noqa: BLE001 — đính file là việc phụ
                print(f"[queue] {token} viết .docx hỏng (bỏ qua): {exc}")
            jobstore.set_status(token, "held", transcribed_at=_now_ms())
            _base_record_held(token, meta)
            print(f"[queue] {token} dịch xong -> tóm tắt lại từ nguyên văn "
                  f"-> held + Base (chờ người dự hỏi)")

        # Trích thuật ngữ ứng viên cho glossary (part B). HAI ràng buộc, cả hai
        # đều là lỗi đã sửa 03/08/2026 chứ không phải phòng xa:
        #  1. Chạy SAU khi đã phát/held, không phải trước. Đây là một lời gọi LLM
        #     (cả transcript, LLM_TIMEOUT=240s); đặt trước `_deliver_requested`
        #     là bắt người vừa hỏi transcript chờ thêm ngần đó — mà recap vốn đã
        #     cố tình đẩy xuống `_backfill_recaps` cuối vòng đúng vì lý do này.
        #  2. CHỈ khi transcript vừa phiên âm xong (`done` rỗng). `count` là "số
        #     CUỘC gặp" và là bộ lọc nhiễu DUY NHẤT của digest (>= MIN_COUNT):
        #     job đã có transcript mà chạy lại (enqueue tay, phát hỏng nên về
        #     `queued`, tiến trình chết giữa chừng) sẽ đi qua `_reuse` và cộng
        #     count lần nữa cho CÙNG một cuộc — đủ để một từ nghe nhầm một lần
        #     leo lên digest như "gặp 2 cuộc", tức hỏng đúng cái nó sinh ra để chặn.
        if config.GLOSSARY_ENABLED and not dry_run and not done:
            _collect_glossary(t, meta)

    if not dry_run:
        # Recap TRƯỚC Base: `_backfill_recaps` sửa recap trong DB, và
        # `retry_missing_records` đọc recap từ DB khi tạo record còn thiếu —
        # chạy ngược thứ tự thì record mới tạo lại mang đúng bản giữ chỗ vừa
        # được thay, và phải chờ thêm một vòng nữa mới đúng.
        try:
            _backfill_recaps()
        except Exception as exc:         # noqa: BLE001 — cùng lý lẽ _backfill_base
            print(f"[recap] thử làm lại tóm tắt hỏng (bỏ qua): {exc}")
        # Gửi bù SAU khi làm lại recap: người chưa nhận được lần nào thì nên
        # nhận bản tóm tắt thật, đừng nhận bản giữ chỗ rồi thôi.
        try:
            _backfill_deliveries()
        except Exception as exc:         # noqa: BLE001 — cùng lý lẽ _backfill_base
            print(f"[deliver] thử gửi bù hỏng (bỏ qua): {exc}")
        _backfill_base()


def _recap_step(meta: MeetingMeta, t, attempts: int, *, dry_run: bool):
    """Recap cho một job đã có transcript. Trả Recap, hoặc None = HOÃN.

    Vì sao tách ra và vì sao có nhánh hoãn (sửa 31/07/2026): `summarize` trước
    đây nuốt mọi lỗi LLM và trả về một Recap "Chưa sinh được recap". Pipeline
    coi đó là thành công -> phát cho tất cả -> job `delivered` -> Base ghi
    `không có recap` -> KHÔNG gì chạy lại. Tức một cú 429 lẻ lúc 8h tối là cuộc
    họp đó vĩnh viễn không có tóm tắt, mà mọi người vẫn nhận được thẻ.

    Nay xử y như whisper tắt: không tiêu quota `MAX_ATTEMPTS`, giữ `queued`,
    vòng sau `_reuse` nạp lại transcript nên chỉ làm lại phần recap (không phiên
    âm lại). Nhưng KHÔNG hoãn vô hạn — hết `RECAP_MAX_TRIES` thì chịu phát bản
    trần, vì transcript vẫn đáng gửi hơn là im lặng mãi.
    """
    from . import summarize
    token = meta.minute_token
    try:
        recap = pipeline.run_recap(meta, t)
    except summarize.RecapUnavailable as exc:
        if dry_run:
            # Dry-run KHÔNG ghi gì, kể cả bộ đếm này: nó là lệnh chẩn đoán,
            # chạy vài lần không được phép đốt ngân sách hoãn của lần gửi thật.
            print(f"[queue] {token} (dry-run) gọi LLM hỏng: {exc}")
            return None
        n = jobstore.bump_recap_fails(token)
        if n < config.RECAP_MAX_TRIES:
            jobstore.unbump_attempts(token)
            jobstore.set_status(token, "queued", error=f"recap: {exc}")
            print(f"[queue] {token} LLM KHÔNG gọi được ({n}/"
                  f"{config.RECAP_MAX_TRIES} lần) — KHÔNG tính lần thử, "
                  f"KHÔNG phát bản trống, vòng sau chỉ làm lại recap: {exc}")
            return None
        print(f"[queue] {token} LLM hỏng {n} lần liên tiếp -> CHỊU phát bản "
              f"KHÔNG có recap (transcript vẫn tới tay người dự): {exc}")
        return pipeline.save_recap(meta, summarize.placeholder(
            f"gọi LLM thất bại {n} lần liên tiếp ({exc})"))
    if not dry_run:
        jobstore.reset_recap_fails(token)
    return recap


# Tối đa bao nhiêu recap được làm lại trong MỘT vòng. Mỗi cái là một lời gọi
# LLM trên transcript đầy — để không giới hạn thì lần đầu chạy sau một đợt LLM
# chết dài sẽ nuốt cả vòng và làm trễ việc phát của cuộc họp mới. Hàng tồn tự
# hết sau vài vòng.
BACKFILL_RECAPS_PER_ROUND = 2


def _backfill_recaps() -> None:
    """Làm lại tóm tắt cho cuộc họp ĐÃ phát mà recap chỉ là bản giữ chỗ.

    Vì sao cần (02/08/2026): `_recap_step` cố ý chịu phát bản trần sau
    `RECAP_MAX_TRIES` lần — transcript vẫn đáng gửi hơn là im lặng mãi. Nhưng
    sau đó KHÔNG có đường quay lại: job thành `delivered` nên không vòng nào
    nhặt nó nữa, `recap_fails` nằm ở mức trần vĩnh viễn, và record Base giữ ô
    tóm tắt rỗng — tức bot trả lời "cuộc họp này không có tóm tắt" mãi mãi dù
    transcript vẫn nằm nguyên trên đĩa. Với `RECAP_MAX_TRIES=3` và
    `POLL_INTERVAL=300` thì chỉ cần LLM chết ~15 phút là mất một cuộc họp.

    Đây là cùng một hình dạng với `bitable.retry_missing_records`: bước phụ
    hỏng, việc chính vẫn xong, và phải có đường vá chạy lại ở vòng sau.

    CỐ Ý KHÔNG gửi lại thẻ cho người dự. Họ đã nhận biên bản (kèm transcript)
    rồi; gửi thêm một thẻ nữa cho cùng cuộc họp là tin rác, và "phát" là hành
    động hướng ra ngoài nên không nên tự động lặp. Ở đây chỉ sửa thứ đọc lại
    được: recap trong DB và ô tóm tắt trên Base — đúng cái bot dùng để trả lời.
    """
    from . import bitable, summarize
    done = 0
    for row in jobstore.by_status("delivered"):
        if done >= BACKFILL_RECAPS_PER_ROUND:
            return
        if not row.get("transcript_path"):
            continue                       # không có transcript thì không làm lại được
        token = row["minute_token"]
        got = _reuse(token, row)
        if not got:
            continue                       # transcript đọc không được -> để yên
        t, recap = got
        if not summarize.is_placeholder(recap):
            continue
        try:
            meta = jobstore.meta_from_json(row["meta_json"])
        except Exception as exc:           # noqa: BLE001 — job cũ méo dữ liệu
            print(f"[recap] {token} meta_json méo, bỏ qua: {exc}")
            continue
        try:
            new = pipeline.run_recap(meta, t)
        except summarize.RecapUnavailable as exc:
            # LLM vẫn chưa sống lại. Im và thử vòng sau — `alerts._check_llm`
            # là chỗ nói to chuyện đó, không phải chỗ này (nó chạy mỗi vòng).
            print(f"[recap] {token} chưa làm lại được tóm tắt: {exc}")
            return                         # hỏng một cái là hỏng cả lượt, khỏi thử tiếp
        if summarize.is_placeholder(new):
            continue                       # LLM trả rỗng: không phải lỗi hạ tầng
        done += 1
        jobstore.reset_recap_fails(token)
        # `run_recap` đặt status='recapping' — trả lại `delivered`, nếu không
        # thì vòng SAU sẽ nhặt job này lên và PHÁT LẠI cho tất cả người dự.
        jobstore.set_status(token, "delivered", delivered_at=row.get("delivered_at"))
        print(f"[recap] {token} ({meta.title!r}) đã có tóm tắt thật sau khi "
              f"LLM sống lại — người dự KHÔNG nhận thêm tin, chỉ Base đổi")
        bitable.update_recap(token, new)


# Tối đa bao nhiêu JOB được gửi bù trong MỘT vòng, và mỗi người được thử lại
# mấy lần trước khi thôi. Cả hai đều nhỏ có chủ ý: gửi bù là hành động HƯỚNG RA
# NGOÀI (tin vào chat người thật), nên thà chậm còn hơn ồn.
BACKFILL_DELIVERIES_PER_ROUND = 2
DELIVERY_MAX_TRIES = 3


def _backfill_deliveries() -> None:
    """Gửi lại cho người mà lần phát trước hỏng, và CHỈ người đó.

    Vì sao cần (02/08/2026): xem docstring `jobstore.pending_recipients`. Job
    hỏng một phần vẫn thành `delivered`, nên không vòng nào nhặt lại — người bị
    429 hay mạng chớp mất biên bản vĩnh viễn, dù dòng `ok=0` nằm sẵn trong DB.

    KHÔNG gửi cho cả danh sách: người đã nhận rồi mà nhận lại là tin trùng cho
    cùng một cuộc họp. `pending_recipients` lọc đúng người chưa từng nhận được.

    Bỏ cuộc sau `DELIVERY_MAX_TRIES` lần: 230013 (app chưa phát hành cho người
    đó) không tự khỏi, thử mãi là mỗi vòng một lần gọi API vô ích. Lúc bỏ cuộc
    thì NÓI TO — im lặng ở đây là quay lại đúng cái lỗi đang sửa.
    """
    done = 0
    for row in jobstore.by_status("delivered"):
        if done >= BACKFILL_DELIVERIES_PER_ROUND:
            return
        token = row["minute_token"]
        pend = jobstore.pending_recipients(token, "recap", DELIVERY_MAX_TRIES)
        # Người đã nhận THẺ mà chưa có FILE: ca riêng, phải gửi bằng đường
        # `deliver_file` chứ không phải `deliver` — xem docstring hàm đó.
        pend_file = jobstore.pending_file_recipients(token, DELIVERY_MAX_TRIES)
        if not (pend or pend_file):
            # Đã hết lượt thử mà vẫn chưa ai nhận được -> nói một câu rõ ràng.
            stuck = jobstore.pending_recipients(token, "recap", 10_000)
            if stuck:
                print(f"[deliver] {token} BỎ CUỘC với {len(stuck)} người sau "
                      f"{DELIVERY_MAX_TRIES} lần thử: {[x[:16] for x in stuck]} "
                      f"— xem `deliveries.error`, nhiều khả năng 230013 "
                      f"(app chưa phát hành cho họ)")
            continue
        got = _reuse(token, row)
        if not got:
            continue                       # không dựng lại được nội dung
        t, recap = got
        if recap is None:
            continue                       # chưa có tóm tắt thì chờ vòng recap
        try:
            meta = jobstore.meta_from_json(row["meta_json"])
        except Exception as exc:           # noqa: BLE001 — job cũ méo dữ liệu
            print(f"[deliver] {token} meta_json méo, bỏ qua: {exc}")
            continue

        # Transcript rỗng thì KHÔNG có gì để gửi bù, và phải loại ở ĐÂY chứ
        # không phải để `deliver_file` từ chối (sửa 02/08/2026, thấy trong log
        # production 5 phút sau khi bản vá chạy): nó cố ý không ghi lần thử nào
        # cho ca rỗng, nên vòng sau `pending_file_recipients` lại trả đúng người
        # đó — một dòng log mỗi 5 phút MÃI MÃI, và mỗi vòng chiếm một suất
        # trong `BACKFILL_DELIVERIES_PER_ROUND`. Job có transcript rỗng đã được
        # `EmptyTranscript` chặn từ đầu vào; đây là dọn cho dữ liệu CŨ.
        if pend_file and t.word_count == 0:
            pend_file = []
        if not (pend or pend_file):
            continue

        done += 1
        if pend:
            print(f"[deliver] {token} gửi BÙ (thẻ+file) cho {len(pend)} người "
                  f"lần trước hỏng")
            pipeline.deliver(meta, recap, t, pend, dry_run=False)
        if pend_file:
            print(f"[deliver] {token} gửi BÙ RIÊNG FILE cho {len(pend_file)} "
                  f"người đã nhận thẻ mà chưa có transcript")
            pipeline.deliver_file(meta, t, pend_file)


def _backfill_base() -> None:
    """Ghi lại record Base cho job đã phát mà lần ghi đầu hỏng.

    Vì sao ở đây: `bitable.write_draft` cố ý không làm job `failed` khi ghi Base
    hỏng — biên bản đã tới tay người dự rồi. Nhưng trước 31/07/2026 KHÔNG có gì
    thử lại, kể cả bằng tay: `sync_tracking` bỏ qua job không có
    `bitable_record_id`. Cuộc họp đó vĩnh viễn không lên Base, và bot báo "CHƯA
    CÓ BIÊN BẢN" mãi mãi dù đã phát xong (qa.pending_meetings).

    Base hỏng KHÔNG được làm chết vòng run — cùng lý lẽ với `alerts.check_all`.
    """
    from . import bitable
    try:
        bitable.retry_missing_records()
        # Rồi đổ trạng thái MỚI lên record đã có. Thiếu bước này thì Base chỉ
        # đúng vào lúc record được TẠO: một cuộc `queued` chuyển sang `held` hay
        # `failed` sau đó sẽ nằm im ở trạng thái cũ, tức cột trạng thái nói dối
        # — tệ hơn là không có cột. Rẻ: 1 lời gọi đọc, chỉ ghi cái lệch.
        bitable.sync_jobs()
    except Exception as exc:             # noqa: BLE001 — xem docstring
        print(f"[base] thử ghi lại record thiếu hỏng (bỏ qua): {exc}")


def _reuse(token: str, row: dict):
    """(transcript, recap|None) đã lưu từ lần trước, hoặc None nếu chưa có gì.

    Vì sao cần: phiên âm là bước ĐẮT NHẤT (họp 1 tiếng mất hàng chục phút CPU).
    Tiến trình chết giữa recap và phát, hoặc gửi hỏng cho tất cả rồi job về
    `queued`, thì phải phát lại được mà KHÔNG phiên âm lại. Cũng là đường để các
    job cũ ở `awaiting_approval` đi tiếp (db._migrate).

    `recap=None` mà transcript có = trạng thái THẬT và hay gặp: LLM hỏng sau khi
    phiên âm xong. Trước 31/07/2026 hàm này đòi CẢ HAI mới chịu dùng lại, nên
    làm lại recap kéo theo phiên âm lại toàn bộ — đúng cái phải tránh nhất.
    """
    if not row.get("transcript_path"):
        return None
    import json
    from .models import ActionItem, Recap, Transcript
    try:
        with open(row["transcript_path"], encoding="utf-8") as f:
            t = Transcript.from_json(json.load(f))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"[queue] {token} không đọc lại được transcript ({exc}) -> làm lại")
        return None
    if not row.get("recap_json"):
        print(f"[queue] {token} có transcript, CHƯA có recap -> chỉ làm recap")
        return t, None
    try:
        rj = json.loads(row["recap_json"])
        recap = Recap(
            summary=rj.get("summary", ""),
            decisions=rj.get("decisions", []),
            action_items=[ActionItem(**a) for a in rj.get("action_items", [])])
        return t, recap
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"[queue] {token} recap cũ méo ({exc}) -> làm lại recap")
        return t, None


# Tối đa bao nhiêu người được mượn token để tra lại người dự. Mỗi người là một
# chuỗi hàng chục lời gọi API, nhưng thử người thứ hai là ĐÁNG: chuỗi tra dùng
# lịch RIÊNG của người cho mượn token (`calendar_primary`), nên người không phải
# chủ toạ có thể không thấy sự kiện trong khi chủ toạ thì thấy.
RERESOLVE_MAX_READERS = 2

# Nguồn người dự coi là ĐÃ TRA ĐƯỢC. Mọi giá trị khác — `agenda_failed`,
# `no_match`, `no_calendar_event`, `no_event_in_window`, `no_start_time`, và cả
# `… -> fallback:owner` — đều là "chưa biết ai dự".
_SOURCE_RESOLVED = ("calendar[verified]", "calendar[title]")


def _needs_reresolve(source: str) -> bool:
    return not (source or "").startswith(_SOURCE_RESOLVED)


def _maybe_reresolve(meta: MeetingMeta, *, dry_run: bool) -> MeetingMeta:
    """Tra lại người dự nếu lần trước không ra. Trả meta dùng để phát.

    Vì sao cần (02/08/2026): `resolve_participants` chạy đúng MỘT lần, trong
    `enqueue_minute`, rồi kết quả nằm im trong `meta_json`. Một cú `LarkError`
    thoáng qua ở `calendar_events` là `agenda_failed` -> `FALLBACK_TO_OWNER` ->
    job giữ danh sách sai mãi mãi, không có đường tra lại kể cả bằng tay.

    Vì sao đặt ĐÚNG ở đây, ngay trước lúc phát: giữa `enqueue` và chỗ này là cả
    bước phiên âm — hàng chục phút với whisper CPU. Một sự cố mạng thoáng qua đã
    có thừa thời gian tự khỏi, và `resolve_participants` cũng đã qua hẳn cửa sổ
    `SETTLE_MINUTES` nên Lark đã liên kết xong bản ghi với cuộc họp.

    FAIL-CLOSED, và đây là phần dễ làm hỏng nhất: tra lại mà vẫn không ra thì
    GIỮ NGUYÊN meta cũ. `resolve_participants` sửa đối tượng TẠI CHỖ và xoá
    trắng `attendees` khi thất bại, nên phải chạy trên một BẢN SAO — làm thẳng
    trên `meta` là một lần tra hỏng sẽ xoá mất cả danh sách `fallback:owner`
    đang có, tức tra lại làm mọi thứ TỆ ĐI.
    """
    if not _needs_reresolve(meta.participants_source):
        return meta

    fresh = jobstore.meta_from_json(jobstore.meta_to_json(meta))
    tried = 0
    for oid in pipeline._reader_candidates(meta):
        if tried >= RERESOLVE_MAX_READERS:
            break
        try:
            token = tokenstore.get_access_token(oid)
        except tokenstore.TokenError:
            continue                     # chưa enroll / token chết: không tính lượt
        tried += 1
        try:
            meetings.resolve_participants(token, fresh)
        except Exception as exc:         # noqa: BLE001 — tra lại hỏng không được làm hỏng việc phát
            print(f"[reresolve] {meta.minute_token} mượn {oid[:12]}… hỏng: {exc}")
            continue
        if not _needs_reresolve(fresh.participants_source):
            print(f"[reresolve] {meta.minute_token}: "
                  f"{meta.participants_source!r} -> "
                  f"{fresh.participants_source!r} "
                  f"({len(fresh.attendees)} người dự)")
            if not dry_run:
                jobstore.update_meta(meta.minute_token, fresh)
            return fresh

    print(f"[reresolve] {meta.minute_token} vẫn không tra được người dự "
          f"({meta.participants_source!r}, đã thử {tried} người) — giữ nguyên")
    return meta


def reverify_after_enroll(info: dict) -> list[str]:
    """Xác minh lại ACL của các minute cũ ngay sau khi một user OAuth.

    Một job có thể đã được tạo trước khi token có scope VC mới, khi Lark chưa gắn
    recording vào cuộc họp, hoặc khi API chập chờn. Khi đó ``meta_json`` giữ nguồn
    ``near``/``no_match`` và người thực sự dự họp bị ẩn mãi, dù lần OAuth sau đã đủ
    quyền để chứng minh quan hệ event -> VC recording -> minute.

    Đây là đường sửa quyền nên cố ý chặt hơn ``_maybe_reresolve``:

    * chỉ xét minute mà Lark trả về trong search có lọc chính open_id vừa OAuth;
    * chỉ ghi khi recording khớp chính xác minute token (``calendar[verified]``);
    * chính open_id/union_id vừa OAuth phải có trong attendee đã xác minh;
    * ``calendar[title]`` hay gần giờ đơn thuần không bao giờ đủ ở đường này.

    Không enqueue, không gửi tin, không gửi transcript. Lỗi API chỉ làm bỏ qua lần
    sửa này; enroll vẫn thành công và job cũ vẫn giữ nguyên theo fail-closed.
    """
    open_id = info.get("open_id", "")
    union_id = info.get("union_id", "")
    name = info.get("name", "")
    if not (open_id and union_id):
        return []

    try:
        token = tokenstore.get_access_token(open_id)
        end = _now_ms()
        start = end - config.LOOKBACK_DAYS * 86_400_000
        items = lark_api.minutes_list(token, start, end, open_id)
    except Exception as exc:                  # noqa: BLE001 - không được làm hỏng OAuth
        # ASCII-only: đường `v2 complete` có thể chạy từ PowerShell cp1252, nơi một
        # ký tự tiếng Việt trong log cũng đủ ném UnicodeEncodeError sau khi DB đã ghi.
        print(f"[reverify-enroll] {open_id}: minute scan failed: {exc!a}")
        return []

    upgraded: list[str] = []
    seen: set[str] = set()
    for item in items:
        minute_token = item.get("token") or item.get("minute_token")
        if not minute_token or minute_token in seen:
            continue
        seen.add(minute_token)

        row = jobstore.get(minute_token)
        if not row:
            continue                         # vòng scan/backlog chịu trách nhiệm tạo job
        try:
            current = jobstore.meta_from_json(row["meta_json"])
        except Exception:
            continue                         # metadata méo: không đoán quyền

        if _explicit_viewer(current, open_id, union_id):
            db.note_viewer(minute_token, open_id, union_id, name)
            continue
        if (current.participants_source or "").startswith("calendar[verified]"):
            continue                         # đã verified mà user không có mặt: giữ nguyên

        fresh = jobstore.meta_from_json(jobstore.meta_to_json(current))
        try:
            meetings.resolve_participants(token, fresh)
        except Exception as exc:              # noqa: BLE001 - giữ ACL cũ khi API lỗi
            print(f"[reverify-enroll] {minute_token}: resolve failed: {exc!a}")
            continue

        if not (fresh.participants_source or "").startswith("calendar[verified]"):
            continue
        if not _explicit_viewer(fresh, open_id, union_id):
            print(f"[reverify-enroll] {minute_token}: recording matched but "
                  f"{open_id} is absent from VC attendees; ACL unchanged")
            continue

        jobstore.update_meta(minute_token, fresh)
        db.note_viewer(minute_token, open_id, union_id, name)
        upgraded.append(minute_token)
        print(f"[reverify-enroll] {minute_token}: {current.participants_source!a} -> "
              f"{fresh.participants_source!a} ({len(fresh.attendees)} attendees)")

    return upgraded


def _recipients(meta: MeetingMeta) -> tuple[list[str], int]:
    """(union_id sẽ nhận biên bản, số người dự KHÔNG nhận vì chưa cấp quyền).

    CHỈ GỬI CHO NGƯỜI ĐÃ ENROLL (chốt 02/08/2026, user quyết). Trước đó ai có
    tên trên lời mời lịch cũng bị đẩy nguyên văn transcript vào chat, kể cả
    người chưa bao giờ cấp quyền cho hệ thống và không biết nó tồn tại. Đo thật
    trên cuộc `Workforce AI Weekly`: 30 người dự, 3 người đã cấp quyền.

    Nguồn duy nhất: `meta.attendees` đã resolve từ lịch/VC, rồi giao với tập
    người đã enroll. `minute_viewers` chỉ nói token của ai TÌM/ĐỌC được bản ghi;
    nó không chứng minh tham dự và tuyệt đối không được dùng để phát nội dung.
    """
    enrolled = {u["union_id"]: (u.get("name") or u["union_id"])
                for u in tokenstore.list_users(active_only=True)
                if u.get("union_id")}

    out: list[str] = []
    skipped = 0
    attendees = ([] if "calendar[near" in (meta.participants_source or "")
                 else meta.attendees)
    for a in attendees:
        if not a.union_id:
            continue
        if a.union_id in enrolled:
            if a.union_id not in out:
                out.append(a.union_id)
        else:
            skipped += 1
    return out, skipped


def _deliver_now(meta: MeetingMeta, recap, t, *, dry_run: bool) -> None:
    recips, skipped = _recipients(meta)
    # In nguồn người nhận TRƯỚC khi phát: không còn ai duyệt bằng mắt nên đây là
    # dấu vết duy nhất để truy "vì sao người này nhận được biên bản".
    note = f" · {skipped} người dự CHƯA cấp quyền nên không nhận" if skipped else ""
    print(f"[deliver] {meta.minute_token} -> {len(recips)} người · "
          f"{meetings.explain_source(meta.participants_source)}{note}")

    if not recips:
        # KHÔNG phải lỗi, và KHÔNG được thử lại: không ai trong cuộc họp này cấp
        # quyền thì vòng sau cũng vậy. Vẫn giữ transcript + recap và vẫn ghi
        # Base — người dự enroll về sau là đọc lại được qua bot, không mất gì.
        # Gần như không xảy ra được (xem bất biến ở `_recipients`); tới đây thì
        # thường là token của người phát hiện vừa bị thu hồi, hoặc job nạp tay
        # bằng `v2 enqueue`.
        print(f"[deliver] {meta.minute_token} KHÔNG có người nhận nào đã cấp "
              f"quyền — giữ biên bản, không gửi cho ai")
        if dry_run:
            # Trả về `queued` y như nhánh dry-run bên dưới. Thiếu dòng này thì
            # job nằm lại ở `recapping` — vẫn được vòng sau nhặt (status đó có
            # trong `by_status` của process_queue) nhưng hai nhánh dry-run để
            # lại hai trạng thái khác nhau là thứ sẽ làm ai đó chẩn sai về sau.
            jobstore.set_status(meta.minute_token, "queued")
            return
        jobstore.set_status(meta.minute_token, "delivered",
                            delivered_at=_now_ms(),
                            error="không ai trong cuộc họp đã cấp quyền")
        from . import bitable
        bitable.write_draft(meta, recap, 0)
        return

    sent, failed = pipeline.deliver(meta, recap, t, recips, dry_run=dry_run)
    if dry_run:
        print(f"[deliver] (dry-run) {meta.minute_token}")
        jobstore.set_status(meta.minute_token, "queued")   # để chạy lại thật
        return
    if failed and not sent:
        # Giữ ở queued: _reuse sẽ phát lại từ transcript đã có, không phiên âm lại.
        jobstore.set_status(meta.minute_token, "queued",
                            error="không gửi được cho ai")
        return
    jobstore.set_status(meta.minute_token, "delivered", delivered_at=_now_ms())
    print(f"[deliver] {meta.minute_token} -> delivered "
          f"({len(sent)} gửi, {len(failed)} hỏng)")

    # Ghi "nội dung đã chốt" ở trạng thái draft. Sau bước phát, và không được
    # làm gãy gì: biên bản đã tới tay người dự rồi.
    from . import bitable
    bitable.write_draft(meta, recap, len(sent))


def _base_record_held(token: str, meta: MeetingMeta) -> None:
    """Ghi record Base cho cuộc đã held/gửi (mô hình kéo, 03/08/2026).

    BẮT BUỘC: Base là nguồn bot Q&A đọc MẶC ĐỊNH (qa.base_records_all). Không ghi
    thì bot không thấy cuộc họp -> người dùng nhắn 'gửi transcript [tên]' mà bot
    tra không ra. Trước khi có mô hình kéo, record được ghi trong `_deliver_now`;
    giờ đường đó bị bỏ nên phải ghi ở ĐÂY.

    Đọc `recap_json` của job. Từ 05/08/2026 caller đã chạy `_recap_step` trên
    NGUYÊN VĂN whisper ngay trước khi gọi hàm này, nên thứ đọc được ở đây là bản
    tóm tắt từ nguyên văn — không còn là bản tóm tắt từ Minute của Lark do
    `_notify_minute` ghi lúc mới phát hiện cuộc họp.

    Vẫn giữ đường đọc `recap_json` chứ không nhận Recap qua tham số: `_deliver_
    requested` và các đường gửi bù cũng gọi hàm này, và job là nguồn sự thật
    chung của tất cả. Không có gì đọc được thì để tóm tắt giữ chỗ; transcript vẫn
    đính, bot vẫn tra được. Ghi Base là việc PHỤ: hỏng thì bỏ qua, không làm chết
    vòng.
    """
    import json as _json
    from .models import ActionItem, Recap
    row = jobstore.get(token) or {}
    recap = None
    if row.get("recap_json"):
        try:
            rj = _json.loads(row["recap_json"])
            recap = Recap(summary=rj.get("summary", ""),
                          decisions=rj.get("decisions", []),
                          action_items=[ActionItem(**a)
                                        for a in rj.get("action_items", [])])
        except (ValueError, TypeError, KeyError):
            recap = None
    if recap is None:
        recap = summarize.placeholder("chưa tóm tắt — nhắn bot để lấy transcript")
    try:
        from . import bitable
        bitable.write_draft(meta, recap, 0)
    except Exception as exc:              # noqa: BLE001 — ghi Base là việc phụ
        print(f"[base] {token} ghi record (held) hỏng (bỏ qua): {exc}")


def _collect_glossary(t, meta: MeetingMeta) -> None:
    """Trích thuật ngữ ứng viên từ transcript -> bảng chờ duyệt (part B).

    BEST-EFFORT: Hermes đọc bản ghi (có thể méo) và đề xuất danh từ riêng/thuật
    ngữ nó tự tin. Ghi pending; admin duyệt qua bot rồi từ mới vào prompt cuộc
    sau. Hỏng ở bất kỳ đâu -> bỏ qua, KHÔNG làm hỏng phát/held.
    """
    try:
        terms = summarize.extract_glossary(t.text, meta.title)
    except Exception as exc:              # noqa: BLE001 — bước phụ
        print(f"[glossary] {meta.minute_token} trích hỏng (bỏ qua): {exc}")
        return
    added = 0
    for term in terms:
        try:
            db.glossary_add_candidate(term, example=meta.title)
            added += 1
        except Exception as exc:          # noqa: BLE001
            print(f"[glossary] ghi ứng viên {term!r} hỏng: {exc}")
    if added:
        print(f"[glossary] {meta.minute_token} +{added} ứng viên: {terms[:8]}")


def _deliver_requested(meta: MeetingMeta, t, *, dry_run: bool) -> None:
    """CỰC CAO (priority=2): gửi transcript cho những người ĐÃ HỎI, rồi xoá
    danh sách hỏi. Không broadcast, không thẻ recap — họ hỏi transcript whisper
    thì nhận đúng transcript.

    Ai gửi hỏng (429/mạng) thì cứ hỏi lại: job thành `delivered`/`held` nên
    `sendfile` sẽ gửi thẳng từ transcript đã có, không cần vòng `run` nữa.
    """
    token = meta.minute_token
    reqs = [r["requester"] for r in db.transcript_requesters(token)
            if r["requester"]]
    if not reqs:
        # Không còn ai chờ (vd đã gửi vòng trước) -> giữ như held.
        if not dry_run:
            jobstore.set_status(token, "held", transcribed_at=_now_ms())
        return
    if dry_run:
        print(f"[queue] (dry-run) {token} CỰC CAO -> sẽ gửi transcript cho "
              f"{len(reqs)} người đã hỏi")
        jobstore.set_status(token, "queued")      # để chạy lại thật
        return
    sent, failed = pipeline.deliver_file(meta, t, reqs)
    db.clear_transcript_requests(token)           # đã thử hết; hỏng thì hỏi lại
    jobstore.set_status(token, "delivered" if sent else "held",
                        delivered_at=_now_ms() if sent else None)
    _base_record_held(token, meta)                # cuộc này cũng phải lên Base
    print(f"[queue] {token} CỰC CAO -> gửi transcript: {len(sent)} ok, "
          f"{len(failed)} hỏng")


# =====================================================================
#  Vòng chính
# =====================================================================

def _start_heartbeat() -> None:
    """Thread nền ghi "tiến trình còn sống" mỗi 60s, cho `alerts._check_run_stale`.

    Vì sao là THREAD chứ không phải một dòng ở đầu vòng lặp: một vòng có thể bận
    phiên âm hàng chục phút cho cuộc họp dài (whisper CPU ~0.6x realtime), nên
    lấy mốc đầu vòng làm chuẩn thì phép kiểm "im quá lâu" báo động giả đúng lúc
    hệ thống đang làm việc chăm nhất.

    daemon=True: nó không được phép giữ tiến trình sống thêm một giây nào khi
    vòng chính đã dừng — mốc cũ là ĐÚNG khi `run` đã chết, đó là cả ý nghĩa của
    phép đo. Ghi hỏng thì im và thử lại nhịp sau; một lỗi SQLite tạm thời không
    được leo lên làm chết `run`, mà mốc trễ 60s cũng không đổi kết luận gì.
    """
    def beat() -> None:
        while True:
            try:
                alerts.note_run_alive()
            except Exception:                # noqa: BLE001 — xem docstring
                pass
            time.sleep(alerts.RUN_BEAT_EVERY_S)

    alerts.note_run_alive()                  # nhịp đầu NGAY, đừng chờ 60s
    threading.Thread(target=beat, daemon=True,
                     name="v2-heartbeat").start()
    print(f"[alert] nhịp sống mỗi {alerts.RUN_BEAT_EVERY_S}s — "
          f"`run` im quá {config.ALERT_RUN_STALE_MIN} phút thì "
          f"`python -m v2 alerts` (Task Scheduler) sẽ báo"
          if config.ALERT_RUN_STALE_MIN > 0 else
          "[alert] KHÔNG canh vòng run (ALERT_RUN_STALE_MIN=0) — `run` chết thì "
          "không ai được báo")


def run() -> None:
    db.init()
    config.ensure_dirs()
    print("=" * 60)
    print("Orchestrator V2")
    print(config.summary())
    print("-" * 60)
    print("Auth các người đã enroll:")
    print(tokenstore.auth_report())
    print("=" * 60)

    if config.PAUSED:
        print("[PAUSED] công tắc dừng đang bật — chỉ theo dõi, không phát.")

    from . import status_push
    # `last_push` phải tính TỪ cú đẩy lúc khởi động, không phải 0.0 (sửa
    # 02/08/2026). Với 0.0 thì điều kiện `now - last_push >= STATUS_PUSH_EVERY`
    # đúng ngay ở vòng đầu, nên mỗi lần khởi động đẩy HAI lần cách nhau vài
    # giây — đo trong log: 23:17:50 và 23:17:59. Mỗi cú là một thao tác ghi
    # Vercel Blob (§32), mà máy này khởi động lại nhiều lần mỗi ngày vì ngủ.
    last_push = 0.0
    if status_push.enabled():
        print(f"[status] dashboard: {config.STATUS_PUSH_URL}")
        status_push.heartbeat()          # đẩy ngay, đừng để trang trống
        last_push = time.monotonic()

    from . import oauth
    if config.OAUTH_PULL_URL and config.STATUS_PUSH_SECRET:
        print(f"[enroll] hộp thư tự phục vụ: {config.OAUTH_PULL_URL}")

    _start_heartbeat()

    if alerts.enabled():
        print(f"[alert] cảnh báo DM: {len(config.ALERT_UNION_IDS)} người, "
              f"whisper báo sau {config.ALERT_WHISPER_AFTER_MIN} phút, "
              f"LLM sau {config.ALERT_LLM_AFTER_MIN} phút")
    else:
        print("[alert] cảnh báo DM TẮT (ALERT_UNION_IDS trống) — hệ thống hỏng "
              "thì không ai được báo")

    if backup.enabled():
        newest, age_h = backup.latest()
        age = f"bản mới nhất {age_h:.1f}h trước" if newest else "CHƯA có bản nào"
        print(f"[backup] mỗi {config.BACKUP_EVERY_HOURS}h -> "
              f"{config.BACKUP_DIR} (giữ {config.BACKUP_KEEP} bản) · {age}")
    else:
        print("[backup] TẮT (V2_BACKUP_EVERY_HOURS=0) — mất state.db là mất cả "
              "token lẫn phân quyền hỏi đáp")

    while True:
        # Công tắc dừng khẩn: đọc lại .env MỖI VÒNG. Trước 31/07/2026 dòng này
        # không tồn tại, nên `PAUSED=1` chỉ ăn khi khởi động lại tiến trình —
        # trong khi sổ tay hứa là ăn ngay. Nói to khi nó đổi, vì đây là thứ người
        # ta bật lúc đang hoảng.
        if config.reload_switches():
            print("!" * 60)
            print(f"[PAUSED] công tắc dừng khẩn -> "
                  f"{'BẬT (không phát nữa)' if config.PAUSED else 'TẮT (phát lại)'}")
            print("!" * 60)

        # Enroll tự phục vụ TRƯỚC khi quét: người vừa bấm Đồng ý thì vòng này
        # đã coi họ là người dự hợp lệ, không phải chờ thêm một vòng. Chạy cả
        # khi PAUSED — cấp quyền không phải là "phát biên bản".
        #
        # Khối try RIÊNG (sửa 02/08/2026, thấy trong log lúc mạng rớt): hộp thư
        # nằm trên VERCEL, còn quét/xử lý nói chuyện với LARK. Để chung một try
        # thì Vercel không gọi được là `scan_once` + `process_queue` KHÔNG CHẠY
        # vòng đó, dù Lark vẫn sống — cùng hình dạng với lỗi "một người token
        # chập làm mù cả vòng quét". Log thật:
        #   [enroll] không đọc được hộp thư: [Errno 11001] getaddrinfo failed
        #   [loop] lỗi vòng lặp: [Errno 11001] getaddrinfo failed
        try:
            oauth.poll_pending()
        except Exception as exc:         # noqa: BLE001 — xem trên
            print(f"[enroll] hộp thư hỏng (bỏ qua, vẫn quét họp): {exc}")

        try:
            if not config.PAUSED:
                # Wrapper cũ chỉ bật Whisper đúng lúc khởi động; nó rớt giữa
                # chừng thì orchestrator vẫn sống và job nằm chờ vô hạn. Chạy
                # supervisor mỗi vòng, trước queue. Hàm này fail-safe: không
                # ném, không kill process sống và không spawn cho URL từ xa.
                whisper_supervisor.ensure_running()
                scan_once()
                # Đẩy cuộc VỪA phát hiện lên Base NGAY, trước khi vào hàng đợi
                # (sửa 04/08/2026). Trước đó Base chỉ được cập nhật ở CUỐI
                # `_process_queue`, mà một cuộc họp 3,8 giờ nuốt cả lượt xử lý
                # ~35 phút — trong suốt thời gian đó mọi cuộc mới phát hiện
                # không có dòng nào trên Base, người vận hành nhìn vào tưởng
                # quét sót. Rẻ khi không có gì đổi: job đã có record thì bỏ qua,
                # `sync_jobs` chỉ ghi record lệch.
                _backfill_base()
                process_queue()
        except KeyboardInterrupt:
            print("\nDừng.")
            break
        except Exception as exc:         # noqa: BLE001
            print(f"[loop] lỗi vòng lặp: {exc}")

        # Cảnh báo: khối try RIÊNG, và đặt NGOÀI khối trên có chủ ý. Để chung
        # thì một lỗi của scan/process nuốt luôn lượt kiểm cảnh báo — đúng lúc
        # hỏng nhất lại là lúc im nhất. Chạy cả khi PAUSED: dừng phát không có
        # nghĩa là dừng báo. Và cảnh báo hỏng KHÔNG được làm chết vòng run.
        try:
            alerts.check_all()
        except Exception as exc:         # noqa: BLE001
            print(f"[alert] lỗi khi kiểm cảnh báo (bỏ qua, vòng run vẫn chạy): "
                  f"{exc}")

        # Sao lưu: khối try RIÊNG, cùng lý lẽ với cảnh báo. Chạy cả khi PAUSED
        # (dừng phát không phải dừng bảo vệ dữ liệu), và SAU process_queue để
        # bản sao chứa luôn kết quả vòng vừa rồi. `maybe_backup` tự quyết định
        # đã tới lúc chưa nên gọi mỗi vòng là rẻ.
        try:
            backup.maybe_backup()
        except Exception as exc:         # noqa: BLE001
            print(f"[backup] lỗi khi sao lưu (bỏ qua, vòng run vẫn chạy): {exc}")

        # Heartbeat dashboard sau khi làm việc: đẩy cả lúc PAUSED để người
        # xem biết hệ thống còn sống mà đang tạm dừng.
        now = time.monotonic()
        if now - last_push >= config.STATUS_PUSH_EVERY:
            last_push = now
            status_push.heartbeat(
                next_scan_at_ms=_now_ms() + config.POLL_INTERVAL * 1000)

        _sleep_with_fast_enroll(config.POLL_INTERVAL)
