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

import time
from datetime import datetime, timezone

from . import (alerts, config, db, jobstore, lark_api, meetings, pipeline,
               tokenstore, transcribe)
from .models import MeetingMeta


def _now_ms() -> int:
    return int(time.time() * 1000)


# =====================================================================
#  1. Phát hiện (polling) + enqueue
# =====================================================================

def enqueue_minute(reader_open_id: str, minute_token: str,
                   raw_item: dict | None = None) -> bool:
    """Dựng job cho một minute nếu chưa ai xử lý. True nếu vừa tạo job.

    Dùng chung cho cả polling và event. reader_open_id là người mà ta có
    token để đọc minute (thường là người mà minute xuất hiện trong danh sách).
    """
    if not db.try_claim_minute(minute_token):
        return False                     # người/luồng khác đã chiếm
    try:
        token = tokenstore.get_access_token(reader_open_id)
        meta = meetings.build_meta(token, minute_token, raw_item)
        meta.owner_open_id = meta.owner_open_id or reader_open_id
        meetings.resolve_participants(token, meta)

        if not meta.attendees and config.FALLBACK_TO_OWNER:
            # Họp mở tay/không có lịch -> gửi cho chính người phát hiện.
            u = tokenstore.list_users(active_only=False)
            info = next((x for x in u if x["open_id"] == reader_open_id), None)
            if info and info.get("union_id"):
                from .models import Attendee
                meta.attendees = [Attendee(open_id=reader_open_id,
                                           union_id=info["union_id"],
                                           name=info.get("name", ""))]
                meta.owner_open_id = reader_open_id
                meta.participants_source += " -> fallback:owner"

        jobstore.create(meta, status="queued")
        print(f"[enqueue] {meta.title}  ({minute_token})  "
              f"{meta.invitee_count} người [{meta.participants_source}]")
        return True
    except Exception as exc:             # noqa: BLE001
        # Nhả khóa để vòng sau thử lại; không để job kẹt vì lỗi tạm thời.
        db.release_minute(minute_token)
        print(f"[enqueue] {minute_token} hỏng, nhả khóa: {exc}")
        return False


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
        # Lọc theo chính người này: minute nào họ có dự thì họ có quyền đọc.
        items = lark_api.minutes_list(token, start, end, oid)
        for it in items:
            mt = it.get("token") or it.get("minute_token")
            if not mt or db.is_claimed(mt) or jobstore.get(mt):
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
    print(f"[{datetime.now():%H:%M:%S}] scan xong, {created} job mới")
    return created


# =====================================================================
#  2. Xử lý hàng đợi
# =====================================================================

def process_queue(dry_run: bool | None = None) -> None:
    """Xử lý job queued -> transcribe + recap -> phát ngay cho người dự."""
    dry_run = (not config.SEND_MODE) if dry_run is None else dry_run
    for row in jobstore.by_status("queued", "transcribing", "recapping"):
        token = row["minute_token"]
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
                jobstore.set_status(token, "failed",
                                    error=f"quá {config.MAX_ATTEMPTS} lần thử")
                print(f"[queue] {token} bỏ sau {attempts} lần thử")
                continue

        meta = jobstore.meta_from_json(row["meta_json"])
        done = _reuse(token, row)
        if done:
            t, recap = done
            print(f"[queue] {token} đã có transcript+recap -> phát luôn, "
                  f"không phiên âm lại")
        else:
            try:
                t, recap, _ = pipeline.run_transcription(meta)
            except transcribe.TranscribeUnavailable as exc:
                # HẠ TẦNG hỏng (whisper tắt/treo), không phải lỗi cuộc họp này.
                # Trả lại lần thử, nếu không thì vòng `run` 5 phút/lần sẽ đốt hết
                # MAX_ATTEMPTS trong ~25 phút và job `failed` VĨNH VIỄN dù nội
                # dung không sai gì — mất luôn biên bản của cuộc họp đó, im lặng.
                jobstore.unbump_attempts(token)
                jobstore.set_status(token, "queued", error=str(exc))
                print(f"[queue] {token} whisper KHÔNG dùng được — "
                      f"KHÔNG tính lần thử (attempts giữ {attempts - 1}): {exc}")
                continue
            except Exception as exc:     # noqa: BLE001
                jobstore.set_status(token, "queued", error=str(exc))
                print(f"[queue] {token} transcription hỏng "
                      f"(thử {attempts}/{config.MAX_ATTEMPTS}): {exc}")
                continue

        _deliver_now(meta, recap, t, dry_run=dry_run)


