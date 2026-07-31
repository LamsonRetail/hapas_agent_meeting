#!/usr/bin/env python3
"""
Kiểm tra toàn bộ cấu hình trước khi chạy luồng.

Chạy:
    cmd /c "call config.bat && python check_config.py"

Kiểm tra từng phần một, báo rõ cái nào hỏng và sửa thế nào.
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

OK = "  [OK]  "
NO = "  [LOI] "
WARN = "  [!]   "


def line(char: str = "-") -> None:
    print("  " + char * 58)


def check_python() -> bool:
    v = sys.version_info
    if v >= (3, 10):
        print(f"{OK}Python {v.major}.{v.minor}.{v.micro}")
        return True
    print(f"{NO}Python {v.major}.{v.minor} — cần 3.10 trở lên")
    return False


def check_lark_cli() -> bool:
    profile = os.environ.get("LARK_PROFILE", "")
    path = shutil.which("lark-cli")
    if not path:
        print(f"{NO}Không thấy lark-cli trong PATH")
        print("        Kiểm tra bằng: where lark-cli")
        return False
    print(f"{OK}lark-cli: {path}")
    print(f"        profile: {profile or '(mặc định — app anh Thiện)'}")

    try:
        cmd = [path, "auth", "status", "--json"]
        if profile:
            cmd += ["--profile", profile]
        proc = subprocess.run(cmd,
                              capture_output=True, text=True, timeout=30,
                              encoding="utf-8", errors="replace")
        info = json.loads(proc.stdout.strip())
    except Exception as exc:  # noqa: BLE001
        print(f"{NO}Không đọc được trạng thái đăng nhập: {exc}")
        return False

    user = info.get("identities", {}).get("user", {})
    bot = info.get("identities", {}).get("bot", {})

    if user.get("available"):
        print(f"{OK}Đăng nhập user: {user.get('userName', '?')}")
        if raw := user.get("refreshExpiresAt"):
            print(f"        token gia hạn tới {raw}")
    else:
        print(f"{NO}Chưa đăng nhập. Chạy: lark-cli auth login")
        return False

    print(f"{OK if bot.get('available') else WARN}"
          f"Bot của app {info.get('appId', '?')}: {bot.get('status', '?')}")
    return True


def check_whisper() -> bool:
    url = os.environ.get("WHISPER_URL", "http://localhost:8000")
    source = os.environ.get("TRANSCRIPT_SOURCE", "whisper")

    if source != "whisper":
        print(f"{WARN}Nguồn transcript = {source}, bỏ qua Whisper")
        return True

    try:
        with urllib.request.urlopen(f"{url}/health", timeout=10) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"{NO}Whisper server không phản hồi tại {url}")
        print(f"        Bật run-server.bat trước. ({exc})")
        return False

    model = info.get("model") or info.get("whisper_model") or "?"
    device = info.get("device") or "?"
    print(f"{OK}Whisper server: model={model} device={device}")
    if device == "cpu":
        print(f"{WARN}Chạy CPU — khoảng 0.6x realtime. "
              f"Họp 1 tiếng mất ~1 giờ 45.")
    return True


def check_openai() -> bool:
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    if not key:
        print(f"{NO}Chưa có OPENAI_API_KEY -> sẽ gửi transcript KHÔNG có recap")
        print("        Điền vào config.bat, không có dấu cách quanh dấu bằng")
        return False

    print(f"{OK}Có API key ({key[:7]}...{key[-4:]}), model={model}")

    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "Trả lời đúng một từ: OK"}],
            "max_tokens": 5,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        reply = out["choices"][0]["message"]["content"].strip()
        print(f"{OK}Gọi thử thành công, model trả về: {reply!r}")
        return True
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        print(f"{NO}Gọi API lỗi HTTP {exc.code}")
        print(f"        {body}")
        if exc.code == 401:
            print("        -> Key sai hoặc đã bị thu hồi")
        elif exc.code == 404:
            print(f"        -> Không có model {model}, đổi LLM_MODEL")
        elif exc.code == 429:
            print("        -> Hết hạn mức hoặc chưa nạp tiền")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"{NO}Gọi API hỏng: {exc}")
        return False


def check_sender() -> bool:
    app_id = os.environ.get("SENDER_APP_ID", "")
    secret = os.environ.get("SENDER_APP_SECRET", "")
    id_type = os.environ.get("SENDER_ID_TYPE", "union_id")

    if not app_id or not secret:
        print(f"{WARN}Chưa cấu hình app riêng -> gửi qua lark-cli (app anh Thiện)")
        return True

    req = urllib.request.Request(
        "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": secret}).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"{NO}Không lấy được token app riêng: {exc}")
        return False

    if out.get("code") != 0:
        print(f"{NO}App riêng lỗi {out.get('code')}: {out.get('msg')}")
        return False

    print(f"{OK}App riêng {app_id}: lấy token thành công")
    print(f"        gửi bằng {id_type}")
    return True


def check_send_mode() -> None:
    deliver_to = os.environ.get("DELIVER_TO")
    if deliver_to:
        print(f"{WARN}DELIVER_TO đang bật -> chỉ gửi cho {deliver_to}")
        print("        Thêm REM vào dòng đó để gửi cho toàn bộ người được mời")
    else:
        print(f"{OK}Gửi cho toàn bộ người được mời")


def main() -> None:
    print()
    print("  Kiểm tra cấu hình - Luồng meeting note")
    line("=")

    results = {
        "Python": check_python(),
        "Lark CLI": check_lark_cli(),
        "Whisper": check_whisper(),
        "OpenAI": check_openai(),
        "App gửi tin": check_sender(),
    }
    check_send_mode()

    line("=")
    hong = [k for k, v in results.items() if not v]
    if hong:
        print(f"  CHUA XONG — cần sửa: {', '.join(hong)}")
        sys.exit(1)
    print("  TAT CA OK — chạy được run-meeting-note.bat")
    print()


if __name__ == "__main__":
    main()
