"""
Luồng enroll qua OAuth: sinh nonce -> authorize URL -> nhận code -> đổi token.

state trong authorize URL PHẢI là nonce một lần dùng, KHÔNG phải open_id trần
(V2_ARCHITECTURE §4[0]): dùng open_id trần thì ai đoán được cũng gọi được
callback. Nonce buộc callback phải khớp đúng phiên enroll vừa mở.

Module này không quan tâm code tới bằng đường nào (callback local, hộp thư
Vercel, hay tunnel) — nơi nào nhận được code + state thì gọi complete().
"""

from __future__ import annotations

import secrets
import time

from . import cloudflare_relay, config, crypto, db, lark_api, tokenstore


def _now_ms() -> int:
    return int(time.time() * 1000)


def start(open_id: str = "") -> tuple[str, str]:
    """Mở một phiên enroll. Trả (authorize_url, nonce).

    open_id để trống được (enroll người lạ mới nhắn bot chưa biết open_id);
    khi đó chỉ ràng buộc bằng nonce.
    """
    nonce = secrets.token_urlsafe(24)
    # Cloudflare uses a stateless short link. Sign state only after the relay
    # URL is explicitly enabled; saving the Queue token alone changes nothing.
    if config.CF_RELAY_URL:
        nonce = cloudflare_relay.make_state(nonce, config.OAUTH_NONCE_TTL)
    with db.tx() as c:
        c.execute("DELETE FROM oauth_nonce WHERE expires_at < ?", (_now_ms(),))
        c.execute(
            "INSERT INTO oauth_nonce(nonce, open_id, expires_at) VALUES (?,?,?)",
            (nonce, open_id, _now_ms() + config.OAUTH_NONCE_TTL * 1000),
        )
    return lark_api.authorize_url(nonce), nonce


def start_or_reuse(open_id: str, min_ttl_s: int = 3600) -> tuple[str, str]:
    """Như `start()` nhưng DÙNG LẠI nonce còn sống của cùng người. (url, nonce).

    Vì sao cần (sửa 31/07/2026): `alerts._check_tokens` gọi `start()` mỗi vòng
    `run` để dựng link gia hạn. Gửi hỏng thì mốc chống spam không được ghi, nên
    vòng sau nó dựng lại — 5 phút một nonce mới, mỗi cái sống 24h. Vừa rác DB,
    vừa là hàng trăm link enroll còn hiệu lực nằm chờ.

    `min_ttl_s`: chỉ dùng lại nonce còn sống lâu hơn ngần này, để không gửi cho
    người ta một link sắp chết.
    """
    rows = db.conn().execute(
        "SELECT nonce FROM oauth_nonce WHERE open_id=? AND expires_at > ? "
        "ORDER BY expires_at DESC",
        (open_id, _now_ms() + min_ttl_s * 1000),
    ).fetchall() if open_id else []
    for row in rows:
        # During migration, old Vercel nonces can remain alive for their
        # already-sent links. Never reuse one in a new Cloudflare URL: the
        # Worker correctly rejects unsigned legacy state.
        if state_reusable(row["nonce"]):
            return lark_api.authorize_url(row["nonce"]), row["nonce"]
    return start(open_id)


def state_reusable(state: str) -> bool:
    """Whether an existing invite state matches the currently active relay."""
    if not config.CF_RELAY_URL:
        return True
    return cloudflare_relay.verify_state(state)


