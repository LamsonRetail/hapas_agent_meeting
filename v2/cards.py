"""
Thẻ Lark cho biên bản đã phát, lời mời cấp quyền và lời chào sau khi cấp quyền.

Thẻ duyệt cũ (4 nút, V2 tự nghe `card.action.trigger`) đã bỏ 30/07/2026 — họp
xong là phát ngay, không ai duyệt. V2 KHÔNG còn nghe card action nữa
(`ws_listener.py`), nên đừng dựng nút nào chờ V2 xử lý.

NÚT VẪN DÙNG ĐƯỢC, qua đường khác (đo 05/08/2026 trên Hermes 0.19.1):

  * Nút `open_url` (`enroll_card`) không cần callback nào cả — Lark tự mở trang.
  * Nút thường đi vào WebSocket của HERMES, không phải V2. Adapter Feishu
    (`plugins/platforms/feishu/adapter.py:3038`) biến cú bấm thành một tin nhắn
    tổng hợp `/card button {json của value}` rồi đẩy qua đúng đường xử lý tin
    nhắn thường. Plugin `v2-enroll-gate` chạy ở `pre_gateway_dispatch` (run.py
    ~14261) — TRƯỚC chỗ Hermes từ chối lệnh lạ (~15480) — nên nó dịch tin đó
    thành yêu cầu tiếng Việt bình thường trước khi có ai kịp chặn.

Hệ quả phải nhớ khi sửa: `value` của nút là HỢP ĐỒNG với plugin. Đổi khoá ở đây
mà không đổi bên plugin thì người dùng bấm nút và nhận về "Unknown command".
"""

from __future__ import annotations

from typing import Any

from . import config

from .models import MeetingMeta, Recap


def _fmt_time_range(meta: MeetingMeta) -> str:
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=7))          # hiển thị +07 (V2_LONGTERM §4.3)
    if not meta.start:
        return ""
    lo = datetime.fromtimestamp(meta.start, tz).strftime("%H:%M %d/%m")
    if meta.duration_sec:
        hi = datetime.fromtimestamp(
            meta.start + meta.duration_sec, tz).strftime("%H:%M")
        return f"{lo}–{hi}"
    return lo


def enroll_card(name: str, link: str) -> dict[str, Any]:
    """Lời mời cấp quyền: một nút `open_url`, KHÔNG dán URL trần vào câu.

    Vì sao là nút (user chốt 05/08/2026): URL authorize dài ~3.800 ký tự. Dán
    thẳng vào tin nhắn Lark thì nó xuống dòng gãy làm đôi, bấm vào ra 404, và
    người nhận nhìn một chuỗi khổng lồ thì ngại bấm. Link rút gọn đỡ hơn nhưng
    vẫn là một dòng lạ giữa câu tiếng Việt.

    `open_url` KHÔNG cần callback: Lark tự mở trình duyệt. Đây là loại nút duy
    nhất chạy được mà không cần ai nghe `card.action.trigger`.
    """
    bot_name = config.BOT_NAME
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text",
                             "content": f"Cấp quyền cho {bot_name}"[:100]}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"Chào **{name}**! Mình là **{bot_name}**.\n"
                "Để đọc được biên bản các cuộc họp **của bạn**, mình cần bạn cho phép một lần.")}},
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                "Bấm nút dưới rồi chọn **Đồng ý**. Xong là mình gửi ngay danh "
                "sách cuộc họp 7 ngày qua của bạn.")}},
            {"tag": "action", "actions": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": "Cấp quyền"},
                 "type": "primary",
                 "url": link},
            ]},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": (
                "Quyền này chỉ dùng để đọc cuộc họp bạn có dự. Người khác "
                "không thấy được cuộc họp của bạn.")}]},
        ],
    }


def list_card(body: str) -> dict[str, Any]:
    """Thẻ cho danh sách cuộc họp gửi thẳng vào chat.

    Vì sao phải là THẺ chứ không phải `im_send_text` (user chốt 05/08/2026):
    tin nhắn `text` của Lark KHÔNG render markdown — gõ `**Tên cuộc**` thì người
    dùng nhìn thấy đúng hai dấu sao. Chỉ `lark_md` trong thẻ mới in đậm được.
    Câu trả lời của agent thì đã đậm sẵn vì Hermes gửi kiểu `post`; danh sách do
    V2 tự gửi là chỗ DUY NHẤT còn trơ, và nó lại là tin dài nhất.

    Giới hạn 4000 ký tự cho một phần tử `div` — cắt cho chắc, thà mất vài dòng
    cuối còn hơn Lark từ chối cả thẻ và người dùng không nhận được gì.
    """
    return {
        "config": {"wide_screen_mode": True},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body[:4000]}},
        ],
    }


