"""
Xử lý một job đầu-cuối: tải bản ghi -> ffmpeg -> transcribe -> recap -> phát.

Chạy MỘT job tại một thời điểm (GPU 6GB không chạy song song large-v3, và
hàng đợi tuần tự dễ đoán — V2_ARCHITECTURE §4[3]).

Các bước dùng USER TOKEN của một người có dự để tải bản ghi; PHÁT dùng bot
(tenant token). Vì sao tách: tải là user-specific, gửi là app-level.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config, jobstore, lark_api, summarize, tokenstore, transcribe, cards
from .models import MeetingMeta, Recap, Transcript


class PipelineError(RuntimeError):
    pass


# --------------------------------------------------------- audio (ffmpeg)


def _safe_name(text: str) -> str:
    out = re.sub(r'[\\/:*?"<>|]', "", text).strip(" .")
    return re.sub(r"\s+", " ", out)[:60] or "Cuoc hop"


def extract_audio(video: Path) -> Path:
    """mp4 -> WAV 16kHz mono PCM lossless (TECHNICAL §8).

    KHÔNG nén MP3 32k: thêm tầng lossy thứ hai khiến Whisper ảo giác/lặp/nuốt
    chữ. Audio chỉ POST sang transcribe_server nên không dính giới hạn 30MB.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("       (không có ffmpeg, dùng file gốc)")
        return video
    audio = video.with_suffix(".wav")
    proc = subprocess.run(
        [ffmpeg, "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", "-y", str(audio)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not audio.exists():
        print("       (tách âm thanh hỏng, dùng file gốc)")
        return video
    before = video.stat().st_size / 1_048_576
    after = audio.stat().st_size / 1_048_576
    # WAV lossless TO HƠN mp4 là bình thường và có chủ ý (xem docstring) —
    # đừng in "giảm -103%" làm người vận hành tưởng hỏng.
    print(f"       ffmpeg: {before:.1f} MB {video.suffix.lstrip('.')} "
          f"-> {after:.1f} MB WAV 16kHz mono (lossless)")
    try:
        video.unlink()
    except OSError:
        pass
    return audio


# ------------------------------------------------------- tải bản ghi


# Lark trả mã này khi token hợp lệ, có scope `minutes:minutes.media:export`,
# nhưng NGƯỜI đó không được phép tải bản ghi CỤ THỂ này.
_CODE_MEDIA_DENY = 2091005


class MediaDenied(PipelineError):
    """Không một ai đã enroll được phép tải bản ghi này.

    VĨNH VIỄN, không phải lỗi tạm thời: thử lại 5 lần trong 25 phút cũng ra đúng
    kết quả đó. Cùng họ với `TranscribeUnavailable` / `RecapUnavailable` nhưng
    ngược chiều — hai cái kia KHÔNG được tiêu quota vì sẽ tự khỏi, cái này phải
    dừng NGAY vì sẽ không tự khỏi. `orchestrator` bắt riêng và đánh `failed`
    luôn, kèm câu nói rõ phải nhờ ai làm gì.
    """


def _reader_candidates(meta: MeetingMeta) -> list[str]:
    """Mọi open_id đã enroll đáng thử mượn token, CHỦ BẢN GHI đứng trước.

    Vì sao phải là danh sách chứ không phải một người (đo 02/08/2026): quyền tải
    bản ghi KHÔNG suy ra được từ việc có dự hay có scope — nó gắn với từng bản
    ghi. Ma trận đo thật trên 5 minute x 3 người (tất cả đều có scope
    `minutes:minutes.media:export`):

        chủ bản ghi                  -> luôn OK
        người dự khác                -> hầu hết 2091005, NHƯNG Chi tải được bản
                                        ghi của Thiện (obsg47j9) trong khi Thẩm
                                        thì không

    Tức không đoán trước được ai tải được; phải THỬ. Bản cũ chọn đúng một người
    rồi bỏ cuộc, nên một cuộc họp mà người-được-chọn không có quyền là hỏng cả
    job dù người bên cạnh tải được.

    Ba nguồn ứng viên, theo thứ tự khả năng thành công giảm dần:
      1. `meta.owner_open_id` — chủ bản ghi, luôn tải được (nếu họ đã enroll).
      2. `meta.attendees` — người dự tra được từ lịch.
      3. `db.viewers_of()` — người đã enroll mà Lark báo có dự. Bắt được cả
         người mà chuỗi tra lịch sót.
    """
    from . import db
    out: list[str] = []

    def add(oid: str) -> None:
        if oid and oid not in out:
            out.append(oid)

    add(meta.owner_open_id)
    for a in meta.attendees:
        add(a.open_id)
    for v in db.viewers_of(meta.minute_token):
        add(v.get("open_id") or "")
    return out


def download_recording(meta: MeetingMeta) -> tuple[Path, str]:
    """Tải bản ghi, thử TỪNG người đã enroll tới khi có ai lấy được URL.

    Trả (đường dẫn file, open_id của người đã mượn token).

    Ném `MediaDenied` khi MỌI ứng viên đều bị từ chối quyền — và chỉ khi đó.
    Lẫn một lỗi khác (mạng, 5xx) là ném `PipelineError` thường để còn thử lại:
    kết luận "vĩnh viễn" mà sai thì mất biên bản của một cuộc họp có thật.
    """
    denied: list[str] = []          # 2091005 — không được phép, VĨNH VIỄN
    no_token: list[str] = []        # chưa enroll / token hết hạn
    other: list[str] = []           # mạng, 5xx, URL rỗng — có thể TỰ KHỎI
    cands = _reader_candidates(meta)

    for oid in cands:
        try:
            token = tokenstore.get_access_token(oid)
        except tokenstore.TokenError:
            # KHÔNG xếp vào `other`: "người này chưa enroll" không phải bằng
            # chứng của một lỗi tạm thời, mà chính là tình huống ta muốn kết
            # luận. Gộp vào `other` thì chủ bản ghi chưa enroll — đúng ca phổ
            # biến nhất — làm điều kiện `denied and not other` không bao giờ
            # đúng, và job lại quay về đốt 5 lần thử rồi báo lỗi vô nghĩa.
            no_token.append(oid)
            continue
        try:
            url = lark_api.minutes_media_url(token, meta.minute_token)
        except lark_api.LarkError as exc:
            if exc.code == _CODE_MEDIA_DENY:
                denied.append(oid)
            else:
                other.append(f"{oid[:12]}: {exc}")
            continue
        if not url:
            other.append(f"{oid[:12]}: Lark trả URL rỗng")
            continue

        dest = config.WORK_DIR / f"{meta.minute_token}.mp4"
        size = lark_api.download_to(url, dest, token)
        print(f"       tải bản ghi: {size / 1_048_576:.1f} MB -> {dest.name} "
              f"(mượn quyền của {oid[:12]}…)")
        return dest, oid

    if not cands or (no_token and not denied and not other):
        # Chưa ai đủ điều kiện thử. Để lỗi THƯỜNG (còn thử lại): người dự enroll
        # về sau là vòng sau chạy được ngay, không cần ai can thiệp.
        raise PipelineError(
            f"không có người dự nào đã enroll để đọc bản ghi "
            f"({len(no_token)} người chưa cấp quyền / token hết hạn)")
    if denied and not other:
        who = meta.owner_name or "(không rõ tên)"
        raise MediaDenied(
            f"không ai được phép tải bản ghi này ({len(denied)} người đã cấp "
            f"quyền đều bị Lark từ chối {_CODE_MEDIA_DENY}"
            + (f", {len(no_token)} người chưa cấp quyền" if no_token else "")
            + f"). Quyền tải gắn với CHỦ bản ghi — ở đây là {who}. "
            f"Cách sửa: nhờ {who} cấp quyền cho hệ thống (nhắn bot để lấy link), "
            f"rồi trả job về hàng đợi. Thử lại mà không làm gì thì vẫn y vậy.")
    raise PipelineError(
        f"không lấy được URL bản ghi {meta.minute_token} "
        f"({len(denied)} bị từ chối quyền, {len(no_token)} chưa enroll; "
        f"lỗi khác: {'; '.join(other)})")


# ------------------------------------------------------- xử lý transcription


def save_recap(meta: MeetingMeta, recap: Recap) -> Recap:
    """Lưu recap vào job rồi trả lại nó (để gọi được dạng `return save_recap(...)`)."""
    import json as _json
    jobstore.set_status(meta.minute_token, "recapping",
                        recap_json=_json.dumps({
                            "summary": recap.summary,
                            "decisions": recap.decisions,
                            "action_items": [a.__dict__ for a in recap.action_items],
                        }, ensure_ascii=False))
    return recap


def run_recap(meta: MeetingMeta, t: Transcript) -> Recap:
    """Recap cho một transcript đã có. Ném `summarize.RecapUnavailable` nếu
    gọi LLM thất bại.

    TÁCH khỏi `run_transcription` (31/07/2026) để làm lại recap mà KHÔNG phiên
    âm lại: transcript đã nằm trên đĩa và `transcript_path` đã lưu trong job,
    nên bước đắt nhất không phải chạy hai lần chỉ vì LLM chớp tắt.
    """
    return save_recap(meta, summarize.summarize(t, meta))


def run_transcription(meta: MeetingMeta) -> tuple[Transcript, Path]:
    """Tải -> ffmpeg -> transcribe. Trả (transcript, path).

    KHÔNG còn làm recap: caller gọi `run_recap` riêng, vì hai bước có cách xử
    lỗi khác nhau (xem docstring `summarize`).
    """
    jobstore.set_status(meta.minute_token, "transcribing")
    video, _reader = download_recording(meta)
    audio = video
    if video.suffix.lower() in (".mp4", ".mkv", ".mov", ".webm"):
        audio = extract_audio(video)

    t0 = time.time()
    t = transcribe.transcribe(audio, meta.minute_token,
                              meeting_title=meta.title)
    whisper_sec = time.time() - t0
    try:
        audio.unlink()
    except OSError:
        pass

    tpath = config.TRANSCRIPT_DIR / f"{_safe_name(meta.title)}-{meta.minute_token}.json"
    transcribe.save_transcript(t, tpath)
    print(f"       transcript {t.word_count} từ, {t.duration:.0f}s audio "
          f"-> {tpath.name}")

    # Lưu transcript_path TRƯỚC khi recap: recap hỏng thì vòng sau còn nạp lại
    # được từ đây (orchestrator._reuse) thay vì phiên âm lại từ đầu.
    jobstore.set_status(meta.minute_token, "recapping",
                        transcript_path=str(tpath),
                        audio_seconds=t.duration, whisper_seconds=whisper_sec)
    return t, tpath


# ------------------------------------------------------------ transcript .txt


def txt_path(meta: MeetingMeta) -> Path:
    """Đường dẫn file .txt của một cuộc họp — TÍNH được, không phải đi tìm.

    Tách khỏi `write_txt` để chỗ khác (bitable.py, khi đính kèm file vào Base)
    dựng lại đúng đường dẫn thay vì glob mò theo tên.

    ⚠️ Tên file PHẢI phân biệt được hai cuộc họp khác nhau, và lý do nghiêm
    trọng hơn "thư mục lộn xộn" (sửa 02/08/2026):
    `bitable._tracking_fields` gọi chính hàm này để lấy file đính kèm vào Base.
    Nếu cuộc B ghi đè file của cuộc A thì record Base của A nhận NGUYÊN VĂN
    transcript của B — nội dung chéo cuộc họp, chảy vào đúng thứ mà bot đọc và
    người dự A mở được. Cửa sổ gây hại là các đường ghi Base MUỘN
    (`sync_tracking`, `retry_missing_records`), chạy sau khi file đã bị đè.

    Không có `meta.start` thì trước đây tên file chỉ còn tiêu đề, nên hai cuộc
    trùng tên là dùng chung một file. Đã xảy ra thật: `Bien ban - Hop tuan.txt`
    (17 byte) nằm trong `data\\transcripts`. Nay thêm `minute_token` cho đúng ca
    đó.

    CÒN LẠI, chấp nhận có ý thức: hai cuộc CÙNG tiêu đề (sau khi `_safe_name`
    cắt 60 ký tự) và CÙNG PHÚT bắt đầu vẫn đụng nhau. Hẹp hơn nhiều, và không
    thêm token vào mọi tên để giữ nguyên tên các file đã có — đổi hết thì
    `_tracking_fields` không thấy file cũ nữa và tụt xuống đính kèm bản .json.
    """
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=7))
    if meta.start:
        stamp = " " + datetime.fromtimestamp(meta.start, tz).strftime("%d-%m-%Y %Hh%M")
    else:
        # Không có giờ -> token là thứ DUY NHẤT còn phân biệt được hai cuộc họp.
        stamp = f" {meta.minute_token[:12]}"
    return config.TRANSCRIPT_DIR / f"Bien ban - {_safe_name(meta.title)}{stamp}.txt"


