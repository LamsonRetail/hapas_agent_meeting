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

from . import (alerts, backup, config, db, jobstore, lark_api, meetings,
               pipeline, tokenstore, transcribe)
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
        # Lọc theo chính người này: minute nào họ có dự thì họ có quyền đọc.
        items = lark_api.minutes_list(token, start, end, oid)
        for it in items:
            mt = it.get("token") or it.get("minute_token")
            if not mt:
                continue
            # Ghi nhận NGƯỜI NHẬN trước mọi cửa bỏ qua bên dưới. Đây là danh
            # sách người vừa THAM DỰ (chính Lark khẳng định qua
            # `participant_ids`) vừa ĐÃ CẤP QUYỀN — nguồn người nhận chính từ
            # 02/08/2026. Người thứ hai thấy cùng cuộc họp thì `is_claimed` chặn
            # họ tạo job thứ hai, nhưng họ VẪN phải được ghi là người nhận; đặt
            # dòng này sau cửa đó là mất đúng những người ta cần nhất.
            db.note_viewer(mt, oid, u.get("union_id", ""), u.get("name", ""))
            if db.is_claimed(mt) or jobstore.get(mt):
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

        if recap is None:
            recap = _recap_step(meta, t, attempts, dry_run=dry_run)
            if recap is None:
                continue             # hoãn sang vòng sau, KHÔNG phát bản trống

        # Tra lại người dự NGAY TRƯỚC khi phát, không sớm hơn: xem docstring.
        # Bọc try riêng — tra lại là việc CẢI THIỆN, hỏng thì phát bằng danh
        # sách cũ chứ không được chặn cả biên bản.
        try:
            meta = _maybe_reresolve(meta, dry_run=dry_run)
        except Exception as exc:     # noqa: BLE001 — xem trên
            print(f"[reresolve] {token} bỏ qua vì lỗi: {exc}")

        _deliver_now(meta, recap, t, dry_run=dry_run)

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
        if not pend:
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
        done += 1
        print(f"[deliver] {token} gửi BÙ cho {len(pend)} người lần trước hỏng")
        pipeline.deliver(meta, recap, t, pend, dry_run=False)


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
_SOURCE_RESOLVED = "calendar["


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


def _recipients(meta: MeetingMeta) -> tuple[list[str], int]:
    """(union_id sẽ nhận biên bản, số người dự KHÔNG nhận vì chưa cấp quyền).

    CHỈ GỬI CHO NGƯỜI ĐÃ ENROLL (chốt 02/08/2026, user quyết). Trước đó ai có
    tên trên lời mời lịch cũng bị đẩy nguyên văn transcript vào chat, kể cả
    người chưa bao giờ cấp quyền cho hệ thống và không biết nó tồn tại. Đo thật
    trên cuộc `Workforce AI Weekly`: 30 người dự, 3 người đã cấp quyền.

    Hợp HAI nguồn, cả hai đều đã qua cửa "phải enroll":
      (1) `meta.attendees` — người dự tra được từ lịch + người thật sự vào phòng
          họp VC (xem `meetings.resolve_participants`).
      (2) `db.viewers_of()` — người đã enroll mà minute này xuất hiện trong
          `minutes/search` của chính họ, tức LARK khẳng định họ có dự.

    Vì sao cần cả (2) chứ không chỉ (1): chuỗi tra người dự sót thật, và sót âm
    thầm — `no_match`, `fallback:owner`, họp mời bằng group chat, sự kiện lịch
    đã bị xoá (`193001`). Nguồn (2) không phụ thuộc bất kỳ thứ nào trong đó.
    Nó cũng cho một bất biến dễ kiểm: minute chỉ vào được hệ thống qua vòng quét
    của một người vừa dự vừa đã cấp quyền, nên người đó luôn nằm trong (2).

    KHÔNG nới ra "gửi cho cả người chưa enroll nếu họ có trong (1)". Đó chính là
    hành vi vừa bỏ.
    """
    enrolled = {u["union_id"]: (u.get("name") or u["union_id"])
                for u in tokenstore.list_users(active_only=True)
                if u.get("union_id")}

    out: list[str] = []
    skipped = 0
    for a in meta.attendees:
        if not a.union_id:
            continue
        if a.union_id in enrolled:
            if a.union_id not in out:
                out.append(a.union_id)
        else:
            skipped += 1
    for v in db.viewers_of(meta.minute_token):
        uid = v.get("union_id") or ""
        if uid and uid in enrolled and uid not in out:
            out.append(uid)
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
    if status_push.enabled():
        print(f"[status] dashboard: {config.STATUS_PUSH_URL}")
        status_push.heartbeat()          # đẩy ngay, đừng để trang trống
    last_push = 0.0

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

        time.sleep(config.POLL_INTERVAL)
