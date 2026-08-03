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


class EmptyTranscript(PipelineError):
    """Phiên âm chạy xong nhưng KHÔNG ra chữ nào.

    VĨNH VIỄN với đúng đoạn audio đó, cùng họ với `MediaDenied`: chạy lại whisper
    trên cùng file cho ra đúng kết quả đó, nên không được đốt `MAX_ATTEMPTS`
    rồi báo một câu vô nghĩa.

    Vì sao phải ném thay vì cứ phát (đo 02/08/2026, job `test`
    obsg22ct6md6ogbe3hi1i782): whisper trả 1 segment `text: ""` cho 61,7s audio
    -> `write_txt` ghi file 0 byte -> `im_upload` lỗi 234010 "File's size can't
    be 0" -> `base_media_upload` lỗi 1061002. Nhưng job vẫn `delivered`,
    `error=NULL`, `recap_fails=0`, Base vẫn có record, và `deliveries` chỉ có
    dòng `recap` — KHÔNG có dòng nào ghi lại việc transcript không gửi được.
    Tức mọi chỗ đọc trạng thái đều nói "xong", không ai được báo, và cách duy
    nhất biết là đọc log bằng mắt.

    KHÔNG lưu `transcript_path` cho ca này: để `_reuse` không nạp lại bản rỗng
    và phát nó mãi. File .json vẫn ghi ra đĩa làm bằng chứng chẩn lỗi.
    """


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

    # Ghi .json xong mới kiểm, và kiểm TRƯỚC khi lưu `transcript_path`:
    # xem docstring `EmptyTranscript`.
    if t.word_count == 0:
        raise EmptyTranscript(
            f"whisper chạy xong nhưng KHÔNG ra chữ nào ({t.duration:.0f}s audio, "
            f"engine {t.engine}). Bản ghi im lặng thật, hoặc whisper đang hỏng "
            f"(sai model/VAD nuốt hết). Bằng chứng: {tpath.name}. "
            f"Nghe thử bản ghi: im lặng thật thì bỏ job này; whisper sai thì sửa "
            f"rồi trả job về `queued` — sẽ phiên âm LẠI từ đầu, không dùng lại "
            f"bản rỗng.")

    # Lưu transcript_path TRƯỚC khi recap: recap hỏng thì vòng sau còn nạp lại
    # được từ đây (orchestrator._reuse) thay vì phiên âm lại từ đầu.
    jobstore.set_status(meta.minute_token, "recapping",
                        transcript_path=str(tpath),
                        audio_seconds=t.duration, whisper_seconds=whisper_sec)
    return t, tpath


# ---------------------------------------------------------- transcript .docx


