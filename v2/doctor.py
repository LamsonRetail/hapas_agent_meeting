"""
Khám sức khỏe hệ thống V2 — một lệnh soi mọi thứ hay hỏng.

    python -m v2 doctor

Gom lại đúng những cái bẫy đã gặp lúc go-live: thiếu offline_access, Fernet
key rỗng/sai, sai cổng whisper, chưa ai enroll, refresh sắp hết, hàng đợi kẹt.
Chỉ ĐỌC trạng thái, không sửa gì. Mã thoát != 0 nếu có mục FAIL (để cắm vào
script giám sát / task scheduler).
"""

from __future__ import annotations

import time
from pathlib import Path

from . import config, jobstore, tokenstore

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_MARK = {OK: "[+]", WARN: "[!]", FAIL: "[x]"}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, label: str, detail: str = "") -> None:
        self.rows.append((level, label, detail))

    def worst(self) -> str:
        if any(r[0] == FAIL for r in self.rows):
            return FAIL
        if any(r[0] == WARN for r in self.rows):
            return WARN
        return OK

    def render(self) -> str:
        out = []
        for level, label, detail in self.rows:
            line = f"  {_MARK[level]} {label}"
            if detail:
                line += f" — {detail}"
            out.append(line)
        return "\n".join(out)


def _check_config(r: Report) -> None:
    if config.APP_SECRET:
        r.add(OK, "LARK_APP_SECRET đã đặt")
    else:
        r.add(FAIL, "LARK_APP_SECRET trống", "OAuth sẽ không đổi được token")

    if config.OAUTH_REDIRECT_URI:
        r.add(OK, "OAUTH_REDIRECT_URI", config.OAUTH_REDIRECT_URI)
    else:
        r.add(FAIL, "OAUTH_REDIRECT_URI chưa đặt", "enroll không chạy")

    if "offline_access" in config.OAUTH_SCOPES.split():
        r.add(OK, "scope offline_access có mặt")
    else:
        r.add(FAIL, "thiếu scope offline_access",
              "token v2 sẽ không trả refresh_token")


def _check_fernet(r: Report) -> None:
    if not config.FERNET_KEY:
        r.add(FAIL, "V2_FERNET_KEY trống", "sinh bằng: python -m v2 genkey")
        return
    try:
        from . import crypto
        if crypto.decrypt(crypto.encrypt("ping")) == "ping":
            r.add(OK, "V2_FERNET_KEY hợp lệ (mã hóa/giải mã thử OK)")
        else:
            r.add(FAIL, "V2_FERNET_KEY: round-trip sai")
    except Exception as exc:  # noqa: BLE001 - báo mọi lỗi key cho người vận hành
        r.add(FAIL, "V2_FERNET_KEY không dùng được", str(exc)[:80])


def _check_enrolled(r: Report) -> None:
    users = tokenstore.list_users(active_only=False)
    active = [u for u in users if u.get("status") == "active"]
    if not active:
        r.add(FAIL, "chưa ai enroll active", "chạy: python -m v2 enroll-url")
        return
    now = int(time.time() * 1000)
    for u in active:
        days = (u["refresh_exp"] - now) / 86_400_000
        name = u.get("name") or u.get("open_id")
        if days <= 0:
            r.add(FAIL, f"{name}: refresh HẾT HẠN", "phải enroll lại")
        elif days <= 3:
            r.add(WARN, f"{name}: refresh còn {days:.1f} ngày", "enroll lại sớm")
        else:
            r.add(OK, f"{name}: refresh còn {days:.1f} ngày")


def _check_whisper(r: Report) -> None:
    url = config.TRANSCRIBE_URL.rstrip("/") + "/health"
    try:
        import httpx
        resp = httpx.get(url, timeout=5.0)
        data = resp.json()
        if data.get("status") == "ok":
            model = data.get("model", "?")
            dev = data.get("device", "?")
            loaded = data.get("model_loaded")
            note = f"model={model} device={dev}"
            note += " (đã nạp)" if loaded else " (chưa nạp — lần đầu chậm)"
            r.add(OK, f"whisper server sống @ {config.TRANSCRIBE_URL}", note)
        else:
            r.add(WARN, "whisper /health trả bất thường", str(data)[:80])
    except Exception as exc:  # noqa: BLE001 - kết nối hỏng đủ kiểu
        r.add(FAIL, f"whisper KHÔNG kết nối được @ {config.TRANSCRIBE_URL}",
              "bật E:\\whisper\\run-server.bat + đúng cổng trong TRANSCRIBE_URL")


