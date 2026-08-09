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

# Tiền tố đánh dấu một `participants_source` đã bị CÁCH LY: nó được ghép chỉ vì
# gần giờ và không được dùng làm bằng chứng tham dự nữa. Giữ chuỗi gốc phía sau
# để còn truy vết được nó từng ghép vào sự kiện nào.
#
# ⚠️ Các phép kiểm ACL tìm `"calendar[near" in src` (chuỗi con, KHÔNG phải
# `startswith`) chính là vì tiền tố này đẩy `calendar[near…]` vào GIỮA chuỗi —
# xem `jobstore.release_waiting_auth` và `orchestrator._explicit_viewer`.
REJECTED_PREFIX = "unsafe_near_rejected:"


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
    if src.startswith(REJECTED_PREFIX):
        # Nguồn bị CÁCH LY ngày 05/08/2026: ghép chỉ vì gần giờ, đã kéo nhầm 22
        # người của một cuộc khác vào ACL. Phải dịch ở đây, nếu không cột `Nguồn
        # người nhận` trên Base hiện nguyên chuỗi máy `unsafe_near_rejected:…`
        # — người vận hành đọc không ra, mà đây đúng là ô họ nhìn để trả lời
        # "vì sao người này nhận được biên bản". Nói RÕ là đã loại: bản thân
        # dòng "khớp theo GIỜ" đọc như một nguồn hợp lệ.
        return ("ĐÃ LOẠI (ghép chỉ vì gần giờ — KHÔNG cấp quyền cho ai) · "
                + explain_source(src[len(REJECTED_PREFIX):]))
    if src.startswith("calendar[verified]"):
        return "đã xác minh khớp cuộc họp · " + _tail(src)
    if src.startswith("calendar[title]"):
        return "khớp theo TÊN sự kiện lịch · " + _tail(src)
    if src.startswith("calendar[near"):
        mins = src.split("near", 1)[1].split("]")[0]
        return f"khớp theo GIỜ (lệch {mins}) · " + _tail(src)
    if "fallback:owner" in src:
        return "KHÔNG tra được người dự → chỉ gửi cho chủ minute"
    return src


def _tail(src: str) -> str:
    """Phần sau dấu ':' của participants_source, dịch các hậu tố sang tiếng Việt.

    Hậu tố do `resolve_participants` gắn: `+vcN` (gộp thêm N người thật sự vào
    phòng họp) và `-declineN` (đã bỏ N người từ chối lời mời). Đây là hai con số
    người vận hành cần thấy ngay trên dòng log `[deliver]` — chúng nói "danh
    sách người nhận vừa bị sửa so với lời mời trên lịch", và đó chính là câu hỏi
    đầu tiên khi ai đó thắc mắc vì sao mình nhận / không nhận biên bản.
    """
    tail = src.split(":", 1)[-1]
    for token, fmt in (("+vc", "+{n} người vào họp thật"),
                       ("-decline", "bỏ {n} người đã từ chối")):
        if token not in tail:
            continue
        head, _, rest = tail.partition(token)
        n = ""
        while rest and rest[0].isdigit():
            n, rest = n + rest[0], rest[1:]
        tail = f"{head.rstrip()} · {fmt.format(n=n)}{rest}"
    return tail


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

    # CHỦ BẢN GHI: `minutes/search` KHÔNG trả `owner_id` (đo 02/08/2026 — item
    # chỉ có `display_info`, `meta_data`, `token`), nên đường polling luôn ra
    # rỗng và `enqueue_minute` lấp bằng NGƯỜI PHÁT HIỆN. Tức trước đây
    # `owner_open_id` nghĩa là "chủ bản ghi HOẶC người tình cờ quét thấy" — hai
    # thứ khác hẳn nhau, và không có gì báo là nó đang là cái thứ hai.
    #
    # Ba chỗ bị sai theo:
    #   - `pipeline._reader_candidates` ưu tiên nhầm người (docstring nói "ưu
    #     tiên chủ minute" nhưng thực tế đưa người phát hiện lên đầu) — mà chủ
    #     bản ghi lại là người DUY NHẤT chắc chắn tải được (xem ma trận quyền).
    #   - cột `Chủ cuộc họp` / `user_id chủ` trên Base ghi tên người phát hiện.
    #   - câu báo lỗi khi thiếu quyền tải chỉ tay nhầm người cần đi nhờ.
    #
    # `minutes_get` CÓ trả `owner_id`, nên hỏi thêm một lời gọi. Chỉ một lần cho
    # mỗi cuộc họp mới, không đáng kể. Hỏng thì để rỗng và caller phải fail-closed;
    # tuyệt đối không lấp bằng người phát hiện vì họ có thể chỉ được share bản ghi.
    owner_open_id = info.get("owner_id", "") or ""
    if not owner_open_id and raw_item is not None:
        try:
            owner_open_id = (lark_api.minutes_get(access_token, minute_token)
                             .get("owner_id", "") or "")
        except lark_api.LarkError as exc:
            print(f"[meetings] không tra được chủ bản ghi {minute_token} ({exc})")

    # Gỡ escape HTML (04/08/2026): Lark Minutes trả `Review HRIS &amp; feedback`
    # và `Lê Quý Thiện - Technical &amp; AI Automation Leader`. Hai chuỗi này đi
    # thẳng vào Base rồi chảy ra BẢY đường người dùng đọc — thẻ biên bản, cảnh
    # báo DM, Lark Task, file .docx, sendfile, cột Base, bot hỏi đáp. Gỡ ở ĐÂY,
    # tức nguồn, thay vì vá bảy chỗ hiển thị. Dữ liệu cũ đã dọn một lần bằng tay.
    import html as _html

    return MeetingMeta(
        minute_token=minute_token,
        title=_html.unescape(title) if title else "(không tiêu đề)",
        start=start,
        duration_sec=duration,
        owner_open_id=owner_open_id,
        owner_name=_html.unescape(owner_name),
        app_link=(info.get("url") or (info.get("meta_data", {}) or {})
                  .get("app_link", "")),
    )


