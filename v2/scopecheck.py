"""
Quét TOÀN BỘ bề mặt Lark mà V2 gọi bằng USER token, cho một người cụ thể.

    python -m v2 scopes                 # mọi người đã enroll
    python -m v2 scopes --open-id ou_x  # một người

Vì sao module này tồn tại (bài học 31/07/2026): scope thiếu là lỗi **im lặng
và trễ**. Hệ thống chạy đúng nhiều ngày với MỘT người dùng, rồi người thứ hai
enroll và `minutes_search` trả 99991679 — nhưng không ai biết cho tới khi thấy
"sao không có biên bản nào". Trước đó cách phát hiện duy nhất là chờ nó cắn,
rồi vá từng cái một. Đó là phương pháp sai: vá xong vẫn không biết còn thiếu
gì, vì chỉ những đường ĐÃ chạy mới lộ ra.

Cách làm ở đây: gọi TỪNG endpoint bằng token của chính người đó, với tham số
**cố ý sai**. Lark kiểm quyền TRƯỚC khi kiểm tham số, nên:

    99991679 / 99991672  -> thiếu quyền, và Lark đọc thẳng tên scope còn thiếu
    lỗi tham số khác     -> ĐÃ qua cửa quyền (đó là điều ta muốn)

Không tác dụng phụ: mọi id đều bịa nên không đọc/ghi/tạo gì. Đây cũng là kỹ
thuật chuẩn của dự án (sổ tay §10) — nay gói lại thành lệnh chạy được.

Dùng khi: có người mới enroll, sau khi đổi OAUTH_SCOPES, hoặc khi nghi "sao
người này không ra cuộc họp nào".
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from . import config, lark_api, tokenstore

# id bịa — không tồn tại nên không chạm dữ liệu thật của ai.
_FAKE = "KHONGCOTHAT0000000000000"

# Mã lỗi nghĩa là "thiếu quyền". Mọi mã khác = đã qua cửa quyền.
_DENIED = (99991679, 99991672)


def _probe(method: str, path: str, token: str, *,
           params: dict | None = None, json_body: dict | None = None,
           files: dict | None = None) -> tuple[bool, int, str]:
    """Trả (thiếu_quyền, code, tên scope Lark đòi)."""
    try:
        resp = httpx.request(
            method, config.base_url() + path,
            headers={"Authorization": f"Bearer {token}"},
            params=params, json=json_body, files=files, timeout=25,
        )
        data = resp.json()
    except Exception as exc:                     # noqa: BLE001
        return False, -1, f"(không gọi được: {exc})"
    code = data.get("code")
    if code not in _DENIED:
        return False, code, ""
    m = re.search(r"\[([a-z:._, ]+)\]", str(data.get("msg", "")))
    return True, code, m.group(1) if m else str(data.get("msg", ""))[:120]


def _checks(token: str) -> list[tuple[str, str, tuple]]:
    """(tên việc, hàm V2 tương ứng, tham số probe). Giữ ĐỒNG BỘ với lark_api."""
    cal = f"/open-apis/calendar/v4/calendars/{_FAKE}"
    return [
        ("định danh người dùng", "user_info",
         ("GET", "/open-apis/authen/v1/user_info", {}, None, None)),
        ("QUÉT phát hiện cuộc họp", "minutes_list",
         ("POST", "/open-apis/minutes/v1/minutes/search",
          None, {"query": "", "page_size": 1}, None)),
        ("đọc thông tin minute", "minutes_get",
         ("GET", f"/open-apis/minutes/v1/minutes/{_FAKE}", None, None, None)),
        ("TẢI bản ghi", "minutes_media_url",
         ("GET", f"/open-apis/minutes/v1/minutes/{_FAKE}/media", None, None, None)),
        ("transcript sẵn của Lark", "minutes_transcript",
         ("GET", f"/open-apis/minutes/v1/minutes/{_FAKE}/transcript",
          {"file_format": "txt"}, None, None)),
        ("lịch chính", "calendar_primary",
         ("GET", "/open-apis/calendar/v4/calendars/primary", None, None, None)),
        ("danh sách sự kiện lịch", "calendar_events",
         ("GET", f"{cal}/events", {"page_size": 1}, None, None)),
        ("sự kiện lặp (instance_view)", "event_meeting_ids",
         ("GET", f"{cal}/events/instance_view",
          {"start_time": "1", "end_time": "2"}, None, None)),
        ("chi tiết một sự kiện", "event_get",
         ("GET", f"{cal}/events/{_FAKE}", None, None, None)),
        ("người được mời", "event_attendees",
         ("GET", f"{cal}/events/{_FAKE}/attendees", {"page_size": 1}, None, None)),
        ("tra cuộc họp theo số", "vc_meetings_by_no",
         ("GET", "/open-apis/vc/v1/meetings/list_by_no",
          {"meeting_no": "000000000", "start_time": "1", "end_time": "2"},
          None, None)),
        ("bản ghi của cuộc họp", "vc_meeting_recording",
         ("GET", f"/open-apis/vc/v1/meetings/{_FAKE}/recording", None, None, None)),
        # Người THẬT SỰ vào phòng họp (thêm 02/08/2026 cùng §23). Phải có probe
        # riêng dù `vc:meeting:readonly` đã được `vc_meetings_by_no` phủ: đó là
        # scope ta ĐOÁN endpoint này cần, chưa phải điều Lark xác nhận. Thiếu
        # quyền ở đây là hỏng IM LẶNG và đúng kiểu khó chẩn nhất — danh sách
        # người nhận tụt về mỗi người được mời trên lịch, tức quay lại đúng lỗi
        # "cuộc họp 30 người chỉ 1 người nhận" vừa vá.
        ("người vào phòng họp", "vc_meeting_participants",
         ("GET", f"/open-apis/vc/v1/meetings/{_FAKE}",
          {"with_participants": "true", "user_id_type": "union_id"}, None, None)),
        ("đính file vào Base", "bitable.base_media_upload",
         ("POST", "/open-apis/drive/v1/medias/upload_all", None, None,
          {"file_name": (None, "probe.txt"), "parent_type": (None, "bitable_file"),
           "parent_node": (None, _FAKE), "size": (None, "4"),
           "file": ("probe.txt", b"test", "text/plain")})),
        # TẠO task từ cuộc họp. Probe an toàn: `client_token` bịa và `summary`
        # rỗng -> Lark từ chối ở tầng THAM SỐ, không tạo task nào. Nếu ngày nào
        # đó Lark chấp nhận summary rỗng thì đổi sang một trường sai kiểu khác
        # — đừng để phép kiểm quyền tự tạo rác trong Task của người ta.
        ("TẠO việc cần làm", "task_create",
         ("POST", "/open-apis/task/v2/tasks", {"user_id_type": "open_id"},
          {"summary": "", "client_token": _FAKE}, None)),
    ]


def _scope_count(open_id: str) -> int:
    """Số scope lưu trong DB. `tokenstore.list_users` KHÔNG trả cột `scopes`
    (nó cố ý chỉ lấy phần không nhạy cảm), nên phải đọc riêng."""
    from . import db
    row = db.conn().execute(
        "SELECT scopes FROM tokens WHERE open_id=?", (open_id,)).fetchone()
    return len(((row["scopes"] if row else "") or "").split())


def check_user(u: dict[str, Any], *, verbose: bool = True) -> list[str]:
    """Quét một người. Trả danh sách đường bị chặn (rỗng = đạt)."""
    name = u.get("name") or u["open_id"]
    if verbose:
        print(f"\n{'=' * 68}\n{name}")
    try:
        token = tokenstore.get_access_token(u["open_id"])
    except tokenstore.TokenError as exc:
        if verbose:
            print(f"  [x] không lấy được token: {exc}")
        return []

    if verbose:
        print(f"  token đang có {_scope_count(u['open_id'])} scope\n")

    missing: list[str] = []
    for what, fn, (method, path, params, body, files) in _checks(token):
        denied, code, detail = _probe(method, path, token, params=params,
                                      json_body=body, files=files)
        if denied:
            if verbose:
                print(f"  [x] {what:<28} {fn}")
                print(f"      Lark đòi: {detail}")
            missing.append(f"{what} ({fn}) — cần: {detail}")
        elif verbose:
            print(f"  [+] {what:<28} {fn}  (code={code})")
    return missing


# Đường nào hỏng là MẤT BIÊN BẢN, khác với đường chỉ mất một ô phụ. Dùng để
# lúc enroll biết nên hét to hay chỉ ghi chú.
_CRITICAL = ("minutes_list", "minutes_media_url")


def critical(missing: list[str]) -> list[str]:
    return [m for m in missing if any(f in m for f in _CRITICAL)]


# Scope V2 THẬT SỰ dùng, liệt kê từng cái (siết 01/08/2026, sổ tay §22).
#
# Trước đó xin TRỌN HỌ: 124 scope. Cách đó có lý do của nó — thêm scope là mọi
# người phải enroll lại, nên xin dư cho đỡ phải xin lần hai. Nhưng cái giá đã rõ:
# màn hình Đồng ý của một bot biên bản họp đòi quyền GHI Docs, GHI Drive, xoá
# comment, đổi permission — người mới nhìn thấy thì ngại bấm (đã gặp thật), và
# máy local giữ refresh token với bề mặt đó.
#
# `base:` và `im:` cố ý KHÔNG có: V2 gọi Base và gửi tin bằng TENANT token, cho
# vào OAUTH_SCOPES chỉ làm dài URL chứ không thêm khả năng gì.
# `drive:` cả họ đã bỏ: đường duy nhất chạm Drive là `base_media_upload`, và nó
# đi bằng `docs:document.media:upload` (đo 31/07 — xem chú thích config.py).
#
# Mỗi dòng dưới đây phải ứng với một lời gọi có thật trong `_checks()`. Thêm
# scope mà không thêm phép kiểm là quay lại đúng chỗ cũ: không ai biết nó còn
# cần hay không.
SCOPES_NEEDED = (
    "offline_access",                    # BẮT BUỘC đứng đầu, thiếu = không có refresh_token
    # phát hiện + đọc + tải bản ghi cuộc họp
    "minutes:minutes:readonly",
    "minutes:minutes.basic:read",
    "minutes:minutes.search:read",
    "minutes:minutes.media:export",
    "minutes:minutes.transcript:export",
    # tra người dự: lịch chính -> sự kiện -> người được mời
    "calendar:calendar:readonly",
    "calendar:calendar:read",
    "calendar:calendar.event:read",
    # nối sự kiện lịch <-> cuộc họp VC <-> minute_token
    "vc:meeting:readonly",
    "vc:record:readonly",
    "vc:recording:read",
    # tên + user_id cho ô "Chủ cuộc họp" / "Người dự" trên Base
    "contact:user.base:readonly",
    "contact:user.id:readonly",
    "contact:user.employee_id:readonly",
    # tạo việc cần làm từ cuộc họp (v2/tasks.py)
    "task:task:write",
    "task:task:read",
    # đính file transcript vào ô attachment của Base
    "docs:document.media:upload",
)

# Trần thực tế của URL authorize, ĐO chứ không suy (31/07/2026, lặp 2 lần mỗi
# mốc để loại nhiễu):
#
#     124 scope ->  3.822 ký tự -> 302  OK
#     181 scope ->  6.137 ký tự -> 302  OK
#     201 scope ->  6.915 ký tự -> 302  OK      <- mốc cao nhất còn chạy
#     222 scope ->  7.523 ký tự -> 502  HỎNG    <- bắt đầu gãy ở đây
#     301 scope ->  9.691 ký tự -> 400  HỎNG
#     411 scope -> 12.761 ký tự -> 400  HỎNG
#
# ⚠️ Bản đầu tôi đặt 8192 (con số sách vở của nhiều proxy) và ĐÓ LÀ SAI: URL
# 7.523 ký tự lọt qua guard nhưng Lark trả 502. Một cái guard nói "OK" cho thứ
# thực tế hỏng còn tệ hơn không có guard, vì nó tạo niềm tin sai. Nay lấy 7.000
# — nằm giữa mốc cao nhất chạy được (6.915) và mốc đầu tiên gãy (7.523).
#
# Hai kiểu gãy khác nhau, đừng nhầm: 400 = URL quá dài, server không parse nổi.
# 502 = đi qua được cửa đầu rồi chết ở trong. Cả hai đều là hỏng với người dùng.
_URL_LIMIT = 7000


def print_all_scopes(everything: bool = False) -> int:
    """In dòng `OAUTH_SCOPES=` để dán vào `v2/.env`, KÈM phép đo độ dài URL.

    Vì sao là lệnh chứ không phải hằng số trong code: danh sách đổi mỗi khi
    admin duyệt thêm/bớt ở Console (đã thấy nhảy 244 -> 410 chỉ sau một lần
    publish version). Dán cứng vào repo là cầm chắc nó lệch mà không ai biết.

    Mặc định in đúng `SCOPES_NEEDED` (từng scope V2 thật sự gọi) và cảnh báo nếu
    Console chưa duyệt cái nào trong đó. `everything=True` lấy tất cả — hiện
    KHÔNG dùng được, và lệnh sẽ nói thẳng là hỏng thay vì để bạn phát hiện lúc
    gửi link cho đồng nghiệp.
    """
    import httpx
    from urllib.parse import urlencode
    try:
        tok = lark_api.tenant_token()
        resp = httpx.get(config.base_url() + "/open-apis/application/v6/scopes",
                         headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        rows = (resp.json().get("data") or {}).get("scopes") or []
    except Exception as exc:                     # noqa: BLE001
        print(f"Không đọc được danh sách scope: {exc}")
        return 1

    allu = sorted({x["scope_name"] for x in rows
                   if x.get("scope_type") == "user"
                   and x.get("grant_status") == 1})
    if not allu:
        print("Console chưa duyệt scope nào ở danh tính user.")
        return 1

    if everything:
        scopes = ["offline_access"] + allu
    else:
        # Danh sách TƯỜNG MINH, giữ nguyên thứ tự trong SCOPES_NEEDED.
        scopes = list(SCOPES_NEEDED)
        # Scope V2 cần mà Console CHƯA duyệt ở danh tính user = xin cũng không
        # được cấp, và đó là lỗi im lặng (người mới enroll thiếu đúng cái đó).
        # Nói ra ngay ở đây, vì đây là lệnh người ta chạy khi đổi danh sách.
        chua_duyet = [s for s in scopes
                      if s != "offline_access" and s not in allu]
        if chua_duyet:
            print(f"# ⚠ Console CHƯA duyệt {len(chua_duyet)} scope V2 cần "
                  f"(danh tính user) — xin cũng không được cấp:")
            for s_ in chua_duyet:
                print(f"#    {s_}")
            print("#    Console -> app -> Permissions -> thêm, rồi Create "
                  "version + admin duyệt.")
    s = " ".join(scopes)

    url = (config.base_url() + "/open-apis/authen/v1/authorize?" + urlencode({
        "client_id": config.APP_ID, "redirect_uri": config.OAUTH_REDIRECT_URI,
        "scope": s, "state": "x" * 32, "response_type": "code"}))

    print(f"# {len(scopes)} scope / {len(allu)} Console duyệt cho user"
          f"{' (LẤY HẾT)' if everything else ' (đúng những cái V2 gọi)'}")
    print(f"# URL authorize sẽ dài {len(url):,} ký tự (trần thực tế ~{_URL_LIMIT:,})")
    if len(url) > _URL_LIMIT:
        print(f"# ⛔ QUÁ DÀI — Lark sẽ trả HTTP 400, link enroll KHÔNG bấm được.")
        print(f"#    Bỏ --everything để lấy đúng {len(SCOPES_NEEDED)} scope V2 "
              f"thật sự gọi.")
    else:
        print(f"# còn dư {_URL_LIMIT - len(url):,} ký tự")
    print("# Dán đè dòng OAUTH_SCOPES trong v2/.env, rồi MỌI người enroll lại.")
    print("OAUTH_SCOPES=" + s)
    return 1 if len(url) > _URL_LIMIT else 0


def run(open_id: str = "") -> int:
    users = tokenstore.list_users(active_only=True)
    if open_id:
        users = [u for u in users if u["open_id"] == open_id]
        if not users:
            print(f"Không thấy ai active có open_id={open_id}")
            return 1
    if not users:
        print("Chưa có ai enroll active.")
        return 1

    print("Gọi từng endpoint bằng token CỦA HỌ với tham số cố ý sai.")
    print("Lark kiểm quyền trước khi kiểm tham số, nên lỗi tham số = ĐẠT.")
    print("Không tác dụng phụ: mọi id đều bịa.")

    bad = 0
    for u in users:
        if check_user(u):
            bad += 1

    print(f"\n{'=' * 68}")
    if bad:
        print(f"CÓ VẤN ĐỀ: {bad}/{len(users)} người thiếu quyền ở ít nhất một "
              f"đường.\nThêm scope vào OAUTH_SCOPES (v2/.env) rồi bảo họ "
              f"enroll LẠI — token cũ không tự có scope mới.")
        return 1
    print(f"ĐẠT: cả {len(users)} người qua được mọi cửa quyền V2 cần.")
    return 0
