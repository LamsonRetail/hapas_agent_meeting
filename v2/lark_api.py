"""
Gom TẤT CẢ lời gọi Lark Open API vào một module (V2_LONGTERM §7.4).

Lark sẽ đổi API — khi đó chỉ sửa file này, không rải requests khắp nơi.

Hai chế độ auth:
    - tenant token (app-level): để BOT gửi tin. Không phụ thuộc người dùng.
    - user token (OAuth):       để ĐỌC minute/calendar/vc bằng danh tính của
                                người có dự (scope user-specific).

Không tự cache/refresh user token ở đây — đó là việc của tokenstore.py.
Module này nhận access_token đã sẵn sàng và chỉ gọi API.

LƯU Ý KIỂM CHỨNG: các endpoint đọc minutes/calendar/vc bên dưới theo tài
liệu Open API. Phải test với tenant thật trước khi tin (V2_ARCHITECTURE §8).
Endpoint nào chưa chắc được đánh dấu [VERIFY].
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from . import config


class LarkError(RuntimeError):
    def __init__(self, code: int, msg: str, where: str = ""):
        self.code = code
        self.msg = msg
        super().__init__(f"{where} lỗi {code}: {msg}")


_client: httpx.Client | None = None

# =====================================================================
#  Thử lại khi Lark chập — CHỈ cho lời gọi ĐỌC
# =====================================================================
#
# Vì sao cần (02/08/2026): trước bản này không có một dòng retry nào trong cả
# file. Mà `meetings.resolve_participants` cho MỘT cuộc họp có thể tốn hàng
# chục lời gọi (tới `MAX_EVENTS_TO_CHECK`=12 sự kiện × (event_get +
# list_by_no + tới 5 lần recording) + 2 lần event_attendees +
# vc_meeting_participants). Một cú 429 hay 502 lẻ ở giữa chuỗi đó không làm
# hỏng job — nó `continue` hoặc trả `agenda_failed`, rồi job được tạo với
# danh sách người dự SAI và giữ nguyên như vậy mãi (xem
# `orchestrator._maybe_reresolve`). Lỗi im lặng, đúng loại đắt nhất ở đây.
#
# CHỈ ĐỌC, có chủ ý. Thử lại một POST là rủi ro gửi trùng, mà trong file này
# POST gồm cả `im_send_card` / `im_send_file` — phát biên bản hai lần cho cả
# phòng họp là thứ ai cũng nhìn thấy. `uuid_key` có chống trùng ở phía Lark
# nhưng không phải mọi endpoint đều nhận nó, nên không dựa vào đó.
#
# Ba endpoint ĐỌC lại là POST theo thiết kế của Lark (`minutes/search`,
# `calendars/primary`, `mget_instance_relation_info`). Chúng tự khai bằng
# header `_READ_HDR`; transport đọc cờ đó rồi bỏ header đi trước khi gửi.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_CALLS = 3            # tổng số LẦN GỌI, không phải số lần thử lại
_RETRY_BASE_S = 0.5
_RETRY_CAP_S = 8.0
_READ_HDR = "x-v2-read"     # POST tự khai "tôi chỉ đọc, thử lại được"

# Header đánh dấu POST-chỉ-đọc. Dùng dict hằng để chỗ gọi không gõ sai tên.
READ_ONLY = {_READ_HDR: "1"}


def _retry_after_s(resp: httpx.Response, attempt: int) -> float:
    """Chờ bao lâu trước lần gọi sau. Ưu tiên `Retry-After` của Lark.

    Lark biết rõ hơn ta khi nào hết bóp; chỉ khi nó không nói mới tự lùi theo
    cấp số nhân. Chặn trên `_RETRY_CAP_S` để một header hỏng (`Retry-After:
    3600`) không treo cả vòng `run`.
    """
    raw = resp.headers.get("retry-after", "")
    if raw.strip().isdigit():
        return min(float(raw.strip()), _RETRY_CAP_S)
    return min(_RETRY_BASE_S * (2 ** attempt), _RETRY_CAP_S)


class _RetryTransport(httpx.HTTPTransport):
    """Thử lại 429/5xx cho GET (và POST đã tự khai là chỉ đọc).

    Đặt ở tầng transport chứ không bọc từng lời gọi: file này có 35 chỗ gọi
    `_http()`, sửa từng chỗ là chắc chắn sót, và chỗ sót sẽ là chỗ im lặng.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        may_retry = (request.method == "GET"
                     or request.headers.get(_READ_HDR) == "1")
        request.headers.pop(_READ_HDR, None)     # đừng gửi header nội bộ ra ngoài

        resp = super().handle_request(request)
        if not may_retry:
            return resp
        for attempt in range(_RETRY_CALLS - 1):
            if resp.status_code not in _RETRY_STATUS:
                return resp
            wait = _retry_after_s(resp, attempt)
            # PHẢI đọc hết rồi đóng: bỏ một response chưa đọc là giữ lại
            # connection trong pool cho tới lúc bị thu, và sau vài chục lần
            # trong một vòng quét thì pool cạn — hỏng ở chỗ không ai ngờ.
            resp.read()
            resp.close()
            print(f"[lark_api] {resp.status_code} {request.url.path} — "
                  f"thử lại sau {wait:.1f}s ({attempt + 2}/{_RETRY_CALLS})")
            time.sleep(wait)
            resp = super().handle_request(request)
        return resp


def _http() -> httpx.Client:
    global _client
    if _client is None:
        # `retries=2` của httpx CHỈ thử lại lúc BẮT TAY kết nối (ConnectError /
        # ConnectTimeout), tức request chưa hề rời máy — an toàn cho cả POST,
        # khác hẳn với việc thử lại một response đã nhận.
        _client = httpx.Client(timeout=60.0, base_url=config.base_url(),
                               transport=_RetryTransport(retries=2))
    return _client


def _check(resp: httpx.Response, where: str) -> dict[str, Any]:
    """Parse response Lark chuẩn {code,msg,data}. Ném LarkError nếu code!=0."""
    try:
        out = resp.json()
    except json.JSONDecodeError as exc:
        raise LarkError(-1, f"không phải JSON: {resp.text[:200]}", where) from exc
    # authen/v2/oauth/token trả field ở top-level, code==0 khi ok
    code = out.get("code", 0)
    if code not in (0, None):
        raise LarkError(code, out.get("msg") or out.get("error_description")
                        or "unknown", where)
    return out


