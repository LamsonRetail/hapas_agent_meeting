"""
Cảnh báo qua Lark DM — để hệ thống TỰ NÓI khi nó hỏng.

Vì sao cần: trước module này không có cảnh báo nào. Muốn biết hệ thống hỏng thì
phải tự đi đọc `v2\\data\\logs\\v2-<ngày>.log` hoặc mở dashboard Vercel — mà
đúng lúc hỏng nhất thì dashboard cũng đứng im, vì nó chỉ cập nhật khi vòng `run`
còn sống (`orchestrator.run` -> `status_push.heartbeat`). Tức là công cụ theo
dõi duy nhất tắt cùng lúc với thứ nó theo dõi.

Năm tình huống, mỗi cái CHỈ báo một lần cho tới khi tình trạng đổi:

    vòng `run` im quá lâu  | hệ thống không làm gì, và mọi mục dưới cũng im
    job -> failed          | mất biên bản của một cuộc họp, im lặng
    whisper gọi không được | mọi job nằm chờ trong `queued`, không ai biết
    LLM recap gọi không được | job chỉ hoãn RECAP_MAX_TRIES vòng rồi PHÁT ĐI
                             với tóm tắt rỗng — khác hẳn whisper
    token còn <= 2 ngày    | hết hạn là V2 không đọc được minutes nữa

Mục ĐẦU là mục duy nhất chỉ có nghĩa khi `check_all()` được gọi TỪ NGOÀI vòng
`run` (Task Scheduler -> `python -m v2 alerts`, xem run-v2-alerts.bat). Bốn mục
còn lại chạy được ở cả hai đường; chạy hai chỗ không sinh DM trùng vì mốc chống
spam nằm ở `alert_state` chứ không ở RAM của tiến trình.

CHỐNG SPAM là yêu cầu số một, không phải tính năng phụ. Vòng `run` chạy mỗi
POLL_INTERVAL (mặc định 300s); báo mỗi vòng thì một job `failed` = 288 cái DM
một ngày, và người ta tắt thông báo của bot — sau đó cảnh báo thật cũng không
ai đọc. Mốc "đã báo" nằm ở bảng `alert_state` (BỀN, không phải RAM: restart
`run` là chuyện thường, mất mốc thì mỗi lần khởi động lại là một cái DM nữa).

KHÔNG có hàm nào ở đây được phép ném ra ngoài lúc bình thường: caller là vòng
`run`, và cảnh báo hỏng KHÔNG được làm chết vòng chạy. Gửi hỏng thì bỏ qua và
thử lại vòng sau — đúng ra là phải vậy: mốc chống spam chỉ ghi khi tin đã tới
tay ít nhất một người.
"""

from __future__ import annotations

import time
from typing import Any

from . import config, db, jobstore, lark_api, tokenstore

# Token còn ít hơn ngần này ngày thì DM. CỐ Ý thấp hơn ngưỡng 3 ngày của
# `doctor._check_enrolled`: doctor là chỗ người ta chủ động vào xem nên cảnh báo
# sớm là rẻ; DM là thứ đi tìm người, phải hiếm mới còn giá trị. Thứ tự mong
# muốn: doctor kêu ở 3 ngày -> DM ở 2 ngày -> hết hạn ở 0.
TOKEN_DAYS = 2

# Gọi /health của whisper. Ngắn: đây là phép kiểm sống-chết, không phải chờ việc.
_HEALTH_TIMEOUT = 5.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def enabled() -> bool:
    """Có ai để báo không. Trống = tắt hẳn (V2 vẫn chạy bình thường)."""
    return bool(config.ALERT_UNION_IDS)


# =====================================================================
#  Mốc chống spam (bảng alert_state)
# =====================================================================


def _mark_get(key: str) -> str | None:
    row = db.conn().execute(
        "SELECT value FROM alert_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _mark_set(key: str, value: str) -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO alert_state(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (key, value, _now_ms()),
        )