def _reuse(token: str, row: dict):
    """(transcript, recap) đã lưu từ lần trước, hoặc None nếu chưa có.

    Vì sao cần: phiên âm là bước ĐẮT NHẤT (whisper CPU ≈ 0,59x realtime — họp
    1 tiếng mất ~35 phút). Tiến trình chết giữa recap và phát, hoặc gửi hỏng
    cho tất cả rồi job về `queued`, thì phải phát lại được mà KHÔNG phiên âm
    lại. Cũng là đường để các job cũ ở `awaiting_approval` đi tiếp (db._migrate).
    """
    if not (row.get("transcript_path") and row.get("recap_json")):
        return None
    import json
    from .models import ActionItem, Recap, Transcript
    try:
        with open(row["transcript_path"], encoding="utf-8") as f:
            t = Transcript.from_json(json.load(f))
        rj = json.loads(row["recap_json"])
        recap = Recap(
            summary=rj.get("summary", ""),
            decisions=rj.get("decisions", []),
            action_items=[ActionItem(**a) for a in rj.get("action_items", [])])
        return t, recap
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"[queue] {token} không dùng lại được bản cũ ({exc}) -> làm lại")
        return None


def _deliver_now(meta: MeetingMeta, recap, t, *, dry_run: bool) -> None:
    recips = [a.union_id for a in meta.attendees if a.union_id]
    # In nguồn người nhận TRƯỚC khi phát: không còn ai duyệt bằng mắt nên đây là
    # dấu vết duy nhất để truy "vì sao người này nhận được biên bản".
    print(f"[deliver] {meta.minute_token} -> {len(recips)} người · "
          f"{meetings.explain_source(meta.participants_source)}")
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


# =====================================================================
#  Vòng chính
# =====================================================================

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
    if status_push.enabled():
        print(f"[status] dashboard: {config.STATUS_PUSH_URL}")
        status_push.heartbeat()          # đẩy ngay, đừng để trang trống
    last_push = 0.0

    from . import oauth
    if config.OAUTH_PULL_URL and config.STATUS_PUSH_SECRET:
        print(f"[enroll] hộp thư tự phục vụ: {config.OAUTH_PULL_URL}")

    if alerts.enabled():
        print(f"[alert] cảnh báo DM: {len(config.ALERT_UNION_IDS)} người, "
              f"whisper báo sau {config.ALERT_WHISPER_AFTER_MIN} phút")
    else:
        print("[alert] cảnh báo DM TẮT (ALERT_UNION_IDS trống) — hệ thống hỏng "
              "thì không ai được báo")

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

        try:
            # Enroll tự phục vụ TRƯỚC khi quét: người vừa bấm Đồng ý thì vòng
            # này đã coi họ là người dự hợp lệ, không phải chờ thêm một vòng.
            # Chạy cả khi PAUSED — cấp quyền không phải là "phát biên bản".
            oauth.poll_pending()
            if not config.PAUSED:
                scan_once()
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

        # Heartbeat dashboard sau khi làm việc: đẩy cả lúc PAUSED để người
        # xem biết hệ thống còn sống mà đang tạm dừng.
        now = time.monotonic()
        if now - last_push >= config.STATUS_PUSH_EVERY:
            last_push = now
            status_push.heartbeat(
                next_scan_at_ms=_now_ms() + config.POLL_INTERVAL * 1000)

        time.sleep(config.POLL_INTERVAL)
