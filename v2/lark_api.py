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


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=60.0, base_url=config.base_url())
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
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
                json={"filter": {
                    "create_time": {"start_time": _iso(start_ms),
                                    "end_time": _iso(end_ms)},
                    "participant_ids": [participant_open_id],
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
        headers={"Authorization": f"Bearer {access_token}"},
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
        headers={"Authorization": f"Bearer {access_token}"},
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
                    id_type: str = "union_id") -> list[dict[str, Any]]:
    """Danh sách người được mời của một sự kiện."""
    resp = _http().get(
        f"/open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"user_id_type": id_type, "page_size": 100},
    )
    return _check(resp, "event_attendees").get("data", {}).get("items", [])


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


def im_send_file(receive_id: str, path: Path, *,
                 id_type: str = "union_id", uuid_key: str | None = None,
                 file_key: str | None = None) -> str:
    """Gửi file. Truyền file_key để tái dùng khi gửi cho nhiều người."""
    ext = path.suffix.lower()
    ftype = {".mp4": "mp4", ".pdf": "pdf", ".opus": "opus"}.get(ext, "stream")
    key = file_key or im_upload_file(path, ftype)
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


def base_fields(base_token: str, table_id: str) -> list[dict[str, Any]]:
    """Field hiện có của table. Mỗi item: {id, name, type, ...}.

    Đọc bằng base/v3 (`data.fields`), KHÔNG phải bitable/v1 (`data.items`, và
    `field_name`/`type` số) — giữ cùng họ endpoint với phần còn lại của file.
    """
    resp = _http().get(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}/fields",
        headers=_im_headers(), params={"page_size": 100},
    )
    return _check(resp, "base_fields").get("data", {}).get("fields") or []


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


def base_records_all(base_token: str, table_id: str,
                     limit: int = 200) -> list[dict[str, Any]]:
    """Đọc record kèm GIÁ TRỊ các field. Trả list dict {field_name: value}.

    Dùng GET .../records để LIỆT KÊ. KHÔNG dùng .../records/search: endpoint đó
    BẮT BUỘC có `keyword` + `search_fields` (đã đo: thiếu -> 800010701), nên nó
    chỉ để tìm, không liệt kê được.

    Cả hai trả dữ liệu ở dạng CỘT (`fields` = tên field, `data` = từng hàng theo
    đúng thứ tự đó) chứ không phải list dict — phải ghép lại ở đây, nếu không
    chỗ gọi sẽ đọc sai cột.
    """
    resp = _http().get(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}/records",
        headers=_im_headers(),
        params={"limit": min(limit, 200), "offset": 0},
    )
    data = _check(resp, "base_records_all").get("data", {})
    names = data.get("fields") or []
    ids = data.get("record_id_list") or []
    out = []
    for i, row in enumerate(data.get("data") or []):
        rec: dict[str, Any] = {"_record_id": ids[i] if i < len(ids) else ""}
        for name, val in zip(names, row):
            # select 1 lựa chọn về dạng ["draft"] -> lấy phần tử đầu cho gọn.
            if isinstance(val, list) and len(val) == 1:
                val = val[0]
            rec[name] = val
        out.append(rec)
    return out


def base_record_update(base_token: str, table_id: str, record_id: str,
                       fields: dict[str, Any]) -> None:
    resp = _http().patch(
        f"/open-apis/base/v3/bases/{base_token}/tables/{table_id}"
        f"/records/{record_id}",
        headers=_im_headers(), json=fields)
    _check(resp, "base_record_update")


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