def _mark_clear(key: str) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM alert_state WHERE key=?", (key,))


def _mark_keys(prefix: str) -> list[str]:
    """Lọc bằng Python, KHÔNG dùng `LIKE prefix||'%'`: trong LIKE của SQLite dấu
    `_` là ký tự đại diện một ký tự, mà khoá của ta có `_` (`job_failed:`) —
    tức LIKE sẽ khớp rộng hơn ý định. Bảng này nhỏ (vài dòng), lọc ở đây rẻ hơn
    là nhớ viết ESCAPE cho đúng."""
    rows = db.conn().execute("SELECT key FROM alert_state").fetchall()
    return [r["key"] for r in rows if r["key"].startswith(prefix)]


# =====================================================================
#  Gửi
# =====================================================================


def _send(text: str) -> bool:
    """DM cho mọi admin. True nếu ÍT NHẤT một người nhận được.

    "Ít nhất một" là điều kiện để ghi mốc chống spam: gửi hỏng hết mà vẫn ghi
    mốc thì cái hỏng đó bị nuốt luôn, không bao giờ báo lại.

    Bắt Exception rộng có chủ ý: đường này gọi HTTP, và một cú mạng hỏng không
    được phép leo lên làm chết vòng `run`.
    """
    ok = False
    for uid in config.ALERT_UNION_IDS:
        try:
            # In message_id: dấu vết duy nhất để về sau truy "cảnh báo đó có
            # thật sự rời khỏi máy này không", thay vì suy từ "không thấy lỗi".
            mid = lark_api.im_send_text(uid, text, id_type="union_id")
            print(f"[alert] -> {uid} msg_id={mid}")
            ok = True
        except Exception as exc:            # noqa: BLE001 — xem docstring
            print(f"[alert] không gửi được cho {uid}: {exc}")
    return ok


# =====================================================================
#  1. Job chuyển failed
# =====================================================================


def _text_failed(row: dict) -> str:
    return (
        "[V2] Job THẤT BẠI — cuộc họp này sẽ KHÔNG có biên bản\n"
        f"Cuộc họp: {row.get('title') or '(không tên)'}\n"
        f"minute_token: {row['minute_token']}\n"
        f"Đã thử: {row.get('attempts')}/{config.MAX_ATTEMPTS}\n"
        f"Lỗi cuối: {(row.get('error') or '(không ghi lại)')[:300]}\n\n"
        "Sửa xong nguyên nhân thì trả job về hàng đợi:\n"
        f'  sqlite3 "{config.DB_PATH}" "UPDATE jobs SET status=\'queued\', '
        f"attempts=0 WHERE minute_token='{row['minute_token']}';\"\n"
        "attempts=0 là bắt buộc — không đặt lại thì vòng sau nó vượt "
        f"MAX_ATTEMPTS ({config.MAX_ATTEMPTS}) ngay và failed lại.\n"
        "Transcript + recap cũ (nếu đã có) được dùng lại, KHÔNG phiên âm lại."
    )


def _check_failed_jobs(out: list[tuple[str, str, Any]]) -> None:
    failed = jobstore.by_status("failed")
    live = {f"job_failed:{r['minute_token']}" for r in failed}

    # Job đã được cứu (về queued/delivered/discarded) -> xoá mốc, để lần sau nó
    # hỏng lại thì còn được báo. Không có bước này thì mỗi job chỉ báo được
    # đúng một lần trong cả đời cái DB.
    for k in _mark_keys("job_failed:"):
        if k not in live:
            _mark_clear(k)

    for row in failed:
        key = f"job_failed:{row['minute_token']}"
        # Vân tay = attempts: job được cứu rồi failed lại thì attempts khác ->
        # báo lại. Cùng attempts = vẫn đúng cái hỏng cũ -> im.
        fp = str(row.get("attempts") or 0)
        if _mark_get(key) == fp:
            continue
        out.append((key, fp, _text_failed(row)))


