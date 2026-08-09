"""
Cửa vào cho bot hỏi đáp: người chưa enroll thì tự bấm link OAuth, xong mới hỏi.

Ai gọi module này: plugin Hermes `v2-enroll-gate` (chạy hook
`pre_gateway_dispatch`, TRƯỚC cửa auth của Hermes) gọi qua `v2-gate.bat` rồi đọc
JSON ở stdout. Vì sao qua subprocess chứ không import: Hermes chạy Python 3.11
của uv, V2 chạy Python 3.12 với bộ thư viện riêng — nhập chéo là mời lỗi phụ
thuộc. Tiến trình tách nhau, giao tiếp bằng JSON một dòng.

Ba trạng thái trả về:
    {"decision": "allow", "asker_token": ...}   đã enroll -> cho vào agent
    {"decision": "invite", ...}            chưa, vừa gửi link
    {"decision": "wait", ...}              chưa, link còn hiệu lực -> im lặng

`asker_token` (thêm 31/07/2026): vé phiên buộc với union_id này. Plugin chèn nó
vào tin nhắn để agent truyền lại qua tham số `asker_token` của tool MCP — đó là
cách duy nhất `qa.py` biết ai đang hỏi, vì MCP server dùng chung một tiến trình
cho cả tenant. Xem `v2/askers.py`.

KHÔNG bao giờ trả "allow" khi không chắc: hàm này là cửa duy nhất (user chốt
31/07/2026), nên nghi ngờ thì đóng.

Khớp danh tính bằng `union_id`. Hermes đưa sang `SessionSource.user_id_alt` —
tài liệu của nó ghi rõ đó là union_id của Feishu — và `tokens` cũng lưu
`union_id`, nên không phải tra danh bạ. `user_id` (kiểu `1fg8g36d`) chỉ để in log:
bảng `tokens` KHÔNG có nó, đừng dùng để so.
"""

from __future__ import annotations

import time
from typing import Any

from . import db, lark_api, oauth

# Nhắc lại link sau bao lâu. KHÔNG dùng TTL nonce (nay 24h — xem config): dùng
# chung thì người mất link phải chờ hết 24h mới được cái mới. Link cũ vẫn sống
# song song, và cả hai đều enroll được nên không sao.
from .config import ENROLL_REINVITE_MINUTES

_INVITE_TEXT = (
    "Chào {name}!\n\n"
    "Trước khi hỏi về biên bản họp, bạn cần cho phép hệ thống đọc biên bản "
    "cuộc họp của bạn một lần. Bấm link dưới rồi chọn Đồng ý:\n\n{link}\n\n"
    "Xong bạn nhắn lại là mình trả lời được ngay."
)

