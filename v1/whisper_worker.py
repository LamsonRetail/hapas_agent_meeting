#!/usr/bin/env python3
"""
Client gọi Whisper server (bản chạy local, sau này là server của anh Thiện).

Server đã có sẵn REST API:
    POST /transcribe        gửi file, nhận job_id
    GET  /status/{job_id}   hỏi xong chưa
    GET  /health            kiểm tra server và model

CHUYỂN SANG SERVER CHÍNH: chỉ cần đổi WHISPER_URL (và API_KEY nếu có).
Không phải sửa gì thêm.

Chạy độc lập để test:
    python whisper_worker.py "minutes\\xxx\\abc.mp4"
"""

import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

WHISPER_URL = os.environ.get("WHISPER_URL", "http://localhost:8000")
API_KEY = os.environ.get("WHISPER_API_KEY")      # chỉ cần khi server bật API_KEY
LANGUAGE = "vi"                                   # ép tiếng Việt, README khuyên vậy
POLL_SECONDS = 10
MAX_WAIT_SECONDS = 4 * 3600                       # bỏ cuộc sau 4 tiếng


class TranscribeError(RuntimeError):
    pass


def _fmt(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


def _get(path: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(f"{WHISPER_URL}{path}", headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_file(path: str, file_path: Path, fields: dict) -> dict:
    """Upload multipart bằng stdlib, không cần thư viện requests."""
    boundary = f"----wp{uuid.uuid4().hex}"
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    parts = bytearray()
    for key, val in fields.items():
        parts += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n{val}\r\n'
        ).encode("utf-8")

    parts += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{file_path.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    parts += file_path.read_bytes()
    parts += f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        f"{WHISPER_URL}{path}",
        data=bytes(parts),
        headers={
            **_headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _dig(data: dict, *keys: str):
    """Lấy giá trị đầu tiên tìm được trong số các tên trường có thể có.

    Chưa biết chắc schema của server nên dò nhiều tên thay vì
    bám cứng một tên. Xem tên thật ở http://localhost:8000/docs
    rồi rút gọn hàm này lại.
    """
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def health() -> dict:
    try:
        return _get("/health", timeout=10)
    except urllib.error.URLError as exc:
        raise TranscribeError(
            f"Không kết nối được Whisper server tại {WHISPER_URL}.\n"
            f"Bật server bằng run-server.bat rồi thử lại. ({exc})"
        ) from exc


def transcribe(audio_path: str | Path, out_file: str | Path) -> str:
    """Gửi file lên server, chờ xong, ghi transcript ra out_file."""
    audio = Path(audio_path)
    if not audio.exists():
        raise TranscribeError(f"không thấy file audio: {audio}")

    info = health()
    print(f"[whisper] server OK  model={_dig(info, 'model', 'whisper_model') or '?'}"
          f"  device={_dig(info, 'device') or '?'}")

    size_mb = audio.stat().st_size / 1_048_576
    print(f"[whisper] gửi {audio.name}  ({size_mb:.1f} MB)")

    try:
        created = _post_file("/transcribe", audio,
                             {"lang": LANGUAGE, "timestamp": "true"})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise TranscribeError(f"POST /transcribe lỗi {exc.code}: {body}") from exc

    job_id = _dig(created, "job_id", "id", "jobId")
    if not job_id:
        raise TranscribeError(f"server không trả job_id: {created}")
    print(f"[whisper] job {job_id}, đang chờ...")

    started = time.monotonic()
    last_note = ""

    while True:
        elapsed = time.monotonic() - started
        if elapsed > MAX_WAIT_SECONDS:
            raise TranscribeError(f"quá {MAX_WAIT_SECONDS // 3600} tiếng chưa xong, bỏ cuộc")

        time.sleep(POLL_SECONDS)

        try:
            st = _get(f"/status/{job_id}")
        except urllib.error.URLError as exc:
            print(f"[warn] hỏi status hỏng, thử lại: {exc}", file=sys.stderr)
            continue

        state = str(_dig(st, "status", "state") or "").lower()
        progress = _dig(st, "progress", "percent")

        note = f"{state} {progress}" if progress is not None else state
        if note != last_note:
            pct = f"  {progress}%" if progress is not None else ""
            print(f"[whisper] {_fmt(elapsed)}  {state}{pct}")
            last_note = note

        if state in ("done", "completed", "finished", "success"):
            break
        if state in ("error", "failed"):
            raise TranscribeError(
                f"server báo lỗi: {_dig(st, 'error', 'message', 'detail')}")

    body = _dig(st, "transcript", "text", "result", "output")
    if not body:
        raise TranscribeError(f"job xong nhưng không có transcript: {st}")

    if isinstance(body, list):     # phòng khi server trả list segment
        body = "\n".join(
            seg if isinstance(seg, str) else str(_dig(seg, "text") or "")
            for seg in body
        )

    total = time.monotonic() - started
    dest = Path(out_file)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")

    print(f"[whisper] xong sau {_fmt(total)}, {len(body.split())} từ -> {dest}")
    return body


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = Path(sys.argv[1])
    try:
        transcribe(src, Path("out") / f"{src.stem}_whisper.txt")
    except TranscribeError as exc:
        print(f"\n[LỖI] {exc}", file=sys.stderr)
        sys.exit(1)
