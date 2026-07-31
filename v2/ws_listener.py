"""
Persistent WebSocket (lark_oapi) — đường NHANH, tùy chọn (V2_ARCHITECTURE §10).

Không cần địa chỉ công khai, không ngrok. Nghe MỘT loại event:
  - minutes.minute.generated_v1  -> gọi orchestrator.enqueue_minute() sớm

Nguyên tắc (§10): event chỉ là "chuông báo". Polling vẫn là nguồn sự thật;
event rớt thì vòng quét sau vẫn bắt được. Nên listener này được phép chết mà
hệ thống không sai — chỉ chậm tối đa POLL_INTERVAL.

ĐỪNG bật `run --ws` khi Hermes đang chạy trên CÙNG app_id (cấu hình hiện tại,
docs §12). Lark KHÔNG từ chối kết nối thứ hai — đã đo, cả hai đều connect — nên
sẽ không có lỗi nào báo cho bạn biết. Vấn đề là không có gì bảo đảm event tới cả
hai kết nối; nếu Lark chia đều thì V2 vẫn đúng (polling đỡ), nhưng **Hermes mất
tin nhắn và không có gì đỡ**. Chính vì tính "được phép chết" ở trên mà nhường
WebSocket cho Hermes là lựa chọn đúng, không phải nhượng bộ.

Không còn nghe `card.action.trigger`: cửa duyệt đã bỏ 30/07/2026 nên thẻ gửi ra
không có nút nào. Nếu sau này lại làm thẻ có nút (vd bot Q&A), LƯU Ý: phải dùng
`register_p2_card_action_trigger`, KHÔNG dùng `register_p1_customized_event` —
loại sau bỏ giá trị trả về nên nút bấm im lặng. Và phải bật Console tab
**Callbacks** (long connection), là cấu hình RIÊNG với tab Events; thiếu thì ra
lỗi 200340. Bản đã chạy được: xem git commit "bỏ cửa duyệt" trở về trước.

[VERIFY] Tên event minutes.minute.generated_v1 phải bật ở Console tab Events.
"""

from __future__ import annotations

import json
import threading

from . import config, orchestrator


def _on_minute_generated(data: dict) -> None:
    """Payload event -> lấy minute_token + open_id người nhận event -> enqueue."""
    try:
        ev = data.get("event", data)
        minute_token = (ev.get("minute_token") or ev.get("token")
                        or ev.get("object_token") or "")
        # open_id người sở hữu/nhận event: để mượn token đọc.
        oid = (ev.get("owner_id", {}) or {}).get("open_id", "") \
            or (ev.get("operator_id", {}) or {}).get("open_id", "")
        if not minute_token or not oid:
            print(f"[ws] minute event thiếu token/open_id: {ev}")
            return
        print(f"[ws] minute.generated {minute_token} (chuông báo)")
        orchestrator.enqueue_minute(oid, minute_token)
        orchestrator.process_queue()
    except Exception as exc:             # noqa: BLE001
        print(f"[ws] xử lý minute event hỏng: {exc}")


def start() -> None:
    """Khởi động WS client. Chặn (blocking) — chạy trong thread/tiến trình riêng."""
    if not (config.APP_ID and config.APP_SECRET):
        print("[ws] thiếu APP_ID/APP_SECRET — không bật WebSocket.")
        return
    try:
        import lark_oapi as lark
    except ImportError:
        print("[ws] chưa cài lark-oapi — bỏ qua WebSocket.")
        return

    def _wrap_minute(raw) -> None:
        try:
            payload = json.loads(raw.event) if hasattr(raw, "event") else {}
        except Exception:                # noqa: BLE001
            payload = {}
        _on_minute_generated(payload)

    handler = (lark.EventDispatcherHandler.builder("", "")
               # customized event theo string key -> không lệ thuộc SDK version
               .register_p1_customized_event(
                   "minutes.minute.generated_v1", _wrap_minute)
               .build())

    domain = (lark.LARK_DOMAIN if config.LARK_DOMAIN == "lark"
              else lark.FEISHU_DOMAIN) if hasattr(lark, "LARK_DOMAIN") else None
    cli = lark.ws.Client(config.APP_ID, config.APP_SECRET,
                         event_handler=handler,
                         **({"domain": domain} if domain else {}))
    print("[ws] kết nối WebSocket... (Ctrl+C để dừng)")
    cli.start()


def start_in_thread() -> threading.Thread:
    t = threading.Thread(target=start, daemon=True, name="ws-listener")
    t.start()
    return t