def complete(code: str, state: str) -> dict:
    """Hoàn tất enroll: kiểm nonce, đổi code lấy token, lưu. Trả user info.

    Ném RuntimeError nếu nonce sai/hết hạn (chống callback giả mạo).
    """
    row = db.conn().execute(
        "SELECT open_id, expires_at FROM oauth_nonce WHERE nonce=?", (state,)
    ).fetchone()
    if not row:
        raise RuntimeError("state không hợp lệ (nonce không tồn tại)")
    if _now_ms() > row["expires_at"]:
        with db.tx() as c:
            c.execute("DELETE FROM oauth_nonce WHERE nonce=?", (state,))
        raise RuntimeError("phiên enroll đã hết hạn, bấm lại link")

    # OAuth code is one-time. Checkpoint the encrypted token pair immediately
    # after exchange so a crash/user_info failure can resume without reusing
    # an already-consumed code. The checkpoint is local SQLite only.
    pending = db.conn().execute(
        "SELECT * FROM oauth_exchange_pending WHERE state=?", (state,)).fetchone()
    if pending:
        access = crypto.decrypt(pending["access_enc"])
        refresh = crypto.decrypt(pending["refresh_enc"])
        expires_in = int(pending["expires_in"])
        refresh_expires_in = int(pending["refresh_expires_in"])
        scopes = pending["scopes"] or ""
    else:
        out = lark_api.exchange_code(code)
        access = out["access_token"]
        refresh = out.get("refresh_token")
        if not refresh:
            raise RuntimeError(
                "Lark không trả refresh_token. Gần như chắc chắn thiếu scope "
                "'offline_access' trong OAUTH_SCOPES (v2/.env). Thêm vào, chạy lại "
                "`python -m v2 enroll-url`, bấm Đồng ý lần nữa rồi complete.")
        expires_in = int(out.get("expires_in", 7200))
        refresh_expires_in = int(out.get("refresh_token_expires_in", 30 * 86400))
        scopes = out.get("scope", "")
        with db.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO oauth_exchange_pending"
                "(state,access_enc,refresh_enc,expires_in,refresh_expires_in,scopes,exchanged_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (state, crypto.encrypt(access), crypto.encrypt(refresh), expires_in,
                 refresh_expires_in, scopes, _now_ms()),
            )
    info = tokenstore.enroll(
        access, refresh,
        expires_in,
        refresh_expires_in,
        scopes=scopes,
    )

    with db.tx() as c:
        c.execute("DELETE FROM oauth_nonce WHERE nonce=?", (state,))
        # Usually removed by the FK cascade. Keep this explicit for old SQLite
        # databases/connections where foreign_keys may have been toggled off.
        c.execute("DELETE FROM oauth_exchange_pending WHERE state=?", (state,))

    # Hai việc dọn/kiểm sau khi enroll, đặt ở ĐÂY chứ không ở caller (sửa
    # 02/08/2026). `complete()` là chỗ nghẽn DUY NHẤT mà cả ba đường enroll đều
    # đi qua: hộp thư Vercel (`poll_pending`), callback cục bộ
    # (`oauth_callback.wait_for_one` -> `v2 enroll`), và dán tay
    # (`v2 complete`). Trước đó chúng nằm trong `poll_pending`, nên **hai đường
    # kia lặng lẽ bỏ qua** — đúng bài học "bốn đường ra cùng một dữ liệu thì
    # phải sửa cả bốn" đã trả giá hai lần (`qa.context`, link rút gọn ở alerts).
    #
    # Nguy hiểm cụ thể: §22 lấy CHÍNH `_warn_if_missing_scopes` làm lý do dám
    # cắt `OAUTH_SCOPES` 124 -> 18 ("hỏng thì ồn và tức thì"). Mà V2_HANDOFF
    # §4.2 lại bảo người vận hành enroll bằng `python -m v2 complete` — đường
    # KHÔNG có lưới. Tức lưới được viện dẫn ở chỗ nó không tồn tại.
    _warn_if_missing_scopes(info)
    if info.get("union_id"):
        try:
            from . import gate
            gate.clear_invite(info["union_id"])
        except Exception as exc:          # noqa: BLE001 — dọn hỏng không được
            print(f"[enroll] không xoá được lời mời cũ: {exc}")   # làm hỏng enroll
    # Token mới có thể vừa bổ sung scope VC mà job cũ chưa từng có. Xác minh lại
    # trước khi đánh thức waiting_auth để người dự thật không bị kẹt vĩnh viễn ở
    # metadata near/no_match cũ. Hàm này chỉ ghi khi recording khớp minute VÀ chính
    # người vừa OAuth nằm trong attendee VC; không gửi tin và không enqueue.
    try:
        from . import orchestrator
        upgraded = orchestrator.reverify_after_enroll(info)
        if upgraded:
            print(f"[enroll] xác minh lại ACL cho {len(upgraded)} minute cũ")
    except Exception as exc:              # noqa: BLE001 - sửa ACL hỏng không phá enroll
        print(f"[enroll] không xác minh lại được minute cũ: {exc}")

    # Chỉ đánh thức job có quan hệ ID với CHÍNH người vừa tự OAuth. Không gửi
    # lời mời cho ai khác; việc gửi link vẫn chỉ do `gate` làm khi họ nhắn bot.
    try:
        from . import jobstore
        released = jobstore.release_waiting_auth(
            info.get("open_id", ""), info.get("union_id", ""))
        if released:
            print(f"[enroll] {len(released)} job chờ quyền -> backlog priority 0")
    except Exception as exc:              # noqa: BLE001 — cứu backlog hỏng không phá enroll
        print(f"[enroll] không đánh thức được job chờ quyền: {exc}")
    return info


