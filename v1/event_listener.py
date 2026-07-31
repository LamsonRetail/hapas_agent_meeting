#!/usr/bin/env python3
"""
Nghe event từ Lark qua persistent connection (WebSocket).

Không cần địa chỉ công khai, không cần ngrok/cloudflared.

CÁCH HOẠT ĐỘNG
    Event chỉ đóng vai trò chuông báo. Nhận được event thì gọi ngay
    run_once() của poller thay vì chờ hết chu kỳ 5 phút. Nhờ vậy:
      - Không phải đoán cấu trúc payload của từng loại event
      - Dùng lại toàn bộ logic dò lịch, lấy transcript, gửi bot
      - Chống trùng tự động qua poller_state.json
      - Event rớt vẫn không mất việc, vì poller vẫn quét định kỳ

CHUẨN BỊ
    pip install lark-oapi

    Đặt biến môi trường (app "Agent meeting"):
        set EVENT_APP_ID=cli_aae288361ef89eed
        set EVENT_APP_SECRET=<secret cua app do>

    App phải được publish và anh Thiện approve thì event mới bắn.

CHẠY
    python event_listener.py

    Chạy song song với run-meeting-note.bat. Hai bên bổ trợ nhau:
    listener cho nhanh, poller cho chắc.
"""

import os
import sys
import threading
import time
from datetime import datetime

try:
    import lark_oapi as lark
except ImportError:
    print("Thiếu thư viện. Chạy:  pip install lark-oapi", file=sys.stderr)
    sys.exit(1)

import meeting_poller

APP_ID = os.environ.get("EVENT_APP_ID", "")
APP_SECRET = os.environ.get("EVENT_APP_SECRET", "")

# Các event đã đăng ký trong Console
EVENTS = [
    "minutes.minute.generated_v1",                    # cái chính, thay polling
    "vc.meeting.all_meeting_ended_v1",                # toàn công ty, để quan sát
    "vc.recording.recording_transcript_generated_v1",  # dự phòng
]

# Chờ bao lâu sau khi nhận event mới quét.
# Lark cần chút thời gian để minute sẵn sàng hoàn toàn.
DELAY_SECONDS = 30

# Không cho hai vòng quét chạy chồng lên nhau
_lock = threading.Lock()
_pending = threading.Event()


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def trigger_scan(reason: str) -> None:
    """Hẹn một vòng quét. Nhiều event dồn dập chỉ quét một lần."""
    if _pending.is_set():
        print(f"[{now()}] {reason} -> đã có vòng quét đang chờ, gộp lại")
        return

    _pending.set()

    def worker() -> None:
        time.sleep(DELAY_SECONDS)
        _pending.clear()
        with _lock:
            print(f"[{now()}] bắt đầu quét (kích hoạt bởi {reason})")
            try:
                found = meeting_poller.run_once()
                if not found:
                    print(f"[{now()}] quét xong, chưa có minute mới. "
                          f"Có thể Lark chưa xử lý kịp, poller sẽ bắt sau.")
            except Exception as exc:  # noqa: BLE001
                print(f"[{now()}] [lỗi] quét hỏng: {exc}", file=sys.stderr)

    threading.Thread(target=worker, daemon=True).start()
    print(f"[{now()}] {reason} -> sẽ quét sau {DELAY_SECONDS}s")


def on_event(data) -> None:
    """Nhận mọi event đã đăng ký."""
    try:
        event_type = data.header.event_type
    except AttributeError:
        event_type = "?"

    print(f"\n[{now()}] === EVENT: {event_type} ===")

    # In nguyên payload để biết Lark thực sự gửi gì.
    # Xem xong vài lần thì có thể bỏ dòng này cho đỡ rối.
    try:
        print(f"          {lark.JSON.marshal(data.event)[:600]}")
    except Exception:  # noqa: BLE001
        pass

    if event_type == "vc.meeting.all_meeting_ended_v1":
        # Chỉ quan sát. Scope này là tenant token nhưng đọc minute lại
        # cần user token, nên cuộc họp mình không dự vẫn không đọc được.
        print("          (chỉ ghi nhận, không kích hoạt quét)")
        return

    trigger_scan(event_type)


def main() -> None:
    if not APP_ID or not APP_SECRET:
        print("Thiếu EVENT_APP_ID hoặc EVENT_APP_SECRET.", file=sys.stderr)
        print("Đặt biến môi trường rồi chạy lại. Xem hướng dẫn đầu file.",
              file=sys.stderr)
        sys.exit(1)

    print()
    print("  Lark event listener")
    print("  " + "-" * 52)
    print(f"  App        : {APP_ID}")
    print(f"  Nguồn       : {meeting_poller.TRANSCRIPT_SOURCE}")
    print(f"  Chế độ      : {'THỬ (không gửi thật)' if meeting_poller.DRY_RUN else 'GỬI THẬT'}")
    print(f"  Trễ sau event: {DELAY_SECONDS}s")
    print("  Event nghe  :")
    for ev in EVENTS:
        print(f"    - {ev}")
    print("  " + "-" * 52)
    print()

    meeting_poller.check_auth()

    handler_builder = lark.EventDispatcherHandler.builder("", "")
    for ev in EVENTS:
        handler_builder = handler_builder.register_p2_customized_event(ev, on_event)
    handler = handler_builder.build()

    client = lark.ws.Client(
        APP_ID, APP_SECRET,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )

    print(f"[{now()}] đang kết nối tới Lark...")
    print("Ctrl+C để dừng.\n")
    client.start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDừng.")