# =====================================================================
#  2. Whisper không gọi được (Việc 2b — watchdog kiểu log + cảnh báo)
# =====================================================================
#
# CỐ Ý không tự bật lại whisper. V2 không nên đi mở cửa sổ Windows: nó chạy
# dưới quyền người đăng nhập, spawn tiến trình từ vòng lặp là thứ không kiểm
# được bằng test và hỏng âm thầm. Ở đây chỉ làm cho cái hỏng NHÌN THẤY ĐƯỢC.

_WHISPER_SINCE = "whisper_down_since"      # mốc bắt đầu hỏng (epoch ms)
_WHISPER_SENT = "whisper_down_alerted"     # đã DM cho lần hỏng này chưa


def _whisper_alive() -> bool:
    try:
        import httpx
        resp = httpx.get(config.TRANSCRIBE_URL.rstrip("/") + "/health",
                         timeout=_HEALTH_TIMEOUT)
        return (resp.json() or {}).get("status") == "ok"
    except Exception:                       # noqa: BLE001 — hỏng đủ kiểu, đều là "chết"
        return False


def _check_whisper(out: list[tuple[str, str, Any]]) -> None:
    if _whisper_alive():
        if _mark_get(_WHISPER_SINCE):
            print("[alert] whisper đã trở lại — xoá mốc cảnh báo")
        _mark_clear(_WHISPER_SINCE)
        _mark_clear(_WHISPER_SENT)
        return

    since = _mark_get(_WHISPER_SINCE)
    if since is None:
        since = str(_now_ms())
        _mark_set(_WHISPER_SINCE, since)
    down_min = (_now_ms() - int(since)) / 60_000
    n_queued = len(jobstore.by_status("queued", "transcribing", "recapping"))

    # In MỖI VÒNG khi còn hỏng (khác với DM chỉ một lần): log là chỗ người ta
    # đọc lại về sau để biết nó tắt bao lâu.
    print(f"[alert] whisper KHÔNG gọi được @ {config.TRANSCRIBE_URL} — "
          f"{down_min:.1f}/{config.ALERT_WHISPER_AFTER_MIN} phút, "
          f"{n_queued} job đang chờ")

    if down_min < config.ALERT_WHISPER_AFTER_MIN:
        return
    if _mark_get(_WHISPER_SENT) == since:
        return
    out.append((_WHISPER_SENT, since, (
        f"[V2] Whisper không gọi được đã {down_min:.0f} phút liên tục\n"
        f"TRANSCRIBE_URL: {config.TRANSCRIBE_URL}\n"
        f"Job đang chờ: {n_queued} — KHÔNG mất, và attempts KHÔNG tăng "
        "(lỗi hạ tầng không tính lần thử).\n"
        "Bật lại: E:\\whisper\\run-server.bat rồi `python -m v2 doctor`.\n"
        "Whisper sống lại là job trong hàng đợi tự đi tiếp, không phải làm gì thêm."
    )))


# =====================================================================
#  3. LLM (recap) không gọi được
# =====================================================================
#
# Song song với whisper nhưng HẬU QUẢ NGƯỢC, và đó là lý do nó cần cảnh báo
# riêng: whisper chết thì job nằm chờ trong `queued` và không mất gì (attempts
# không tăng). LLM chết thì `_recap_step` chỉ hoãn được `RECAP_MAX_TRIES` vòng
# — mặc định 3 × 5 phút — rồi CHỊU phát biên bản với tóm tắt rỗng. Tức 15 phút
# LLM chết là đủ để một cuộc họp mất tóm tắt, trong khi whisper chết 15 tiếng
# cũng không mất gì.
#
# `_backfill_recaps` (orchestrator) vá lại được cái đã mất, nhưng chỉ khi có
# người biết mà bật LLM lên. Đó là việc của mục này.

