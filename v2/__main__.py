"""
CLI orchestrator V2.

    python -m v2 status                 # auth + hàng đợi
    python -m v2 doctor                 # khám sức khỏe toàn hệ thống
    python -m v2 enroll [--port 8080]   # OAuth qua callback LOCAL (localhost)
    python -m v2 enroll-url             # chỉ in link OAuth (dùng với Vercel)
    python -m v2 complete --code X --state Y   # hoàn tất thủ công (Vercel/xa)
    python -m v2 cloudflare-config --account-id X --queue-id Y  # nhập Queue token ẩn
    python -m v2 scan                   # quét 1 lần
    python -m v2 enqueue --token T [--reader ou_x]  # nạp 1 minute tay (test)
    python -m v2 backfill --days 180 [--yes]   # nạp bù cuộc họp cũ hơn LOOKBACK_DAYS
    python -m v2 process [--send]       # xử lý hàng đợi 1 lần
    python -m v2 run [--ws] [--send]    # vòng lặp chính
    python -m v2 alerts [--dry-run]     # kiểm + gửi cảnh báo DM một lượt
    python -m v2 users                  # ai đã enroll (kể cả đã thu hồi)
    python -m v2 revoke --open-id ou_X --yes   # thu hồi quyền một người
    python -m v2 unauth --union-id on_X --yes  # xoá quyền để cấp lại từ đầu (tự test)
    python -m v2 push-status [--print]  # đẩy snapshot lên dashboard Vercel
    python -m v2 base-init              # tạo Base "nội dung đã chốt" (1 lần)
    python -m v2 base-sync              # đổ lại ô theo dõi + cột Trạng thái
    python -m v2 ask "tuần này chốt gì?"        # hỏi đáp ở terminal
    python -m v2 transcript <token|tên> [--part N]   # nguyên văn whisper
    python -m v2 mcp                    # MCP server dữ liệu họp (Hermes gọi)
    python -m v2 genkey                 # sinh Fernet key

Cấu hình trong v2/.env (xem .env.example).
"""

from __future__ import annotations

import argparse
import sys
import time

from . import config, db, jobstore, oauth, orchestrator, tokenstore


def _init() -> None:
    config.ensure_dirs()
    db.init()


def cmd_genkey(_) -> None:
    from cryptography.fernet import Fernet
    print(Fernet.generate_key().decode())
    print("# -> đặt vào V2_FERNET_KEY trong v2/.env", file=sys.stderr)