# Tối đa bao nhiêu cuộc họp VC được thử khớp recording cho MỘT sự kiện lịch.
# Mỗi ứng viên tốn một lời gọi `vc_meeting_recording`, và số cuộc dùng chung một
# số phòng trong ±CAL_WINDOW_HOURS thực tế chỉ vài cái.
MAX_MEETING_CANDIDATES = 5


def _meeting_ids_via_no(access_token: str, cal_id: str,
                        ev: dict) -> list[str]:
    """MỌI meeting_id dùng chung số phòng của sự kiện này, trong cửa sổ giờ.

    Đường `vchat.meeting_url` -> meeting_no -> list_by_no. Cần đường thứ hai này
    vì `mget_instance_relation_info` trên tenant này KHÔNG trả `meeting_id` — kể
    cả sau khi token đã có `vc:meeting:readonly` (đã đo 31/07/2026, hai lần chẩn
    đoán sai trước đó đều quy cho scope).

    Trả DANH SÁCH, không trả một cái (sửa 02/08/2026). Trước đó hàm này trả
    `metts[0]` và caller coi "có meeting_id mà recording không khớp" = sự kiện
    khác -> `continue`. Với phòng họp cá nhân dùng lại (rất phổ biến: 2-3 cuộc
    liên tiếp cùng một số phòng) thì ứng viên đầu tiên thường KHÔNG phải cuộc
    đang xử lý, và cả sự kiện đúng cũng bị bỏ — mất luôn danh sách người dự,
    rơi về `near…`/`fallback:owner`. Đo thật: 0/5 job đạt `verified`.

    Trả [] khi: sự kiện không có VC, thiếu quyền, hoặc không đọc được giờ.
    """
    try:
        detail = lark_api.event_get(access_token, cal_id, ev["event_id"])
    except lark_api.LarkError as exc:
        print(f"[meetings] không đọc được chi tiết sự kiện ({exc})")
        return []
    no = lark_api.meeting_no_of(detail)
    if not no:
        return []                     # sự kiện không có phòng họp online

    st = ev.get("start_time", {}).get("timestamp")
    try:
        center = int(st) if st else 0
    except (TypeError, ValueError):
        center = 0
    if not center:
        return []
    half = config.CAL_WINDOW_HOURS * 3600
    try:
        metts = lark_api.vc_meetings_by_no(
            access_token, no, center - half, center + half)
    except lark_api.LarkError as exc:
        print(f"[meetings] list_by_no hỏng ({exc}) — không xác minh được")
        return []
    out = []
    for m in metts:
        mid = m.get("id") or m.get("meeting_id") or ""
        if mid and mid not in out:
            out.append(mid)
    return out