def welcome_card(name: str, body: str) -> dict[str, Any]:
    """Thẻ chào sau khi cấp quyền xong: lời chào + danh sách 7 ngày + hướng dẫn tính năng."""
    bot_name = config.BOT_NAME
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": f"Chào **{name}**! Mình là **{bot_name}**. Cảm ơn bạn đã cấp quyền! 🎉"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": body[:4000]}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": (
            "💡 **CÁC TÍNH NĂNG MÌNH CÓ THỂ GIÚP BẠN:**\n"
            "• **Họp xong nhận ngay tóm tắt**: Tự động gửi thẻ tóm tắt (ý chính, quyết định, việc cần làm) về chat 1-1.\n"
            "• **Hỏi đáp nội dung cuộc họp**: Nhắn hỏi về bất kỳ cuộc họp nào bạn có tham dự.\n"
            "• **Nhận bản dịch chuẩn từ Hapas**: Sau mỗi cuộc họp mình sẽ hỏi "
            "bạn có muốn lấy không; nếu bạn đồng ý, bản chưa có sẽ được ưu tiên "
            "xử lý và tự gửi khi xong.\n"
            "• **Tạo việc trên Lark Task**: Tự động tạo task từ cuộc họp và giao việc cho bạn."
        )}},
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green",
                   "title": {"tag": "plain_text",
                             "content": "Đã cấp quyền xong"}},
        "elements": elements,
    }


def recap_card(meta: MeetingMeta, recap: Recap) -> dict[str, Any]:
    """Thẻ recap phát cho người nhận."""
    when = _fmt_time_range(meta)
    elements: list[dict[str, Any]] = []
    if when:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"🕐 {when}"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                    "content": recap.to_markdown()[:4000]}})
    if meta.app_link:
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"[Xem trên Lark Minutes]({meta.app_link})"}})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "green",
                   "title": {"tag": "plain_text",
                             "content": f"Biên bản: {meta.title}"[:100]}},
        "elements": elements,
    }


def minute_notice_card(meta: MeetingMeta, recap: Recap, *,
                       hapas_ready: bool = False) -> dict[str, Any]:
    """Thẻ báo NGAY khi họp xong (mô hình kéo, 03/08/2026).

    Gửi liền tóm tắt NỘI DUNG MEETING NOTE LARK + link Minute, rồi LUÔN hỏi
    người nhận có muốn lấy bản dịch chuẩn từ Hapas không. Nếu bản chưa có thì
    chỉ xử lý sau khi chính người dùng đồng ý; nút bấm đi qua gate + ACL như
    yêu cầu gõ tay, không tự gửi transcript.
    """
    when = _fmt_time_range(meta)
    elements: list[dict[str, Any]] = []
    if when:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"🕐 {when}"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                    "content": recap.to_markdown()[:4000]}})
    if meta.app_link:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"📄 [Mở bản Minute trên Lark]({meta.app_link})"}})
    elements.append({"tag": "hr"})
    # KHÔNG có nút ở thẻ này (bỏ 19/08/2026 — user chốt sau khi đo).
    #
    # Nút cũ `value={"v2": "transcript", ...}` là đồ trang trí từ 13/08 tới 19/08:
    # đo trên MỌI file log của Hermes (gateway/agent/stdio/errors) không có một
    # dòng `card action` / `Routing card` / `/card` nào — Lark chưa từng đẩy
    # `card.action.trigger` về app, nên hàm dịch cú bấm bên plugin chưa chạy lần
    # nào. Và kể cả nếu Lark đẩy về thì vẫn bị bỏ: adapter Feishu gọi
    # `_resolve_source_chat_type(..., event_chat_type="group")` GHIM CỨNG cho card
    # action, hàm đó chỉ trả "dm" khi tham số ấy là "p2p", nên cú bấm trong chat
    # 1-1 bị dán nhãn `group` và plugin bỏ đúng theo luật chỉ-trả-lời-DM.
    #
    # Vì sao BỎ chứ không sửa: nút đó phụ thuộc HAI thứ nằm ngoài repo — event
    # subscription trong Lark Console và nội bộ adapter của Hermes — không test
    # nào phủ được, và khi hỏng thì im lặng. Đường gõ chữ làm đúng việc đó và đã
    # chạy thật. Ít mảnh chuyển động hơn = bền hơn.
    #
    # ĐỪNG dựng lại nút callback ở đây mà không có đường kiểm live: `selftest`
    # có phép kiểm chặn đúng việc này.
    if hapas_ready:
        offer = ("💬 Bạn có muốn lấy **bản dịch chuẩn từ Hapas** không? "
                 "Bản này đã sẵn sàng — trả lời **có** là mình gửi file Word ngay.")
    else:
        offer = ("💬 Bạn có muốn lấy **bản dịch chuẩn từ Hapas** không? "
                 "Trả lời **có** thì mình ưu tiên xử lý và tự gửi file Word khi "
                 "xong.")
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                                               "content": offer}})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text",
                             "content": f"Họp xong: {meta.title}"[:100]}},
        "elements": elements,
    }