_LLM_SINCE = "llm_down_since"
_LLM_SENT = "llm_down_alerted"


def _llm_alive() -> bool | None:
    """LLM recap còn gọi được không. None = không kiểm được, đừng kết luận.

    Chỉ gọi thử với provider LOCAL (Hermes api_server). Provider từ xa thì trả
    None: gọi thử tốn tiền, và một cú 429 của họ không phải là "chết" theo
    nghĩa cần đánh thức người vận hành. Cùng quy tắc với `doctor._check_llm` —
    hai chỗ lệch nhau thì doctor báo xanh còn DM báo đỏ, không ai tin cái nào.
    """
    if not config.LLM_API_KEY:
        return None                          # chưa cấu hình: doctor lo, không DM
    if not any(h in config.LLM_BASE_URL for h in ("127.0.0.1", "localhost", "::1")):
        return None
    try:
        import httpx
        resp = httpx.get(
            config.LLM_BASE_URL.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            timeout=_HEALTH_TIMEOUT)
        return resp.status_code == 200
    except Exception:                        # noqa: BLE001 — hỏng đủ kiểu, đều là "chết"
        return False


def _check_llm(out: list[tuple[str, str, Any]]) -> None:
    alive = _llm_alive()
    if alive is None:
        return
    if alive:
        if _mark_get(_LLM_SINCE):
            print("[alert] LLM recap đã trở lại — xoá mốc cảnh báo")
        _mark_clear(_LLM_SINCE)
        _mark_clear(_LLM_SENT)
        return

    since = _mark_get(_LLM_SINCE)
    if since is None:
        since = str(_now_ms())
        _mark_set(_LLM_SINCE, since)
    down_min = (_now_ms() - int(since)) / 60_000
    n_wait = len(jobstore.by_status("queued", "transcribing", "recapping"))
    print(f"[alert] LLM recap KHÔNG gọi được @ {config.LLM_BASE_URL} — "
          f"{down_min:.1f}/{config.ALERT_LLM_AFTER_MIN} phút, "
          f"{n_wait} job đang chờ")

    if down_min < config.ALERT_LLM_AFTER_MIN:
        return
    if _mark_get(_LLM_SENT) == since:
        return
    out.append((_LLM_SENT, since, (
        f"[V2] LLM recap không gọi được đã {down_min:.0f} phút liên tục\n"
        f"LLM_BASE_URL: {config.LLM_BASE_URL}\n"
        f"Job đang chờ: {n_wait}.\n\n"
        f"KHÁC whisper: job KHÔNG nằm chờ vô hạn. Mỗi job chỉ hoãn "
        f"{config.RECAP_MAX_TRIES} vòng rồi PHÁT ĐI với tóm tắt rỗng.\n"
        "Bật lại: `hermes gateway status` / `hermes gateway restart`, rồi "
        "`python -m v2 doctor`.\n"
        "Cuộc họp đã lỡ phát bản rỗng sẽ được vòng `run` tự làm lại tóm tắt "
        "và cập nhật Base khi LLM sống lại — không phải làm gì thêm."
    )))


# =====================================================================
#  4. Vòng `run` đã chết
# =====================================================================
#
# Phép kiểm DUY NHẤT ở file này chỉ có nghĩa khi chạy TỪ NGOÀI vòng `run`.
# Gọi từ trong vòng thì heartbeat luôn tươi nên nó không bao giờ kêu — đúng
# như vậy, và đó là lý do cùng một `check_all()` dùng được cho cả hai đường.
#
# Đo cái gì: TIẾN TRÌNH còn sống, không phải vòng lặp còn tiến. Một thread nền
# ghi mốc mỗi 60s (`note_run_alive`), vì một vòng lặp có thể bận phiên âm hàng
# chục phút cho một cuộc họp dài — lấy mốc đầu vòng làm chuẩn thì báo động giả.
# CHẤP NHẬN CÓ Ý THỨC: tiến trình treo mà thread còn chạy thì mục này im. Muốn
# bắt cả ca đó phải đo tiến độ hàng đợi, phức tạp hơn nhiều và chưa cần.