def _attendee_ids(items: list[dict]) -> tuple[list[str], int]:
    """(user_id của người được mời còn hiệu lực, số người ĐÃ TỪ CHỐI bị loại).

    Lọc `rsvp_status == "decline"` (thêm 02/08/2026). Trước đó người bấm "Từ
    chối" trên lời mời vẫn nhận NGUYÊN VĂN transcript — và vì cửa duyệt đã bỏ,
    không còn ai chặn được việc đó bằng mắt. Đây là đường rò nội dung rẻ nhất mà
    hệ thống có: mời nhầm một người vào cuộc họp lương, họ từ chối, vẫn nhận
    toàn văn.

    CỐ Ý không lọc `needs_action` / `tentative`: chưa bấm nút không có nghĩa là
    không dự — họp nội bộ hầu như không ai bấm. Chỉ `decline` mới là lời từ chối
    tường minh, và chỉ nó mới đủ cơ sở để cắt người ra khỏi danh sách.
    """
    keep, dropped = _attendee_map(items)
    return list(keep.values()), dropped


def _attendee_map(items: list[dict]) -> tuple[dict[str, str], int]:
    """({attendee_id: user_id} còn hiệu lực, số người ĐÃ TỪ CHỐI bị loại).

    `attendee_id` là khoá ghép giữa hai lời gọi `event_attendees` khác
    `user_id_type` — đo thật 02/08/2026 trên sự kiện 11 người: cùng một người
    có `attendee_id` GIỐNG HỆT ở cả lời gọi `union_id` lẫn `open_id`
    (`user_7534535284232880160`), chỉ `user_id` là đổi dạng. Trước đó code ghép
    hai danh sách THEO THỨ TỰ, mà Lark không hứa gì về thứ tự.

    Lọc `type != "user"` ở đây cũng là chỗ loại phòng họp (`resource_…`) và
    group chat — cùng lý do làm `event_attendees` trả 11 item cho 10 người.
    """
    keep: dict[str, str] = {}
    dropped = 0
    for a in items:
        if a.get("type") != "user":
            continue
        if str(a.get("rsvp_status") or "").lower() == "decline":
            dropped += 1
            continue
        uid = a.get("user_id") or ""
        if not uid:
            continue
        # Không có `attendee_id` thì tự chế một khoá không đụng ai: thà mất
        # cặp union/open của riêng người đó còn hơn ghép nhầm sang người khác.
        keep[str(a.get("attendee_id") or f"_noid:{uid}")] = uid
    return keep, dropped