def doc_path(meta: MeetingMeta) -> Path:
    """Đường dẫn file biên bản của một cuộc họp — TÍNH được, không phải đi tìm.

    Đuôi đổi `.txt` -> `.docx` ngày 03/08/2026 (user chốt, V2_MAINTENANCE §35).
    File cũ vẫn còn trên đĩa: xem `legacy_txt_path`.

    Tách khỏi `write_doc` để chỗ khác (bitable.py, khi đính kèm file vào Base)
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
    return config.TRANSCRIPT_DIR / f"Bien ban - {_safe_name(meta.title)}{stamp}.docx"


def legacy_txt_path(meta: MeetingMeta) -> Path:
    """Đường dẫn .txt của các cuộc họp phát TRƯỚC 03/08/2026.

    `txt_path` đổi đuôi .txt -> .docx hôm đó. Các file cũ vẫn nằm trên đĩa và
    `bitable._tracking_fields` vẫn cần thấy chúng: mất dấu là những record cũ
    khi được ghi lại (sync_tracking / retry_missing_records) sẽ tụt xuống đính
    kèm bản .json — người mở ô file trên Base nhận một cục JSON thay vì biên bản.
    """
    return doc_path(meta).with_suffix(".txt")


def write_doc(t: Transcript, meta: MeetingMeta) -> Path:
    """Xuất transcript ra .docx (user chốt 03/08/2026, V2_MAINTENANCE §35)."""
    from . import docxfile
    return docxfile.write_docx(t, meta, doc_path(meta))


# --------------------------------------------------------------- phát


def _upload_doc(txt: Path, t: Transcript | None = None) -> tuple[str | None, str]:
    """(file_key, lý do KHÔNG có file). Đúng một trong hai giá trị có nghĩa.

    ⚠️ Từ 03/08/2026 phải kiểm CẢ transcript, không chỉ cỡ file. Bản .txt rỗng
    là 0 byte nên `st_size` bắt được; bản .docx của một transcript rỗng vẫn
    ~1 KB (tiêu đề + dòng cảnh báo + cấu trúc ZIP). Bỏ phép kiểm này là gửi cho
    người dự một file mở ra không có chữ nào, và mọi chỗ đọc trạng thái đều
    báo `ok=1` — đúng lỗi §31.3 quay lại bằng cửa khác.
    """
    if t is not None and not (t.text or "").strip():
        return None, "transcript rỗng — không có gì để gửi"
    if txt.stat().st_size == 0:
        # Lark từ chối file 0 byte (234010) — đừng gọi API để nhận đúng câu đó.
        # Với job mới thì `EmptyTranscript` đã chặn từ trước; nhánh này là cho
        # job CŨ đang được phát lại từ một transcript rỗng đã lưu.
        return None, "transcript rỗng (0 byte) — không có gì để gửi"
    try:
        ftype = lark_api.FILE_TYPES.get(txt.suffix.lower(), "stream")
        return lark_api.im_upload_file(txt, ftype), ""
    except lark_api.LarkError as exc:
        return None, f"upload transcript hỏng: {exc}"


def deliver_file(meta: MeetingMeta, t: Transcript,
                 recipients: list[str]) -> tuple[list[str], list[str]]:
    """Gửi RIÊNG file transcript, KHÔNG gửi lại thẻ tóm tắt.

    Vì sao phải có hàm riêng (02/08/2026): `deliver` luôn gửi thẻ rồi mới tới
    file. Người đã nhận được thẻ mà file hỏng (429, mạng chớp) thì gọi lại
    `deliver` là họ nhận **thẻ trùng** cho cùng một cuộc họp — nên trước đó
    không có đường nào thử lại, và `_backfill_deliveries` chỉ dám tra
    `kind="recap"`. Đây là mảnh còn thiếu của chính bản vá đó.

    Transcript rỗng thì KHÔNG ghi gì và KHÔNG tính là một lần thử: không có gì
    để gửi thì thử lại bao nhiêu lần cũng vậy, mà ghi thêm dòng `ok=0` mỗi vòng
    chỉ làm bảng `deliveries` nói dối là đã cố.
    """
    txt = write_doc(t, meta)
    token = meta.minute_token
    file_key, why = _upload_doc(txt, t)
    if file_key is None:
        print(f"[deliver] {token} gửi bù file: {why}")
        if txt.stat().st_size > 0:
            # Upload hỏng (khác với rỗng) LÀ một lần thử — phải ghi, nếu không
            # bộ đếm không bao giờ tới hạn và vòng nào cũng thử lại mãi.
            for rid in recipients:
                jobstore.record_delivery(token, rid, "full", False, why)
        return [], list(recipients)

    sent, failed = [], []
    for rid in recipients:
        try:
            lark_api.im_send_file(rid, txt, id_type="union_id",
                                  uuid_key=f"txt-{token}-{rid}",
                                  file_key=file_key)
            jobstore.record_delivery(token, rid, "full", True)
            sent.append(rid)
        except lark_api.LarkError as exc:
            jobstore.record_delivery(token, rid, "full", False, str(exc))
            failed.append(rid)
            print(f"[deliver] gửi bù transcript cho {rid} hỏng: {exc}")
    return sent, failed


def deliver(meta: MeetingMeta, recap: Recap, t: Transcript,
            recipients: list[str], *,
            dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Phát tóm tắt + file transcript cho MỌI người dự (danh sách union_id).

    Trả (đã gửi tóm tắt, hỏng). Gửi hỏng một người không làm gãy cả job.

    Ai nhận: người trong sự kiện lịch của host (meetings.resolve_participants).
    Không còn cửa duyệt — xem docstring orchestrator.
    """
    card = cards.recap_card(meta, recap)
    txt = write_doc(t, meta)

    if dry_run:
        print(f"\n{'='*56}\nDRY-RUN — sẽ phát cho {len(recipients)} người\n{'='*56}")
        print(recap.to_markdown()[:1500])
        print(f"{'-'*56}\nkèm transcript: {txt}\n{'='*56}\n")
        return recipients, []

    sent, failed = [], []
    token = meta.minute_token

    # Upload transcript MỘT lần rồi dùng lại file_key cho mọi người: upload lặp
    # theo từng người là n lần tải file lên cho cùng một nội dung.
    file_key, no_file_why = _upload_doc(txt, t)
    if no_file_why:
        print(f"[deliver] {no_file_why}, chỉ gửi tóm tắt")

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
            # GHI LẠI việc không gửi được transcript. Trước 02/08/2026 nhánh này
            # `continue` lặng lẽ, nên `deliveries` chỉ có dòng `recap` và bảng
            # đó — thứ duy nhất trả lời được "ai đã nhận gì" — nói rằng mọi
            # thứ đều ổn. Đã xảy ra thật với job `test` (234010).
            jobstore.record_delivery(token, rid, "full", False, no_file_why)
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