def cmd_cloudflare_config(args) -> None:
    """Verify and save the scoped Queue token without echoing it to screen."""
    from . import cloudflare_relay
    try:
        token = None
        if args.token_stdin:
            token = sys.stdin.readline().strip()
        elif args.token_dialog:
            import tkinter as tk
            from tkinter import simpledialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                token = simpledialog.askstring(
                    "Cloudflare Queue token",
                    "Dán token vào đây rồi bấm OK (ký tự được che):",
                    show="*", parent=root) or ""
            finally:
                root.destroy()
        cloudflare_relay.configure_interactive(
            args.account_id, args.queue_id, token=token)
    except RuntimeError as exc:
        print(f"Không lưu cấu hình: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def cmd_status(_) -> None:
    _init()
    print(config.summary())
    print("\nĐã enroll:")
    print(tokenstore.auth_report())
    print("\nHàng đợi:")
    counts: dict[str, int] = {}
    # Danh sách nằm ở `jobstore.ALL_STATUSES`, KHÔNG gõ lại ở đây: bản cũ gõ tay
    # và quên `held`, nên bảng này cộng ra 61 trong khi DB có 72 job.
    for st in jobstore.ALL_STATUSES:
        rows = jobstore.by_status(st)
        if rows:
            counts[st] = len(rows)
    if not counts:
        print("  (trống)")
    for st, n in counts.items():
        print(f"  {st:<18} {n}")


def cmd_enroll(args) -> None:
    _init()
    if not config.OAUTH_REDIRECT_URI:
        print("Chưa đặt OAUTH_REDIRECT_URI trong .env "
              "(vd http://localhost:8080/oauth/callback).", file=sys.stderr)
        sys.exit(1)
    url, nonce = oauth.start()
    print("Gửi link này cho người cần enroll (hoặc tự mở):\n")
    print(url)
    print(f"\nĐang chờ callback tại cổng {args.port} ... (Ctrl+C để hủy)")
    from . import oauth_callback
    res = oauth_callback.wait_for_one(args.port, timeout=args.timeout)
    if res.get("ok"):
        info = res["info"]
        print(f"\n✓ Đã enroll: {info.get('name')} ({info.get('open_id')})")
    else:
        print(f"\n✗ Thất bại: {res.get('error')}", file=sys.stderr)
        sys.exit(1)


def cmd_enroll_url(args) -> None:
    """In link OAuth rồi thoát. Dùng khi redirect về Vercel (không mở server
    local). Người dùng bấm link -> Vercel giữ code -> vòng `run` tự kéo về
    (enroll-poll), hoặc admin chạy `complete` bằng tay."""
    _init()
    if not config.OAUTH_REDIRECT_URI:
        print("Chưa đặt OAUTH_REDIRECT_URI (URL Vercel).", file=sys.stderr)
        sys.exit(1)
    url, nonce = oauth.start()
    # Đọc TTL thật từ config, đừng dán cứng: từ 31/07/2026 nonce sống 24h
    # (§14) chứ không phải 10 phút như bản đầu — in sai làm người ta tưởng
    # link đã chết và đi xin link mới.
    hours = config.OAUTH_NONCE_TTL / 3600
    han = f"~{hours:.0f} giờ" if hours >= 1 else f"~{config.OAUTH_NONCE_TTL // 60} phút"

    short = oauth.short_link(url) if not args.long else ""
    print(f"Gửi link này cho người cần enroll (hết hạn sau {han}):\n")
    print(short or url)
    if short:
        print(f"\n(link đầy đủ {len(url)} ký tự, đã rút gọn qua chính Vercel của "
              f"dự án — không qua bên thứ ba. Dùng --long để lấy bản đầy đủ.)")
    print(f"\nstate (nonce) = {nonce}")
    print("Họ bấm Đồng ý là xong: vòng `run` tự kéo code về mỗi vòng quét.")
    print("Muốn ngay: python -m v2 enroll-poll")
    print("Hộp thư Vercel tắt thì dán tay:")
    print(f"  python -m v2 complete --code <CODE> --state {nonce}")


def cmd_complete(args) -> None:
    _init()
    info = oauth.complete(args.code, args.state)
    print(f"✓ Đã enroll: {info.get('name')} ({info.get('open_id')})")


def cmd_doctor(_) -> None:
    from . import doctor
    sys.exit(doctor.run())


def cmd_profile(_) -> None:
    """In đúng hồ sơ mà bot đang dùng để tự giới thiệu.

    Không cần `_init()`: hồ sơ là hằng số trong code, không đọc DB. Có lệnh này
    để người vận hành đối chiếu được "bot nói gì về mình" mà không phải đi hỏi
    bot rồi đoán xem câu trả lời có phải LLM tự bịa không.
    """
    from . import profile
    print(profile.text())


def cmd_gate(args) -> None:
    """In MỘT dòng JSON cho plugin Hermes đọc. Log đi stderr, không đi stdout.

    Hợp đồng với plugin: stdout chỉ có JSON. `_init()` và các hàm khác in log
    bằng print() nên phải chuyển hướng stdout khi chúng chạy — cùng bẫy với
    mcp_server (docs §12).
    """
    import contextlib
    import json as _json
    from . import gate

    with contextlib.redirect_stdout(sys.stderr):
        _init()
        try:
            out = gate.check(args.union_id, args.user_id, args.name,
                             send=not args.no_send,
                             chat_type=args.chat_type)
        except Exception as exc:                      # noqa: BLE001
            # Cửa đóng khi hỏng: thà bot im còn hơn mở cho người chưa cấp quyền.
            out = {"decision": "wait", "reason": f"gate lỗi: {exc}"}
    print(_json.dumps(out, ensure_ascii=False))


def cmd_base_sync(args) -> None:
    _init()
    from . import bitable
    # TẠO record thiếu trước khi đổ lại ô: `sync_tracking` chỉ sửa record đã có,
    # nên job phát xong mà ghi Base hỏng thì nó bỏ qua vĩnh viễn (xem
    # bitable.retry_missing_records). Đây là đường vá bằng tay của người vận hành.
    renamed = bitable.migrate_fields()
    if renamed:
        print("Đã đổi tên cột: " + "; ".join(renamed))
    created = bitable.retry_missing_records()
    if created:
        print(f"Đã tạo {created} record còn thiếu (job chưa có trên Base).")
    synced = bitable.sync_jobs()
    if synced:
        print(f"Đã đổ lại trạng thái cho {synced} record.")
    n = bitable.sync_tracking(args.token)
    print(f"Xong: {n} record đã cập nhật ô theo dõi.")


def cmd_enroll_poll(_) -> None:
    _init()
    from . import oauth
    # force: người gõ lệnh này chính là để kiểm hộp thư, đừng bỏ qua vì
    # không có nonce sống (xem docstring poll_pending).
    done = oauth.poll_pending(force=True)
    if not done:
        print("Hộp thư trống (hoặc chưa bật OAUTH_PULL_URL).")


def cmd_invites(_) -> None:
    _init()
    from . import gate
    rows = gate.pending_invites()
    if not rows:
        print("Không có ai đang chờ cấp quyền.")
        return
    print(f"{len(rows)} người đã được mời mà chưa cấp quyền:")
    for r in rows:
        when = time.strftime("%d/%m %H:%M",
                             time.localtime((r["sent_at"] or 0) / 1000))
        print(f"  {r['name'] or '(chưa rõ tên)':40s} {r['union_id']}  "
              f"mời {r['times']} lần, lần cuối {when}")


def cmd_scopes(args) -> None:
    """Quét toàn bộ bề mặt user-token, cho từng người đã enroll.

    Thay cho cách cũ "chờ nó cắn rồi vá": scope thiếu là lỗi im lặng và trễ,
    chỉ những đường ĐÃ chạy mới lộ ra. Xem docstring v2/scopecheck.py.
    """
    _init()
    from . import scopecheck
    if args.print_all or args.everything:
        sys.exit(scopecheck.print_all_scopes(everything=args.everything))
    sys.exit(scopecheck.run(args.open_id))


def cmd_users(_) -> None:
    """Bảng người đã enroll. `active_only=False` để thấy CẢ người đã revoke —
    "không thấy tên" và "thấy tên đã khoá" là hai chuyện khác nhau."""
    _init()
    users = tokenstore.list_users(active_only=False)
    if not users:
        print("Chưa có ai enroll. Chạy: python -m v2 enroll-url")
        return
    now = int(time.time() * 1000)
    print(f"{'Tên':<34} {'open_id':<36} {'union_id':<36} "
          f"{'status':<8} {'refresh':>9}  lần dùng cuối")
    print("-" * 145)
    for u in sorted(users, key=lambda x: (x.get("status") != "active",
                                          x.get("name") or "")):
        days = (u["refresh_exp"] - now) / 86_400_000
        refresh = "HẾT HẠN" if days <= 0 else f"{days:.1f} ngày"
        last = u.get("last_used") or 0
        when = (time.strftime("%d/%m %H:%M", time.localtime(last / 1000))
                if last else "(chưa)")
        print(f"{(u.get('name') or '(chưa rõ tên)')[:33]:<34} "
              f"{u.get('open_id') or '':<36} {u.get('union_id') or '':<36} "
              f"{u.get('status') or '?':<8} {refresh:>9}  {when}")


def cmd_revoke(args) -> None:
    """Thu hồi quyền của một người.

    Bắt buộc `--yes`: đây là hành động làm V2 MẤT quyền đọc minutes của người
    đó, và nếu đó là người cuối cùng thì hệ thống không còn đọc được gì. Tra
    được bằng cả open_id lẫn union_id vì log của `gate` in union_id — đó mới là
    thứ hay có trong tay khi cần khoá gấp.
    """
    _init()
    users = tokenstore.list_users(active_only=False)
    if args.union_id:
        who = next((u for u in users if u.get("union_id") == args.union_id), None)
        label = args.union_id
    else:
        who = next((u for u in users if u.get("open_id") == args.open_id), None)
        label = args.open_id
    if not who:
        print(f"Không thấy ai có {label} trong bảng tokens. "
              f"Xem danh sách: python -m v2 users", file=sys.stderr)
        sys.exit(1)

    name = who.get("name") or who["open_id"]
    if who.get("status") == "revoked":
        print(f"{name} đã ở trạng thái revoked từ trước — không làm gì.")
        return

    still_active = [u for u in users
                    if u.get("status") == "active" and u["open_id"] != who["open_id"]]
    if not args.yes:
        print(f"Sẽ thu hồi: {name} ({who['open_id']})")
        if not still_active:
            print("  [!] Đây là người ACTIVE CUỐI CÙNG — thu hồi xong V2 không "
                  "còn đọc được minutes của ai.")
        print("Thêm --yes để làm thật.", file=sys.stderr)
        sys.exit(1)

    tokenstore.revoke(who["open_id"])
    print(f"✓ Đã thu hồi: {name} ({who['open_id']})")
    if not still_active:
        print("[!] Không còn ai active. Enroll lại: python -m v2 enroll-url")
    else:
        print(f"Còn {len(still_active)} người active.")


def cmd_unauth(args) -> None:
    """XOÁ HẲN quyền của một người để họ cấp lại từ đầu — dành cho việc TỰ TEST.

    Khác `revoke` ở chỗ dùng vào việc gì:
      * `revoke` = khoá gấp một người, giữ lại dòng `tokens` với status
        `revoked` làm dấu vết ai từng có quyền;
      * `unauth` = xoá sạch dấu vết auth của CHÍNH MÌNH để diễn lại luồng người
        mới. Giữ `revoked` thì cửa vào tra ra "người này đã bị thu hồi" và đi
        nhánh khác — không diễn lại được cảnh người lạ nhắn bot lần đầu.

    XOÁ: dòng `tokens` + lời mời đang treo (`enroll_invites`). Lời mời phải xoá
    theo, nếu không `gate.check` thấy nonce còn hạn và chỉ nhắc "bấm link cũ đi"
    thay vì gửi link mới.

    GIỮ NGUYÊN: jobs, transcript, người dự, record Base, ứng viên glossary. Cấp
    quyền lại là dùng được ngay toàn bộ dữ liệu cũ — `tokens` chỉ là chìa khoá
    đọc Lark, không phải nơi chứa dữ liệu họp.
    """
    _init()
    from . import db
    users = tokenstore.list_users(active_only=False)
    key = args.union_id or args.open_id
    who = next((u for u in users
                if u.get("union_id") == key or u.get("open_id") == key), None)
    if not who:
        print(f"Không thấy ai có {key} trong bảng tokens. "
              f"Xem: python -m v2 users", file=sys.stderr)
        sys.exit(1)
    name = who.get("name") or who["open_id"]
    if not args.yes:
        print(f"Sẽ XOÁ quyền của: {name} ({who['open_id']})")
        print("  Giữ nguyên: biên bản, transcript, người dự, record Base.")
        print("  Người đó nhắn bot lần sau sẽ nhận link cấp quyền mới.")
        print("Thêm --yes để làm thật.", file=sys.stderr)
        sys.exit(1)
    with db.tx() as c:
        n_tok = c.execute("DELETE FROM tokens WHERE open_id=?",
                          (who["open_id"],)).rowcount
        n_inv = c.execute("DELETE FROM enroll_invites WHERE union_id=?",
                          (who.get("union_id") or "",)).rowcount
        n_ses = c.execute("DELETE FROM qa_sessions WHERE open_id=?",
                          (who["open_id"],)).rowcount
    print(f"✓ Đã xoá quyền: {name}")
    print(f"  tokens {n_tok} dòng · lời mời {n_inv} · vé phiên {n_ses}")
    left = [u for u in tokenstore.list_users(active_only=True)]
    print(f"  còn {len(left)} người active.")
    print("Giờ nhắn bot bằng chính tài khoản đó — nó sẽ gửi link cấp quyền mới.")


def cmd_alerts(args) -> None:
    """Chạy một lượt kiểm cảnh báo ngay, không phải chờ vòng `run`.

    `--dry-run` in ra cái sắp gửi mà KHÔNG gửi và KHÔNG ghi mốc chống spam —
    dùng để kiểm mà không đốt mất lần báo duy nhất của một tình huống.
    """
    _init()
    from . import alerts
    if not alerts.enabled():
        print("ALERT_UNION_IDS trống -> cảnh báo đang TẮT. "
              "Đặt union_id (phẩy ngăn cách) trong v2/.env.", file=sys.stderr)
    sent = alerts.check_all(dry_run=args.dry_run)
    if not sent and not args.dry_run:
        print("Không có gì để báo (hoặc đã báo rồi — xem bảng alert_state).")


def cmd_glossary_digest(args) -> None:
    """Gửi digest tuần: thuật ngữ chờ duyệt -> DM admin (Task Scheduler gọi tuần).

    In ra danh sách dù không gửi được (chưa cấu hình người nhận) để chạy tay vẫn
    thấy có gì đang chờ.

    `--dry-run` như `alerts --dry-run`, và cùng lý do: người ta chạy lệnh này
    bằng tay để XEM đang có gì chờ. Không có cửa đó thì mỗi lần xem là một cái DM
    thật vào chat admin — kiểu phiền khiến người ta thôi không kiểm nữa.
    """
    _init()
    from . import config, glossary
    if not config.GLOSSARY_ENABLED:
        print("V2_GLOSSARY_ENABLED=0 -> glossary đang TẮT.", file=sys.stderr)
        return
    body = glossary.digest_body(config.GLOSSARY_MIN_COUNT)
    if not body:
        print(f"Không có thuật ngữ nào chờ duyệt (gặp >= "
              f"{config.GLOSSARY_MIN_COUNT} cuộc).")
        return
    print(body)
    print("-" * 40)
    if args.dry_run:
        print(f"[glossary] (dry-run) KHÔNG gửi. Chạy thật sẽ DM "
              f"{len(config.ALERT_UNION_IDS)} người trong ALERT_UNION_IDS.")
        return
    n = glossary.send_digest(config.GLOSSARY_MIN_COUNT, config.ALERT_UNION_IDS)
    print(f"[glossary] đã DM {n} admin" if n else
          "[glossary] KHÔNG gửi: ALERT_UNION_IDS trống — đặt trong v2/.env.")


def cmd_scan(_) -> None:
    _init()
    orchestrator.scan_once()


def cmd_enqueue(args) -> None:
    """Nạp một minute_token cụ thể vào hàng đợi — bỏ qua minutes_list.

    Dùng để kiểm chứng pipeline (tải->phiên âm->recap->phát) độc lập với
    endpoint list còn phải [VERIFY]. reader là người đã enroll để mượn token
    đọc; để trống thì lấy người enroll đầu tiên.
    """
    _init()
    reader = args.reader
    if not reader:
        users = tokenstore.list_users(active_only=True)
        if not users:
            print("Chưa có ai enroll. Chạy: python -m v2 enroll", file=sys.stderr)
            sys.exit(1)
        reader = users[0]["open_id"]
        print(f"reader = {users[0]['name'] or reader}")
    ok = orchestrator.enqueue_minute(reader, args.token)
    print("Đã nạp job." if ok else "Không nạp (đã có/đã khóa hoặc lỗi).")


def cmd_coverage(args) -> None:
    """Chỉ đọc: không tạo job, không đổi status, không nhắn ai."""
    _init()
    from . import coverage
    sys.exit(coverage.report(args.days))


def cmd_backfill(args) -> None:
    """Nạp bù cuộc họp cũ hơn cửa sổ quét thường. Mặc định THỬ KHÔ.

    Đòi `--yes` để ghi thật, cùng lý lẽ với `revoke`/`unauth`: nó tạo hàng chục
    job và từng ấy record trên Base, rồi whisper sẽ phiên âm dần ở nền — không
    phải thứ nên chạy vì gõ nhầm. Xem `orchestrator.backfill_missing` để biết vì
    sao KHÔNG chữa bằng cách nới `LOOKBACK_DAYS`.
    """
    _init()
    rows = orchestrator.backfill_missing(args.days, dry_run=not args.yes,
                                         priority=args.priority)
    if not args.yes:
        print(f"\nThử khô: {len(rows)} cuộc SẼ được nạp. Thêm --yes để làm thật.")
        print("Chúng vào backlog (priority 0), KHÔNG gửi tin cho ai; vòng `run` "
              "phiên âm dần, mỗi vòng một cuộc.")


def cmd_org_sync(args) -> None:
    """V3 YC2: kéo cây quản lý từ Lark Contact. Mặc định THỬ KHÔ (chỉ đọc Lark).

    Ghi thật = đổi quyền xem: quản lý các cấp thấy biên bản của nhánh mình."""
    _init()
    from . import org
    got = org.sync(dry_run=not args.yes)
    print(f"{got['users']} người trong cây tổ chức, {got['changed']} dòng đổi quản lý.")
    if not args.yes:
        print("Thử khô — chưa ghi. Thêm --yes để lưu (sau đó đặt ORG_SYNC_HOURS=24 "
              "để vòng run tự đồng bộ).")


def cmd_drive_provision(args) -> None:
    """V3 YC2: tạo folder Drive cho mọi người chưa có. Mặc định THỬ KHÔ."""
    _init()
    from . import notes
    names = notes.provision(dry_run=not args.yes)
    print(f"{len(names)} người {'đã' if args.yes else 'SẼ'} được tạo folder "
          f"(share im lặng, không thông báo):")
    for n in names:
        print("  -", n)
    if not args.yes:
        print("Thử khô. Chạy `v2 org-sync --yes` trước, rồi thêm --yes ở đây.")


def cmd_grants_backfill(args) -> None:
    """V3 YC2: cấp LÙI quyền cuộc cũ cho một quản lý (vd sếp mới). THỬ KHÔ mặc định."""
    _init()
    from . import notes
    import datetime as _dt
    since = 0
    if args.since:
        since = int(_dt.datetime.strptime(args.since, "%Y-%m-%d").timestamp() * 1000)
    hits = notes.backfill_grants(args.open_id, since_ms=since, dry_run=not args.yes)
    print(f"{len(hits)} cuộc họp {'đã' if args.yes else 'SẼ'} được cấp quyền cho "
          f"{args.open_id}.")
    if not args.yes:
        print("Thử khô. Thêm --yes để cấp thật (không thu hồi được bằng lệnh này).")


def cmd_notes(args) -> None:
    """V3 YC2: ghi biên bản .md + chụp quyền cho một cuộc, hoặc MỌI cuộc đã có
    nội dung (held/delivered, không đang chờ duyệt). Chạy lại sau khi bật
    `org-sync` để quản lý có quyền các cuộc CŨ. Bật DRIVE_ENABLED thì cũng đẩy
    Drive (share im lặng) — nên chạy `drive-provision` trước."""
    _init()
    from . import confirm, jobstore, notes
    if args.token:
        toks = [args.token]
    else:
        toks = [j["minute_token"] for j in jobstore.all_jobs()
                if j.get("status") in ("held", "delivered")
                and (confirm.get(j["minute_token"]) or {}).get("state") != "pending"]
    for t in toks:
        print(t, "->", notes.publish(t))
    print(f"{len(toks)} cuộc họp.")


def cmd_search(args) -> None:
    """V3 YC3: tìm ngữ nghĩa ở terminal — cũng là cách so model (`--model`)."""
    _init()
    from . import askers, semantic
    who = askers.admin_view()
    if args.as_who:
        who = askers.find_enrolled(args.as_who)
        if not who:
            sys.exit(f"không thấy người đã enroll: {args.as_who}")
    if args.reindex:
        n = semantic.backfill(limit=10_000) if not args.model else sum(
            semantic.index(t, args.model) for t in semantic.stale(10_000))
        print(f"đã index {n} {'cuộc' if not args.model else 'đoạn'}")
    print(semantic.search(who, " ".join(args.query), model=args.model))


def cmd_dashboard(_) -> None:
    """V3 YC4: web dashboard trên 127.0.0.1:DASHBOARD_PORT (Cloudflare Tunnel trỏ vào)."""
    _init()
    from . import dashboard
    dashboard.serve()


def cmd_process(args) -> None:
    _init()
    orchestrator.process_queue(dry_run=not args.send)


def cmd_run(args) -> None:
    # Khóa TRƯỚC `_init()`: process thua không được mở DB, chạy migration hay
    # khởi tạo bất kỳ side effect nào. Batch vẫn quét process để báo sớm, nhưng
    # mutex này mới là hàng rào nguyên tử chống hai lần phát.
    from . import singleton
    try:
        run_lock = singleton.acquire(config.DB_PATH)
    except singleton.AlreadyRunning as exc:
        print(f"[x] V2 run đã có tiến trình khác: {exc}", file=sys.stderr)
        raise SystemExit(singleton.EXIT_ALREADY_RUNNING)

    with run_lock:
        _init()
        if args.send:
            config.SEND_MODE = True
        if args.ws:
            # Cấu hình hiện tại: Hermes dùng CÙNG app_id (docs §12). Lark không từ
            # chối kết nối thứ hai nên không có lỗi nào báo — chỉ là Hermes lặng lẽ
            # mất tin nhắn. Nói to ở đây vì đó là lỗi im lặng duy nhất còn lại.
            print("!" * 60)
            print("[!] --ws: nếu Hermes đang chạy trên cùng app_id, TẮT cờ này.")
            print("    Lark nhận cả 2 kết nối, không báo lỗi, nhưng Hermes có thể")
            print("    mất tin nhắn. V2 không cần --ws (polling là nguồn sự thật).")
            print("!" * 60)
            from . import ws_listener
            ws_listener.start_in_thread()
        orchestrator.run()


def cmd_push_status(args) -> None:
    """Gom trạng thái ẩn danh rồi POST lên dashboard Vercel.

    --print để xem đúng cái sắp gửi (kiểm tra không lọt open_id/token trước
    khi mở dashboard cho người khác xem).
    """
    _init()
    from . import status_push
    snap = status_push.build_snapshot()
    if args.print_only:
        import json
        print(json.dumps(snap, ensure_ascii=False, indent=2))
        return
    sys.exit(0 if status_push.push(snap) else 1)


def cmd_base_init(_) -> None:
    """In các lệnh tạo Base "nội dung đã chốt" (chạy một lần), rồi tự kiểm."""
    _init()
    from . import bitable
    if bitable.enabled():
        ok, detail = bitable.check()
        print(f"Đã cấu hình Base: {detail}")
        print(f"  BITABLE_APP_TOKEN={config.BITABLE_APP_TOKEN}")
        print(f"  BITABLE_TABLE_ID={config.BITABLE_TABLE_ID}")
        if not ok:
            print("\n[x] app V2 KHÔNG đọc được table này. Kiểm lại bước 2 "
                  "(thêm app làm collaborator):\n")
            print(bitable.setup_commands())
        sys.exit(0 if ok else 1)
    print(bitable.setup_commands())


def cmd_selftest(_) -> None:
    """Tự kiểm hành vi code. KHÔNG gọi `_init()`: xem docstring `selftest.run`
    — nó chạy lại chính nó trong tiến trình con với `V2_DB_PATH` trỏ vào DB tạm,
    và `_init()` ở đây sẽ mở DB THẬT trước khi kịp làm việc đó."""
    from . import selftest
    sys.exit(selftest.run())


def cmd_backup(args) -> None:
    """Sao lưu ngay, hoặc liệt kê các bản đã có.

    Chạy tay được nhưng KHÔNG phải cách dùng chính: vòng `run` tự gọi
    `backup.maybe_backup()` mỗi vòng. Lệnh này để (a) tạo bản đầu tiên ngay lập
    tức thay vì chờ tới hạn, (b) kiểm trước khi làm gì nguy hiểm với DB.
    """
    _init()
    from . import backup
    files = backup.snapshots()
    if args.list:
        print(f"Thư mục: {config.BACKUP_DIR}")
        print(f"Khóa Fernet: {config.KEY_BACKUP_PATH} "
              f"({'có' if config.KEY_BACKUP_PATH.exists() else 'CHƯA CÓ'}, "
              f"vân tay {backup.key_fingerprint()})")
        if not files:
            print("  (chưa có bản nào — chạy `python -m v2 backup`)")
            return
        now = time.time()
        for p in files:
            st = p.stat()
            print(f"  {p.name:<28} {st.st_size/1_048_576:6.1f} MB   "
                  f"{(now - st.st_mtime)/3600:7.1f}h trước")
        return
    dest = backup.run_backup()
    sys.exit(0 if dest else 1)


def cmd_ask(args) -> None:
    """Hỏi đáp ở terminal — thử được cả đường dữ liệu lẫn backend mà KHÔNG cần
    app Lark thứ hai.

    `--as <union_id|open_id>` để hỏi BẰNG DANH TÍNH của một người đã enroll: đây
    là cách duy nhất kiểm được bộ lọc phân quyền (§20) mà không cần hai tài khoản
    Lark. Không có cờ thì xem bằng quyền admin — nói TO điều đó, vì kết quả lúc
    ấy khác hẳn cái người dùng thật sẽ thấy.
    """
    _init()
    from . import askers, qa
    if args.as_who:
        who = askers.find_enrolled(args.as_who)
        if not who:
            print(f"Không thấy ai đã enroll có id '{args.as_who}'. "
                  f"Xem: python -m v2 users", file=sys.stderr)
            sys.exit(1)
        print(f"[hỏi bằng danh tính] {who['name']}"
              f"{' (là admin — duyệt được từ điển, KHÔNG thấy cuộc của người khác)' if who['admin'] else ''}\n")
    else:
        who = askers.admin_view("(CLI, quyền admin)")
        print("[!] Không có --as: đang xem bằng QUYỀN ADMIN, thấy hết mọi cuộc "
              "họp.\n    Người dùng thật chỉ thấy cuộc họp họ có dự — thử bằng "
              "`--as <union_id>`.\n")
    if args.show_context:
        print(qa.context(who))
        print("-" * 60)
    print(qa.answer(who, " ".join(args.question)))


def cmd_transcript(args) -> None:
    """In nguyên văn whisper ở terminal — smoke test cho tool `get_transcript`.

    Đi thẳng vào `qa.get_transcript`, KHÔNG qua LLM: cần thấy đúng chuỗi mà agent
    sẽ nhận, kể cả các nhánh từ chối. `--as` giống `ask` — không có cờ thì xem
    bằng quyền admin, tức KHÔNG phải cái người dùng thật thấy.
    """
    _init()
    from . import askers, qa
    if args.as_who:
        who = askers.find_enrolled(args.as_who)
        if not who:
            print(f"Không thấy ai đã enroll có id '{args.as_who}'. "
                  f"Xem: python -m v2 users", file=sys.stderr)
            sys.exit(1)
        print(f"[xem bằng danh tính] {who['name']}"
              f"{' (là admin — duyệt được từ điển, KHÔNG thấy cuộc của người khác)' if who['admin'] else ''}\n")
    else:
        who = askers.admin_view("(CLI, quyền admin)")
        print("[!] Không có --as: đang xem bằng QUYỀN ADMIN, thấy hết mọi cuộc "
              "họp.\n")
    if args.send_file:
        # Gửi THẬT một tin nhắn Lark. Đòi `--as` chứ không cho chạy bằng quyền
        # admin: `admin_view` không có union_id nên `sendfile` sẽ từ chối, và
        # người gõ lệnh sẽ tưởng tool hỏng thay vì hiểu là mình gọi sai.
        from . import sendfile
        if not args.as_who:
            print("`--send-file` cần `--as <union_id>` — gửi cho CHÍNH người "
                  "đó, không gửi cho ai khác được.", file=sys.stderr)
            sys.exit(1)
        print(sendfile.send_transcript(who, " ".join(args.query)))
        return
    print(qa.get_transcript(who, " ".join(args.query), part=args.part))


def cmd_mcp(_) -> None:
    """MCP server phơi dữ liệu họp cho Hermes. Hermes tự spawn lệnh này.

    KHÔNG in gì ra stdout trước khi serve() chiếm nó — stdout là kênh giao thức.
    """
    config.ensure_dirs()
    db.init()
    from . import mcp_server
    mcp_server.serve()


# Lệnh `base-final` ĐÃ BỎ (31/07/2026, Việc 5b): cột `Trạng thái` nay nói về
# việc PHÁT (`đã phát` / `phát hỏng` / `không có recap`), không còn `final` để
# chốt. Muốn đổ lại cột đó cho record cũ thì dùng `base-sync`.


def main() -> None:
    # Console Windows mặc định cp1252 -> in tên tiếng Việt ("ễ") là crash
    # UnicodeEncodeError. Ép UTF-8, thay ký tự không vẽ được thay vì chết.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(prog="v2", description="Orchestrator V2")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("genkey").set_defaults(fn=cmd_genkey)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)

    e = sub.add_parser("enroll")
    e.add_argument("--port", type=int, default=8080)
    e.add_argument("--timeout", type=int, default=600)
    e.set_defaults(fn=cmd_enroll)

    eu = sub.add_parser("enroll-url", help="in link enroll (mặc định: rút gọn)")
    eu.add_argument("--long", action="store_true",
                    help="in URL authorize đầy đủ thay vì link rút gọn")
    eu.set_defaults(fn=cmd_enroll_url)

    c = sub.add_parser("complete")
    c.add_argument("--code", required=True)
    c.add_argument("--state", required=True)
    c.set_defaults(fn=cmd_complete)

    pf = sub.add_parser("profile",
                        help="in hồ sơ bot (tên, việc làm được, luồng, giới hạn)")
    pf.set_defaults(fn=cmd_profile)

    g = sub.add_parser("gate", help="cửa vào bot hỏi đáp (plugin Hermes gọi)")
    g.add_argument("--union-id", default="", help="SessionSource.user_id_alt")
    g.add_argument("--user-id", default="", help="chỉ để đọc log")
    g.add_argument("--name", default="")
    g.add_argument("--chat-type", default="",
                   help="SessionSource.chat_type: dm | group | channel | "
                        "thread. Chỉ `dm` được trả lời — trong phòng nhiều "
                        "người thì câu trả lời lọt sang người không có quyền "
                        "xem. Trống = plugin đời cũ, cho đi tiếp + cảnh báo")
    g.add_argument("--no-send", action="store_true",
                   help="chỉ tra, không nhắn ai (để thử)")
    g.set_defaults(fn=cmd_gate)

    ep = sub.add_parser("enroll-poll",
                        help="kéo code OAuth từ hộp thư Vercel rồi enroll")
    ep.set_defaults(fn=cmd_enroll_poll)

    cf = sub.add_parser(
        "cloudflare-config",
        help="nhập ẩn + kiểm Queue API token; chưa kích hoạt/cutover callback")
    cf.add_argument("--account-id", required=True, help="Cloudflare Account ID (32 hex)")
    cf.add_argument("--queue-id", required=True, help="Cloudflare Queue ID (32 hex)")
    cf_token = cf.add_mutually_exclusive_group()
    cf_token.add_argument("--token-stdin", action="store_true",
                          help="đọc token từ stdin")
    cf_token.add_argument("--token-dialog", action="store_true",
                          help="mở hộp thoại Windows có che ký tự để dán token")
    cf.set_defaults(fn=cmd_cloudflare_config)

    sub.add_parser("invites", help="ai đã được mời cấp quyền mà chưa xong")\
       .set_defaults(fn=cmd_invites)

    bs = sub.add_parser("base-sync",
                        help="thêm field còn thiếu + đổ lại ô theo dõi lên Base")
    bs.add_argument("--token", default="", help="chỉ một minute_token")
    bs.set_defaults(fn=cmd_base_sync)

    sub.add_parser("users", help="bảng người đã enroll (kể cả đã thu hồi)")\
       .set_defaults(fn=cmd_users)

    sc = sub.add_parser("scopes",
                        help="quét: mỗi người đã enroll có ĐỦ quyền V2 cần chưa")
    sc.add_argument("--open-id", default="", help="chỉ kiểm một người")
    sc.add_argument("--print-all", action="store_true",
                    help="in dòng OAUTH_SCOPES theo các HỌ scope V2 dùng, kèm "
                         "đo độ dài URL authorize (dán vào v2/.env)")
    sc.add_argument("--everything", action="store_true",
                    help="lấy TẤT CẢ scope Console duyệt — hiện vượt trần độ "
                         "dài URL, lệnh sẽ báo hỏng")
    sc.set_defaults(fn=cmd_scopes)

    rv = sub.add_parser("revoke", help="thu hồi quyền của một người")
    who = rv.add_mutually_exclusive_group(required=True)
    who.add_argument("--open-id", help="ou_...")
    who.add_argument("--union-id", help="on_... (gate in ra cái này)")
    rv.add_argument("--yes", action="store_true",
                    help="xác nhận — bắt buộc, không có thì chỉ in ra rồi thoát")
    rv.set_defaults(fn=cmd_revoke)

    ua = sub.add_parser(
        "unauth",
        help="XOÁ quyền của một người để họ cấp lại từ đầu (giữ nguyên biên bản)")
    uw = ua.add_mutually_exclusive_group(required=True)
    uw.add_argument("--open-id", help="ou_...")
    uw.add_argument("--union-id", help="on_... (gate in ra cái này)")
    ua.add_argument("--yes", action="store_true",
                    help="xác nhận — không có thì chỉ in ra rồi thoát")
    ua.set_defaults(fn=cmd_unauth)

    al = sub.add_parser("alerts",
                        help="kiểm + gửi cảnh báo DM một lượt (vòng run tự gọi)")
    al.add_argument("--dry-run", action="store_true",
                    help="chỉ in cái sắp gửi, không gửi, không ghi mốc")
    al.set_defaults(fn=cmd_alerts)

    gd = sub.add_parser(
        "glossary-digest",
        help="DM admin thuật ngữ chờ duyệt (Task Scheduler gọi tuần)")
    gd.add_argument("--dry-run", action="store_true",
                    help="chỉ in cái sắp gửi, không DM ai")
    gd.set_defaults(fn=cmd_glossary_digest)

    sub.add_parser("scan").set_defaults(fn=cmd_scan)

    eq = sub.add_parser("enqueue")
    eq.add_argument("--token", required=True, help="minute_token cần nạp")
    eq.add_argument("--reader", help="open_id người enroll để mượn token đọc")
    eq.set_defaults(fn=cmd_enqueue)

    cv = sub.add_parser(
        "coverage",
        help="ai đang thấy được bao nhiêu cuộc họp của họ, và ai đang chặn")
    cv.add_argument("--days", type=int, default=90,
                    help="cửa sổ đối chiếu với Lark (mặc định 90)")
    cv.set_defaults(fn=cmd_coverage)

    bf = sub.add_parser(
        "backfill",
        help="nạp bù cuộc họp CŨ hơn LOOKBACK_DAYS (mặc định chỉ thử khô)")
    bf.add_argument("--days", type=int, default=180,
                    help="quét lùi bao nhiêu ngày (mặc định 180)")
    bf.add_argument("--priority", type=int, default=0,
                    help="0=backlog (mặc định). Đừng đặt 1: cuộc cũ không phải "
                         "cuộc mới, và priority 1 làm chúng chen trước cuộc "
                         "vừa họp xong")
    bf.add_argument("--yes", action="store_true",
                    help="nạp thật — không có thì chỉ liệt kê rồi thoát")
    bf.set_defaults(fn=cmd_backfill)

    p = sub.add_parser("process")
    p.add_argument("--send", action="store_true", help="gửi thật")
    p.set_defaults(fn=cmd_process)

    r = sub.add_parser("run")
    r.add_argument("--ws", action="store_true",
                   help="WebSocket đường nhanh. ĐỪNG bật nếu Hermes đang dùng "
                        "cùng app_id — xem docs §12")
    r.add_argument("--send", action="store_true", help="gửi thật")
    r.set_defaults(fn=cmd_run)

    ps = sub.add_parser("push-status")
    ps.add_argument("--print", dest="print_only", action="store_true",
                    help="chỉ in JSON sẽ gửi, không gửi")
    ps.set_defaults(fn=cmd_push_status)

    bi = sub.add_parser("base-init",
                        help="in lệnh tạo Base 'nội dung đã chốt' + tự kiểm")
    bi.set_defaults(fn=cmd_base_init)

    sub.add_parser("selftest",
                   help="tự kiểm hành vi code trên DB tạm (không mạng, không "
                        "đụng state.db thật)").set_defaults(fn=cmd_selftest)

    bk = sub.add_parser("backup",
                        help="sao lưu state.db + khóa Fernet (vòng `run` tự "
                             "chạy mỗi V2_BACKUP_EVERY_HOURS giờ)")
    bk.add_argument("--list", action="store_true",
                    help="chỉ liệt kê các bản đã có, không tạo bản mới")
    bk.set_defaults(fn=cmd_backup)

    sub.add_parser("mcp", help="MCP server dữ liệu họp (Hermes gọi vào)"
                   ).set_defaults(fn=cmd_mcp)

    og = sub.add_parser("org-sync", help="V3: kéo cây quản lý từ Lark Contact")
    og.add_argument("--yes", action="store_true", help="ghi thật")
    og.set_defaults(fn=cmd_org_sync)
    dp = sub.add_parser("drive-provision", help="V3: tạo folder Drive cho mọi người")
    dp.add_argument("--yes", action="store_true", help="tạo thật")
    dp.set_defaults(fn=cmd_drive_provision)
    gb = sub.add_parser("grants-backfill",
                        help="V3: cấp lùi quyền cuộc cũ cho một quản lý")
    gb.add_argument("--open-id", required=True)
    gb.add_argument("--since", default="", help="YYYY-MM-DD")
    gb.add_argument("--yes", action="store_true", help="cấp thật")
    gb.set_defaults(fn=cmd_grants_backfill)
    se = sub.add_parser("search", help="V3: tìm cuộc họp theo ngữ nghĩa")
    se.add_argument("query", nargs="+")
    se.add_argument("--as", dest="as_who", default="",
                    help="tìm bằng danh tính một người đã enroll (kiểm quyền)")
    se.add_argument("--model", default="", help="thử model embedding khác")
    se.add_argument("--reindex", action="store_true", help="index trước khi tìm")
    se.set_defaults(fn=cmd_search)
    sub.add_parser("dashboard", help="V3: chạy web dashboard (đăng nhập Lark)")\
       .set_defaults(fn=cmd_dashboard)
    nt = sub.add_parser("notes", help="V3: ghi lại file biên bản .md")
    nt.add_argument("token", nargs="?", default="")
    nt.set_defaults(fn=cmd_notes)

    q = sub.add_parser("ask", help="hỏi đáp về các cuộc họp ở terminal")
    q.add_argument("question", nargs="+")
    q.add_argument("--as", dest="as_who", default="",
                   help="hỏi bằng danh tính một người đã enroll (union_id hoặc "
                        "open_id) — để kiểm bộ lọc phân quyền")
    q.add_argument("--show-context", action="store_true",
                   help="in cả dữ liệu Base đưa vào prompt")
    q.set_defaults(fn=cmd_ask)

    tr = sub.add_parser("transcript",
                        help="in nguyên văn whisper của một cuộc họp")
    tr.add_argument("query", nargs="+", help="minute_token hoặc một phần tên")
    tr.add_argument("--as", dest="as_who", default="",
                    help="xem bằng danh tính một người đã enroll — để kiểm bộ "
                         "lọc phân quyền")
    tr.add_argument("--part", type=int, default=1,
                    help="phần thứ mấy (mặc định 1)")
    tr.add_argument("--send-file", action="store_true",
                    help="GỬI THẬT file .docx cho người ở --as (query phải là "
                         "minute_token), thay vì in ra màn hình")
    tr.set_defaults(fn=cmd_transcript)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
