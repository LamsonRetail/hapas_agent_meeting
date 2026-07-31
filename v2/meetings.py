"""
Phát hiện minute mới + tra người được mời (port logic V1 sang Open API).

Chuỗi tra người dự (V1 §6, đã kiểm chứng bằng dữ liệu thật):
    calendar events quanh giờ họp  -> chọn sự kiện trùng tên / gần giờ nhất
    calendar +meeting (event->meeting_id)
    vc recording (meeting_id -> minute_token)   khớp minute_token?
    event attendees list  -> union_id + open_id

Ràng buộc cứng: cuộc họp phải đặt qua Calendar mới có attendees. Họp mở tay
-> rơi về chủ tài khoản (FALLBACK_TO_OWNER).

LƯU Ý: các bước đọc dùng USER TOKEN của người có dự (scope user-specific).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import config, lark_api
from .models import Attendee, MeetingMeta

# Parse chuỗi mô tả minute của Lark (không có trường riêng, phải bóc chuỗi).
_RE_START = re.compile(r"Start time:\s*([\d.]+\s[\d:]+)")
_RE_DURATION = re.compile(r"Duration:\s*(?:(\d+)\s*min)?\s*(?:(\d+)\s*sec)?")
_RE_OWNER = re.compile(r"Owner:\s*(.+?)\s+Start time:")


# Chuỗi "Start time: 2026.07.28 18:25:33" trong description KHÔNG kèm offset.
# Lark phát nó theo UTC+8 (TECHNICAL §4) — đã đối chiếu dữ liệu thật: minute
# 18:25:33 khớp sự kiện lịch 17:30 (+07) khi đọc là +08, lệch 4 phút; nếu đọc
# là UTC thì lệch 8 TIẾNG và mọi phép so "gần giờ nhất" sai hoàn toàn.
_LARK_DESC_TZ = timezone(timedelta(hours=8))


def _epoch(dt: datetime) -> float:
    return dt.replace(tzinfo=_LARK_DESC_TZ).timestamp()


def explain_source(src: str) -> str:
    """Dịch participants_source thành câu người đọc hiểu.

    Trước 30/07/2026 dòng này in trên thẻ duyệt để chủ phòng soi trước khi phát.
    Cửa duyệt đã bỏ -> KHÔNG còn ai kiểm bằng mắt, nên nó chỉ còn để ghi log
    (orchestrator._deliver_now in ra trước mỗi lần phát). Ghép sai sự kiện lịch
    = gửi biên bản cho người không dự họp, và đó là lỗi IM LẶNG. Nếu log hiện
    `calendar[near…]` mà người nhận sai, siết `CAL_STRICT_MINUTES` /
    `CAL_WINDOW_HOURS` trong .env — không phải sửa code.
    """
    if not src:
        return "không rõ nguồn người nhận"
    if src.startswith("calendar[verified]"):
        return "đã xác minh khớp cuộc họp · " + src.split(":", 1)[-1]
    if src.startswith("calendar[title]"):
        return "khớp theo TÊN sự kiện lịch · " + src.split(":", 1)[-1]
    if src.startswith("calendar[near"):
        mins = src.split("near", 1)[1].split("]")[0]
        return f"khớp theo GIỜ (lệch {mins}) · " + src.split(":", 1)[-1]
    if "fallback:owner" in src:
        return "KHÔNG tra được người dự → chỉ gửi cho chủ minute"
    return src


def build_meta(access_token: str, minute_token: str,
               raw_item: dict | None = None) -> MeetingMeta:
    """Dựng MeetingMeta từ minute. raw_item là item từ list (nếu có), nếu
    không thì gọi minutes_get."""
    info = raw_item or lark_api.minutes_get(access_token, minute_token)

    title = (info.get("topic") or info.get("title")
             or info.get("display_info") or "").split("\n", 1)[0].strip()

    start = None
    duration = None
    owner_name = ""

    # HAI nguồn có hình dạng KHÁC NHAU, phải đỡ cả hai:
    #   minutes/search  -> chỉ có chuỗi meta_data.description (phải bóc regex,
    #                      giờ không kèm múi giờ -> đọc theo +08, xem _epoch)
    #   minutes_get     -> có create_time/duration dạng epoch ms tử tế
    # Ưu tiên trường số: chính xác hơn và không phụ thuộc định dạng chuỗi.
    # Trước đây chỉ đọc description nên đường `enqueue --token` (dùng
    # minutes_get) luôn ra start=None -> không bao giờ tra được người dự.
    for key in ("start_time", "create_time"):
        if info.get(key) and start is None:
            try:
                v = float(info[key])
                start = v / 1000 if v > 1e12 else v
            except (ValueError, TypeError):
                pass
    if info.get("duration"):
        try:
            d = float(info["duration"])
            duration = int(d / 1000 if d > 10_000 else d)   # ms hay giây
        except (ValueError, TypeError):
            pass

    desc = (info.get("meta_data", {}) or {}).get("description", "") or ""
    if start is None and (m := _RE_START.search(desc)):
        try:
            start = _epoch(datetime.strptime(m.group(1), "%Y.%m.%d %H:%M:%S"))
        except ValueError:
            pass
    if duration is None and (m := _RE_DURATION.search(desc)):
        if m.group(1) or m.group(2):
            duration = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    if m := _RE_OWNER.search(desc):
        owner_name = m.group(1).strip()

    return MeetingMeta(
        minute_token=minute_token,
        title=title or "(không tiêu đề)",
        start=start,
        duration_sec=duration,
        owner_open_id=info.get("owner_id", "") or "",
        owner_name=owner_name,
        app_link=(info.get("url") or (info.get("meta_data", {}) or {})
                  .get("app_link", "")),
    )


def _meeting_id_via_no(access_token: str, cal_id: str,
                       ev: dict) -> str:
    """meeting_id qua đường `vchat.meeting_url` -> meeting_no -> list_by_no.

    Vì sao cần đường thứ hai: `mget_instance_relation_info` trên tenant này KHÔNG
    trả `meeting_id` — kể cả sau khi token đã có `vc:meeting:readonly` (đã đo
    31/07/2026, hai lần chẩn đoán sai trước đó đều quy cho scope). Đường này đo
    được là chạy: nó lấy số phòng họp từ chi tiết sự kiện rồi tra cuộc họp VC
    trong cửa sổ ±CAL_WINDOW_HOURS quanh sự kiện.

    Trả "" khi: sự kiện không có VC, thiếu quyền, hoặc số phòng dùng lại cho
    nhiều cuộc (khi đó caller vẫn phải khớp recording nên không sai được).
    """
    try:
        detail = lark_api.event_get(access_token, cal_id, ev["event_id"])
    except lark_api.LarkError as exc:
        print(f"[meetings] không đọc được chi tiết sự kiện ({exc})")
        return ""
    no = lark_api.meeting_no_of(detail)
    if not no:
        return ""                     # sự kiện không có phòng họp online

    st = ev.get("start_time", {}).get("timestamp")
    try:
        center = int(st) if st else 0
    except (TypeError, ValueError):
        center = 0
    if not center:
        return ""
    half = config.CAL_WINDOW_HOURS * 3600
    try:
        metts = lark_api.vc_meetings_by_no(
            access_token, no, center - half, center + half)
    except lark_api.LarkError as exc:
        print(f"[meetings] list_by_no hỏng ({exc}) — không xác minh được")
        return ""
    # Nhiều cuộc cùng số phòng (phòng cá nhân dùng lại) -> trả cái đầu, việc
    # khớp minute_token ở caller sẽ loại cái sai.
    for m in metts:
        mid = m.get("id") or m.get("meeting_id") or ""
        if mid:
            return mid
    return ""


def _recording_matches(access_token: str, meeting_id: str,
                       minute_token: str) -> bool:
    """Bản ghi của cuộc họp này có đúng minute_token đang xử lý không."""
    try:
        rec = lark_api.vc_meeting_recording(access_token, meeting_id)
    except lark_api.LarkError:
        return False
    url = rec.get("url", "") or rec.get("minute_url", "")
    return minute_token in url or rec.get("minute_token") == minute_token


def resolve_participants(access_token: str, meta: MeetingMeta) -> MeetingMeta:
    """Điền meta.attendees. Đổi meta tại chỗ và trả lại nó.

    Không tra được thì để attendees rỗng và ghi participants_source.
    """
    if not meta.start:
        meta.participants_source = "no_start_time"
        return meta

    lo = int(meta.start - config.CAL_WINDOW_HOURS * 3600)
    hi = int(meta.start + config.CAL_WINDOW_HOURS * 3600)

    try:
        cal_id = lark_api.calendar_primary(access_token)
        events = lark_api.calendar_events(access_token, cal_id, lo, hi)
    except lark_api.LarkError as exc:
        print(f"[meetings] đọc lịch hỏng: {exc}")
        meta.participants_source = "agenda_failed"
        return meta

    if not events:
        meta.participants_source = "no_calendar_event"
        return meta

    title = meta.title.strip().lower()

    def ev_start(ev: dict) -> float | None:
        """Giờ bắt đầu lần diễn ra này (epoch giây). instance_view trả ISO."""
        st = ev.get("start_time") or {}
        if st.get("datetime"):
            try:
                return datetime.fromisoformat(st["datetime"]).timestamp()
            except ValueError:
                pass
        try:
            return float(st["timestamp"])
        except (KeyError, ValueError, TypeError):
            return None

    def gap_of(ev: dict) -> float:
        s = ev_start(ev)
        return 1e9 if s is None else abs(s - meta.start)

    def rank(ev: dict) -> tuple[int, float]:
        same = 0 if (ev.get("summary") or "").strip().lower() == title else 1
        return (same, gap_of(ev))

    # Chốt cứng cửa sổ ở phía mình: KHÔNG tin API đã lọc đúng. Sự kiện lệch quá
    # CAL_WINDOW_HOURS thì không thể là cuộc họp này, dù xếp hạng có chọn nó.
    window = config.CAL_WINDOW_HOURS * 3600
    cands = [e for e in events if e.get("event_id") and gap_of(e) <= window]
    if not cands:
        meta.participants_source = "no_event_in_window"
        return meta
    cands = sorted(cands, key=rank)[:config.MAX_EVENTS_TO_CHECK]

    # Xác minh THẬT: event -> meeting_id -> recording -> minute_token.
    # Một lời gọi cho cả lô, thay vì mỗi sự kiện một lời gọi.
    try:
        mids = lark_api.event_meeting_ids(
            access_token, cal_id, [e["event_id"] for e in cands])
    except lark_api.LarkError as exc:
        print(f"[meetings] không lấy được meeting_id ({exc})")
        mids = {}

    strict = config.CAL_STRICT_MINUTES * 60

    for ev in cands:
        event_id = ev["event_id"]
        name = ev.get("summary") or event_id
        gap_min = gap_of(ev) / 60
        same_title = (ev.get("summary") or "").strip().lower() == title

        # --- quyết định có TIN sự kiện này không (fail-closed) ---
        # Hai đường xác minh, cùng đích: chứng minh sự kiện này sinh ra ĐÚNG
        # minute_token đang xử lý.
        #   (a) mget_instance_relation_info -> meeting_id. Rẻ (1 lời gọi cả lô)
        #       nhưng tenant này KHÔNG trả meeting_id, kể cả sau khi đã có
        #       `vc:meeting:readonly` ở danh tính user (đo 31/07/2026). Vẫn thử
        #       trước vì tenant khác có thể trả.
        #   (b) vchat.meeting_url -> meeting_no -> list_by_no -> meeting_id.
        #       Tốn 2-3 lời gọi mỗi sự kiện nhưng CHẠY THẬT trên tenant này.
        meeting_id = mids.get(event_id, "")
        if not meeting_id:
            meeting_id = _meeting_id_via_no(access_token, cal_id, ev)
        if meeting_id and _recording_matches(access_token, meeting_id,
                                             meta.minute_token):
            how = "verified"
        elif meeting_id:
            # Xác minh được mà KHÔNG khớp = cuộc họp khác. Bỏ, không đoán tiếp.
            continue
        elif same_title or gap_min * 60 <= strict:
            # Không xác minh được (sự kiện không có VC, hoặc API không trả).
            # Chỉ chấp nhận khi bằng chứng gián tiếp đủ mạnh: trùng CHÍNH XÁC
            # tên, hoặc sát giờ. Ngoài ra thì THÀ KHÔNG BIẾT còn hơn gửi biên
            # bản cho sai người (V1 fail-closed; V2 trước đây fail-open và đã
            # ghép một minute thử 59 giây với buổi đào tạo cách 33 tiếng).
            how = "title" if same_title else f"near{gap_min:.0f}m"
        else:
            continue

        try:
            atts = lark_api.event_attendees(access_token, cal_id, event_id,
                                            id_type="union_id")
            atts_open = lark_api.event_attendees(access_token, cal_id, event_id,
                                                 id_type="open_id")
        except lark_api.LarkError:
            continue
        unions = [a.get("user_id", "") for a in atts if a.get("type") == "user"]
        opens = [a.get("user_id", "") for a in atts_open
                 if a.get("type") == "user"]

        if unions or opens:
            # Ghép theo thứ tự trả về từ cùng sự kiện.
            n = max(len(unions), len(opens))
            meta.attendees = [
                Attendee(open_id=opens[i] if i < len(opens) else "",
                         union_id=unions[i] if i < len(unions) else "")
                for i in range(n)
            ]
            meta.participants_source = f"calendar[{how}]:{name}"
            return meta

    meta.participants_source = "no_match"
    return meta
