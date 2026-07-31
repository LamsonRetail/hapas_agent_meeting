#!/usr/bin/env python3
"""
Gửi tin nhắn Lark bằng app riêng (không qua lark-cli).

lark-cli chỉ gắn được một app, mà app đó là của anh Thiện. Muốn gửi
bằng app "Agent meeting" của mình thì phải gọi API trực tiếp.

Phần ĐỌC dữ liệu vẫn qua lark-cli với user token, không đụng tới.

CHUẨN BỊ TRONG CONSOLE
    1. Add Features -> Bot -> Add
    2. Permissions & Scopes -> thêm im:message (Tenant token)
    3. Create Version -> chờ duyệt

BIẾN MÔI TRƯỜNG
    SENDER_APP_ID       app_id của app dùng để gửi
    SENDER_APP_SECRET   app_secret tương ứng

Chạy thử:
    python lark_sender.py ou_1bc55b6d5b20ee06cbee1326d5b72715
"""

import json
import mimetypes
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "https://open.larksuite.com/open-apis"

APP_ID = os.environ.get("SENDER_APP_ID", "")
APP_SECRET = os.environ.get("SENDER_APP_SECRET", "")

# open_id gắn với TỪNG app: mã do app của anh Thiện cấp không dùng được
# với app của mình (lỗi "open_id cross app").
# union_id dùng chung giữa các app cùng tenant và KHÔNG cần thêm quyền,
# trong khi user_id đòi scope contact:user.employee_id:readonly.
RECEIVE_ID_TYPE = os.environ.get("SENDER_ID_TYPE", "union_id")


class SendError(RuntimeError):
    pass


# --------------------------------------------------------------- token

_token: str | None = None
_token_expires_at: float = 0.0
_token_lock = threading.Lock()


def _tenant_token() -> str:
    """Lấy tenant_access_token, có cache. Token sống 2 tiếng."""
    global _token, _token_expires_at

    with _token_lock:
        if _token and time.time() < _token_expires_at - 300:
            return _token

        if not APP_ID or not APP_SECRET:
            raise SendError(
                "Thiếu SENDER_APP_ID hoặc SENDER_APP_SECRET.\n"
                "Điền vào config.bat rồi chạy lại."
            )

        req = urllib.request.Request(
            f"{BASE}/auth/v3/tenant_access_token/internal",
            data=json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}
                            ).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise SendError(f"không lấy được token: {exc}") from exc

        if out.get("code") != 0:
            raise SendError(f"lấy token lỗi {out.get('code')}: {out.get('msg')}")

        _token = out["tenant_access_token"]
        _token_expires_at = time.time() + int(out.get("expire", 7200))
        return _token


def _headers(extra: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {_tenant_token()}"}
    if extra:
        h.update(extra)
    return h


def _post_json(path: str, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_headers({"Content-Type": "application/json; charset=utf-8"}),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise SendError(f"{path} lỗi HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SendError(f"{path} không kết nối được: {exc}") from exc

    if out.get("code") != 0:
        raise SendError(f"{path} lỗi {out.get('code')}: {out.get('msg')}")
    return out.get("data", {})


# ------------------------------------------------------------ gửi tin


def send_markdown(receive_id: str, title: str, body: str,
                  idem_key: str | None = None) -> str:
    """Gửi thẻ nội dung có định dạng markdown. Trả message_id."""
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:100]},
            "template": "blue",
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}}
        ],
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    if idem_key:
        payload["uuid"] = idem_key[:50]

    data = _post_json(f"/im/v1/messages?receive_id_type={RECEIVE_ID_TYPE}", payload)
    return data.get("message_id", "")


def send_text(receive_id: str, text: str, idem_key: str | None = None) -> str:
    """Gửi tin nhắn chữ thuần. Dùng khi thẻ không hiển thị được."""
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    if idem_key:
        payload["uuid"] = idem_key[:50]

    data = _post_json(f"/im/v1/messages?receive_id_type={RECEIVE_ID_TYPE}", payload)
    return data.get("message_id", "")


# ------------------------------------------------------- gửi kèm file