def _vc_joiners(access_token: str, meeting_id: str) -> list[str]:
    """union_id của người THẬT SỰ vào phòng họp. [] nếu không đọc được.

    Vì sao cần (02/08/2026): danh sách lịch là người được MỜI, không phải người
    DỰ. Người được nhờ dự thay, người vào bằng link, người được kéo vào giữa
    chừng — không ai trong số đó có trên lịch. Trước đây họ vừa không nhận biên
    bản, vừa bị bot GIẤU luôn cuộc họp đó (`qa.viewers_index` dùng đúng danh
    sách này), nên họ còn không biết là mình đang thiếu.

    Gộp thêm chứ không thay thế: người được mời mà hôm đó bận không vào vẫn nên
    nhận biên bản.

    CHỈ lấy union_id, có chủ ý. Ghép union_id với open_id từ hai lời gọi API
    khác nhau THEO THỨ TỰ là cái bẫy đang còn mở ở nhánh lịch bên dưới
    (V2_HANDOFF §4.5 mục 11b) — đừng nhân nó lên ở đây. union_id đủ cho cả hai
    chỗ dùng tới danh sách này: phát biên bản (`pipeline.deliver` gửi theo
    union_id) và phân quyền hỏi đáp (`qa.viewers_index` hợp cả hai loại id).

    Bỏ qua phòng Rooms / người gọi vào bằng điện thoại (`user_type != 1`, không
    nhắn tin được) và người ngoài tenant (`is_external`, bot không phát hành cho
    họ) — gửi cho hai nhóm đó chỉ sinh ra dòng `deliveries` hỏng.
    """
    out: list[str] = []
    for p in lark_api.vc_meeting_participants(access_token, meeting_id,
                                              id_type="union_id"):
        if p.get("is_external"):
            continue
        ut = p.get("user_type")
        if ut is not None:
            try:
                if int(ut) != 1:
                    continue
            except (TypeError, ValueError):
                pass
        uid = p.get("id") or p.get("user_id") or ""
        if uid and uid not in out:
            out.append(uid)
    return out


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
        #
        # Thử MỌI ứng viên rồi mới kết luận (sửa 02/08/2026): xem docstring
        # `_meeting_ids_via_no`. Ngữ nghĩa fail-closed KHÔNG đổi — có ứng viên
        # mà không cái nào sinh ra minute này thì vẫn là sự kiện khác, vẫn bỏ.
        cand_mids = [mids[event_id]] if mids.get(event_id) else []
        cand_mids += [m for m in _meeting_ids_via_no(access_token, cal_id, ev)
                      if m not in cand_mids]

        meeting_id = ""
        for mid in cand_mids[:MAX_MEETING_CANDIDATES]:
            if _recording_matches(access_token, mid, meta.minute_token):
                meeting_id = mid
                break

        if meeting_id:
            how = "verified"
        elif cand_mids:
            # Xác minh được mà KHÔNG khớp = cuộc họp khác. Bỏ, không đoán tiếp.
            continue
        elif same_title:
            # Không xác minh được recording (sự kiện không có VC, hoặc API
            # không trả): chỉ tên khớp CHÍNH XÁC mới đủ dùng. Gần giờ đơn thuần
            # là không an toàn — dữ liệu thật 05/08/2026 đã ghép `Daily CDP
            # Checkin` với `HAPAS | PROJECT TRANG SỨC...` cách 19 phút và kéo
            # nhầm 22 attendee vào ACL. Thà bỏ sót còn hơn lộ biên bản.
            how = "title"
        else:
            continue

        try:
            atts = lark_api.event_attendees(access_token, cal_id, event_id,
                                            id_type="union_id")
            atts_open = lark_api.event_attendees(access_token, cal_id, event_id,
                                                 id_type="open_id")
        except lark_api.LarkError:
            continue
        umap, declined = _attendee_map(atts)
        omap, _ = _attendee_map(atts_open)
        unions, opens = list(umap.values()), list(omap.values())

        # Người thật sự vào phòng họp, gộp thêm vào người được mời. Chỉ làm
        # được khi đã xác minh (`verified`) vì chỉ khi đó ta mới có meeting_id
        # CHẮC CHẮN thuộc về minute này — lấy người dự của một cuộc họp đoán mò
        # là phát biên bản cho người của cuộc họp khác.
        extra = [u for u in _vc_joiners(access_token, meeting_id)
                 if u not in unions] if meeting_id else []

        if not (unions or opens or extra):
            continue

        # Ghép theo `attendee_id`, KHÔNG theo thứ tự (mục 11b — đóng
        # 02/08/2026). Lark không hứa hai lời gọi trả cùng thứ tự; hôm nay
        # chúng trùng nhau nên lỗi này vô hình, và nó sẽ chỉ lộ ra vào đúng
        # ngày ai đó ghép hai id thành MỘT người rồi gửi nhầm.
        attendees = [Attendee(open_id=omap.get(k, ""), union_id=u)
                     for k, u in umap.items()]
        # Người chỉ xuất hiện ở lời gọi open_id: giữ lại, đứng riêng. Không có
        # union_id thì không gửi được, nhưng `_reader_candidates` vẫn mượn được
        # token của họ để tải bản ghi.
        le = [Attendee(open_id=o) for k, o in omap.items() if k not in umap]
        if le:
            print(f"[meetings] {event_id}: {len(le)} người chỉ có ở lời gọi "
                  f"open_id (union={len(umap)} open={len(omap)}) — giữ riêng, "
                  f"không ghép bừa")
            attendees += le

        attendees += [Attendee(union_id=u) for u in extra]

        meta.attendees = attendees
        src = f"calendar[{how}]:{name}"
        if extra:
            src += f" +vc{len(extra)}"
        if declined:
            src += f" -decline{declined}"
        meta.participants_source = src
        return meta

    meta.participants_source = "no_match"
    return meta
