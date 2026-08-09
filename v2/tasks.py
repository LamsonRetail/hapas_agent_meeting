"""Tạo Lark Task từ nội dung cuộc họp — đường GHI duy nhất của bot hỏi đáp.

Vì sao KHÔNG đặt trong `qa.py`: file đó có bất biến "chỉ đọc, đừng thêm hàm
ghi/xoá" (một trong ba điều không được làm sai — V2_HANDOFF §5). Bất biến đó tồn
tại vì dữ liệu Base bắt nguồn từ lời người ta nói trong họp và chảy thẳng vào
prompt của agent. Thêm hàm ghi vào đó là xoá ranh giới; để riêng ở đây thì ranh
giới còn nguyên và mọi rủi ro ghi nằm gọn trong một file đọc được hết trong
một lần.

BỐN RÀNG BUỘC, cưỡng chế bằng CODE chứ không bằng prompt (prompt nào cũng có thể
bị nội dung họp lái đi):

 1. Phải gắn với một cuộc họp CÓ THẬT trong `jobs`. Không có `minute_token` hợp
    lệ thì không tạo được task. Đây là chỗ "bot chỉ làm việc liên quan cuộc họp"
    trở thành sự thật kỹ thuật, không phải lời dặn.
 2. Người hỏi phải ĐƯỢC XEM cuộc họp đó (đúng bộ lọc của `qa`). Không thì họ
    tạo được task đính kèm link/tiêu đề cuộc họp không phải của mình.
 3. Giao cho CHÍNH người hỏi. Không nhận assignee tuỳ ý: tên người trong
    transcript là dữ liệu không tin cậy, và "bot tự giao việc cho đồng nghiệp
    theo lời một bản phiên âm nhiễu" là thứ hỏng rất khó gỡ. Muốn mở rộng thì
    phải giới hạn trong danh sách người dự của ĐÚNG cuộc họp đó, và nên hỏi
    người dùng xác nhận trước — chưa làm.
 4. Tạo bằng USER TOKEN của người hỏi. Bot không bao giờ làm được nhiều hơn
    quyền người đó vốn có, và trên Lark task hiện đúng người tạo.

Luôn nhét nguồn gốc vào description: cuộc họp nào, link Minutes nào. Task sinh
từ máy mà không nói rõ nó từ đâu thì vài hôm sau không ai dám tin nó.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import jobstore, lark_api, qa, tokenstore

_TZ = timezone(timedelta(hours=7))

# Câu từ chối khi không đủ điều kiện — nói RÕ vì sao, để agent không thử lại mù.
NO_MEETING = (
    "Không tạo được: task phải gắn với một cuộc họp cụ thể. Hãy gọi "
    "`list_meetings` hoặc `search_meetings` trước để lấy đúng `minute_token`, "
    "rồi gọi lại. Bot này chỉ tạo task cho việc phát sinh từ cuộc họp."
)


def _parse_due(due: str) -> int | None:
    """'YYYY-MM-DD' -> epoch MILI GIÂY của nửa đêm **UTC** ngày đó. Sai -> None.

    Vì sao nửa đêm UTC chứ không phải nửa đêm giờ VN: task hạn-cả-ngày của Lark
    (`is_all_day=True`) neo theo UTC. Gửi nửa đêm +07 thì Lark làm tròn xuống và
    hạn LÙI MỘT NGÀY — đo thật 01/08/2026, xem `lark_api.task_create`. Đây là lỗi
    im lặng: Lark trả code=0, task tạo ra bình thường, chỉ sai ngày.

    Không đoán ngày kiểu 'thứ sáu tuần sau': để agent tự quy ra ngày cụ thể rồi
    truyền vào. Máy đoán ngày là chỗ sai lặng lẽ, mà hạn sai thì người ta trễ việc.
    """
    due = (due or "").strip()
    if not due:
        return None
    try:
        d = datetime.strptime(due, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(d.timestamp()) * 1000


def create_from_meeting(who: dict[str, Any] | None, minute_token: str,
                        summary: str, *, due: str = "",
                        note: str = "") -> str:
    """Tạo task gắn với một cuộc họp. Trả câu trả lời cho người dùng."""
    if not who:
        return qa.NO_ASKER
    summary = (summary or "").strip()
    if not summary:
        return "Không tạo được: thiếu nội dung công việc."

    # Nhận cả TÊN cuộc họp, không chỉ `minute_token` — dùng CHUNG bộ giải tên
    # với `send_transcript_file` (xem `sendfile._resolve_token`, §40.1). Cùng
    # một agent, cùng một thói quen truyền tên; sửa một chỗ mà bỏ chỗ kia là để
    # dành đúng lỗi đó cho lần sau. Cửa quyền bên dưới KHÔNG đổi.
    from . import sendfile
    minute_token, _err_tok = sendfile._resolve_token(who, minute_token)
    row = jobstore.get(minute_token) if minute_token else None
    if not row:
        return _err_tok or NO_MEETING

    # Ràng buộc 2: đúng bộ lọc của qa, không dựng luật thứ hai song song —
    # hai luật phân quyền sẽ lệch nhau, và cái lỏng hơn sẽ thắng.
    if not qa._may_see(minute_token, who, qa.viewers_index()):
        return ("Không tạo được: bạn không có trong danh sách người dự của cuộc "
                "họp này nên không tạo task cho nó được.")

    open_id = who.get("open_id") or ""
    if not open_id:
        return qa.NO_ASKER
    try:
        token = tokenstore.get_access_token(open_id)
    except tokenstore.TokenError as exc:
        return (f"Không tạo được: quyền của bạn với hệ thống đã hết hạn ({exc}). "
                f"Nhắn quản trị hệ thống để lấy link cấp quyền lại.")

    try:
        meta = jobstore.meta_from_json(row["meta_json"])
    except Exception:                          # noqa: BLE001 — job cũ méo dữ liệu
        return NO_MEETING

    due_ms = _parse_due(due)
    _today = datetime.now(_TZ).date()
    if due and due_ms is None:
        return (f"Không tạo được: hạn phải dạng YYYY-MM-DD (ví dụ 2026-08-15). "
                f"HÔM NAY là {_today:%Y-%m-%d} — tự quy 'thứ sáu tuần này' ra "
                f"ngày cụ thể rồi gọi lại.")
    # Hạn nằm trong QUÁ KHỨ (thêm 04/08/2026 sau ca thật): agent quy "thứ sáu
    # tuần này" ra 31/07 trong khi hôm nay đã 04/08 — nhiều khả năng nó neo vào
    # ngày CUỘC HỌP chứ không phải hôm nay. `_parse_due` cố ý không đoán ngày,
    # nhưng cũng không kiểm, nên task sẽ được tạo với hạn đã trôi qua: Lark trả
    # code=0, task hiện ra bình thường, chỉ là quá hạn ngay lúc sinh ra — đúng
    # loại hỏng im lặng mà cả sổ tay này viết ra để chặn.
    #
    # Câu từ chối PHẢI kèm ngày hôm nay: agent không tự biết hôm nay là ngày
    # nào, và dặn suông "tính lại cho đúng" thì nó tính sai y như cũ (§40.1 —
    # chỉ thị bằng lời không điều khiển được agent, dữ liệu thì có).
    if due_ms is not None:
        _due_date = datetime.fromtimestamp(due_ms / 1000, timezone.utc).date()
        if _due_date < _today:
            return (f"Không tạo được: hạn {due} ĐÃ QUA — hôm nay là "
                    f"{_today:%Y-%m-%d}. Đừng neo vào ngày diễn ra cuộc họp; "
                    f"tính hạn từ HÔM NAY rồi gọi lại. Người dùng thật sự muốn "
                    f"hạn trong quá khứ thì bảo họ tự sửa trong Lark Task.")

    parts = [f"Từ cuộc họp: {meta.title or '(không tiêu đề)'}"]
    if meta.app_link:
        parts.append(f"Biên bản gốc: {meta.app_link}")
    if note.strip():
        parts.append(note.strip())
    parts.append("(Task này do trợ lý biên bản họp tạo theo yêu cầu của bạn.)")

    try:
        task = lark_api.task_create(
            token, summary, description="\n".join(parts),
            due_ms=due_ms, all_day=True, assignee_open_id=open_id)
    except lark_api.LarkError as exc:
        print(f"[task] tạo hỏng cho {who.get('name')}: {exc}")
        return f"Không tạo được task ({exc}). Thử lại sau hoặc báo quản trị."

    guid = task.get("guid") or ""
    url = task.get("url") or ""
    out = [f"Đã tạo task: {summary}"]
    if due_ms:
        out.append(f"Hạn: {due}")
    out.append(f"Giao cho: {who.get('name') or 'bạn'}")
    out.append(f"Gắn với cuộc họp: {meta.title}")
    if url:
        out.append(f"Mở task: {url}")
    elif guid:
        out.append(f"task guid: {guid}")
    return "\n".join(out)
