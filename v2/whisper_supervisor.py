"""Giám sát nhẹ và tự bật lại Whisper local cho vòng ``v2 run``.

Module này cố ý chỉ có quyền *khởi động* script đã cấu hình. Nó không kill một
process còn sống và không đụng endpoint từ xa. Nếu health lỗi nhưng cổng vẫn mở
hoặc process do chính nó bật vẫn còn chạy, nó để watchdog/cảnh báo báo cho người
vận hành thay vì mạo hiểm tạo thêm một model CPU thứ hai.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from . import config

_lock = threading.Lock()
_process: subprocess.Popen | None = None
_last_start = float("-inf")
_last_missing_notice = ""


def is_local_url(url: str | None = None) -> bool:
    """True chỉ với loopback; không bao giờ spawn dịch vụ cho host từ xa."""
    host = (urlparse(url or config.TRANSCRIBE_URL).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _healthy() -> bool:
    try:
        import httpx
        response = httpx.get(
            config.TRANSCRIBE_URL.rstrip("/") + "/health", timeout=5.0)
        return response.status_code == 200 and (
            response.json() or {}).get("status") == "ok"
    except Exception:  # noqa: BLE001 - mọi kiểu không gọi được đều là chưa sẵn sàng
        return False


def _port_open() -> bool:
    """Cổng vẫn nhận TCP = có process nghe; đừng spawn thêm dù health đang lỗi."""
    parsed = urlparse(config.TRANSCRIBE_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _spawn(script: Path) -> subprocess.Popen:
    config.ensure_dirs()
    log_dir = config.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"whisper-{datetime.now():%Y-%m-%d}.log"
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        if os.name == "nt":
            comspec = os.environ.get("ComSpec", "cmd.exe")
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            command = [comspec, "/d", "/c", str(script)]
            return subprocess.Popen(  # noqa: S603 - path là cấu hình vận hành có chủ ý
                command, cwd=str(script.parent), stdin=subprocess.DEVNULL,
                stdout=log_handle, stderr=subprocess.STDOUT,
                creationflags=flags, close_fds=True)
        return subprocess.Popen(  # noqa: S603 - như trên, không qua shell
            [str(script)], cwd=str(script.parent), stdin=subprocess.DEVNULL,
            stdout=log_handle, stderr=subprocess.STDOUT, close_fds=True)
    finally:
        # Child đã duplicate handle; parent không giữ file mở suốt đời orchestrator.
        log_handle.close()


def ensure_running() -> str:
    """Bảo đảm Whisper local có cơ hội tự hồi phục.

    Kết quả dùng cho log/test: disabled, remote, healthy, listening, starting,
    cooldown, missing, started hoặc error. Không trạng thái nào ném lỗi ra vòng run.
    """
    global _process, _last_start, _last_missing_notice

    if not config.WHISPER_AUTOSTART:
        return "disabled"
    if not is_local_url():
        return "remote"
    if _healthy():
        return "healthy"

    with _lock:
        # Kiểm lại sau khi lấy lock để hai thread không cùng spawn.
        if _healthy():
            return "healthy"

        if _process is not None:
            rc = _process.poll()
            if rc is None:
                return "starting"
            print(f"[whisper] process tự khởi động đã thoát (mã {rc})")
            _process = None

        # Có listener nhưng /health chưa OK: có thể model đang nạp hoặc server bị
        # kẹt. Cả hai trường hợp đều không an toàn để bật thêm một bản.
        if _port_open():
            return "listening"

        now = time.monotonic()
        if now - _last_start < config.WHISPER_RESTART_COOLDOWN:
            return "cooldown"

        script = config.WHISPER_START_SCRIPT.expanduser().resolve()
        if not script.is_file():
            note = str(script)
            if note != _last_missing_notice:
                print(f"[whisper] không tự bật được: không thấy script {script}")
                _last_missing_notice = note
            return "missing"

        # Ghi mốc TRƯỚC spawn: ngay cả Popen lỗi cũng phải chịu cooldown, nếu
        # không một lỗi cấu hình sẽ tạo một lần thử mới ở mọi đường gọi/thread.
        _last_start = now
        try:
            _process = _spawn(script)
        except Exception as exc:  # noqa: BLE001 - tự cứu không được làm chết run
            print(f"[whisper] bật lại hỏng: {type(exc).__name__}: {exc}")
            return "error"
        print(f"[whisper] đã bật lại nền từ {script} (pid={_process.pid})")
        return "started"


def _reset_for_tests() -> None:
    """Xóa state trong RAM; chỉ selftest dùng, tuyệt đối không dừng child."""
    global _process, _last_start, _last_missing_notice
    _process = None
    _last_start = float("-inf")
    _last_missing_notice = ""
