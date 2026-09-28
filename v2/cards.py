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
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text",
                             "content": "Cấp quyền cho trợ lý biên bản họp"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"Chào **{name}**! Để đọc được biên bản các cuộc họp "
                "**của bạn**, mình cần bạn cho phép một lần.")}},
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
    """Thẻ chào sau khi cấp quyền xong: lời chào + danh sách 7 ngày. Hết.

    `body` là chuỗi danh sách THÀNH PHẨM do `qa.list_text` dựng — dùng lại đúng
    bộ dựng của bot để hai chỗ không bao giờ lệch định dạng.

    BỎ khối nút "Nhận bản nguyên văn" (user chốt 05/08/2026). Nó lặp lại đúng
    câu đã có trong chân trang của `body` ngay phía trên ("Nhắn *gửi nguyên văn
    <tên cuộc họp>* để nhận file Word"), và hai ô vuông tên cuộc họp cắt cụt ở 20
    ký tự làm cuối thẻ trông rối. Một lời mời nói một lần là đủ.

    Đường xử lý cú bấm nút (`_rewrite_card_click` bên plugin) GIỮ NGUYÊN dù giờ
    không thẻ nào sinh nút: nó là cầu duy nhất giữ cho một nút thêm sau này khỏi
    rơi vào nhánh "Unknown command" của Hermes.
    """
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": f"Xong rồi **{name}**!"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": body[:4000]}},
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


UNREVIEWED_NOTE = ("⚠️ Biên bản **chưa được chủ trì review** — nội dung do máy "
                   "tóm tắt, có thể còn sai sót.")


def minute_notice_card(meta: MeetingMeta, recap: Recap,
                       unreviewed: bool = False,
                       revised: bool = False) -> dict[str, Any]:
    """Thẻ báo NGAY khi họp xong (mô hình kéo, 03/08/2026).

    Gửi liền: tóm tắt NỘI DUNG MINUTE LARK + link Minute + LỜI MỜI lấy bản
    transcript whisper chuẩn. KHÔNG kèm transcript (whisper chạy nền, giữ chờ
    hỏi). Không có nút (card action đi vào WebSocket của Hermes, không phải V2)
    — người dùng NHẮN để lấy, và plugin gate + bot Q&A lo phần đó.
    """
    when = _fmt_time_range(meta)
    elements: list[dict[str, Any]] = []
    if unreviewed:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": UNREVIEWED_NOTE}})
    if when:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"🕐 {when}"}})
    elements.append({"tag": "div", "text": {"tag": "lark_md",
                    "content": recap.to_markdown()[:4000]}})
    if meta.app_link:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"📄 [Mở bản Minute trên Lark]({meta.app_link})"}})
    elements.append({"tag": "hr"})
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": (
        "💬 Cần **bản nguyên văn** — chép lại đúng từng câu mọi người đã nói, "
        "đầy đủ hơn bản tóm tắt ở trên? Nhắn mình: "
        f"**gửi nguyên văn {meta.title}** — mình làm ngay và tự gửi khi xong.")}})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text",
                             "content": f"{'Đã hiệu chỉnh' if revised else 'Họp xong'}: {meta.title}"[:100]}},
        "elements": elements,
    }


def confirm_card(meta: MeetingMeta, recap: Recap, *,
                 updated: bool = False, reminder: bool = False) -> dict[str, Any]:
    """Thẻ gửi CHỦ cuộc họp để duyệt/sửa biên bản trước khi phát (V3 YC1).

    Không có nút — cùng lý do `minute_notice_card`: card action đi vào WebSocket
    của Hermes. Chủ NHẮN "duyệt …" / "sửa …", agent gọi tool `confirm_meeting` /
    `edit_meeting`.
    """
    from . import config
    head = ("Nhắc: " if reminder else "") + ("Bản cập nhật — " if updated else "")
    lead = ("Tóm tắt đã được làm lại từ **bản nguyên văn** (chi tiết hơn). "
            if updated else "")
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": (
            f"{lead}Bạn là chủ trì cuộc họp này. Biên bản **chưa gửi** cho "
            "người dự — bạn xem giúp và chọn:\n"
            f"• Nhắn **duyệt {meta.title}** để phát cho người dự\n"
            f"• Nhắn **sửa {meta.title}: <nội dung cần sửa>** để chỉnh\n"
            f"Sau {config.CONFIRM_TIMEOUT_HOURS} giờ không phản hồi, biên bản tự "
            "phát kèm ghi chú *chưa được review* — bạn vẫn duyệt/sửa được sau đó.")}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": recap.to_markdown()[:4000]}},
    ]
    if meta.app_link:
        elements.append({"tag": "div", "text": {"tag": "lark_md",
                        "content": f"📄 [Mở bản Minute trên Lark]({meta.app_link})"}})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "orange",
                   "title": {"tag": "plain_text",
                             "content": f"{head}Cần bạn duyệt: {meta.title}"[:100]}},
        "elements": elements,
    }
