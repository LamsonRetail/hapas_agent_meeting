"""
Đẩy snapshot trạng thái lên dashboard Vercel — "local chủ động, Vercel câm".

    python -m v2 push-status            # đẩy một lần
    python -m v2 push-status --print    # chỉ in JSON, không gửi

Vercel KHÔNG BAO GIỜ chạm token / state.db (V2_ARCHITECTURE §3). Nên chiều dữ
liệu là local -> Vercel, không có đường ngược lại: máy local gom trạng thái,
ẩn danh, rồi POST lên `/api/status` kèm bearer secret dùng chung.

Ẩn danh nghĩa là gì (kỷ luật, đừng phá):
  - CÓ:     display_name, status, mốc hết hạn refresh, ĐẾM job theo status,
            kết quả doctor, cấu hình không bí mật (chu kỳ quét, dry-run).
  - KHÔNG:  open_id / union_id, access/refresh token, tên cuộc họp,
            minute_token, transcript, tên file, đường dẫn tuyệt đối.
Snapshot phải công khai được mà không hại — nếu thêm field mới, đo bằng thước
đó trước khi thêm.

Đẩy hỏng KHÔNG BAO GIỜ được làm chết vòng orchestrator: mọi lỗi đều nuốt và
chỉ in cảnh báo.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, jobstore, tokenstore

# Đường dẫn ổ đĩa Windows, cắt tới khoảng trắng đầu tiên. Lưới CUỐI cho những
# đường dẫn chưa có trong `_known_paths()`.
_ABS_PATH = re.compile(r"[A-Za-z]:\\[^\s]*")


def _known_paths() -> list[tuple[str, str]]:
    """(đường dẫn thật, chuỗi thay thế) — dài trước, để cái ngắn không nuốt cái dài."""
    pairs = [
        (str(config.KEY_BACKUP_PATH), "<khóa Fernet, để ngoài repo>"),
        (str(config.BACKUP_DIR), "<thư mục sao lưu>"),
        (str(config.DB_PATH), "<state.db>"),
        (str(config.TRANSCRIPT_DIR), "<transcripts>"),
        (str(config.WORK_DIR), "<work>"),
        (str(config.DATA_DIR), "<data>"),
        (str(Path.home()), "~"),
    ]
    return sorted([p for p in pairs if p[0]], key=lambda kv: -len(kv[0]))


def scrub(text: str) -> str:
    """Bỏ đường dẫn tuyệt đối khỏi chuỗi TRƯỚC khi đẩy lên dashboard.

    Vì sao phải cưỡng chế bằng code chứ không bằng lời dặn ở docstring module
    (thêm 02/08/2026): `build_snapshot` nhét NGUYÊN kết quả `doctor.collect()`
    vào snapshot, mà `doctor` là công cụ cho người ngồi trước máy nên nó in
    đường dẫn đầy đủ — rất đúng ở terminal, rất sai trên một trang web.

    Đã rò thật, đo được trên `…/api/status?json=1` (GET, **không cần xác thực**):
      - có sẵn từ trước: `E:\\whisper\\run-server.bat` (gợi ý sửa lỗi whisper)
      - và mục `_check_backup` mới thêm hôm nay sẽ đẩy nốt đường dẫn file khóa
        Fernet, KÈM tên người dùng Windows — tức chỉ thẳng cho người lạ biết
        khóa giải mã token nằm ở đâu.

    Đặt ở `status_push` chứ không ở `doctor` có chủ ý: `doctor` phải giữ đường
    dẫn đầy đủ (người vận hành cần copy-paste), còn đây là bề mặt CÔNG KHAI nên
    đây là chỗ đúng để siết. Lưới này cũng chặn luôn mọi mục `doctor` thêm về
    sau, không phải nhớ sửa hai nơi.
    """
    if not text:
        return text
    for real, mask in _known_paths():
        if real in text:
            text = text.replace(real, mask)
    text = _ABS_PATH.sub("<đường dẫn cục bộ>", text)

    # Vân tay khóa Fernet: có ích cho người ngồi trước máy (đối chiếu bản sao
    # với khóa nào), VÔ DỤNG với người xem từ xa. Không phải lỗ hổng — 16 ký tự
    # đầu của SHA-256 không lần ngược ra khóa — nhưng thứ không có lý do gì để
    # ở trên trang công khai thì đừng để nó ở đó.
    if config.FERNET_KEY:
        from . import backup
        fp = backup.key_fingerprint()
        if fp and fp != "(trống)":
            text = text.replace(fp, "…")
    return text

# v2 (31/07/2026): thêm `pushed_by` — xem build_snapshot.
SNAPSHOT_VERSION = 2

# Các status job muốn đếm — cùng danh sách với `doctor` và `status`.
# awaiting_approval / owner_only / expired / discarded là DI SẢN của cửa duyệt
# đã bỏ — giữ trong danh sách đếm để job cũ trong DB không biến mất khỏi
# dashboard; job mới không bao giờ vào các status đó nữa.
_QUEUE_STATUSES = (
    "queued", "transcribing", "recapping",
    "delivered", "failed",
    "awaiting_approval", "owner_only", "expired", "discarded",
)

_TZ_VN = timezone(timedelta(hours=7))


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_snapshot(next_scan_at_ms: int | None = None,
                   with_checks: bool = True,
                   pushed_by: str = "manual") -> dict:
    """Gom trạng thái hiện tại thành dict đã ẩn danh, sẵn sàng POST.

    next_scan_at_ms: mốc dự kiến vòng quét kế tiếp (orchestrator biết, CLI thì
    không) — để dashboard đếm ngược. Để None thì suy ra từ POLL_INTERVAL.

    pushed_by: `"run"` (vòng orchestrator) hay `"manual"` (gõ `push-status`).
    Vì sao PHẢI có, và vì sao "để push-status đọc SEND_MODE từ .env" KHÔNG sửa
    được (đo 31/07/2026): `send_mode` dưới đây là của TIẾN TRÌNH đang đẩy. Vòng
    thật chạy `python -m v2 run --send`, tức `config.SEND_MODE` được cờ dòng
    lệnh bật lên True — trong khi `.env` vẫn ghi `SEND_MODE=0`. Nên một lần
    `push-status` gõ tay đọc `.env` ra 0 và trang hiện "dry-run" **dù hệ thống
    đang gửi thật**. Tiến trình gõ tay KHÔNG có cách nào biết cờ của vòng run;
    thứ duy nhất trung thực làm được là NÓI RA rằng snapshot này không phải của
    vòng chạy. Đã gặp thật: một lượt `run` phụ (dry-run, whisper cổng chết) đẩy
    đè lên và trang báo hệ thống hỏng trong khi nó vẫn chạy bình thường.
    """
    now = _now_ms()

    users = [
        {
            "name": u.get("name") or "(chưa rõ tên)",
            "status": u.get("status") or "?",
            "refresh_exp_ms": int(u.get("refresh_exp") or 0),
        }
        for u in tokenstore.list_users(active_only=False)
    ]

    queue = {}
    for st in _QUEUE_STATUSES:
        n = len(jobstore.by_status(st))
        if n:
            queue[st] = n

    snap: dict = {
        "snapshot_version": SNAPSHOT_VERSION,
        "pushed_at_ms": now,
        "pushed_at_local": datetime.now(_TZ_VN).strftime("%d/%m/%Y %H:%M:%S %z"),
        "users": users,
        "queue": queue,
        "poll_interval_s": config.POLL_INTERVAL,
        "next_scan_at_ms": int(next_scan_at_ms
                               if next_scan_at_ms is not None
                               else now + config.POLL_INTERVAL * 1000),
        # Đẩy bằng CLI thì không có vòng lặp nào đang chạy -> mốc quét kế tiếp
        # chỉ là phỏng đoán. Nói thẳng ra để dashboard không bịa.
        "next_scan_estimated": next_scan_at_ms is None,
        "paused": bool(config.PAUSED),
        "send_mode": bool(config.SEND_MODE),
        # "run" | "manual" — xem docstring. Trang Vercel dựa vào đây để KHÔNG
        # trình bày `send_mode` của một lần đẩy tay như thể là của vòng chạy.
        "pushed_by": "run" if pushed_by == "run" else "manual",
    }

    if with_checks:
        # Dùng đúng bộ kiểm tra của `doctor` để dashboard không lệch với CLI.
        from . import doctor
        # Không tự chấm sức khỏe của chính status push trong snapshot đang chuẩn
        # bị đẩy: nếu lần trước hỏng nhưng lần này vừa hồi phục, nhét lỗi cũ vào
        # snapshot sẽ làm dashboard đỏ giả cho tới nhịp kế tiếp. CLI `doctor`
        # vẫn đọc log lần đẩy gần nhất và báo relay đầy đủ.
        report = doctor.collect(check_relay=False)
        # `scrub` BẮT BUỘC ở đây: doctor in đường dẫn đầy đủ cho người ngồi
        # trước máy, còn trang này ai cũng GET được (đo 02/08: HTTP 200, không
        # cần xác thực). Xem docstring `scrub`.
        snap["checks"] = [
            {"level": lvl, "label": scrub(label), "detail": scrub(detail)}
            for lvl, label, detail in report.rows
        ]
        snap["verdict"] = report.worst()
    else:
        snap["checks"] = []
        snap["verdict"] = "OK"

    return snap


def push(snapshot: dict | None = None, *, quiet: bool = False) -> bool:
    """POST snapshot lên Vercel. True nếu server nhận. Không bao giờ raise."""
    url = config.STATUS_PUSH_URL.strip()
    secret = config.STATUS_PUSH_SECRET.strip()
    if not url or not secret:
        if not quiet:
            print("[status] chưa cấu hình STATUS_PUSH_URL/STATUS_PUSH_SECRET "
                  "— bỏ qua đẩy dashboard")
        return False

    if snapshot is None:
        snapshot = build_snapshot()

    try:
        import httpx
        resp = httpx.post(
            url,
            content=json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=config.STATUS_PUSH_TIMEOUT,
        )
    except Exception as exc:                       # noqa: BLE001
        if not quiet:
            print(f"[status] đẩy hỏng (mạng): {exc}")
        return False

    if resp.status_code == 200:
        if not quiet:
            print(f"[status] đã đẩy snapshot -> {url}")
        return True
    if not quiet:
        detail = resp.text[:160].replace("\n", " ")
        hint = ""
        if resp.status_code == 401:
            hint = "  (STATUS_PUSH_SECRET hai đầu không khớp)"
        elif resp.status_code == 404:
            hint = "  (sai URL? phải là .../api/status)"
        print(f"[status] đẩy hỏng HTTP {resp.status_code}: {detail}{hint}")
    return False


def enabled() -> bool:
    return bool(config.STATUS_PUSH_URL.strip() and config.STATUS_PUSH_SECRET.strip())


def heartbeat(next_scan_at_ms: int | None = None) -> bool:
    """Gọi từ vòng orchestrator. Nuốt MỌI lỗi (kể cả lỗi gom dữ liệu) — một
    dashboard hỏng không được phép dừng việc phát biên bản."""
    if not enabled():
        return False
    try:
        return push(build_snapshot(next_scan_at_ms=next_scan_at_ms,
                                   pushed_by="run"))
    except Exception as exc:                       # noqa: BLE001
        print(f"[status] heartbeat hỏng: {exc}")
        return False