_FILE_TYPES = {".opus": "opus", ".mp4": "mp4", ".pdf": "pdf",
               ".doc": "doc", ".docx": "doc", ".xls": "xls",
               ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt"}


def upload_file(path: Path) -> str:
    """Tải file lên Lark, trả về file_key."""
    if not path.exists():
        raise SendError(f"không thấy file: {path}")

    size_mb = path.stat().st_size / 1_048_576
    if size_mb > 30:
        raise SendError(f"file {size_mb:.1f} MB, vượt giới hạn 30 MB của Lark")

    file_type = _FILE_TYPES.get(path.suffix.lower(), "stream")
    boundary = f"----lark{uuid.uuid4().hex}"
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    parts = bytearray()
    for key, val in (("file_type", file_type), ("file_name", path.name)):
        parts += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n{val}\r\n'
        ).encode("utf-8")

    parts += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{path.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    parts += path.read_bytes()
    parts += f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        f"{BASE}/im/v1/files",
        data=bytes(parts),
        headers=_headers({
            "Content-Type": f"multipart/form-data; boundary={boundary}"
        }),
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise SendError(f"upload lỗi HTTP {exc.code}: {detail}") from exc

    if out.get("code") != 0:
        raise SendError(f"upload lỗi {out.get('code')}: {out.get('msg')}")

    return out["data"]["file_key"]


def send_file(receive_id: str, path: Path, idem_key: str | None = None) -> str:
    """Tải file lên rồi gửi cho một người."""
    file_key = upload_file(path)
    payload = {
        "receive_id": receive_id,
        "msg_type": "file",
        "content": json.dumps({"file_key": file_key}),
    }
    if idem_key:
        payload["uuid"] = idem_key[:50]

    data = _post_json(f"/im/v1/messages?receive_id_type={RECEIVE_ID_TYPE}", payload)
    return data.get("message_id", "")


def send_file_to_many(open_ids: list[str], path: Path,
                      idem_prefix: str = "") -> dict[str, str]:
    """Gửi cùng một file cho nhiều người. Chỉ upload MỘT lần.

    Tiết kiệm đáng kể khi cuộc họp đông người: file_key dùng lại được.
    """
    file_key = upload_file(path)
    results = {}

    for open_id in open_ids:
        payload = {
            "receive_id": receive_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}),
        }
        if idem_prefix:
            payload["uuid"] = f"{idem_prefix}-{open_id}"[:50]
        try:
            data = _post_json(f"/im/v1/messages?receive_id_type={RECEIVE_ID_TYPE}", payload)
            results[open_id] = data.get("message_id", "")
        except SendError as exc:
            print(f"[warn] gửi file cho {open_id} hỏng: {exc}", file=sys.stderr)
            results[open_id] = ""

    return results


def ready() -> bool:
    """Có đủ cấu hình để gửi bằng app riêng không."""
    return bool(APP_ID and APP_SECRET)


# ------------------------------------------------------------ chạy thử


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nDùng: python lark_sender.py <id> [user_id|union_id|open_id]")
        sys.exit(1)

    target = sys.argv[1]
    if len(sys.argv) > 2:
        RECEIVE_ID_TYPE = sys.argv[2]
    print(f"App     : {APP_ID or '(chưa đặt)'}")
    print(f"Loại ID : {RECEIVE_ID_TYPE}")

    try:
        _tenant_token()
        print("Token   : OK")
    except SendError as exc:
        print(f"Token   : HỎNG - {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        mid = send_markdown(
            target,
            "Thử gửi tin",
            "Tin nhắn thử từ app riêng.\n\n"
            "**In đậm**, *in nghiêng*, và một [liên kết](https://larksuite.com).\n\n"
            "Nhận được tin này nghĩa là quyền `im:message` đã hoạt động.",
            idem_key=f"test-{uuid.uuid4().hex[:12]}",
        )
        print(f"Gửi     : OK  message_id={mid}")
    except SendError as exc:
        print(f"Gửi     : HỎNG - {exc}", file=sys.stderr)
        print("\nKiểm tra: đã bật Bot trong Add Features chưa, "
              "đã thêm scope im:message chưa, đã publish version chưa.",
              file=sys.stderr)
        sys.exit(1)
