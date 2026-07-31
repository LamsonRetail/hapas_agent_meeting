"""
Seam phiên âm (V2_LONGTERM §4.1).

    transcribe(audio_path, minute_token) -> Transcript

Đằng sau là whisper server (E:\\whisper\\server.py, dùng từ V1): POST /transcribe
-> job_id, poll /status/{job_id} đến khi status=="done". Pipeline KHÔNG bao giờ
gọi faster_whisper trực tiếp — đổi CPU/GPU/cloud chỉ là đổi TRANSCRIBE_URL.

Contract server (đã kiểm chứng qua whisper_worker.py của V1):
  POST /transcribe  (form: file, language, task) -> {"job_id": ...}
  GET  /status/{id} -> {"status": queued|running|done|error,
                        "language", "duration", "text", "timestamped", "error"}
  timestamped = các dòng "[HH:MM:SS -> HH:MM:SS] nội dung".

KHÔNG truyền callback_* khi submit: để orchestrator lo phân phối, server chỉ
phiên âm (V2_ARCHITECTURE §4[3]). Server tự bỏ qua nhắn tin nếu thiếu callback.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx

from . import config
from .models import Segment, Transcript


class TranscribeError(RuntimeError):
    pass


class TranscribeUnavailable(TranscribeError):
    """Không phải lỗi của cuộc họp — là hạ tầng (whisper tắt/treo/mạng hỏng).

    Vì sao cần một lớp riêng: `MAX_ATTEMPTS` đếm số lần thử một JOB, ý là "nội
    dung này xử lý mãi không được thì bỏ". Nhưng whisper tắt thì mọi job đều
    hỏng, và vòng `run` cộng attempts mỗi POLL_INTERVAL → ~25 phút là job bị
    đánh `failed` VĨNH VIỄN dù nội dung không sai gì, và không ai được báo.
    `orchestrator.process_queue` bắt riêng lớp này và KHÔNG tính lần thử.
    """


# Whisper server trả "[HH:MM:SS -> HH:MM:SS] nội dung"; phần "-> end" là tùy
# chọn để vẫn nuốt được định dạng cũ "[HH:MM:SS] nội dung".
_LINE = re.compile(
    r"^\[(\d{2}):(\d{2}):(\d{2})(?:\s*->\s*(\d{2}):(\d{2}):(\d{2}))?\]\s*(.*)$"
)


def _hms(h: str, mm: str, s: str) -> float:
    return float(int(h) * 3600 + int(mm) * 60 + int(s))


def _parse_segments(transcript_text: str, duration: float) -> list[Segment]:
    """Bóc các dòng có mốc thời gian thành segment.

    Ưu tiên end thật của server ('-> HH:MM:SS'); nếu dòng không có end thì suy
    ra bằng start của đoạn kế, đoạn cuối kéo tới hết duration.
    """
    starts: list[float] = []
    ends: list[float | None] = []
    texts: list[str] = []
    for line in transcript_text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            if texts:                    # dòng nối tiếp -> gộp vào đoạn trước
                texts[-1] += " " + line.strip()
            continue
        sh, smm, ss, eh, emm, es, txt = m.groups()
        starts.append(_hms(sh, smm, ss))
        ends.append(_hms(eh, emm, es) if eh is not None else None)
        texts.append(txt.strip())

    if not starts:                       # không có timestamp -> một đoạn
        return [Segment(0.0, duration, transcript_text.strip())]

    segs = []
    for i, (st, tx) in enumerate(zip(starts, texts)):
        end = ends[i]
        if end is None:
            end = starts[i + 1] if i + 1 < len(starts) else max(duration, st)
        segs.append(Segment(float(st), float(end), tx))
    return segs


def transcribe(audio_path: Path, minute_token: str,
               lang: str | None = None,
               meeting_title: str = "",
               poll_interval: float = 5.0,
               timeout_sec: float = 6 * 3600) -> Transcript:
    """Phiên âm một file audio. Đồng bộ, có thể chậm (chờ cả hàng đợi GPU)."""
    lang = lang if lang is not None else config.TRANSCRIBE_LANG
    base = config.TRANSCRIBE_URL.rstrip("/")

    with audio_path.open("rb") as f:
        files = {"file": (audio_path.name, f, "application/octet-stream")}
        # Server đọc language/task qua form (Form), KHÔNG qua query param.
        data: dict[str, str] = {"task": "transcribe"}
        if lang:
            data["language"] = lang
        if meeting_title:
            data["meeting_title"] = meeting_title   # server bỏ qua nếu không dùng
        try:
            r = httpx.post(f"{base}/transcribe", files=files, data=data,
                           timeout=300.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise TranscribeUnavailable(f"không gọi được whisper: {exc}") from exc

    job_id = r.json().get("job_id")
    if not job_id:
        raise TranscribeError(f"server không trả job_id: {r.text[:200]}")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            res = httpx.get(f"{base}/status/{job_id}", timeout=30.0).json()
        except httpx.HTTPError:
            continue                     # server bận/chưa sẵn -> thử lại
        status = str(res.get("status") or "").lower()
        if status in ("done", "completed", "finished", "success"):
            duration = float(res.get("duration") or res.get("duration_seconds")
                             or 0.0)
            engine = f"faster-whisper/{res.get('model', 'whisper')}"
            # 'timestamped' có mốc [HH:MM:SS -> HH:MM:SS]; 'text' là bản phẳng.
            body = res.get("timestamped") or res.get("transcript") \
                or res.get("text") or ""
            return Transcript(
                minute_token=minute_token,
                lang=res.get("language") or lang or "vi",
                duration=duration,
                engine=engine,
                segments=_parse_segments(body, duration),
            )
        if status in ("error", "failed"):
            raise TranscribeError(f"server lỗi: {res.get('error')}")
        # queued / running -> tiếp tục chờ

    raise TranscribeUnavailable(
        f"quá thời gian chờ ({timeout_sec/3600:.1f}h) — server treo?")


def save_transcript(t: Transcript, dest: Path) -> None:
    """Lưu JSON có segments (V2_LONGTERM §3.4) — không tạo lại được về sau."""
    import json
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(t.to_json(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