# =====================================================================
#  Kéo code từ hộp thư Vercel (enroll tự phục vụ)
# =====================================================================
#
# Chiều gọi vẫn là LOCAL -> VERCEL, Vercel không bao giờ gọi vào máy này
# (V2_ARCHITECTURE §3). Vercel giữ {code,state} trong Blob với TTL ngắn; máy local
# là chỗ duy nhất có app_secret nên cũng là chỗ duy nhất đổi được code lấy token.
# Đọc xong thì Vercel xoá luôn (code của Lark dùng-một-lần, đọc hai lần vô nghĩa).


def short_link(url: str) -> str:
    """Đẩy `url` lên Vercel, trả link ngắn dạng `<gốc>/e/<code>`. "" nếu không được.

    Vì sao cần: URL authorize dài ~700 ký tự (18 scope, đo 03/08/2026). Dán vào
    chat Lark vẫn hay bị xuống dòng làm gãy link, và chuỗi dài thì người nhận
    ngại bấm. Đã gặp thật: Chi báo lỗi mà không rõ vì link gãy hay vì lý do khác.
    (Trước 01/08/2026 xin trọn họ scope thì URL tận ~3.800 ký tự; đã siết
    124 -> 18 scope, xem config.OAUTH_SCOPES §22.)

    Không dùng bit.ly/tinyurl: link enroll không nên đi qua bên thứ ba. Ở đây nó
    nằm trên chính hạ tầng dự án, cùng bearer với dashboard.
    """
    from urllib.parse import parse_qs, urlsplit

    # No KV/write is needed: the short URL carries the signed one-time state.
    if config.CF_RELAY_URL:
        state = (parse_qs(urlsplit(url).query).get("state") or [""])[0]
        return cloudflare_relay.short_link(state)

    import secrets
    import httpx

    if not (config.STATUS_PUSH_URL and config.STATUS_PUSH_SECRET):
        return ""
    root = config.STATUS_PUSH_URL.rsplit("/api/", 1)[0]
    code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")[:10]
    try:
        r = httpx.post(
            f"{root}/api/enroll-link",
            headers={"Authorization": f"Bearer {config.STATUS_PUSH_SECRET}"},
            json={"code": code, "url": url, "ttl_s": config.OAUTH_NONCE_TTL},
            timeout=config.STATUS_PUSH_TIMEOUT,
        )
        if r.status_code != 200:
            print(f"[enroll] rút gọn hỏng ({r.status_code}): {r.text[:120]}")
            return ""
    except Exception as exc:      # noqa: BLE001 — rút gọn hỏng thì dùng link dài
        print(f"[enroll] rút gọn hỏng: {exc}")
        return ""
    return f"{root}/e/{code}"