_RUN_BEAT = "run_heartbeat"
_RUN_SENT = "run_dead_alerted"
RUN_BEAT_EVERY_S = 60


def note_run_alive() -> None:
    """Vòng `run` báo "tôi còn sống". CHỈ `orchestrator.run` được gọi.

    Đừng gọi từ `v2 alerts`, `v2 process` hay bất cứ lệnh chạy tay nào: ghi mốc
    này ở chỗ khác là che mất đúng cái nó sinh ra để phát hiện.
    """
    _mark_set(_RUN_BEAT, str(_now_ms()))


def _check_run_stale(out: list[tuple[str, str, Any]]) -> None:
    if config.ALERT_RUN_STALE_MIN <= 0:
        return
    beat = _mark_get(_RUN_BEAT)
    if not beat:
        return          # chưa bao giờ chạy `run` -> không có gì để gọi là "chết"
    try:
        last = int(beat)
    except ValueError:
        return
    quiet_min = (_now_ms() - last) / 60_000
    if quiet_min < config.ALERT_RUN_STALE_MIN:
        _mark_clear(_RUN_SENT)               # sống lại -> cho phép báo lần sau
        return
    if _mark_get(_RUN_SENT) == beat:
        return

    from datetime import datetime
    when = datetime.fromtimestamp(last / 1000).strftime("%d/%m %H:%M")
    out.append((_RUN_SENT, beat, (
        f"[V2] Vòng `run` đã im {quiet_min:.0f} phút — nhiều khả năng nó CHẾT\n"
        f"Nhịp cuối: {when}\n\n"
        "Trong lúc này hệ thống KHÔNG làm gì cả: không phát hiện cuộc họp mới, "
        "không phiên âm, không phát biên bản, và mọi cảnh báo khác cũng im "
        "(chúng chạy bên trong chính vòng này).\n\n"
        "GẤP hơn vẻ ngoài: refresh token của Lark sống 7 NGÀY và được gia hạn "
        "bởi chính vòng `run`. Tắt quá 7 ngày là MỌI người phải bấm lại link "
        "cấp quyền từ đầu.\n\n"
        "Bật lại: E:\\meetingxlark\\run-v2-auto.bat (hoặc đăng xuất/đăng nhập "
        "lại Windows), rồi `python -m v2 doctor`."
    )))


# =====================================================================
#  5. Token sắp hết hạn
# =====================================================================


def _token_text(u: dict, han: str) -> str:
    """Nội dung DM gia hạn token. Dựng LÚC SẮP GỬI, không dựng sẵn.

    Hai lý do, cả hai đều đã cắn thật ở chỗ khác:
      - Link phải RÚT GỌN. `enroll-url` và `gate` đã qua `short_link()` từ
        31/07/2026, đường này thì bị sót — mà URL authorize nay 3.132 ký tự, dán
        vào chat Lark là xuống dòng gãy link và người nhận ngại bấm. Đây lại là
        cái link dễ bị chuyển tiếp nhất (DM gia hạn), nên nó là đường tệ nhất để
        sót. Bốn đường ra cùng một thứ thì phải sửa cả bốn.
      - Dựng lúc gửi thì `check_all` không phải sinh nonce cho những cảnh báo
        cuối cùng KHÔNG gửi (dry-run, hoặc ALERT_UNION_IDS trống).
    """
    from . import oauth
    # Nonce buộc theo open_id và DÙNG LẠI cái còn sống: gửi hỏng thì vòng sau
    # vẫn là cùng một link, không phải một nonce mới mỗi 5 phút.
    link, _ = oauth.start_or_reuse(u["open_id"])
    link = oauth.short_link(link) or link
    name = u.get("name") or u["open_id"]
    return (
        f"[V2] Token của {name} {han}\n"
        "Hết hạn là V2 không đọc được minutes của người này nữa — không lỗi, "
        "không cảnh báo, chỉ là không còn biên bản.\n\n"
        "Gia hạn: bấm link rồi chọn Đồng ý (link sống 24h):\n"
        f"{link}"
    )


