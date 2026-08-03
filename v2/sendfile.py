"""
Gửi file biên bản `.docx` cho CHÍNH người đang hỏi bot — đường GHI thứ hai.

Vì sao KHÔNG đặt trong `qa.py`: file đó có bất biến "chỉ đọc, đừng thêm hàm
ghi/xoá" (V2_HANDOFF §5.3). Cùng lý do `tasks.py` tồn tại riêng — xem docstring
ở đó. Mọi thứ có tác dụng ra ngoài (tạo task, gửi tin) nằm ngoài `qa.py`, để
ranh giới "dữ liệu họp chảy vào prompt agent" không bao giờ chạm vào cửa ghi.

Vì sao cần (user chốt 03/08/2026): `get_transcript` trả nguyên văn dạng TEXT vào
chat, cắt thành phần. Đọc trên điện thoại thì dài và không lưu lại được. Đường
tự động (`pipeline.deliver`) có gửi file `.docx`, nhưng chỉ MỘT lần lúc phát —
hỏi lại một cuộc họp cũ thì không có cửa nào lấy file.

NĂM RÀNG BUỘC, cưỡng chế bằng CODE chứ không bằng prompt:

 1. Phải là một cuộc họp CÓ THẬT trong `jobs`, tra bằng `minute_token`.
 2. Người hỏi phải ĐƯỢC XEM cuộc họp đó — dùng đúng `qa._may_see`, KHÔNG dựng
    luật phân quyền thứ hai (hai luật song song sẽ lệch, cái lỏng hơn thắng).
 3. Gửi cho CHÍNH người hỏi, không nhận người nhận tuỳ ý. Đây là ràng buộc quan
    trọng nhất của file này: một tool "gửi file cho <ai đó>" mà agent gọi được
    là một máy phát tán biên bản, và nội dung họp — thứ lái được agent — chảy
    thẳng vào prompt của nó.
 4. Transcript phải CÓ CHỮ. Gửi file rỗng thì người nhận mở ra không thấy gì mà
    mọi chỗ đọc trạng thái đều báo thành công (§31.3).
 5. Chống gửi lặp: cùng người + cùng cuộc họp trong `RESEND_COOLDOWN_MIN` phút
    thì KHÔNG gửi lại. Agent gặp lỗi hay hiểu nhầm là nó thử lại, và không có
    cửa này thì một vòng lặp của agent = hai chục file rơi vào chat người dùng.
"""

from __future__ import annotations

from typing import Any

from . import db, jobstore, lark_api, pipeline, qa

# Đủ dài để chặn một vòng lặp của agent, đủ ngắn để người dùng thật đổi ý và
# xin lại file vẫn được phục vụ trong cùng buổi làm việc.
RESEND_COOLDOWN_MIN = 10

# `kind` riêng trong bảng `deliveries`. KHÔNG dùng lại `full`: bảng đó trả lời
# câu "ai đã nhận biên bản khi hệ thống PHÁT", và `_backfill_deliveries` đọc nó
# để biết còn ai chưa nhận. Nhét lượt gửi theo yêu cầu vào `full` là làm
# backfill tưởng người đó đã được phát rồi, rồi thôi không gửi nữa.
KIND = "ondemand"

NO_MEETING = (
    "Không gửi được: cần `minute_token` của một cuộc họp có thật. Gọi "
    "`list_meetings` hoặc `search_meetings` trước để lấy, rồi gọi lại."
)


def _recent_send(minute_token: str, recipient: str) -> bool:
    """Đã gửi thành công file này cho người này trong cửa sổ chống lặp chưa."""
    row = db.conn().execute(
        "SELECT sent_at FROM deliveries WHERE minute_token=? AND recipient=? "
        "AND kind=? AND ok=1 ORDER BY sent_at DESC LIMIT 1",
        (minute_token, recipient, KIND),
    ).fetchone()
    if not row:
        return False
    import time
    return (time.time() * 1000 - int(row["sent_at"] or 0)) < RESEND_COOLDOWN_MIN * 60_000


