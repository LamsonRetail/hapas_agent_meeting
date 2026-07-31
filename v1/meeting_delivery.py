#!/usr/bin/env python3
"""
Nhận job từ poller -> lấy transcript -> sinh recap -> gửi qua bot Lark.

Bước 3, 4, 5 của luồng auto meeting note.

Hai nguồn transcript:
    --source whisper   tải audio về rồi chạy Whisper local (mặc định)
    --source lark      lấy transcript sẵn có của Lark (nhanh, để đối chiếu)

Chạy:
    python meeting_delivery.py --dry-run --to ou_xxx
    python meeting_delivery.py --to ou_xxx
    python meeting_delivery.py --source lark --dry-run --to ou_xxx

Biến môi trường để sinh recap (tùy chọn):
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

JOB_DIR = Path("jobs")
DONE_DIR = Path("jobs/done")
OUT_DIR = Path("out")          # phải tương đối: --file từ chối đường tuyệt đối

GLOSSARY = [
    "MCP", "Claude", "Node.js", "Lark", "connector", "authorize",
    "terminal", "macOS", "Windows", "restart", "install", "token",
]

LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")


class DeliveryError(RuntimeError):
    pass


_LARK_BIN: str | None = None

# Profile của lark-cli. Để trống -> dùng profile mặc định (app anh Thiện).
# Đặt LARK_PROFILE=mine để đọc bằng app riêng.
LARK_PROFILE = os.environ.get("LARK_PROFILE", "")


def _lark_bin() -> str:
    """Trên Windows lark-cli là file .cmd, subprocess không tự dò PATHEXT."""
    global _LARK_BIN
    if _LARK_BIN is None:
        found = shutil.which("lark-cli")
        if not found:
            raise DeliveryError(
                "Không tìm thấy lark-cli trong PATH.\n"
                "Kiểm tra bằng: where lark-cli"
            )
        _LARK_BIN = found
    return _LARK_BIN


def lark(*args: str) -> dict:
    proc = subprocess.run(
        [_lark_bin(), *args, "--json"]
        + (["--profile", LARK_PROFILE] if LARK_PROFILE else []),
        capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
    )
    raw = proc.stdout.strip()
    if not raw:
        raise DeliveryError(f"lark-cli không trả gì: {proc.stderr.strip()}")
    payload = json.loads(raw)
    if not payload.get("ok"):
        err = payload.get("error", {})
        raise DeliveryError(f"lark-cli lỗi {err.get('code')}: {err.get('message')}")
    return payload.get("data", {})


# --------------------------------------------------- bước 3+4: lấy transcript


def _safe_name(text: str) -> str:
    """Bỏ ký tự Windows không cho phép đặt tên file."""
    out = re.sub(r'[\\/:*?"<>|]', "", text).strip(" .")
    out = re.sub(r"\s+", " ", out)
    return out[:60] or "Cuoc hop"


def _transcript_dest(job: dict, source: str) -> Path:
    """Tên file dễ đọc, đường dẫn tương đối so với cwd.

    Phải tương đối vì im +messages-send --file từ chối đường tuyệt đối.
    """
    title = _safe_name(job.get("title") or "")

    stamp = ""
    raw = job.get("started_at")
    if raw:
        try:
            stamp = datetime.fromisoformat(str(raw)).strftime(" %d-%m-%Y %Hh%M")
        except ValueError:
            pass

    return OUT_DIR / f"Bien ban - {title}{stamp}.txt"


def _extract_audio(video: Path) -> Path:
    """Tách âm thanh khỏi video, giảm mạnh dung lượng trước khi upload.

    Whisper vốn chuyển mọi thứ về 16kHz mono, nên bỏ hình + gộp mono +
    hạ sample-rate không mất gì. NHƯNG phải giữ PCM lossless: nén xuống
    MP3 32k như bản cũ thêm một tầng lossy thứ hai khiến Whisper ảo giác,
    lặp câu, nuốt chữ (nặng nhất ở đoạn nói nhỏ / âm có dấu).

    WAV 16kHz mono ~1.8 MB/phút (30' ~55MB, 1h ~110MB). Lớn hơn mp3 cũ
    nhưng vẫn nhỏ hơn nhiều so với mp4 gốc, và audio chỉ POST sang
    localhost:8000 nên không dính giới hạn 30MB của Lark IM.
    Không có ffmpeg thì dùng nguyên file gốc.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("       (không có ffmpeg, gửi thẳng file gốc)")
        return video

    audio = video.with_suffix(".wav")
    proc = subprocess.run(
        [ffmpeg, "-i", str(video),
         "-vn",                  # bỏ hình
         "-ac", "1",             # gộp về 1 kênh
         "-ar", "16000",         # 16kHz, đúng thứ Whisper dùng
         "-c:a", "pcm_s16le",    # PCM lossless - KHÔNG nén lossy.
                                 # MP3 32k trước đây thêm 1 tầng mất mát
                                 # thứ hai -> Whisper ảo giác/lặp/nuốt chữ.
                                 # Audio chỉ POST sang localhost nên không
                                 # dính giới hạn 30MB của Lark IM.
         "-y", str(audio)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )

    if proc.returncode != 0 or not audio.exists():
        print(f"       (tách âm thanh hỏng, dùng file gốc)")
        return video

    before = video.stat().st_size / 1_048_576
    after = audio.stat().st_size / 1_048_576
    print(f"       tách âm thanh: {before:.1f} MB -> {after:.1f} MB "
          f"(giảm {100 * (1 - after / before):.0f}%)")

    try:
        video.unlink()          # bỏ video gốc cho đỡ chật đĩa
    except OSError:
        pass

    return audio


def get_transcript_whisper(job: dict) -> tuple[str, Path]:
    """Tải audio từ Lark rồi chạy Whisper local.

    ĐỔI SANG SERVER THẬT: giữ nguyên phần tải audio, thay lời gọi
    whisper_worker.transcribe() bằng POST sang endpoint của anh Thiện.
    """
    from whisper_worker import TranscribeError, transcribe  # noqa: PLC0415

    minute_token = job["minute_token"]
    print("       tải bản ghi từ Lark...")
    data = lark("minutes", "+download",
                "--minute-tokens", minute_token, "--overwrite")

    saved = data.get("saved_path")
    if not saved:
        raise DeliveryError(f"không tải được bản ghi của {minute_token}")

    media = Path(saved)                        # đường dẫn tuyệt đối
    size_mb = data.get("size_bytes", 0) / 1_048_576
    print(f"       {media.name}  ({size_mb:.1f} MB)")

    if media.suffix.lower() in (".mp4", ".mkv", ".mov", ".avi", ".webm"):
        media = _extract_audio(media)

    dest = _transcript_dest(job, "whisper")
    try:
        body = transcribe(media, dest)
    except TranscribeError as exc:
        raise DeliveryError(str(exc)) from exc

    return body, dest


def get_transcript_lark(job: dict) -> tuple[str, Path]:
    """Lấy transcript sẵn có của Lark. Nhanh, có nhãn người nói."""
    minute_token = job["minute_token"]
    data = lark("minutes", "+detail",
                "--minute-tokens", minute_token,
                "--transcript", "--overwrite")

    items = data.get("minutes", [])
    if not items:
        raise DeliveryError(f"không thấy minute {minute_token}")

    rel = items[0].get("artifacts", {}).get("transcript_file")
    if not rel:
        raise DeliveryError(f"minute {minute_token} không có transcript")

    src = Path(rel)
    if not src.exists():
        raise DeliveryError(f"lark báo có file nhưng không thấy: {src}")

    dest = _transcript_dest(job, "lark")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)

    return dest.read_text(encoding="utf-8", errors="replace"), dest


def make_recap(transcript: str, title: str) -> str | None:
    if not LLM_API_KEY:
        return None

    prompt = (
        f"Đây là bản ghi lời cuộc họp tiêu đề {title}. "
        f"Bản ghi do máy phiên âm nên có thể sai chính tả, nhất là thuật ngữ "
        f"kỹ thuật. Gặp từ nghe giống các thuật ngữ sau thì viết đúng lại: "
        f"{', '.join(GLOSSARY)}.\n\n"
        f"Viết recap tiếng Việt gồm ba phần: nội dung chính, quyết định đã "
        f"chốt, việc cần làm kèm người phụ trách nếu có. Viết gọn, không bịa "
        f"thông tin ngoài bản ghi. Nếu bản ghi quá ngắn hoặc không rõ nội "
        f"dung thì nói thẳng như vậy.\n\n---\n{transcript[:60000]}"
    )

    req = urllib.request.Request(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        data=json.dumps({
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LLM_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        return out["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"[warn] sinh recap hỏng, gửi không kèm recap: {exc}", file=sys.stderr)
        return None


# ------------------------------------------------------------- bước 5: gửi


def send_to(open_id: str, job: dict, recap: str | None,
            tpath: Path, source: str, dry_run: bool) -> None:
    title = job.get("title") or "(không tiêu đề)"
    token = job["minute_token"]

    lines = []
    if job.get("started_at"):
        lines.append(f"Thời gian: {job['started_at']}")
    if job.get("duration_sec"):
        lines.append(f"Thời lượng: {job['duration_sec'] // 60} phút")
    if lines:
        lines.append("")
    lines.append(recap or "Chưa sinh được recap. Bản ghi đầy đủ ở file đính kèm.")
    if job.get("app_link"):
        lines.append("")
        lines.append(f"[Xem trên Lark Minutes]({job['app_link']})")

    body = "\n".join(lines)
    header = f"Biên bản: {title}"

    if dry_run:
        print(f"\n{'=' * 60}\nSẼ GỬI CHO {open_id}\n{'=' * 60}")
        print(f"**{header}**\n{body}")
        print(f"{'-' * 60}\nkèm file: {tpath}\n{'=' * 60}\n")
        return

    # Ưu tiên app riêng nếu đã cấu hình, không thì dùng lark-cli.
    try:
        import lark_sender  # noqa: PLC0415
        use_own_app = lark_sender.ready()
    except ImportError:
        use_own_app = False

    if use_own_app:
        # open_id gắn với từng app -> app riêng phải dùng user_id
        rid = job.get("union_id_map", {}).get(open_id) or open_id
        lark_sender.send_markdown(rid, header, body,
                                  idem_key=f"r-{source}-{token}-{rid}")
        lark_sender.send_file(rid, tpath,
                              idem_key=f"s-{source}-{token}-{rid}")
        print(f"       -> đã gửi cho {rid}  (app riêng)")
    else:
        message = f"**{header}**\n{body}"
        lark("im", "+messages-send", "--as", "bot", "--user-id", open_id,
             "--markdown", message,
             "--idempotency-key", f"r-{source}-{token}-{open_id}"[:50])
        lark("im", "+messages-send", "--as", "bot", "--user-id", open_id,
             "--file", str(tpath),
             "--idempotency-key", f"s-{source}-{token}-{open_id}"[:50])
        print(f"       -> đã gửi cho {open_id}  (qua lark-cli)")


# ------------------------------------------------------------ xử lý 1 job


def process(job_path: Path, source: str, dry_run: bool,
            force_to: str | None) -> bool:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    token = job["minute_token"]
    print(f"\n[job] {job.get('title')}  ({token})  nguồn={source}")

    try:
        if source == "whisper":
            transcript, tpath = get_transcript_whisper(job)
        else:
            transcript, tpath = get_transcript_lark(job)
    except DeliveryError as exc:
        print(f"[error] lấy transcript hỏng: {exc}", file=sys.stderr)
        return False

    words = len(transcript.split())
    print(f"       transcript {words} từ -> {tpath}")

    if words < 20:
        print("[warn] transcript quá ngắn, bỏ qua để vòng sau thử lại.",
              file=sys.stderr)
        return False

    recap = make_recap(transcript, job.get("title") or "")
    print(f"       recap: {'có' if recap else 'chưa cấu hình LLM'}")

    # Ghép open_id với user_id theo thứ tự trả về từ cùng một sự kiện lịch
    opens = job.get("participants") or []
    unions = job.get("participant_union_ids") or []
    if len(opens) == len(unions):
        job["union_id_map"] = dict(zip(opens, unions))

    targets = [force_to] if force_to else opens
    if not targets:
        print("[warn] không có người nhận. Dùng --to ou_xxx để gửi thử.",
              file=sys.stderr)
        return False

    sent, failed = [], []
    for open_id in targets:
        try:
            send_to(open_id, job, recap, tpath, source, dry_run)
            sent.append(open_id)
        except DeliveryError as exc:
            failed.append(open_id)
            print(f"[error] gửi cho {open_id} hỏng: {exc}", file=sys.stderr)
            if "availability" in str(exc).lower() or "230013" in str(exc):
                print("        -> App chưa phát hành cho người này.",
                      file=sys.stderr)
                print("        -> Console: Version Management & Release",
                      file=sys.stderr)
                print("           đổi Availability sang toàn công ty.",
                      file=sys.stderr)

    if failed and not sent:
        # không gửi được cho ai -> giữ job lại để thử lại sau
        return False

    if failed:
        print(f"       gửi được {len(sent)}/{len(targets)}, "
              f"bỏ qua {len(failed)} người không gửi được")

    if not dry_run:
        DONE_DIR.mkdir(parents=True, exist_ok=True)
        job["delivered_at"] = datetime.now().isoformat(timespec="seconds")
        job["delivered_to"] = sent
        job["delivery_failed"] = failed
        job["transcript_source"] = source
        (DONE_DIR / job_path.name).write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        job_path.unlink()

    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Gửi biên bản cuộc họp")
    ap.add_argument("--job", type=Path, help="xử lý đúng 1 file job")
    ap.add_argument("--source", choices=["whisper", "lark"], default="whisper",
                    help="nguồn transcript (mặc định whisper)")
    ap.add_argument("--dry-run", action="store_true",
                    help="in nội dung sẽ gửi, không gửi thật")
    ap.add_argument("--to", metavar="OPEN_ID", help="ép gửi cho 1 người")
    args = ap.parse_args()

    jobs = [args.job] if args.job else sorted(JOB_DIR.glob("*.json"))
    jobs = [p for p in jobs if p.is_file()]

    if not jobs:
        print("Không có job nào trong jobs/")
        return

    ok = sum(process(p, args.source, args.dry_run, args.to) for p in jobs)
    print(f"\nXong {ok}/{len(jobs)} job.")


if __name__ == "__main__":
    main()