# =====================================================================
#  AUTH — tenant token (bot)
# =====================================================================

# Cache THEO app_id: có hai app (pipeline + bot hỏi đáp) và token của app này
# gửi tin bằng app kia là 100% sai danh tính.
_tenant_cache: dict[str, tuple[str, float]] = {}
_tenant_lock = threading.Lock()


def tenant_token_for(app_id: str, app_secret: str, *, label: str = "") -> str:
    """tenant_access_token của MỘT app cụ thể, có cache."""
    if not app_id or not app_secret:
        raise LarkError(-1, f"thiếu app_id/app_secret {label or ''}".strip(),
                        "auth")
    with _tenant_lock:
        hit = _tenant_cache.get(app_id)
        if hit and time.time() < hit[1] - 300:
            return hit[0]
        resp = _http().post(
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        out = _check(resp, "tenant_access_token")
        tok = out["tenant_access_token"]
        _tenant_cache[app_id] = (tok, time.time() + int(out.get("expire", 7200)))
        return tok


def tenant_token() -> str:
    """Token của app PIPELINE (LARK_APP_ID). Bot hỏi đáp dùng app khác."""
    return tenant_token_for(config.APP_ID, config.APP_SECRET,
                            label="LARK_APP_ID / LARK_APP_SECRET")


# =====================================================================
#  AUTH — OAuth user token
# =====================================================================

def authorize_url(state: str) -> str:
    """URL để người dùng bấm và Đồng ý. Trả code về redirect_uri."""
    from urllib.parse import urlencode
    q = urlencode({
        "client_id": config.APP_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "scope": config.OAUTH_SCOPES,
        "state": state,
        "response_type": "code",
    })
    return f"{config.base_url()}/open-apis/authen/v1/authorize?{q}"


def exchange_code(code: str) -> dict[str, Any]:
    """Đổi authorization code lấy user access + refresh token.

    Dùng endpoint OAuth 2.0 v2 (authen/v2/oauth/token) — trả cả
    refresh_token_expires_in để tokenstore biết hạn refresh.
    """
    resp = _http().post(
        "/open-apis/authen/v2/oauth/token",
        json={
            "grant_type": "authorization_code",
            "client_id": config.APP_ID,
            "client_secret": config.APP_SECRET,
            "code": code,
            "redirect_uri": config.OAUTH_REDIRECT_URI,
        },
    )
    return _check(resp, "oauth/token(code)")


def refresh_user_token(refresh_token: str) -> dict[str, Any]:
    """Gia hạn user token bằng refresh token (xoay vòng mỗi lần gọi)."""
    resp = _http().post(
        "/open-apis/authen/v2/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": config.APP_ID,
            "client_secret": config.APP_SECRET,
            "refresh_token": refresh_token,
        },
    )
    return _check(resp, "oauth/token(refresh)")