_WAIT_TEXT = (
    "Link cấp quyền mình vừa gửi vẫn còn hiệu lực — bạn bấm vào đó và chọn "
    "Đồng ý nhé. Chưa có quyền thì mình chưa đọc được biên bản của bạn."
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _memory_block(union_id: str) -> str:
    """Ký ức hội thoại của người này, dạng khối prompt. "" nếu chưa có gì.

    Vì sao ký ức đi qua CỬA NÀY chứ không nằm trong lịch sử phiên của Hermes
    (06/08/2026): lịch sử phiên chết khi phiên tự đóng (im 1 tiếng, hoặc 4h
    sáng) và khi `hermes gateway restart`. Sau mỗi lần đó, "gửi nguyên văn cuộc
    đó" mất hết chỗ dựa. Cửa này thì chạy lại ở MỌI tin nhắn và đọc từ SQLite,
    nên ký ức sống qua cả hai.

    Chỉ token + tiêu đề + mốc thời gian, và chỉ của cuộc đã qua `qa._may_see`
    (xem `qa.remember`). Lời dặn kèm theo là phần bắt buộc: thiếu nó thì agent
    rất dễ coi đây là dữ liệu và trả lời "cuộc đó bàn về..." mà không gọi tool —
    đúng lỗi trí-nhớ-thay-dữ-liệu đã sửa ngày 04/08/2026.
    """
    try:
        from . import db
        rows = db.recent_meetings(union_id, limit=3)
    except Exception as exc:                  # noqa: BLE001 — cửa vào không được chết
        print(f"[gate] không đọc được ký ức hội thoại: {exc}")
        return ""
    if not rows:
        return ""

    import datetime as _dt
    now = _dt.datetime.now()
    today = now.date()
    lines = []
    for r in rows:
        at = _dt.datetime.fromtimestamp(int(r["touched_at"] or 0) / 1000)
        delta = (today - at.date()).days
        when = ("hôm nay" if delta == 0 else
                "hôm qua" if delta == 1 else at.strftime("%d/%m"))
        lines.append(f"  • \"{r['title'] or '(không tiêu đề)'}\" "
                     f"(minute_token: {r['minute_token']}) — {when} "
                     f"{at:%H:%M}")
    return "\n".join([
        "[NGƯỜI NÀY VỪA NHẮC TỚI / VỪA NHẬN THẺ VỀ NHỮNG CUỘC HỌP SAU]",
        "(mới nhất trước — gồm cả cuộc mà hệ thống vừa đẩy thẻ 'Họp xong')",
        *lines,
        "Dùng danh sách trên CHỈ để hiểu câu nói tắt — \"cuộc đó\", \"cuộc vừa "
        "rồi\", \"phân tích đi\", \"bản ngày 29/07\" — rồi gọi tool với đúng "
        "minute_token, và để khỏi bắt người ta gõ lại cả tên cuộc họp.",
        # Ca thật 06/08/2026 19:51: người dùng TRẢ LỜI một thẻ rồi gõ "phân
        # tích"; agent lấy nhầm một cuộc cũ hơn trong danh sách. Thứ tự thôi
        # không đủ — phải nói thẳng mục đầu là cuộc mặc định.
        "Người dùng nói tắt mà KHÔNG nêu tên cuộc nào thì mặc định là MỤC ĐẦU "
        "TIÊN của danh sách. Chỉ chọn mục khác khi chính họ nêu tên/ngày khớp "
        "mục đó. Không chắc giữa hai cuộc thì hỏi lại MỘT câu ngắn, đừng đoán.",
        "KHÔNG được dùng nó làm nội dung trả lời: ở đây chỉ có tên cuộc họp, "
        "không có tóm tắt hay nguyên văn. Mọi câu hỏi về NỘI DUNG vẫn phải gọi "
        "tool meetings trong chính lượt này.",
        "Không tự nhắc tới danh sách này khi người dùng không hỏi tới nó.",
    ])


def _enrolled(union_id: str) -> dict | None:
    row = db.conn().execute(
        "SELECT open_id, name, status FROM tokens WHERE union_id=?",
        (union_id,),
    ).fetchone()
    if row and row["status"] == "active":
        return {"open_id": row["open_id"], "name": row["name"]}
    return None


# Loại phòng chat được phép hỏi bot. Hermes dùng "dm" cho 1-1
# (`_map_chat_type`: `"dm" if chat_type == "p2p" else "group"`); nhận luôn "p2p"
# để gọi thẳng từ terminal cho tiện.
_P2P = {"dm", "p2p"}


def _refuse_group(chat_type: str) -> dict[str, Any] | None:
    """Chặn mọi phòng KHÔNG phải 1-1. None = được đi tiếp.

    Vì sao phải chặn, và vì sao lý do KHÔNG phải cái tôi tưởng lúc đầu
    (02/08/2026): bộ lọc `qa._may_see` cấp quyền cho **người HỎI**, không cấp
    cho **người ĐỌC**. Trong group, A hỏi và bot trả lời vào phòng — cả phòng
    đọc được biên bản mà chỉ A có quyền xem. Không cấu hình nào chặn việc đó,
    và nó im lặng.

    Chuyện tôi tưởng lúc đầu — ngữ cảnh agent lẫn vé của nhiều người rồi trả
    lời B bằng danh tính A — thì **đã bị chặn sẵn**: Hermes để
    `group_sessions_per_user: true`, mỗi người trong group là một session
    riêng. Ghi lại để không ai đi vá một lỗ không tồn tại.

    Hôm nay còn HAI lớp chặn nữa ở ngoài repo (`FEISHU_GROUP_POLICY=allowlist`
    không có `ALLOWED_GROUPS`, và plugin `v2-enroll-gate` tự bỏ tin group).
    Lớp này là lớp duy nhất nằm TRONG repo và có test — đúng bài học §28.1/§29:
    thứ chỉ được giữ bởi một dòng cấu hình ngoài repo thì không ai biết khi nó
    đổi.

    `chat_type` rỗng = plugin đời cũ chưa gửi trường này -> CHO ĐI TIẾP, và nói
    to. Chọn vậy có chủ ý: `v2-gate.bat` spawn Python mới nên ăn code mới ngay,
    còn plugin thì phải `hermes gateway restart` mới cập nhật (§20). Đóng ở đây
    là bot câm với TẤT CẢ mọi người trong khoảng giữa hai lần đó, để chữa một lỗ
    mà hai lớp ngoài kia đang chặn rồi.
    """
    ct = (chat_type or "").strip().lower()
    if not ct:
        print("[gate] CẢNH BÁO: không biết loại phòng chat (plugin đời cũ?) — "
              "cho đi tiếp. Chạy hermes/install-plugin.bat rồi "
              "`hermes gateway restart` để bật lớp chặn group trong V2.")
        return None
    if ct in _P2P:
        return None
    return {"decision": "wait",
            "reason": f"chỉ trả lời trong chat 1-1, không trả lời trong "
                      f"{ct} (biên bản chỉ hiện cho người có dự)"}


def check(union_id: str, user_id: str = "", name: str = "",
          *, send: bool = True, chat_type: str = "") -> dict[str, Any]:
    """Quyết định cho vào hay không. `send=False` để thử mà không nhắn ai."""
    # TRƯỚC mọi thứ khác, kể cả trước khi tra `tokens`: trong phòng nhiều người
    # thì ngay cả câu "bạn chưa cấp quyền, bấm link này" cũng không nên phát ra
    # giữa phòng, và ta cũng không muốn cấp vé phiên cho một ngữ cảnh mà câu trả
    # lời sẽ bị người khác đọc.
    if (no := _refuse_group(chat_type)) is not None:
        return no

    if not union_id:
        # Không có union_id thì không định danh được -> đóng.
        return {"decision": "wait", "reason": "thiếu union_id"}

    who = _enrolled(union_id)
    if not who:
        # Người này chưa có trong `tokens` — nhưng RẤT CÓ THỂ họ vừa bấm "Đồng ý"
        # xong và đang nhắn lại ngay (04/08/2026, lỗi người mới nào cũng dính):
        # code nằm ở hộp thư Vercel, mà chỉ vòng `run` mới kéo về — 5 phút/lần.
        # Trong khoảng đó bot vẫn nói "bạn bấm link rồi chọn Đồng ý nhé" dù trang
        # web đã báo "Đã cấp quyền ✓", nên người mới tưởng hỏng và bấm lại.
        # Kéo hộp thư NGAY tại đây rồi tra lại. `poll_pending` tự bỏ qua khi
        # không có nonce nào còn sống, nên chỉ tốn một lời gọi Vercel đúng lúc
        # có người đang enroll dở — không đụng vào hạn mức Blob của vòng `run`.
        try:
            # ĐỔI LẠI notify=True (user chốt 05/08/2026). Bản trước để False
            # nhằm tránh "bot trả lời hai lần", nhưng nó gây một lỗi nặng hơn:
            # đường này vẫn hoàn tất OAuth và ACK queue, chỉ bỏ qua welcome — và
            # KHÔNG có ai gửi bù. Người vừa cấp quyền mà lỡ nhắn trước khi vòng
            # nền kịp poll thì mất hẳn danh sách 7 ngày, tức mất đúng cái luồng
            # chính của sản phẩm.
            #
            # Hai tin lúc đó KHÁC nội dung (danh sách chào mừng + câu trả lời cho
            # câu họ vừa hỏi), không phải trả lời trùng như sự cố 05/08. Và vòng
            # nền giờ poll nhanh khi có người enroll dở, nên gần như luôn gửi
            # xong danh sách TRƯỚC khi họ kịp nhắn — va chạm này hiếm khi xảy ra.
            oauth.poll_pending()
        except Exception as exc:              # noqa: BLE001 — cửa vào không được chết
            print(f"[gate] kéo hộp thư hỏng (bỏ qua): {exc}")
        who = _enrolled(union_id)
    if who:
        # Cấp vé TẠI ĐÂY: đây là chỗ duy nhất đã xác thực người gửi bằng dữ liệu
        # của V2 (bảng `tokens`), nên cũng là chỗ duy nhất được phép nói "người
        # này là ai". Cấp vé hỏng thì vẫn cho vào — tầng dữ liệu sẽ tự từ chối
        # khi thiếu vé (fail-closed nằm ở `qa`, một chỗ), chứ không chặn ở đây
        # rồi người ta không hiểu vì sao bot im.
        tok = ""
        try:
            from . import askers
            tok = askers.issue(union_id, who["open_id"], who["name"] or name)
        except Exception as exc:              # noqa: BLE001 — xem trên
            print(f"[gate] không cấp được vé phiên cho {union_id}: {exc}")
        # Hồ sơ + ký ức đi kèm quyết định `allow` (06/08/2026). Vì sao V2 cấp
        # chứ không để plugin tự viết: plugin chạy Python của Hermes nên không
        # import được `v2.*` (xem docstring module), mà hồ sơ thì phải nằm trong
        # repo có version control chứ không nằm rải trong `%LOCALAPPDATA%`.
        # Plugin có bản dự phòng cho ca V2 đời cũ không trả hai trường này.
        from . import profile
        return {"decision": "allow", "open_id": who["open_id"],
                "name": who["name"], "asker_token": tok,
                "profile": profile.prompt_block(),
                "memory": _memory_block(union_id)}

    inv = db.conn().execute(
        "SELECT nonce, sent_at, times FROM enroll_invites WHERE union_id=?",
        (union_id,),
    ).fetchone()
    # A recent invite can belong to the old Vercel relay. Keep its nonce alive
    # so an already-open page may still drain, but do not tell the user that an
    # incompatible old link remains usable. Only when they message again do we
    # issue a fresh Cloudflare link; there is still no proactive auth DM.
    if inv and not oauth.state_reusable(inv["nonce"]):
        inv = None
    if inv and _now_ms() - int(inv["sent_at"] or 0) < ENROLL_REINVITE_MINUTES * 60_000:
        if send:
            try:
                lark_api.im_send_text(union_id, _WAIT_TEXT, id_type="union_id")
            except lark_api.LarkError as exc:
                print(f"[gate] không nhắc được {union_id}: {exc}")
        return {"decision": "wait", "nonce": inv["nonce"],
                "times": inv["times"]}

    link, nonce = oauth.start()          # open_id trống: chỉ ràng buộc bằng nonce
    # Rút gọn TRƯỚC khi nhắn. Đây là đường người mới thực tế đi (nhắn bot ->
    # nhận link), nên nó là chỗ link dài gây hại nhất: URL authorize ~3.800 ký
    # tự dán vào tin nhắn Lark thì xuống dòng gãy link, và người nhận nhìn một
    # chuỗi khổng lồ thì ngại bấm. Rút gọn hỏng (Vercel tắt) thì `short_link`
    # trả "" và ta gửi link dài — vẫn chạy, chỉ xấu.
    link = oauth.short_link(link) or link
    if send:
        # Thẻ có NÚT thay vì URL trần (user chốt 05/08/2026). Nút `open_url`
        # không cần callback nào — xem `cards.enroll_card`.
        #
        # Có đường lùi về text vì đây là CỬA VÀO: thẻ hỏng (Lark đổi schema,
        # tenant chặn interactive…) mà không lùi thì người mới không bao giờ cấp
        # quyền được, và triệu chứng là bot im lặng — thứ khó chẩn nhất.
        try:
            from . import cards
            lark_api.im_send_card(
                union_id, cards.enroll_card(name or "bạn", link),
                id_type="union_id")
        except lark_api.LarkError as exc:
            print(f"[gate] thẻ cấp quyền hỏng ({exc}) — gửi lại bằng text")
            try:
                lark_api.im_send_text(
                    union_id,
                    _INVITE_TEXT.format(name=name or "bạn", link=link),
                    id_type="union_id",
                )
            except lark_api.LarkError as exc2:
                # Gửi hỏng thì ĐỪNG ghi invite, để lần sau thử lại.
                print(f"[gate] không gửi được link cho {union_id}: {exc2}")
                return {"decision": "wait", "reason": f"gửi link hỏng: {exc2.code}"}

    if not send:
        # THỬ KHÔ thì KHÔNG được ghi gì (sửa 04/08/2026 — đã dính thật). Ghi
        # `enroll_invites` ở đây nghĩa là lần nhắn THẬT kế tiếp rơi vào nhánh
        # "wait" phía trên và bot nói "link mình VỪA GỬI vẫn còn hiệu lực" —
        # trong khi chưa có link nào được gửi. Người dùng ngồi chờ một tin nhắn
        # không tồn tại suốt ENROLL_REINVITE_MINUTES, và người chẩn lỗi thì
        # thấy DB có lời mời nên tưởng đã gửi rồi.
        # Cùng lý lẽ với nhánh "gửi hỏng thì ĐỪNG ghi invite" ngay trên.
        return {"decision": "invite", "nonce": nonce, "link": link,
                "dry_run": True}

    with db.tx() as c:
        c.execute(
            "INSERT INTO enroll_invites(union_id,user_id,name,nonce,sent_at,times) "
            "VALUES (?,?,?,?,?,1) "
            "ON CONFLICT(union_id) DO UPDATE SET "
            "  user_id=excluded.user_id, name=excluded.name, "
            "  nonce=excluded.nonce, sent_at=excluded.sent_at, "
            "  times=enroll_invites.times+1",
            (union_id, user_id, name, nonce, _now_ms()),
        )
    return {"decision": "invite", "nonce": nonce, "link": link}


def clear_invite(union_id: str) -> None:
    """Xoá lời mời sau khi người đó enroll xong."""
    with db.tx() as c:
        c.execute("DELETE FROM enroll_invites WHERE union_id=?", (union_id,))


def pending_invites() -> list[dict]:
    rows = db.conn().execute(
        "SELECT union_id, user_id, name, sent_at, times FROM enroll_invites "
        "ORDER BY sent_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]