def _check_tokens(out: list[tuple[str, str, Any]]) -> None:
    now = _now_ms()
    # active_only=False rồi tự lọc 'revoked': người bị `tokenstore` đánh
    # 'expired' (đã quá refresh_exp) phải VẪN được báo — đó đúng là tình huống
    # cần báo nhất, mà active_only=True thì lọc mất họ.
    for u in tokenstore.list_users(active_only=False):
        if u.get("status") == "revoked":
            continue
        key = f"token:{u['open_id']}"
        days = (u["refresh_exp"] - now) / 86_400_000
        if days > TOKEN_DAYS:
            _mark_clear(key)                # đã enroll lại -> cho phép báo lần sau
            continue
        # Vân tay = refresh_exp: enroll lại thì mốc này đổi, nên lần tới sắp hết
        # hạn vẫn được báo. Cùng refresh_exp = vẫn cái token cũ -> im.
        fp = str(u["refresh_exp"])
        if _mark_get(key) == fp:
            continue

        han = "ĐÃ HẾT HẠN" if days <= 0 else f"còn {days:.1f} ngày"
        # Hoãn dựng nội dung (kèm sinh link) tới lúc thật sự gửi — xem _token_text.
        out.append((key, fp, lambda u=u, han=han: _token_text(u, han)))


# =====================================================================
#  Điểm vào
# =====================================================================


def check_all(*, dry_run: bool = False) -> list[str]:
    """Chạy cả ba phép kiểm, gửi cái nào chưa gửi. Trả danh sách key đã báo.

    Gọi từ vòng `run` sau `process_queue()`. `dry_run=True` chỉ in ra cái sắp
    gửi và KHÔNG ghi mốc — để kiểm mà không đốt mất lần báo duy nhất.

    Phần tử thứ ba của mỗi mục là str HOẶC callable trả str: cảnh báo nào tốn
    tài nguyên để dựng nội dung (token -> sinh nonce + gọi Vercel rút gọn) thì
    hoãn tới lúc chắc chắn gửi.
    """
    pending: list[tuple[str, str, Any]] = []
    # `run` chết đứng ĐẦU: nếu nó chết thì mọi mục dưới đây đang nói về một hệ
    # thống không chạy, và đó là câu người đọc cần thấy trước tiên.
    _check_run_stale(pending)
    # Whisper + LLM: hai cái này in log mỗi vòng (Việc 2b) kể cả khi không ai
    # nhận DM, nên chúng phải chạy trước mấy mục chỉ-DM.
    _check_whisper(pending)
    _check_llm(pending)
    _check_failed_jobs(pending)
    _check_tokens(pending)

    if not pending:
        return []

    if not enabled():
        print(f"[alert] có {len(pending)} cảnh báo nhưng ALERT_UNION_IDS trống "
              f"-> không gửi cho ai: {', '.join(k for k, _, _ in pending)}")
        return []

    sent: list[str] = []
    for key, fp, body in pending:
        try:
            text = body() if callable(body) else body
        except Exception as exc:            # noqa: BLE001 — dựng nội dung hỏng
            # Không được làm chết cả lượt kiểm: những cảnh báo khác vẫn phải đi.
            print(f"[alert] không dựng được nội dung [{key}] (bỏ qua): {exc}")
            continue
        if dry_run:
            print(f"\n--- (dry-run) sẽ gửi [{key}] ---\n{text}\n")
            continue
        if _send(text):
            _mark_set(key, fp)
            sent.append(key)
            print(f"[alert] đã báo [{key}] cho "
                  f"{len(config.ALERT_UNION_IDS)} người")
    return sent
