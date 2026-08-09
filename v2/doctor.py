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

# Mức nghiêm trọng của từng status khi đếm hàng đợi. Khoá PHẢI phủ đúng
# `jobstore.ALL_STATUSES` — selftest cưỡng chế điều đó, vì bản cũ gõ tay danh
# sách này và quên `held`, tức doctor báo "hàng đợi" mà giấu mất trạng thái mà
# gần như mọi cuộc họp kết thúc ở đó (mô hình kéo, 03/08/2026).
#
# `held` là OK chứ không phải WARN: nó là điểm dừng BÌNH THƯỜNG — đã phiên âm
# xong, cố ý giữ lại chờ người dự hỏi. Không có gì để người vận hành phải làm.
QUEUE_LEVELS = {
    # WARN: `create()` luôn đặt 'queued', nên một job còn ở 'detected' nghĩa là
    # có đường ghi nào đó bỏ sót — đáng để người vận hành nhìn thấy.
    "detected": WARN,
    "queued": OK, "transcribing": OK, "recapping": OK,
    "waiting_auth": OK,
    "held": OK, "delivered": OK, "failed": FAIL,
    "awaiting_approval": WARN, "owner_only": OK, "expired": WARN,
    "discarded": OK,
}
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


def relay_log_health(text: str) -> tuple[str, str]:
    """Đọc kết quả đẩy Vercel gần nhất mà không chạm mạng hay xoá OAuth code.

    Dashboard status, link rút gọn và hộp thư callback của deployment hiện tại
    cùng dùng một Blob store. Vì endpoint ``oauth-pending`` là kiểu đọc-rồi-xoá,
    doctor tuyệt đối không được gọi nó để health-check: chạy doctor đúng lúc ai
    đó auth sẽ nuốt mất code một-lần. Log của lần POST status gần nhất là phép
    thử an toàn đã có sẵn; nếu Blob bị suspend thì chính ``put()`` này báo lỗi.
    """
    ok_at = text.rfind("[status] đã đẩy snapshot")
    fail_at = max(text.rfind("[status] đẩy hỏng"),
                  text.rfind("[status] heartbeat hỏng"))
    if fail_at > ok_at:
        tail = text[fail_at:fail_at + 500].lower()
        if "store has been suspended" in tail or "blob" in tail and "suspend" in tail:
            return FAIL, ("Vercel Blob bị suspend; callback OAuth không tự chuyển "
                          "code về máy")
        return FAIL, "lần đẩy Vercel gần nhất bị hỏng; xem log v2"
    if ok_at >= 0:
        return OK, "lần đẩy Vercel gần nhất thành công"
    return WARN, "chưa thấy kết quả đẩy Vercel trong log"


