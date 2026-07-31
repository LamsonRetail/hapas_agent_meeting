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


def _reader_token(meta: MeetingMeta) -> tuple[str, str]:
    """Chọn một người có dự đã enroll để mượn token đọc. Trả (open_id, token).

    Ưu tiên chủ minute, rồi tới người dự đầu tiên còn token sống.
    """
    candidates = []
    if meta.owner_open_id:
        candidates.append(meta.owner_open_id)
    candidates += [a.open_id for a in meta.attendees if a.open_id]
    seen = set()
    for oid in candidates:
        if oid in seen:
            continue
        seen.add(oid)
        try:
            return oid, tokenstore.get_access_token(oid)
        except tokenstore.TokenError:
            continue
    raise PipelineError("không có người dự nào đã enroll để đọc bản ghi")


def download_recording(meta: MeetingMeta, access_token: str) -> Path:
    url = lark_api.minutes_media_url(access_token, meta.minute_token)
    if not url:
        raise PipelineError(f"không lấy được URL bản ghi {meta.minute_token}")
    dest = config.WORK_DIR / f"{meta.minute_token}.mp4"
    size = lark_api.download_to(url, dest, access_token)
    print(f"       tải bản ghi: {size / 1_048_576:.1f} MB -> {dest.name}")
    return dest


# ------------------------------------------------------- xử lý transcription


def run_transcription(meta: MeetingMeta) -> tuple[Transcript, Recap, Path]:
    """Tải -> ffmpeg -> transcribe -> recap. Trả (transcript, recap, path)."""
    jobstore.set_status(meta.minute_token, "transcribing")
    _, token = _reader_token(meta)

    video = download_recording(meta, token)
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

    jobstore.set_status(meta.minute_token, "recapping",
                        transcript_path=str(tpath),
                        audio_seconds=t.duration, whisper_seconds=whisper_sec)

    recap = summarize.summarize(t, meta)
    import json as _json
    jobstore.set_status(meta.minute_token, "recapping",
                        recap_json=_json.dumps({
                            "summary": recap.summary,
                            "decisions": recap.decisions,
                            "action_items": [a.__dict__ for a in recap.action_items],
                        }, ensure_ascii=False))
    return t, recap, tpath


# ------------------------------------------------------------ transcript .txt


def txt_path(meta: MeetingMeta) -> Path:
    """Đường dẫn file .txt của một cuộc họp — TÍNH được, không phải đi tìm.

    Tách khỏi `write_txt` để chỗ khác (bitable.py, khi đính kèm file vào Base)
    dựng lại đúng đường dẫn thay vì glob mò theo tên.
    """
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=7))
    stamp = ""
    if meta.start:
        stamp = " " + datetime.fromtimestamp(meta.start, tz).strftime("%d-%m-%Y %Hh%M")
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