def _warn_if_missing_scopes(info: dict) -> None:
    """Ngay sau khi ai đó enroll, kiểm token của họ có ĐỦ quyền V2 cần không.

    Vì sao đúng chỗ này (bài học 31/07/2026): thiếu scope là lỗi **im lặng và
    trễ**. Người thứ hai enroll thành công, mọi thứ trông ổn, rồi nhiều ngày
    sau mới có người hỏi "sao không thấy biên bản của chị ấy". Kiểm ngay lúc
    enroll biến nó thành lỗi ồn và tức thì — mất một lượt gọi API, đổi lại
    không bao giờ phải chẩn ngược nữa.

    Không được ném ra ngoài: enroll đã THÀNH CÔNG rồi, một phép kiểm hỏng
    không được phép làm hỏng việc đó.
    """
    try:
        from . import alerts, scopecheck
        u = {"open_id": info.get("open_id", ""), "name": info.get("name", "")}
        if not u["open_id"]:
            return
        missing = scopecheck.check_user(u, verbose=False)
        if not missing:
            print("[enroll] quyền: đủ cho mọi đường V2 cần")
            return

        crit = scopecheck.critical(missing)
        muc = "CHẶN ĐƯỜNG CHÍNH" if crit else "chỉ ảnh hưởng ô phụ"
        print(f"[enroll] ⚠ {u['name']} THIẾU QUYỀN ({muc}):")
        for m in missing:
            print(f"          - {m}")
        print("          Sửa: thêm scope vào OAUTH_SCOPES (v2/.env) rồi bảo "
              "họ bấm lại link enroll.")

        if crit and alerts.enabled():
            alerts._send(
                f"[V2] {u['name']} vừa enroll nhưng THIẾU QUYỀN\n"
                f"V2 sẽ KHÔNG phát hiện được cuộc họp của người này.\n\n"
                + "\n".join(f"• {m}" for m in missing)
                + "\n\nSửa: thêm scope vào OAUTH_SCOPES trong v2/.env, "
                  "rồi gửi lại link enroll (`python -m v2 enroll-url`).\n"
                  "Kiểm lại: `python -m v2 scopes`")
    except Exception as exc:              # noqa: BLE001 — xem docstring
        print(f"[enroll] không kiểm được quyền (bỏ qua): {exc}")


def has_live_nonce() -> bool:
    """Có ai đang giữa chừng cấp quyền không (nonce chưa hết hạn)."""
    return db.conn().execute(
        "SELECT 1 FROM oauth_nonce WHERE expires_at > ? LIMIT 1",
        (_now_ms(),)).fetchone() is not None


def _announce_completed(info: dict, notify: bool) -> None:
    """Run the user-facing tail shared by both remote callback transports."""
    name = info.get("name") or info.get("open_id")
    union_id = info.get("union_id", "")
    print(f"[enroll] ✓ {name} đã cấp quyền")
    if union_id and notify:
        try:
            from . import orchestrator
            orchestrator.welcome_and_backlog(info)
        except Exception as exc:          # noqa: BLE001 - token was already stored
            print(f"[enroll] chào/backlog hỏng cho {name}: {exc}")


def _terminal_cloudflare_error(exc: Exception, state: str, attempts: int) -> bool:
    """Return True when retrying the same one-time code cannot help."""
    # Missing/expired nonce is authoritative and also handles a duplicate after
    # a prior success whose ACK response was lost.
    row = db.conn().execute(
        "SELECT expires_at FROM oauth_nonce WHERE nonce=?", (state,)).fetchone()
    if not row or _now_ms() > int(row["expires_at"]):
        return True
    detail = str(exc).lower()
    if "refresh_token" in detail or "offline_access" in detail:
        return True  # code was already exchanged and cannot be used again
    return attempts >= config.CF_QUEUE_MAX_ATTEMPTS


def _poll_cloudflare(*, notify: bool, force: bool) -> list[dict]:
    """Pull Queue messages and ACK only after terminal/success processing."""
    if not force and not has_live_nonce():
        return []
    try:
        messages = cloudflare_relay.pull_messages()
    except Exception as exc:              # noqa: BLE001 - never kill the run loop
        print(f"[enroll] không đọc được Cloudflare Queue: {exc}")
        return []

    done: list[dict] = []
    ack: list[str] = []
    retry: list[str] = []
    for message in messages:
        item = message.body
        if item.get("fail"):
            fail = item["fail"] if isinstance(item["fail"], dict) else {}
            print("[enroll] ⚠ CÓ NGƯỜI BẤM LINK MÀ LARK TỪ CHỐI: "
                  f"{fail.get('error')} — {fail.get('error_description') or ''}")
            ack.append(message.lease_id)
            continue

        code, state = item.get("code"), item.get("state")
        if item.get("version") != 1 or not isinstance(code, str) or not isinstance(state, str):
            print("[enroll] Cloudflare Queue có message sai định dạng — đã loại")
            ack.append(message.lease_id)
            continue

        try:
            info = complete(code, state)
        except Exception as exc:          # noqa: BLE001 - classify ACK vs retry
            if _terminal_cloudflare_error(exc, state, message.attempts):
                print(f"[enroll] state={state[:12]}… lỗi vĩnh viễn — đã loại: {exc}")
                ack.append(message.lease_id)
            else:
                print(f"[enroll] state={state[:12]}… lỗi tạm thời — Queue sẽ thử lại: {exc}")
                retry.append(message.lease_id)
            continue

        _announce_completed(info, notify)
        done.append(info)
        ack.append(message.lease_id)

    try:
        cloudflare_relay.settle_messages(ack=ack, retry=retry)
    except Exception as exc:              # noqa: BLE001 - Queue is at-least-once
        # If ACK is unconfirmed, a duplicate can return. complete() consumed
        # the nonce, so the duplicate is terminal and cannot enroll/send twice.
        print(f"[enroll] không xác nhận được Cloudflare Queue; message có thể giao lại: {exc}")
    return done