def send_transcript(who: dict[str, Any] | None, minute_token: str) -> str:
    """Gửi file .docx nguyên văn cho chính `who`. Trả câu báo cho agent."""
    if not who:
        return qa.NO_ASKER
    minute_token = (minute_token or "").strip()
    if not minute_token:
        return NO_MEETING

    # (1) cuộc họp có thật
    row = jobstore.get(minute_token)
    if not row:
        return NO_MEETING

    # (2) được xem — ĐÚNG bộ lọc của qa, không phải luật thứ hai
    if not qa._may_see(minute_token, who, qa.viewers_index()):
        return ("Cuộc họp này CÓ trong hệ thống nhưng bạn không có trong danh "
                "sách người dự, nên mình không gửi biên bản được. Nếu bạn có dự "
                "thì nhắn quản trị hệ thống — có thể việc tra người dự bị sót.")

    # (3) gửi cho CHÍNH người hỏi. `admin_view` (đường `v2 ask`) không có id nào
    # -> không gửi đi đâu cả, và nói rõ vì sao thay vì im lặng "đã gửi".
    rid = who.get("union_id") or ""
    id_type = "union_id"
    if not rid:
        rid, id_type = who.get("open_id") or "", "open_id"
    if not rid:
        return ("Không gửi được: phiên này không có định danh Lark để gửi tới "
                "(đường terminal/admin). Thử trong Lark.")

    # (5) chống lặp — kiểm TRƯỚC khi sinh file và trước khi upload
    if _recent_send(minute_token, rid):
        return (f"Đã gửi file biên bản cuộc họp này cho bạn trong "
                f"{RESEND_COOLDOWN_MIN} phút vừa rồi — kiểm tra lại trong khung "
                f"chat. KHÔNG gọi lại tool này; nếu người dùng vẫn không thấy "
                f"thì bảo họ nhắn quản trị hệ thống.")

    # (4) phải có chữ. Nạp transcript từ đĩa: `jobs.transcript_path` là nguồn
    # sự thật, file .docx chỉ là bản xuất và có thể chưa từng được tạo (cuộc họp
    # cũ chỉ có .txt, hoặc phát trước khi đổi định dạng — V2_MAINTENANCE §35).
    tpath = row.get("transcript_path")
    if not tpath:
        # CHƯA có transcript whisper. Mô hình KÉO (03/08/2026): thay vì chỉ báo
        # tình trạng, NÂNG cuộc này lên CỰC CAO (priority=2) và ghi người hỏi —
        # vòng `run` dịch nó trước tiên rồi `_deliver_requested` tự gửi cho họ.
        db.add_transcript_request(minute_token, rid, who.get("name", ""))
        st = row.get("status") or ""
        # Job ở trạng thái KHÔNG tự chạy lại (discarded/failed/owner_only/...):
        # kéo về `queued` để được nhặt. Job đang chạy (queued/transcribing/
        # recapping) thì để nguyên, chỉ nâng priority.
        if st not in ("queued", "transcribing", "recapping"):
            jobstore.set_status(minute_token, "queued", error=None)
        jobstore.set_priority(minute_token, 2)
        return ("Cuộc họp này CHƯA có bản transcript chuẩn (whisper). Mình đã "
                "ƯU TIÊN DỊCH NGAY và sẽ TỰ GỬI cho bạn khi xong — bạn không cần "
                "hỏi lại. Cuộc ngắn vài phút, cuộc dài thì lâu hơn. Trong lúc "
                "chờ, bản Minute của Lark xem tạm được. Báo đúng vậy cho người "
                "dùng, đừng bịa là đã gửi.")
    import json
    from .models import Transcript
    try:
        with open(tpath, encoding="utf-8") as f:
            t = Transcript.from_json(json.load(f))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return (f"Có ghi đường dẫn nguyên văn nhưng đọc KHÔNG được ({exc}). "
                f"Đây là lỗi hệ thống, không phải cuộc họp thiếu dữ liệu — báo "
                f"người dùng nhắn quản trị hệ thống.")
    if not (t.text or "").strip():
        return ("Cuộc họp này có file nguyên văn nhưng KHÔNG có chữ nào — "
                "bản ghi im lặng, hoặc whisper hỏng lúc chạy. Không có gì để "
                "gửi; báo người dùng nhắn quản trị hệ thống.")

    meta = jobstore.meta_from_json(row["meta_json"])
    doc = pipeline.write_doc(t, meta)
    try:
        lark_api.im_send_file(rid, doc, id_type=id_type,
                              uuid_key=f"ond-{minute_token}-{rid}"[:50])
    except lark_api.LarkError as exc:
        jobstore.record_delivery(minute_token, rid, KIND, False, str(exc))
        return (f"Gửi file hỏng: {exc}. Nói thẳng với người dùng là chưa gửi "
                f"được, đừng nói đã gửi.")
    jobstore.record_delivery(minute_token, rid, KIND, True)
    return (f"ĐÃ gửi file '{doc.name}' vào khung chat này. Báo ngắn gọn cho "
            f"người dùng là file đã ở trong chat, và nhắc một câu rằng đó là "
            f"bản máy phiên âm nên có lỗi nghe nhầm. KHÔNG chép lại nội dung "
            f"biên bản ra tin nhắn — người dùng xin FILE.")
