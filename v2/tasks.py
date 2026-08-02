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

    minute_token = (minute_token or "").strip()
    row = jobstore.get(minute_token) if minute_token else None
    if not row:
        return NO_MEETING

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
    if due and due_ms is None:
        return ("Không tạo được: hạn phải dạng YYYY-MM-DD (ví dụ 2026-08-15). "
                "Hãy tự quy 'thứ sáu tuần sau' ra ngày cụ thể rồi gọi lại.")

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