def _check_oauth_relay(r: Report) -> None:
    if config.CF_RELAY_URL:
        from . import cloudflare_relay
        if not cloudflare_relay.queue_enabled():
            r.add(FAIL, "Cloudflare OAuth relay",
                  "đã bật CF_RELAY_URL nhưng thiếu account/queue/token/khóa ký")
            return
        try:
            detail = cloudflare_relay.live_health()
        except Exception as exc:  # noqa: BLE001 - doctor must report, not crash
            r.add(FAIL, "Cloudflare OAuth relay", str(exc))
        else:
            r.add(OK, "Cloudflare OAuth relay", detail)
        return
    if not config.OAUTH_PULL_URL:
        r.add(FAIL, "hộp thư OAuth tự phục vụ chưa cấu hình",
              "auth xong sẽ không tự trả code về máy")
        return
    log_dir = config.DATA_DIR / "logs"
    files = sorted(log_dir.glob("v2-*.log"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "[status]" not in text:
            continue
        level, detail = relay_log_health(text)
        r.add(level, "Vercel Blob / callback OAuth", detail)
        return
    r.add(WARN, "Vercel Blob / callback OAuth",
          "chưa có log để xác nhận relay đang sống")


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
        elif days <= tokenstore.WARN_DAYS:
            # Ngưỡng dùng CHUNG với `tokenstore.auth_report` — xem chú thích ở
            # `tokenstore.WARN_DAYS` để biết vì sao KHÔNG được đặt là 7.
            r.add(WARN, f"{name}: refresh còn {days:.1f} ngày", "enroll lại sớm")
        else:
            r.add(OK, f"{name}: refresh còn {days:.1f} ngày")


def _check_whisper(r: Report) -> None:
    url = config.TRANSCRIBE_URL.rstrip("/") + "/health"
    from . import whisper_supervisor
    if config.WHISPER_AUTOSTART and whisper_supervisor.is_local_url():
        script = config.WHISPER_START_SCRIPT.expanduser().resolve()
        if script.is_file():
            r.add(OK, "whisper tự phục hồi đã cấu hình", str(script))
        else:
            r.add(FAIL, "whisper tự phục hồi thiếu script", str(script))
    elif config.WHISPER_AUTOSTART:
        r.add(WARN, "whisper là endpoint từ xa — không tự khởi động",
              config.TRANSCRIBE_URL)
    else:
        r.add(WARN, "whisper tự phục hồi đang TẮT",
              "WHISPER_AUTOSTART=0; tiến trình chết phải bật lại bằng tay")
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
              f"v2 run sẽ thử bật {config.WHISPER_START_SCRIPT}; kiểm tra log "
              "v2/data/logs/whisper-YYYY-MM-DD.log")


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


# =====================================================================
#  Ai đọc được Base — CỬA THỨ HAI vào cùng dữ liệu
# =====================================================================
#
# Vì sao mục này tồn tại (thêm 02/08/2026): mọi phân quyền của V2 nằm ở CODE —
# `qa._may_see` cho đường hỏi đáp (§20), `_recipients` cho đường phát (§24). Cả
# hai chỉ gác đúng một cửa. Cùng nội dung đó còn nằm trên Base: tóm tắt, quyết
# định, danh sách người dự, và cột `File transcript` là **attachment chứa
# nguyên văn** buổi họp. Ai mở được Base thì thấy hết, không đi qua dòng code
# nào của V2.
#
# ACL của Base là thiết lập Lark thủ công, nằm ngoài repo, không ai theo dõi.
# Mục này KHÔNG sửa gì — chỉ làm cửa thứ hai thành thứ nhìn thấy được, để việc
# nó rộng hay hẹp là một quyết định có người ký chứ không phải mặc định ai đó
# bấm nhầm sáu tháng trước.

# Giá trị `link_share_entity` -> (mức, câu giải thích). Xếp từ hẹp tới rộng.
LINK_SHARE: dict[str, tuple[str, str]] = {
    "closed": (OK, "chỉ người được thêm vào mới xem được"),
    # Với V2, Base chứa nguyên văn transcript và đường bot có ACL theo người dự.
    # Bất kỳ link-share vượt ACL đó đều là lỗi chặn go-live, không chỉ cảnh báo.
    "tenant_readable": (FAIL, "MỌI người trong công ty có link đều ĐỌC được"),
    "tenant_editable": (FAIL, "MỌI người trong công ty có link đều SỬA được"),
    "partner_tenant_readable": (FAIL,
                                "công ty mình VÀ tổ chức đối tác đều đọc được"),
    "anyone_readable": (FAIL, "BẤT KỲ AI có link, kể cả ngoài công ty, ĐỌC được"),
    "anyone_editable": (FAIL, "BẤT KỲ AI có link, kể cả ngoài công ty, SỬA được"),
}

# Câu nói rõ CÁI GÌ bị phơi. Không viết chung chung "dữ liệu nhạy cảm": người
# đọc phải biết đây là nguyên văn lời nói trong phòng họp, không phải tóm tắt.
_WHAT_LEAKS = ("Base chứa tóm tắt + quyết định + người dự + attachment NGUYÊN "
               "VĂN transcript của MỌI cuộc họp; đường này KHÔNG qua bộ lọc "
               "người dự của bot")


def judge_base_access(members: list[dict], public: dict,
                      names: dict[str, str] | None = None
                      ) -> list[tuple[str, str, str]]:
    """(mức, nhãn, chi tiết) cho quyền truy cập Base. Hàm THUẦN — không gọi mạng.

    Tách khỏi `_check_base_access` để selftest kiểm được bảng ánh xạ mà không
    cần Base thật: đây là loại logic mà sai một giá trị enum là báo an toàn cho
    một Base đang mở toang.
    """
    names = names or {}
    out: list[tuple[str, str, str]] = []

    who = []
    for m in members:
        mid = str(m.get("member_id") or "")
        who.append(f"{names.get(mid) or mid[:14]} ({m.get('perm') or '?'})")
    out.append((OK, f"Base: {len(members)} người được thêm tường minh",
                ", ".join(who) or "(không ai)"))

    ent = str(public.get("link_share_entity") or "")
    level, why = LINK_SHARE.get(
        ent, (WARN, f"giá trị lạ {ent!r} — tra Console rồi bổ sung vào LINK_SHARE"))
    if level == OK:
        out.append((OK, "Base: chia sẻ bằng link đang ĐÓNG", why))
    else:
        detail = f"{why} [{ent}]. {_WHAT_LEAKS}"
        if public.get("external_access_entity") == "open":
            detail += ". Link còn chuyển được RA NGOÀI công ty"
        out.append((level, "Base: ai có link cũng đọc được", detail))
    return out


def _check_base_access(r: Report) -> None:
    """Gọi hai API đọc rồi giao cho `judge_base_access`. Hỏng thì WARN, không FAIL:
    không tra được quyền là chuyện của phép đo, không phải của hệ thống."""
    from . import bitable, lark_api
    if not bitable.enabled():
        return                                # `_check_base` đã nói Base đang tắt
    try:
        members = lark_api.drive_members(config.BITABLE_APP_TOKEN)
        public = lark_api.drive_public(config.BITABLE_APP_TOKEN)
    except Exception as exc:                  # noqa: BLE001 — mạng/quyền đủ kiểu
        r.add(WARN, "không đọc được quyền truy cập Base", str(exc)[:110])
        return

    names: dict[str, str] = {}
    try:                                      # tên người cho dễ đọc; thiếu cũng không sao
        ids = [str(m.get("member_id") or "") for m in members
               if m.get("member_type") == "openid"]
        names = {k: v.get("name") or "" for k, v in
                 lark_api.contact_batch([i for i in ids if i]).items()}
    except Exception:                         # noqa: BLE001
        pass

    for level, label, detail in judge_base_access(members, public, names):
        r.add(level, label, detail)


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
    parts = []
    worst_seen = OK
    for st, lvl in QUEUE_LEVELS.items():
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


def _check_backup(r: Report) -> None:
    """Sao lưu state.db. Không có backup = một lỗi đĩa là enroll lại cả công ty
    VÀ cả công ty mất quyền đọc biên bản của chính mình (xem backup.py)."""
    from . import backup
    if not backup.enabled():
        r.add(WARN, "sao lưu state.db đang TẮT",
              "đặt V2_BACKUP_EVERY_HOURS > 0 trong v2/.env")
        return

    newest, age_h = backup.latest()
    n = len(backup.snapshots())
    if newest is None:
        r.add(WARN, "chưa có bản sao nào",
              f"chạy ngay: python -m v2 backup (đích: {config.BACKUP_DIR})")
    elif age_h > config.BACKUP_EVERY_HOURS * 2:
        r.add(WARN, f"bản sao mới nhất đã {age_h:.0f}h tuổi",
              f"{newest.name} — vòng `run` có đang chạy không?")
    else:
        r.add(OK, f"sao lưu: {n} bản, mới nhất {age_h:.1f}h trước",
              newest.name)

    # Khóa Fernet: DB không có khóa là rác, nên thiếu nửa này thì nửa kia vô nghĩa.
    if not config.FERNET_KEY:
        return                              # _check_fernet đã báo FAIL rồi
    if config.KEY_BACKUP_PATH.exists():
        r.add(OK, "khóa Fernet đã lưu riêng chỗ",
              f"{config.KEY_BACKUP_PATH} (vân tay {backup.key_fingerprint()})")
    else:
        r.add(WARN, "khóa Fernet CHƯA được lưu riêng",
              f"sẽ tự ghi ở lần backup tới -> {config.KEY_BACKUP_PATH}")

    # Nói to giới hạn, đừng để `[+]` làm người ta tưởng đã an toàn trước mọi thứ.
    same = (str(config.BACKUP_DIR)[:1].upper()
            == str(config.DB_PATH)[:1].upper())
    if same:
        r.add(WARN, "bản sao nằm CÙNG Ổ với state.db",
              "chống được xoá nhầm/DB hỏng, KHÔNG chống được chết ổ — trỏ "
              "V2_BACKUP_DIR ra ngoài máy nếu cần")


def collect(*, check_relay: bool = True) -> Report:
    """Chạy toàn bộ kiểm tra, trả Report (không in gì).

    Tách khỏi run() để status_push.py đẩy cùng bộ kiểm tra lên dashboard —
    một nguồn sự thật, dashboard không bao giờ lệch với `doctor`.
    """
    config.ensure_dirs()
    from . import db
    db.init()

    r = Report()
    _check_config(r)
    if check_relay:
        _check_oauth_relay(r)
    _check_fernet(r)
    _check_enrolled(r)
    _check_whisper(r)
    _check_llm(r)
    _check_base(r)
    _check_base_access(r)
    _check_alerts(r)
    _check_queue(r)
    _check_disk(r)
    _check_backup(r)
    return r


def run() -> int:
    r = collect(check_relay=True)

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