def poll_pending(*, notify: bool = True, force: bool = False) -> list[dict]:
    """Lấy code ở Cloudflare và vét link Vercel cũ rồi enroll.

    Lỗi mạng KHÔNG ném ra ngoài: hàm này chạy trong vòng `run`, một cú Vercel
    502 không được phép làm chết orchestrator.

    KHÔNG gọi khi không có nonce nào còn sống (02/08/2026), trừ khi `force`.
    Vì sao: mỗi lời gọi là một `list()` trên Vercel Blob, tính vào hạn mức
    **Advanced Requests — 2.000 thao tác/tháng ở gói free**. Vòng `run` gọi mỗi
    5 phút bất kể có ai đang enroll hay không: đo từ log là ~100 lần/ngày, tức
    riêng hộp thư đã ăn 1/20 hạn mức THÁNG mỗi ngày. Vercel đã gửi thư báo 75%.
    Cạn hạn mức = hộp thư ngừng chạy = **không ai enroll được nữa**, và đó là
    thứ hỏng đúng lúc cần nhất.
    Không mất gì: `complete()` từ chối state không có nonce hoặc nonce hết hạn,
    nên code nằm trong hộp thư lúc không có nonce sống thì dù đọc về cũng không
    dùng được.
    `force=True` cho lệnh tay `v2 enroll-poll` — người ta gõ nó chính là để
    kiểm tra hộp thư.
    """
    import httpx

    done: list[dict] = []
    if cloudflare_relay.queue_enabled():
        # Keep polling Vercel below as a migration drain for links that were
        # already sent before cutover. New states go only through Cloudflare.
        done.extend(_poll_cloudflare(notify=notify, force=force))

    if not (config.OAUTH_PULL_URL and config.STATUS_PUSH_SECRET):
        return done
    if not force and not has_live_nonce():
        return done

    try:
        resp = httpx.get(
            config.OAUTH_PULL_URL,
            headers={"Authorization": f"Bearer {config.STATUS_PUSH_SECRET}"},
            timeout=config.STATUS_PUSH_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"[enroll] hộp thư trả {resp.status_code}: {resp.text[:120]}")
            return done
        items = (resp.json() or {}).get("pending") or []
    except Exception as exc:      # noqa: BLE001 — mạng hỏng không được làm chết vòng run
        print(f"[enroll] không đọc được hộp thư: {exc}")
        return done

    for it in items:
        # Lark từ chối ở bước Đồng ý: không có code, chỉ có lý do. Nói TO —
        # đây là bằng chứng duy nhất rằng có người đã bấm mà hỏng. Trước
        # 31/07/2026 nhánh này không tồn tại và ta chỉ thấy "hộp thư trống",
        # không phân biệt được với "chưa ai bấm".
        if it.get("fail"):
            f = it["fail"]
            print(f"[enroll] ⚠ CÓ NGƯỜI BẤM LINK MÀ LARK TỪ CHỐI: "
                  f"{f.get('error')} — {f.get('error_description') or ''}")
            continue
        code, state = it.get("code"), it.get("state")
        if not (code and state):
            continue
        try:
            info = complete(code, state)
        except Exception as exc:      # noqa: BLE001
            # Nonce hết hạn / code đã dùng: bỏ qua, không chặn người kế tiếp.
            print(f"[enroll] state={state[:8]}… bỏ qua: {exc}")
            continue

        _announce_completed(info, notify)
        done.append(info)
    return done
