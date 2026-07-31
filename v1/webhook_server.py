#!/usr/bin/env python3
"""
Server nhận event callback từ Lark.

Hai việc:
  1. Trả lời challenge để Lark cho lưu Request URL trong Console
  2. Ghi lại mọi event nhận được vào events.jsonl để xem Lark gửi những gì

Chỉ dùng thư viện chuẩn của Python, không cần pip install.

Chạy:
    python webhook_server.py
    python webhook_server.py --port 8001

Sau đó mở cửa sổ khác:
    ngrok http 8001

Lay dia chi https ngrok in ra, dien vao Console:
    Events & Callbacks -> Request URL: https://<ngrok>.ngrok-free.app/lark

LUU Y: de trong o Encrypt Key trong Console. Co Encrypt Key thi Lark ma
hoa event bang AES, ma Python chuan khong giai ma duoc.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG_FILE = Path("events.jsonl")

# Dien Verification Token tu Console de kiem tra event that su tu Lark.
# De trong thi bo qua buoc kiem tra (chap nhan duoc khi dang test).
VERIFICATION_TOKEN = ""


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log_event(payload: dict) -> None:
    """Ghi event ra file de xem lai sau."""
    record = {
        "received_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
    }
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def describe(payload: dict) -> str:
    """Tom tat event cho de doc tren man hinh."""
    header = payload.get("header", {})
    event_type = header.get("event_type") or payload.get("type") or "?"

    event = payload.get("event", {})
    bits = [f"event: {event_type}"]

    # bới vài trường hay gặp để nhìn nhanh
    for key in ("meeting", "object", "minute"):
        node = event.get(key)
        if isinstance(node, dict):
            for k in ("id", "meeting_id", "topic", "minute_token", "token"):
                if node.get(k):
                    bits.append(f"{k}={node[k]}")
    return "  |  ".join(bits)


class Handler(BaseHTTPRequestHandler):

    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        """Cho de mo trinh duyet kiem tra server con song."""
        self._send(200, {
            "status": "ok",
            "message": "Lark webhook server dang chay",
            "post_to": "/lark",
        })

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[{now()}] nhan goi tin khong phai JSON, bo qua")
            self._send(400, {"error": "invalid json"})
            return

        # --- Truong hop 1: Lark ma hoa event ---
        if "encrypt" in payload:
            print(f"\n[{now()}] Lark gui event DA MA HOA.")
            print("          Vao Console -> Events & Callbacks -> xoa trong")
            print("          o Encrypt Key, roi thu lai.\n")
            self._send(200, {"msg": "encrypt not supported"})
            return

        # --- Truong hop 2: Lark xac minh URL ---
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge", "")
            token = payload.get("token", "")

            if VERIFICATION_TOKEN and token != VERIFICATION_TOKEN:
                print(f"[{now()}] challenge nhung token khong khop, tu choi")
                self._send(403, {"error": "bad token"})
                return

            print(f"\n[{now()}] LARK XAC MINH URL -> tra challenge")
            print(f"          challenge = {challenge[:40]}...")
            print("          Neu Console bao thanh cong la xong buoc nay.\n")
            self._send(200, {"challenge": challenge})
            return

        # --- Truong hop 3: event that ---
        log_event(payload)
        print(f"[{now()}] {describe(payload)}")
        print(f"          da ghi vao {LOG_FILE}")

        # Phai tra 200 nhanh, neu khong Lark se gui lai nhieu lan
        self._send(200, {"msg": "ok"})

    def log_message(self, fmt, *args) -> None:
        """Tat log mac dinh cua http.server cho do roi man hinh."""
        return


def main() -> None:
    ap = argparse.ArgumentParser(description="Lark webhook server")
    ap.add_argument("--port", type=int, default=8001,
                    help="cong lang nghe (mac dinh 8001, tranh 8000 cua Whisper)")
    args = ap.parse_args()

    print()
    print("  Lark webhook server")
    print("  " + "-" * 50)
    print(f"  Lang nghe    : http://localhost:{args.port}")
    print(f"  Duong dan    : /lark  (hoac bat ky duong dan nao)")
    print(f"  Ghi event    : {LOG_FILE.resolve()}")
    if VERIFICATION_TOKEN:
        print("  Kiem tra token: BAT")
    else:
        print("  Kiem tra token: TAT (dien VERIFICATION_TOKEN de bat)")
    print("  " + "-" * 50)
    print()
    print("  Buoc tiep theo:")
    print(f"    1. Mo cua so khac, chay:  ngrok http {args.port}")
    print("    2. Copy dia chi https ngrok in ra")
    print("    3. Console -> Events & Callbacks -> Request URL:")
    print("       https://<ngrok>.ngrok-free.app/lark")
    print("    4. De TRONG o Encrypt Key")
    print()
    print("  Ctrl+C de dung.")
    print()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Da dung.")
        server.shutdown()


if __name__ == "__main__":
    main()
