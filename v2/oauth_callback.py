"""
Máy chủ callback OAuth cục bộ — intake đơn giản nhất cho bản đầu.

redirect_uri trỏ về http://localhost:<PORT>/oauth/callback (qua Cloudflare
Tunnel nếu cần địa chỉ công khai). Lark gọi vào kèm ?code=&state=, server đổi
code lấy token ngay tại máy — token KHÔNG đi qua bên thứ ba.

Đây là phương án tunnel/localhost (V2_ARCHITECTURE §3, mục "Cloudflare
Tunnel"). Muốn dùng hộp thư Vercel (không mở cổng nào) thì thay module này
bằng một vòng poll /api/oauth/pending — phần oauth.complete() giữ nguyên.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from . import oauth


class _Handler(BaseHTTPRequestHandler):
    result: dict = {}
    done = threading.Event()

    def log_message(self, *_):           # tắt log mặc định của http.server
        pass

    def _reply(self, html: str, code: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:            # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in ("/oauth/callback", "/callback"):
            self._reply("<h3>Not found</h3>", 404)
            return
        q = parse_qs(parsed.query)
        code = (q.get("code") or [""])[0]
        state = (q.get("state") or [""])[0]
        if not code:
            self._reply("<h3>Thiếu code</h3>", 400)
            return
        try:
            info = oauth.complete(code, state)
            _Handler.result = {"ok": True, "info": info}
            self._reply(f"<h2>Đã kết nối ✓</h2><p>Xin chào "
                        f"{info.get('name', '')}. Từ giờ biên bản sẽ tự về.</p>"
                        f"<p>Có thể đóng tab này.</p>")
        except Exception as exc:         # noqa: BLE001
            _Handler.result = {"ok": False, "error": str(exc)}
            self._reply(f"<h2>Lỗi kết nối</h2><p>{exc}</p>"
                        f"<p>Bấm lại link enroll để thử lại.</p>", 400)
        finally:
            _Handler.done.set()


def wait_for_one(port: int, timeout: float = 600) -> dict:
    """Chạy server, chờ đúng một callback rồi trả kết quả và tắt."""
    _Handler.result = {}
    _Handler.done.clear()
    srv = HTTPServer(("0.0.0.0", port), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    got = _Handler.done.wait(timeout)
    srv.shutdown()
    if not got:
        return {"ok": False, "error": "hết thời gian chờ callback"}
    return _Handler.result