def _check_llm(r: Report) -> None:
    """Kiểm recap. Provider LOCAL thì gọi thật, không chỉ in cấu hình.

    Vì sao: từ 31/07/2026 recap đi qua `api_server` của Hermes (localhost), tức
    nó phụ thuộc gateway Hermes còn sống. Gateway chết mà doctor vẫn báo `[+]`
    thì hỏng biểu hiện ra ngoài là "biên bản không có tóm tắt" — không ai đoán
    được nguyên nhân. Provider từ xa (api.openai.com) thì chỉ in cấu hình: gọi
    thử là tốn tiền và chậm.
    """
    if not config.LLM_API_KEY:
        r.add(WARN, "không có LLM_API_KEY",
              "sẽ gửi transcript thô, không có recap tóm tắt")
        return

    where = f"{config.LLM_MODEL} @ {config.LLM_BASE_URL}"
    is_local = any(h in config.LLM_BASE_URL
                   for h in ("127.0.0.1", "localhost", "::1"))
    if not is_local:
        r.add(OK, "LLM recap", where)
        return

    url = config.LLM_BASE_URL.rstrip("/") + "/models"
    try:
        import httpx
        resp = httpx.get(url, timeout=8.0,
                         headers={"Authorization": f"Bearer {config.LLM_API_KEY}"})
        if resp.status_code == 200:
            r.add(OK, "LLM recap (local, đã gọi thử)", where)
        elif resp.status_code in (401, 403):
            r.add(FAIL, "LLM recap: sai khoá",
                  f"{where} trả {resp.status_code} — LLM_API_KEY phải khớp "
                  f"API_SERVER_KEY trong .env của Hermes")
        else:
            r.add(WARN, "LLM recap trả bất thường",
                  f"{where} -> HTTP {resp.status_code}")
    except Exception:  # noqa: BLE001 - kết nối hỏng đủ kiểu
        r.add(FAIL, f"LLM recap KHÔNG kết nối được @ {config.LLM_BASE_URL}",
              "gateway Hermes đang tắt? `hermes gateway status` / `restart`. "
              "Không có nó thì biên bản gửi đi KHÔNG có tóm tắt")


def _check_base(r: Report) -> None:
    """Base "nội dung đã chốt". Tắt là hợp lệ — phát biên bản không phụ thuộc nó."""
    from . import bitable
    if not bitable.enabled():
        r.add(WARN, "Base 'nội dung đã chốt' đang TẮT",
              "chạy `python -m v2 base-init` nếu muốn lưu biên bản vào Base")
        return
    ok, detail = bitable.check()
    r.add(OK if ok else FAIL,
          "Base 'nội dung đã chốt'" if ok else "Base cấu hình sai", detail)


def _check_alerts(r: Report) -> None:
    """Cảnh báo DM. Tắt là hợp lệ nhưng phải NÓI ra: tắt nghĩa là mọi thứ dưới
    đây hỏng mà không ai được báo — mà đúng lúc đó dashboard cũng đứng im."""
    from . import alerts
    if not alerts.enabled():
        r.add(WARN, "cảnh báo DM đang TẮT",
              "đặt ALERT_UNION_IDS (union_id, phẩy ngăn) trong v2/.env")
        return
    r.add(OK, f"cảnh báo DM -> {len(config.ALERT_UNION_IDS)} người",
          f"whisper báo sau {config.ALERT_WHISPER_AFTER_MIN} phút, "
          f"token báo khi còn {alerts.TOKEN_DAYS} ngày")


def _check_queue(r: Report) -> None:
    # 4 status cuối là di sản của cửa duyệt đã bỏ (xem jobstore docstring).
    buckets = {
        "queued": OK, "transcribing": OK, "recapping": OK,
        "delivered": OK, "failed": FAIL,
        "awaiting_approval": WARN, "owner_only": OK, "expired": WARN,
        "discarded": OK,
    }
    parts = []
    worst_seen = OK
    for st, lvl in buckets.items():
        n = len(jobstore.by_status(st))
        if n:
            parts.append(f"{st}={n}")
            if lvl == FAIL:
                worst_seen = FAIL
            elif lvl == WARN and worst_seen != FAIL:
                worst_seen = WARN
    detail = ", ".join(parts) or "trống"
    if worst_seen == FAIL:
        r.add(FAIL, "hàng đợi có job failed", detail)
    elif worst_seen == WARN:
        r.add(WARN, "hàng đợi có job ở status di sản (expired/chờ duyệt)", detail)
    else:
        r.add(OK, "hàng đợi", detail)


def _dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total / 1_048_576


def _check_disk(r: Report) -> None:
    work = _dir_size_mb(config.WORK_DIR)
    trans = _dir_size_mb(config.TRANSCRIPT_DIR)
    detail = f"work={work:.0f}MB transcripts={trans:.0f}MB"
    # work/ chỉ chứa file tạm; phình to = job kẹt không dọn.
    if work > 2000:
        r.add(WARN, "thư mục work/ lớn", detail + " (job kẹt? dọn thủ công)")
    else:
        r.add(OK, "dung lượng data", detail)


def collect() -> Report:
    """Chạy toàn bộ kiểm tra, trả Report (không in gì).

    Tách khỏi run() để status_push.py đẩy cùng bộ kiểm tra lên dashboard —
    một nguồn sự thật, dashboard không bao giờ lệch với `doctor`.
    """
    config.ensure_dirs()
    from . import db
    db.init()

    r = Report()
    _check_config(r)
    _check_fernet(r)
    _check_enrolled(r)
    _check_whisper(r)
    _check_llm(r)
    _check_base(r)
    _check_alerts(r)
    _check_queue(r)
    _check_disk(r)
    return r


def run() -> int:
    r = collect()

    print("=== V2 doctor ===")
    print(r.render())
    verdict = r.worst()
    print()
    if verdict == OK:
        print("Kết luận: SẴN SÀNG — mọi mục OK.")
        return 0
    if verdict == WARN:
        print("Kết luận: CHẠY ĐƯỢC nhưng có cảnh báo (xem [!]).")
        return 0
    print("Kết luận: CÓ LỖI CHẶN (xem [x]) — sửa trước khi chạy thật.")
    return 1