def write_txt(t: Transcript, meta: MeetingMeta) -> Path:
    """Xuất transcript .txt (đường dẫn tương đối để gửi IM được)."""
    dest = txt_path(meta)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(t.text, encoding="utf-8")
    return dest


# --------------------------------------------------------------- phát


def deliver(meta: MeetingMeta, recap: Recap, t: Transcript,
            recipients: list[str], *,
            dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Phát tóm tắt + file transcript cho MỌI người dự (danh sách union_id).

    Trả (đã gửi tóm tắt, hỏng). Gửi hỏng một người không làm gãy cả job.

    Ai nhận: người trong sự kiện lịch của host (meetings.resolve_participants).
    Không còn cửa duyệt — xem docstring orchestrator.
    """
    card = cards.recap_card(meta, recap)
    txt = write_txt(t, meta)

    if dry_run:
        print(f"\n{'='*56}\nDRY-RUN — sẽ phát cho {len(recipients)} người\n{'='*56}")
        print(recap.to_markdown()[:1500])
        print(f"{'-'*56}\nkèm transcript: {txt}\n{'='*56}\n")
        return recipients, []

    sent, failed = [], []
    token = meta.minute_token

    # Upload transcript MỘT lần rồi dùng lại file_key cho mọi người: upload lặp
    # theo từng người là n lần tải file lên cho cùng một nội dung.
    file_key = None
    try:
        ftype = {".mp4": "mp4", ".pdf": "pdf", ".opus": "opus"}.get(
            txt.suffix.lower(), "stream")
        file_key = lark_api.im_upload_file(txt, ftype)
    except lark_api.LarkError as exc:
        print(f"[deliver] upload transcript hỏng, chỉ gửi tóm tắt: {exc}")

    for rid in recipients:
        try:
            lark_api.im_send_card(rid, card, id_type="union_id",
                                  uuid_key=f"recap-{token}-{rid}")
            jobstore.record_delivery(token, rid, "recap", True)
            sent.append(rid)
        except lark_api.LarkError as exc:
            failed.append(rid)
            jobstore.record_delivery(token, rid, "recap", False, str(exc))
            print(f"[deliver] gửi recap cho {rid} hỏng: {exc}")
            if exc.code == 230013:
                print("   -> App chưa phát hành cho người này (Availability). "
                      "Console: Version Management & Release -> toàn công ty.")
            continue                      # tóm tắt hỏng thì khỏi gửi transcript

        if not file_key:
            continue
        try:
            lark_api.im_send_file(rid, txt, id_type="union_id",
                                  uuid_key=f"txt-{token}-{rid}",
                                  file_key=file_key)
            jobstore.record_delivery(token, rid, "full", True)
        except lark_api.LarkError as exc:
            # Đã nhận tóm tắt = coi như gửi được; thiếu file không tính là hỏng.
            jobstore.record_delivery(token, rid, "full", False, str(exc))
            print(f"[deliver] gửi transcript cho {rid} hỏng: {exc}")

    return sent, failed
