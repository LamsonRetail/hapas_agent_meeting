"""
Gửi DANH SÁCH cuộc họp thẳng vào khung chat — đường GHI thứ ba.

Vì sao tồn tại (user chốt 04/08/2026, sau ba lượt siết prompt đều thất bại):
câu chữ cuối cùng gửi tới người dùng do LLM của Hermes viết, nên mọi chỉ thị
"chép nguyên văn" chỉ là LỜI ĐỀ NGHỊ. Đo thật, cùng một khối đã dựng sẵn:

    tool trả  : "1. 04/08/2026 15:34 · test agent meeting"
    bot gửi   : "1. 04/08, 15:34 — test agent meeting"   (link gắn vào tên)
    tool trả  : "ĐÃ CÓ BIÊN BẢN (3)"
    bot gửi   : (bỏ hẳn)

và trước đó nặng hơn: "Bạn có 8 cuộc họp" bị viết lại thành "hệ thống tìm thấy
22 cuộc họp gắn với tài khoản của bạn" — sai số, sai cả chủ sở hữu.

Cách chữa KHÔNG phải siết prompt lần thứ tư mà là lấy quyền viết khỏi nó. Khuôn
mẫu có sẵn trong repo: `sendfile.send_transcript` gửi thẳng .docx vào chat rồi
chỉ trả câu xác nhận; cửa vào `gate` gửi thẳng link enroll rồi trả `skip`. Ở cả
hai chỗ agent không hề cầm nội dung nên không sửa được nó.

NĂM RÀNG BUỘC, cưỡng chế bằng CODE (không dựa vào mô tả tool):

 1. Phải giải được người hỏi. `who=None` -> `qa.NO_ASKER`, không gửi gì.
 2. Người nhận SUY TỪ `who`, không nhận tham số. Không có đường nào cho agent
    nói "gửi cho người khác" vì tham số đó không tồn tại. Và vì cùng một `who`
    quyết định CẢ nội dung LẪN người nhận, danh sách của A không thể bay sang
    B: muốn dựng được danh sách của A thì phải cầm vé của A.
 3. Không có định danh Lark (đường terminal `admin_view`) -> KHÔNG gửi, trả text
    về cho caller in ra. `v2 ask` vẫn chạy như cũ.
 4. Lark gửi hỏng -> rơi về trả text kèm nhãn hai kênh, để người dùng vẫn nhận
    được câu trả lời. Một cú mạng chập không được thành "bot im lặng".
 5. Chống gửi lặp: cùng người + cùng nội dung trong `RESEND_COOLDOWN_S` giây thì
    KHÔNG gửi lại. Agent hiểu nhầm hay gặp lỗi là nó gọi lại tool, và không có
    cửa này thì một vòng lặp của agent = chục tin nhắn giống hệt.
"""

from __future__ import annotations

import hashlib
from typing import Any

from . import db, lark_api, qa

# Đủ dài để chặn một vòng lặp của agent trong cùng một lượt hỏi, đủ ngắn để
# người dùng hỏi lại thật sự thì vẫn được gửi.
RESEND_COOLDOWN_S = 60

# Chống lặp không dùng bảng `deliveries` (04/08/2026).
# `deliveries.minute_token` trả lời câu "ai đã nhận biên bản của cuộc họp nào";
# một lượt gửi danh sách không gắn với cuộc họp nào cả, nhét khoá giả vào đó là
# làm hỏng ý nghĩa của bảng và làm `_backfill_deliveries` đọc nhầm. Reservation
# nằm trong `outbound_dedup`, bền qua restart để một gateway vừa bật lại không
# phát lại đúng tin mà process trước vừa gửi; mỗi recipient chỉ chiếm một dòng.
_DEDUP_CHANNEL = "meeting-list"


def _fingerprint(text: str) -> str:
    # Không dùng hash() của Python: nó được salt lại mỗi process nên vô dụng sau
    # restart — đúng lúc cửa chống lặp cần còn hiệu lực.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Lời dặn trả cho agent SAU KHI đã gửi. Không kèm một dòng dữ liệu nào: đưa cho