def user_info(access_token: str) -> dict[str, Any]:
    """Thông tin người vừa authorize: open_id, union_id, name."""
    resp = _http().get(
        "/open-apis/authen/v1/user_info",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return _check(resp, "user_info").get("data", {})


# =====================================================================
#  MINUTES (user token)
# =====================================================================

# Múi giờ dùng để đóng gói mốc thời gian gửi lên Lark. API nhận ISO 8601 CÓ
# offset nên đây chỉ là cách biểu diễn, không làm lệch khoảng thời gian.
_ISO_TZ = timezone(timedelta(hours=7))


def _iso(ms: int) -> str:
    """epoch ms -> ISO 8601 kèm offset, dạng API search chấp nhận."""
    return datetime.fromtimestamp(ms / 1000, _ISO_TZ).isoformat(timespec="seconds")


def minutes_list(access_token: str, start_ms: int, end_ms: int,
                 participant_open_id: str, page_size: int = 30,
                 max_pages: int = 5) -> list[dict[str, Any]]:
    """Minute mà `participant_open_id` có tham dự, trong khoảng thời gian.

    Đây là lưới an toàn (đường B — polling) khi WebSocket event rớt.

    KHÔNG có endpoint "GET /minutes/v1/minutes" — nó trả 404 (đã đo). Đường
    đúng là POST .../minutes/search, contract lấy từ `lark-cli minutes +search
    --dry-run` (V1 đã chạy production bằng chính lệnh này):

        POST /open-apis/minutes/v1/minutes/search?page_size=N
        body {"filter": {"create_time": {"start_time": ISO, "end_time": ISO},
                         "participant_ids": [open_id]}}

    Lọc theo `participant_ids` (không phải owner) để bắt cả cuộc họp người khác
    tạo mà mình được mời. Trả [] khi lỗi thay vì crash để poller vẫn sống.

    `participant_open_id=""` -> BỎ hẳn bộ lọc người, trả về mọi minute mà token
    này MỞ XEM ĐƯỢC. Thêm 04/08/2026 sau khi đo: Lark không xếp một người vào
    `participant_ids` của mọi bản ghi họ dự (đo trên tài khoản BOD: lọc theo
    người ra 5, chỉ lọc thời gian ra 7 — hai cuộc chênh đều là cuộc họ mở xem
    được và khẳng định có dự). Người gọi PHẢI tự phân biệt hai nguồn: xem được
    KHÔNG đồng nghĩa có dự, và `db.note_viewer` chỉ được ghi cho nguồn có lọc.

    ⚠️ `page_size` trần THẬT là 30 — gửi 100 thì Lark trả `2094006` và 0 item
    (đo 04/08/2026), tức hỏng im lặng nếu ai đó "tối ưu" bằng cách tăng số này.
    """
    items: list[dict[str, Any]] = []
    page_token = ""
    try:
        for _ in range(max_pages):
            params: dict[str, Any] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            resp = _http().post(
                "/open-apis/minutes/v1/minutes/search",
                headers={"Authorization": f"Bearer {access_token}", **READ_ONLY},
                params=params,
                json={"filter": {
                    "create_time": {"start_time": _iso(start_ms),
                                    "end_time": _iso(end_ms)},
                    **({"participant_ids": [participant_open_id]}
                       if participant_open_id else {}),
                }},
            )
            data = _check(resp, "minutes_search").get("data", {})
            items.extend(data.get("items") or [])
            page_token = data.get("page_token") or ""
            if not data.get("has_more") or not page_token:
                break
    except LarkError as exc:
        print(f"[lark_api] minutes_search hỏng ({exc}); "
              "vòng quét này bỏ qua, dựa vào event/vòng sau.")
        return []
    return items


def minutes_get(access_token: str, minute_token: str) -> dict[str, Any]:
    """Thông tin cơ bản của một minute: title, owner, thời gian, url."""
    resp = _http().get(
        f"/open-apis/minutes/v1/minutes/{minute_token}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return _check(resp, "minutes_get").get("data", {}).get("minute", {})


def minutes_transcript(access_token: str, minute_token: str, *,
                       need_speaker: bool = True,
                       need_timestamp: bool = True) -> str:
    """Transcript sẵn có của Lark (có nhãn người nói). Nhanh, để đối chiếu."""
    resp = _http().get(
        f"/open-apis/minutes/v1/minutes/{minute_token}/transcript",
        headers={"Authorization": f"Bearer {access_token}"},
        params={
            "need_speaker": str(need_speaker).lower(),
            "need_timestamp": str(need_timestamp).lower(),
            "file_format": "txt",
        },
    )
    # transcript có thể trả text thô (không JSON) tùy file_format
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        data = _check(resp, "minutes_transcript").get("data", {})
        return data.get("transcript", "") or data.get("content", "")
    return resp.text


def minutes_media_url(access_token: str, minute_token: str) -> str:
    """Lấy URL tải bản ghi (mp4). Tách riêng bước tải để stream ra file."""
    resp = _http().get(
        f"/open-apis/minutes/v1/minutes/{minute_token}/media",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    data = _check(resp, "minutes_media").get("data", {})
    return data.get("download_url") or data.get("url", "")


def download_to(url: str, dest: Path, access_token: str | None = None) -> int:
    """Stream một URL xuống file. Trả số byte. Dùng cho mp4 (có thể lớn)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    total = 0
    with httpx.stream("GET", url, headers=headers, timeout=600.0,
                      follow_redirects=True) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                total += len(chunk)
    return total


# =====================================================================
#  CALENDAR (user token) — chuỗi tra người được mời (V1 §6)
# =====================================================================

def calendar_primary(access_token: str) -> str:
    """calendar_id của lịch chính người dùng."""
    resp = _http().post(
        "/open-apis/calendar/v4/calendars/primary",
        headers={"Authorization": f"Bearer {access_token}", **READ_ONLY},
    )
    data = _check(resp, "calendar_primary").get("data", {})
    cals = data.get("calendars", [])
    if cals:
        return cals[0].get("calendar", {}).get("calendar_id", "")
    return data.get("calendar", {}).get("calendar_id", "")


def calendar_events(access_token: str, calendar_id: str,
                    start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    """Các LẦN DIỄN RA của sự kiện trong [start_ts, end_ts] (epoch giây).

    PHẢI dùng `instance_view`, KHÔNG dùng `/events`. `/events` trả về sự kiện
    GỐC của chuỗi lặp với giờ bắt đầu là lần đầu tiên trong quá khứ — hỏi cửa
    sổ ±3h quanh hôm nay vẫn nhận về sự kiện từ tháng 3, tháng 4 (đã đo), khiến
    phép so "gần giờ nhất" vô nghĩa. `instance_view` trải chuỗi lặp thành từng
    lần diễn ra và giờ trả về là giờ thật của lần đó.

    Contract lấy từ `lark-cli calendar +agenda --dry-run` (V1 dùng lệnh này).
    Item có start_time.datetime = ISO 8601 kèm offset, và event_id ở dạng
    instance id "<event_id>_<original_time>".
    """
    resp = _http().get(
        f"/open-apis/calendar/v4/calendars/{calendar_id}/events/instance_view",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"start_time": str(start_ts), "end_time": str(end_ts)},
    )
    return _check(resp, "calendar_instance_view").get("data", {}).get("items", [])


def event_meeting_ids(access_token: str, calendar_id: str,
                      instance_ids: list[str]) -> dict[str, str]:
    """instance_id -> meeting_id (mắt nối sự kiện lịch <-> cuộc họp VC).

    Đây là bước V1 dùng để XÁC MINH sự kiện lịch có đúng sinh ra minute này
    (event -> meeting_id -> recording -> minute_token). V2 trước đây đọc
    `ev.vchat.meeting_id` từ payload sự kiện — tenant này KHÔNG có trường đó ở
    bất kỳ sự kiện nào, nên bước xác minh im lặng bị bỏ qua.

    Contract từ `lark-cli calendar +meeting`. Trả dict rỗng nếu API không cho
    meeting_id (thiếu scope — xem docs/V2_MAINTENANCE.md §5); caller PHẢI coi
    đó là "không xác minh được", không được coi là "đã xác minh".
    """
    if not instance_ids:
        return {}
    resp = _http().post(
        f"/open-apis/calendar/v4/calendars/{calendar_id}"
        "/events/mget_instance_relation_info",
        headers={"Authorization": f"Bearer {access_token}", **READ_ONLY},
        json={"instance_ids": instance_ids},
    )
    infos = (_check(resp, "event_meeting_ids").get("data", {})
             .get("instance_relation_infos") or [])
    return {i["instance_id"]: i["meeting_id"] for i in infos
            if i.get("instance_id") and i.get("meeting_id")}


def event_get(access_token: str, calendar_id: str, event_id: str) -> dict[str, Any]:
    """Chi tiết MỘT lần diễn ra của sự kiện. Cần nó vì `instance_view` không
    trả `vchat` — mà `vchat.meeting_url` là chỗ duy nhất trên tenant này có
    số phòng họp (meeting_no)."""
    resp = _http().get(
        f"/open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return _check(resp, "event_get").get("data", {}).get("event", {})


_MEETING_NO_RE = re.compile(r"/j/(\d+)")


def meeting_no_of(event: dict[str, Any]) -> str:
    """meeting_no lấy từ `vchat.meeting_url` (dạng https://.../j/459359142).

    Tenant này KHÔNG có `vchat.meeting_id` ở bất kỳ sự kiện nào (đã đo), nhưng
    CÓ `meeting_url` — bản ghi cũ kết luận "không có gì để nối" là thiếu.
    """
    url = ((event.get("vchat") or {}).get("meeting_url") or "")
    m = _MEETING_NO_RE.search(url)
    return m.group(1) if m else ""


def vc_meetings_by_no(access_token: str, meeting_no: str,
                      start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    """Các cuộc họp VC có cùng số phòng trong [start_ts, end_ts] (epoch giây).

    Đây là mắt nối meeting_no -> meeting_id. Cần scope `vc:meeting:readonly` ở
    danh tính NGƯỜI DÙNG **và** tên đó phải có trong OAUTH_SCOPES (đo 31/07:
    Console duyệt thôi chưa đủ). Thiếu -> 99991679 và Lark đọc thẳng tên scope.
    """
    resp = _http().get(
        "/open-apis/vc/v1/meetings/list_by_no",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"meeting_no": meeting_no,
                "start_time": str(start_ts), "end_time": str(end_ts)},
    )
    data = _check(resp, "vc_meetings_by_no").get("data", {})
    return data.get("meeting_briefs") or data.get("meetings") or []


def event_attendees(access_token: str, calendar_id: str, event_id: str,
                    id_type: str = "union_id",
                    max_pages: int = 10) -> list[dict[str, Any]]:
    """Danh sách người được mời của một sự kiện. CÓ phân trang.

    Trước 02/08/2026 hàm này gửi `page_size=100` rồi lấy trang đầu và thôi —
    cuộc họp đông hơn 100 người bị cắt IM LẶNG, không lỗi, không cảnh báo, và
    hệ quả rơi đúng vào chỗ nguy hiểm nhất: danh sách người dự. Đã có cuộc 30
    người thật (`Workforce AI Weekly`) nên ngưỡng 100 không còn xa.

    `max_pages` là chốt chặn vòng lặp vô hạn nếu Lark trả `has_more` mãi —
    1.000 người là quá đủ cho một sự kiện lịch.
    """
    items: list[dict[str, Any]] = []
    page_token = ""
    for _ in range(max_pages):
        params: dict[str, Any] = {"user_id_type": id_type, "page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = _http().get(
            f"/open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        data = _check(resp, "event_attendees").get("data", {})
        items.extend(data.get("items") or [])
        page_token = data.get("page_token") or ""
        if not data.get("has_more") or not page_token:
            break
    else:
        print(f"[lark_api] event_attendees {event_id}: còn trang sau khi đã lấy "
              f"{max_pages} trang ({len(items)} người) — danh sách CÓ THỂ thiếu")
    return items


# =====================================================================
#  VC (user token) — nối meeting_id -> minute_token
# =====================================================================

def vc_meeting_recording(access_token: str, meeting_id: str) -> dict[str, Any]:
    """Bản ghi của một cuộc họp VC (chứa minute/url để khớp minute_token)."""
    resp = _http().get(
        f"/open-apis/vc/v1/meetings/{meeting_id}/recording",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return _check(resp, "vc_recording").get("data", {}).get("recording", {})


def vc_meeting_participants(access_token: str, meeting_id: str,
                            id_type: str = "union_id") -> list[dict[str, Any]]:
    """Người THẬT SỰ vào phòng họp (khác hẳn với người được MỜI trên lịch).

    Contract lấy từ `lark-cli vc meeting get --help` (02/08/2026): cờ
    `--with-participants` trên `GET /open-apis/vc/v1/meetings/{meeting_id}`.
    Cần `vc:meeting:readonly` ở danh tính NGƯỜI DÙNG — đã có trong
    `OAUTH_SCOPES`, nhưng người enroll trước 02/08 phải enroll lại mới có.

    Trả `[]` thay vì ném, có chủ ý: đây là bước LÀM GIÀU danh sách người nhận.
    Thiếu quyền / cuộc họp quá cũ / API đổi hình dạng thì rơi về danh sách lịch
    như trước, chứ không được làm gãy việc phát biên bản.
    """
    try:
        resp = _http().get(
            f"/open-apis/vc/v1/meetings/{meeting_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"with_participants": "true", "user_id_type": id_type},
        )
        meeting = (_check(resp, "vc_meeting_get").get("data", {})
                   .get("meeting", {}))
    except LarkError as exc:
        print(f"[lark_api] không đọc được người dự VC của {meeting_id} ({exc})")
        return []
    return meeting.get("participants") or []


# =====================================================================
#  Task (USER token) — tạo việc cần làm từ nội dung cuộc họp
# =====================================================================

def task_create(access_token: str, summary: str, *,
                description: str = "", due_ms: int | None = None,
                all_day: bool = True,
                assignee_open_id: str = "") -> dict[str, Any]:
    """Tạo một Lark Task bằng danh tính NGƯỜI DÙNG. Trả object task.

    Dùng user token chứ không phải tenant token có chủ ý: task hiện ra là do
    CHÍNH người hỏi tạo, và bot không bao giờ làm được nhiều hơn quyền người đó
    vốn có. Bot tạo task danh nghĩa app thì nó thành một thực thể có quyền riêng,
    khó truy trách nhiệm khi nội dung task bắt nguồn từ transcript (không tin cậy).

    `due_ms` là epoch MILI GIÂY, dạng chuỗi khi gửi đi. ĐO THẬT 01/08/2026 (sổ
    tay §21) — cả ba điều dưới đây đều là bẫy im lặng, Lark trả `code=0` rồi lưu
    sai chứ không báo lỗi:

        gửi epoch GIÂY (1786726800)     -> Lark lưu 1728000000 = 04/10/2024 (!)
        ms + is_all_day=True            -> Lark làm tròn về nửa đêm UTC, tức LÙI
                                           một ngày khi xem ở giờ VN
        ms nửa đêm UTC + is_all_day=True -> đúng ngày mong muốn

    Nên caller phải tự quy ra **nửa đêm UTC của ngày muốn** khi `all_day=True`
    (xem `tasks._parse_due`). Sai chỗ này là người ta trễ hạn mà không hiểu vì sao.

    `client_token` chống tạo trùng khi mạng chập chờn và ta gọi lại.
    """
    body: dict[str, Any] = {
        "summary": summary[:255],
        "client_token": str(uuid.uuid4()),
    }
    if description:
        body["description"] = description[:3000]
    if due_ms:
        body["due"] = {"timestamp": str(int(due_ms)), "is_all_day": all_day}
    if assignee_open_id:
        body["members"] = [{"id": assignee_open_id, "role": "assignee",
                            "type": "user"}]
    resp = _http().post(
        "/open-apis/task/v2/tasks",
        headers={"Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/json; charset=utf-8"},
        params={"user_id_type": "open_id"},
        json=body,
    )
    return _check(resp, "task_create").get("data", {}).get("task", {})


# =====================================================================
#  IM (tenant token / bot) — gửi tin, upload file
# =====================================================================

def _im_headers(token: str | None = None) -> dict[str, str]:
    """Header IM. `token` để gửi bằng app KHÁC (bot hỏi đáp dùng app thứ hai)."""
    return {"Authorization": f"Bearer {token or tenant_token()}",
            "Content-Type": "application/json; charset=utf-8"}


def im_send(receive_id: str, msg_type: str, content: dict[str, Any], *,
            id_type: str = "union_id", uuid_key: str | None = None) -> str:
    """Gửi một message. Trả message_id. uuid_key để chống gửi trùng."""
    payload: dict[str, Any] = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }
    if uuid_key:
        payload["uuid"] = uuid_key[:50]
    resp = _http().post(
        "/open-apis/im/v1/messages",
        headers=_im_headers(), params={"receive_id_type": id_type},
        json=payload,
    )
    return _check(resp, "im_send").get("data", {}).get("message_id", "")


def im_send_card(receive_id: str, card: dict[str, Any], *,
                 id_type: str = "union_id", uuid_key: str | None = None) -> str:
    return im_send(receive_id, "interactive", card,
                   id_type=id_type, uuid_key=uuid_key)


def im_send_text(receive_id: str, text: str, *,
                 id_type: str = "union_id", uuid_key: str | None = None) -> str:
    return im_send(receive_id, "text", {"text": text},
                   id_type=id_type, uuid_key=uuid_key)


def im_delete_message(message_id: str) -> None:
    """Thu hồi một tin do chính bot gửi. Chỉ caller vận hành dùng trực tiếp."""
    if not message_id:
        raise LarkError(-1, "thiếu message_id", "im_delete")
    resp = _http().delete(
        f"/open-apis/im/v1/messages/{message_id}",
        headers=_im_headers(),
    )
    _check(resp, "im_delete")


def im_upload_file(path: Path, file_type: str = "stream") -> str:
    """Upload file cho IM. Trả file_key. Giới hạn Lark IM: 30 MB."""
    if not path.exists():
        raise LarkError(-1, f"không thấy file: {path}", "im_upload")
    size_mb = path.stat().st_size / 1_048_576
    if size_mb > 30:
        raise LarkError(-1, f"file {size_mb:.1f} MB vượt 30 MB", "im_upload")

    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    files = {
        "file_type": (None, file_type),
        "file_name": (None, path.name),
        "file": (path.name, path.read_bytes(), ctype),
    }
    resp = _http().post(
        "/open-apis/im/v1/files",
        headers={"Authorization": f"Bearer {tenant_token()}"},
        files=files, timeout=300.0,
    )
    return _check(resp, "im_upload").get("data", {}).get("file_key", "")


# `file_type` của `im/v1/files`, theo đuôi file. Sai giá trị KHÔNG làm hỏng lời
# gọi — Lark nhận `stream` cho mọi thứ — nên nó không lộ ra ở đâu ngoài phía
# người nhận: `stream` hiện như tệp nhị phân, phải tải về mới đọc; `doc` thì xem
# trước được ngay trong chat.
#
# MỘT chỗ duy nhất, có chủ ý: bảng này từng nằm hai nơi (`im_send_file` và
# `pipeline._upload_doc`). Thêm `.docx` vào một bên mà quên bên kia thì đường
# `deliver` (có file_key) hiện đẹp còn đường gửi lẻ (tự upload) hiện xấu — khác
# nhau tuỳ đường đi, không ai đoán ra vì sao.
FILE_TYPES = {".mp4": "mp4", ".pdf": "pdf", ".opus": "opus",
              ".docx": "doc", ".doc": "doc"}


def im_send_file(receive_id: str, path: Path, *,
                 id_type: str = "union_id", uuid_key: str | None = None,
                 file_key: str | None = None) -> str:
    """Gửi file. Truyền file_key để tái dùng khi gửi cho nhiều người."""
    key = file_key or im_upload_file(path, FILE_TYPES.get(path.suffix.lower(),
                                                          "stream"))
    return im_send(receive_id, "file", {"file_key": key},
                   id_type=id_type, uuid_key=uuid_key)


# =====================================================================
#  BASE / Bitable (tenant token — bot ghi "nội dung đã chốt")
# =====================================================================
#
# Dùng họ endpoint base/v3 (KHÔNG phải bitable/v1). Khác biệt quan trọng:
# base/v3 nhận field map PHẲNG ở body, bitable/v1 bọc trong {"fields": {...}}.
# Contract lấy từ `lark-cli base +base-create/+record-upsert --dry-run`.
#
# Quyền: app V2 ĐÃ có sẵn quyền đọc+ghi Base ở tầng app (đo 30/07/2026 bằng
# cách gọi với app_token giả -> trả 91402 NOTEXIST tức đã qua cửa quyền, không
# phải lỗi permission). Không cần xin scope, không cần enroll lại.


def base_create(name: str, folder_token: str = "") -> str:
    """Tạo Base mới. Trả base_token (app_token)."""
    body: dict[str, Any] = {"name": name}
    if folder_token:
        body["folder_token"] = folder_token
    resp = _http().post("/open-apis/base/v3/bases",
                        headers=_im_headers(), json=body)
    data = _check(resp, "base_create").get("data", {})
    return (data.get("base") or data).get("base_token") or data.get("app_token", "")


def base_tables(base_token: str) -> list[dict[str, Any]]:
    """Danh sách table. Mỗi item được chuẩn hóa để LUÔN có khóa `table_id`.

    base/v3 trả khóa `id` (không phải `table_id` như bitable/v1) — đã đo. Chuẩn
    hóa ở đây để chỗ gọi không phải đoán.
    """
    resp = _http().get(f"/open-apis/base/v3/bases/{base_token}/tables",
                       headers=_im_headers(),
                       params={"limit": 100, "offset": 0})
    data = _check(resp, "base_tables").get("data", {})
    out = []
    for t in (data.get("tables") or data.get("items") or []):
        t = dict(t)
        t["table_id"] = t.get("table_id") or t.get("id") or ""
        out.append(t)
    return out


def base_table_create(base_token: str, name: str,
                      fields: list[dict[str, Any]]) -> str:
    """Tạo table kèm schema. Trả table_id.

    Response không chắc chứa id (đã gặp trường hợp rỗng) -> tra lại bằng
    base_tables theo TÊN thay vì trả "" rồi để chỗ gọi ghi thiếu cấu hình.
    """
    resp = _http().post(f"/open-apis/base/v3/bases/{base_token}/tables",
                        headers=_im_headers(),
                        json={"name": name, "fields": fields})
    data = _check(resp, "base_table_create").get("data", {})
    inner = data.get("table") or data
    tid = inner.get("table_id") or inner.get("id") or ""
    if tid:
        return tid
    for t in base_tables(base_token):
        if t.get("name") == name:
            return t["table_id"]
    return ""


def contact_batch(open_ids: list[str]) -> dict[str, dict[str, Any]]:
    """open_id -> {name, user_id, email}. Bỏ qua id tra không ra.

    Vì sao cần `user_id`: đó là id ngắn (kiểu `1fg8g36d`) mà admin/HR nhìn thấy,
    còn `open_id` chỉ có nghĩa trong PHẠM VI MỘT APP. Ghi vào Base thì user_id
    mới tra chéo được với hệ thống khác.
    """
    if not open_ids:
        return {}
    params = [("user_ids", i) for i in open_ids[:50]]
    params.append(("user_id_type", "open_id"))
    resp = _http().get("/open-apis/contact/v3/users/batch",
                       headers=_im_headers(), params=params)
    out: dict[str, dict[str, Any]] = {}
    for u in _check(resp, "contact_batch").get("data", {}).get("items") or []:
        oid = u.get("open_id") or ""
        if oid:
            out[oid] = {
                "name": u.get("name") or "",
                "user_id": u.get("user_id") or "",
                "email": u.get("enterprise_email") or u.get("email") or "",
            }
    return out


# bitable/v1 trả `type` là SỐ; phần còn lại của file nói chuyện bằng TÊN
# (`base_field_create` nhận "text"/"select"/...). Bảng này dịch ngược lại.
_V1_FIELD_TYPES = {
    1: "text", 2: "number", 3: "select", 4: "multi_select", 5: "datetime",
    7: "checkbox", 11: "user", 13: "phone", 15: "url", 17: "attachment",
    18: "single_link", 19: "lookup", 20: "formula", 21: "duplex_link",
    1001: "created_time", 1002: "modified_time", 1003: "created_user",
    1004: "modified_user", 1005: "auto_number",
}


def base_fields(base_token: str, table_id: str) -> list[dict[str, Any]]:
    """Field hiện có của table. Mỗi item: {id, name, type, options}.

    Đọc bằng **bitable/v1**, KHÔNG phải base/v3 — ngược với phần còn lại của
    file, và có lý do đo được (03/08/2026):
    base/v3 `.../fields` **cắt cụt ở 20 field** bất kể `page_size`, KHÔNG trả
    `page_token`, KHÔNG trả `has_more`. Dấu hiệu duy nhất là `data.total` (=25)
    lệch với số item trả về (=20). Hậu quả nếu tin nó: `bitable.ensure_fields`
    so theo TÊN trên danh sách thiếu, kết luận cột đã có là "chưa có", rồi TẠO
    TRÙNG — bảng mọc thêm `Tóm tắt (1)`, `Người dự (1)`... và code ghi vào cột
    rỗng trong khi người dùng nhìn cột cũ. Đã suýt dính đúng lúc bảng vượt 20
    cột (thêm 6 cột gương của `jobs`).
    v1 trả đủ 25 và có `page_token` thật, nên phân trang được.
    """
    out: list[dict[str, Any]] = []
    token = ""
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if token:
            params["page_token"] = token
        resp = _http().get(
            f"/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/fields",
            headers=_im_headers(), params=params)
        data = _check(resp, "base_fields").get("data", {})
        for it in data.get("items") or []:
            out.append({
                "id": it.get("field_id"),
                "name": it.get("field_name"),
                "type": _V1_FIELD_TYPES.get(it.get("type"), it.get("type")),
                "options": ((it.get("property") or {}).get("options") or []),
            })
        token = data.get("page_token") or ""
        if not (data.get("has_more") and token):
            return out


def base_field_delete(base_token: str, table_id: str, field_id: str) -> None:
    """Xoá MỘT field. KHÔNG hoàn tác được — dữ liệu trong cột đó mất theo.

    Chỉ dùng cho cột đã xác minh RỖNG (xem `bitable._RENAMES`: cột di sản của
    cửa duyệt cũ). Đổi tên để tái dùng luôn tốt hơn; xoá là đường cuối khi tên
    mới đã bị một cột khác chiếm.
    """
    resp = _http().delete(
        f"/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/fields/{field_id}",
        headers=_im_headers())
    _check(resp, "base_field_delete")


def base_field_create(base_token: str, table_id: str,
                      spec: dict[str, Any]) -> str:
    """Thêm MỘT field vào table đã tồn tại. `spec` cùng dạng với SCHEMA.

    Trả field_id, hoặc "" nếu response không kèm id (đã gặp: tạo thành công
    code=0 mà không trả id) — caller đừng coi "" là thất bại, hãy tra lại bằng
    base_fields theo tên.

    Tên type là CHUỖI: text / number / datetime / select / attachment.
    "file" KHÔNG hợp lệ (đã đo: 800010701 invalid discriminator).
    """
    resp = _http().post(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}/fields",
        headers=_im_headers(), json=spec,
    )
    data = _check(resp, "base_field_create").get("data", {})
    inner = data.get("field") or data
    return inner.get("id") or inner.get("field_id") or ""


def base_field_update(base_token: str, table_id: str, field_id: str,
                      spec: dict[str, Any]) -> None:
    """Sửa MỘT field đã tồn tại (đổi tên / đổi bộ option của select).

    Method là **PUT**, không phải PATCH. Đo 31/07/2026 bằng cách gọi với
    `field_id` cố ý sai: PATCH và POST trả HTTP 404 (sai method), còn PUT trả
    HTTP 200 `code=800030201 not_found` — tức đã qua cửa quyền, chỉ là field
    không tồn tại. Body cùng dạng với `base_field_create`.

    ⚠️ Với select: bộ `options` gửi lên là THAY THẾ, không phải thêm. Option bị
    bỏ khỏi danh sách thì các record đang giữ giá trị đó mất giá trị ô — đổi
    option xong phải đổ lại dữ liệu (bitable.sync_tracking).
    """
    resp = _http().put(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}/fields/{field_id}",
        headers=_im_headers(), json=spec,
    )
    _check(resp, "base_field_update")


def base_media_upload(path: Path, base_token: str,
                      access_token: str = "") -> str:
    """Upload file để gắn vào ô attachment của Base. Trả file_token.

    parent_type=bitable_file + parent_node=<app_token của Base>: file thuộc về
    Base đó, ai xem được Base thì xem được file.

    PHẢI truyền `access_token` của NGƯỜI DÙNG. Đo 31/07/2026: gọi bằng tenant
    token thì Lark trả 99991672 và liệt kê các scope thay thế; app này chỉ có
    `docs:document.media:upload` ở danh tính **user**, không có ở tenant. Để
    trống thì vẫn thử bằng tenant token (cho tenant nào có quyền đó ở app level).
    """
    if not path.exists():
        raise LarkError(-1, f"không thấy file: {path}", "base_media_upload")
    size = path.stat().st_size
    files = {
        "file_name": (None, path.name),
        "parent_type": (None, "bitable_file"),
        "parent_node": (None, base_token),
        "size": (None, str(size)),
        "file": (path.name, path.read_bytes(),
                 mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
    }
    resp = _http().post(
        "/open-apis/drive/v1/medias/upload_all",
        headers={"Authorization": f"Bearer {access_token or tenant_token()}"},
        files=files, timeout=300.0,
    )
    return _check(resp, "base_media_upload").get("data", {}).get("file_token", "")


def base_table_delete(base_token: str, table_id: str) -> None:
    resp = _http().delete(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}",
        headers=_im_headers())
    _check(resp, "base_table_delete")


def base_record_create(base_token: str, table_id: str,
                       fields: dict[str, Any]) -> str:
    """Thêm record. `fields` là map PHẲNG tên field -> giá trị. Trả record_id.

    base/v3 trả `{"data": {"record_id_list": ["recXXX"]}}` — KHÔNG phải
    `data.record.record_id` như bitable/v1 (đã đo 30/07/2026).
    """
    resp = _http().post(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}/records",
        headers=_im_headers(), json=fields)
    data = _check(resp, "base_record_create").get("data", {})
    ids = data.get("record_id_list") or []
    if ids:
        return ids[0]
    return (data.get("record") or data).get("record_id", "")


def base_record_find(base_token: str, table_id: str, field: str,
                     keyword: str) -> str:
    """Tìm record_id đầu tiên có `field` chứa `keyword`. "" nếu không thấy.

    Dùng để chống ghi trùng: nếu record đã tạo trên Base nhưng ta chưa lưu được
    record_id (tiến trình chết, response đổi shape), lần sau phải tìm ra nó chứ
    không tạo record thứ hai cho cùng cuộc họp.
    """
    resp = _http().post(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}"
        f"/records/search",
        headers=_im_headers(),
        json={"keyword": keyword, "search_fields": [field],
              "select_fields": [field], "limit": 5, "offset": 0},
    )
    data = _check(resp, "base_record_find").get("data", {})
    ids = data.get("record_id_list") or []
    if ids:
        return ids[0]
    for item in (data.get("items") or data.get("records") or []):
        rid = item.get("record_id") or item.get("id")
        if rid:
            return rid
    return ""


# Trần `limit` MỘT trang của base/v3 records. Xin hơn thì Lark im lặng cắt về
# đây — không lỗi, không `has_more`, chỉ là thiếu record.
_RECORDS_PAGE = 200
# Chốt chặn vòng lặp nếu Lark trả mãi không hết (200 x 50 = 10.000 record).
_RECORDS_MAX_PAGES = 50


def base_records_all(base_token: str, table_id: str,
                     limit: int = 1000) -> list[dict[str, Any]]:
    """Đọc record kèm GIÁ TRỊ các field. Trả list dict {field_name: value}.

    Dùng GET .../records để LIỆT KÊ. KHÔNG dùng .../records/search: endpoint đó
    BẮT BUỘC có `keyword` + `search_fields` (đã đo: thiếu -> 800010701), nên nó
    chỉ để tìm, không liệt kê được.

    Cả hai trả dữ liệu ở dạng CỘT (`fields` = tên field, `data` = từng hàng theo
    đúng thứ tự đó) chứ không phải list dict — phải ghép lại ở đây, nếu không
    chỗ gọi sẽ đọc sai cột.

    CÓ PHÂN TRANG (sửa 05/08/2026). Trước đó hàm này gửi đúng một lời gọi với
    `offset: 0` và trần cứng 200, không đọc `has_more`, không cộng offset — tức
    record thứ 201 trở đi KHÔNG TỒN TẠI với cả hệ thống, im lặng. Cùng một họ
    lỗi cắt-cụt đã phải sửa hai lần ở file này (`event_attendees` lấy đúng trang
    đầu; `base_fields` của base/v3 cắt ở 20 field mà không báo).

    Hậu quả nếu để nguyên, và vì sao nó nguy hiểm hơn vẻ ngoài: `qa.records()`
    đọc qua đây, nên cuộc họp thứ 201 biến mất khỏi `list_meetings` /
    `get_meeting` / `search_meetings` — người dự hỏi bot thì nhận "không tìm
    thấy" cho một biên bản có thật. `bitable.sync_jobs()` cũng đọc qua đây, nên
    những record đó không bao giờ được đồng bộ lại nữa.

    `limit` là TỔNG số record tối đa, không phải cỡ trang.
    """
    out: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_RECORDS_MAX_PAGES):
        want = min(_RECORDS_PAGE, max(0, limit - len(out)))
        if want <= 0:
            return out
        resp = _http().get(
            f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}/records",
            headers=_im_headers(),
            params={"limit": want, "offset": offset},
        )
        data = _check(resp, "base_records_all").get("data", {})
        names = data.get("fields") or []
        ids = data.get("record_id_list") or []
        rows = data.get("data") or []
        for i, row in enumerate(rows):
            rec: dict[str, Any] = {"_record_id": ids[i] if i < len(ids) else ""}
            for name, val in zip(names, row):
                # select 1 lựa chọn về dạng ["draft"] -> lấy phần tử đầu cho gọn.
                if isinstance(val, list) and len(val) == 1:
                    val = val[0]
                rec[name] = val
            out.append(rec)
        # Trang non = đã hết. KHÔNG tin `has_more`: endpoint này không hứa có
        # trường đó, và đọc thiếu một trang ở đây là mất cuộc họp im lặng.
        if len(rows) < want:
            return out
        offset += len(rows)
    print(f"[lark_api] base_records_all: còn record sau {_RECORDS_MAX_PAGES} "
          f"trang ({len(out)} đã đọc) — danh sách CÓ THỂ thiếu")
    return out


def base_record_delete(base_token: str, table_id: str, record_id: str) -> None:
    """Xoá MỘT record. KHÔNG hoàn tác được.

    Chỉ dành cho người VẬN HÀNH chạy tay, cùng kỷ luật với
    `im_delete_message`: không có đường nào từ bot/agent/MCP gọi tới đây, và
    đừng thêm. Dữ liệu Base bắt nguồn từ lời nói trong họp và chảy vào prompt
    của agent — một tool "xoá record" mà agent gọi được là thứ không gỡ lại được.

    Dùng **bitable/v1** chứ không base/v3, cùng lý do với `base_field_delete`:
    đó là họ endpoint có hợp đồng xoá đã đo được trên tenant này.

    Ca dùng thật (05/08/2026): hai dòng rỗng hoàn toàn trên bảng `Biên bản` —
    không `minute_token`, không tên, không giờ. Chúng vô hình với bot
    (`qa._may_see("")` fail-closed) và `bitable.sync_jobs` không khớp được vào
    job nào, nên chúng chỉ làm lệch mọi phép đếm "Base có bao nhiêu cuộc họp".
    """
    if not record_id:
        raise LarkError(-1, "thiếu record_id", "base_record_delete")
    resp = _http().delete(
        f"/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}"
        f"/records/{record_id}",
        headers=_im_headers())
    _check(resp, "base_record_delete")


def base_record_update(base_token: str, table_id: str, record_id: str,
                       fields: dict[str, Any]) -> None:
    resp = _http().patch(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}"
        f"/records/{record_id}",
        headers=_im_headers(), json=fields)
    _check(resp, "base_record_update")


def drive_members(token: str, doc_type: str = "bitable") -> list[dict[str, Any]]:
    """Ai được thêm tường minh vào tài liệu này (cộng tác viên).

    Đây mới là NỬA ĐẦU của câu "ai đọc được Base": nửa sau là `drive_public()`
    (chia sẻ bằng link), và nửa sau mới là chỗ thường rộng hơn người ta tưởng.

    Chạy bằng tenant token, KHÔNG cần scope thêm — đã đo 02/08/2026 trên Base
    thật: `code=0`, trả đúng danh sách. Cùng họ endpoint với `drive_member_add`
    vốn đã dùng được từ trước.
    """
    resp = _http().get(f"/open-apis/drive/v1/permissions/{token}/members",
                       headers=_im_headers(), params={"type": doc_type})
    data = _check(resp, "drive_members").get("data", {})
    return list(data.get("items") or [])


def drive_public(token: str, doc_type: str = "bitable") -> dict[str, Any]:
    """Đọc thiết lập chia sẻ bằng LINK của tài liệu này.

    Khóa quan trọng nhất là `link_share_entity`: `closed` = chỉ cộng tác viên;
    `tenant_*` = mọi người trong công ty có link; `anyone_*` = bất kỳ ai trên
    Internet. Kèm `external_access_entity` (link có chuyển ra ngoài tenant được
    không) và `share_entity` (ai được chia sẻ tiếp).

    Dùng v2 chứ không v1: v1 trả thiếu `external_access_entity` trên tenant này.
    """
    resp = _http().get(f"/open-apis/drive/v2/permissions/{token}/public",
                       headers=_im_headers(), params={"type": doc_type})
    data = _check(resp, "drive_public").get("data", {})
    return dict(data.get("permission_public") or {})


def drive_link_close(token: str, doc_type: str = "bitable") -> dict[str, Any]:
    """Đóng chia sẻ-bằng-link và đọc lại để xác minh trạng thái thật.

    Đây là thao tác THU HẸP quyền bên ngoài DB; caller phải có xác nhận của
    người vận hành trước khi gọi. Chỉ đổi ``link_share_entity`` — không tự ý
    đụng quyền của cộng tác viên tường minh hay trường chia sẻ đối tác khác.
    """
    if not token:
        raise ValueError("drive_link_close cần token tài liệu")
    resp = _http().patch(
        f"/open-apis/drive/v2/permissions/{token}/public",
        headers=_im_headers(), params={"type": doc_type},
        json={"link_share_entity": "closed"},
    )
    _check(resp, "drive_link_close")
    current = drive_public(token, doc_type)
    if current.get("link_share_entity") != "closed":
        raise LarkError(
            -1, "PATCH trả thành công nhưng đọc lại link_share_entity chưa closed",
            "drive_link_close_verify")
    return current


def drive_member_add(token: str, doc_type: str, member_id: str,
                     perm: str = "full_access",
                     member_type: str = "openid") -> None:
    """Thêm người vào tài liệu Drive/Base. Bot tạo Base thì người KHÔNG thấy nó
    tới khi được thêm vào đây."""
    resp = _http().post(
        f"/open-apis/drive/v1/permissions/{token}/members",
        headers=_im_headers(), params={"type": doc_type},
        json={"member_id": member_id, "member_type": member_type,
              "perm": perm, "type": "user"},
    )
    _check(resp, "drive_member_add")


def new_idem(prefix: str) -> str:
    """Sinh uuid chống trùng ngắn gọn."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