# nó danh sách là mở lại đúng cửa vừa đóng.
_SENT_NOTE = (
    "Danh sách đầy đủ ĐÃ ĐƯỢC GỬI thẳng vào khung chat của người dùng rồi — họ "
    "đang nhìn thấy nó ngay phía trên.\n"
    "Việc của bạn: trả lời ĐÚNG MỘT CÂU NGẮN, ví dụ \"Danh sách của bạn ở trên "
    "nhé.\" Tuyệt đối KHÔNG liệt kê lại, KHÔNG tóm tắt, KHÔNG đếm, KHÔNG nhắc "
    "tên cuộc họp nào — bạn không có dữ liệu đó và nói ra là bịa.\n"
    "Cần minute_token để tạo task hay lấy nguyên văn thì gọi `search_meetings` "
    "hoặc `get_meeting`."
)

_ALREADY_NOTE = (
    "Danh sách này VỪA được gửi cho người dùng cách đây chưa tới một phút, "
    "không gửi lại. Chỉ nói ngắn gọn là danh sách ở phía trên. KHÔNG gọi lại "
    "tool này."
)


def send_list(who: dict[str, Any] | None, *, status: str = "all",
              since: str = "", until: str = "", limit: int = 50) -> str:
    """Gửi danh sách cho CHÍNH người hỏi. Trả câu báo cho agent."""
    # (1) phải biết người hỏi
    if not who:
        return qa.NO_ASKER

    text = qa.list_text(who, status=status, since=since, until=until,
                        limit=limit)

    # (2)+(3) người nhận suy từ `who`, không có tham số nào khác
    rid = who.get("union_id") or ""
    id_type = "union_id"
    if not rid:
        rid, id_type = who.get("open_id") or "", "open_id"
    if not rid:
        # Đường terminal (`v2 ask` không có --as): không có khung chat nào để
        # gửi. Trả text như cũ thay vì im lặng báo "đã gửi".
        return qa.two_channel(text)

    # (5) chống lặp bền qua restart và nguyên tử giữa nhiều MCP process.
    fingerprint = _fingerprint(text)
    if not db.try_reserve_outbound(_DEDUP_CHANNEL, rid, fingerprint,
                                   RESEND_COOLDOWN_S):
        return qa.agent_only(_ALREADY_NOTE)

    # (4) gửi; hỏng thì rơi về đường cũ chứ không nuốt lỗi.
    #
    # THẺ chứ không phải text (05/08/2026): tin `text` của Lark không render
    # markdown, mà danh sách giờ in đậm tên cuộc họp và các tiêu đề khối. Gửi
    # bằng text thì người dùng đọc thấy đầy dấu sao. Xem `cards.list_card`.
    #
    # Thẻ hỏng thì thử lại bằng text TRƯỚC khi bỏ cuộc: dấu sao vẫn còn đọc
    # được, im lặng thì không.
    from . import cards
    try:
        lark_api.im_send_card(rid, cards.list_card(text), id_type=id_type)
    except lark_api.LarkError as exc:
        print(f"[sendlist] thẻ hỏng ({exc}) — thử lại bằng text")
        try:
            lark_api.im_send_text(rid, text, id_type=id_type)
        except lark_api.LarkError as exc2:
            db.release_outbound(_DEDUP_CHANNEL, rid, fingerprint)
            print(f"[sendlist] gửi cho {rid[:14]} hỏng ({exc2}) — trả text cho agent")
            return qa.two_channel(text)
    except Exception:
        # Lỗi lập trình/SDK vẫn phải nhả chỗ; call_tool sẽ log và trả lỗi thay vì
        # làm người dùng bị khoá oan 60 giây dù chưa có tin nào được gửi.
        db.release_outbound(_DEDUP_CHANNEL, rid, fingerprint)
        raise

    return qa.agent_only(_SENT_NOTE)
