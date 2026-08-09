"""Khóa liên-process cho tiến trình ``python -m v2 run``.

Batch quét process chỉ là lớp UX: nó có cửa race giữa lúc kiểm tra và lúc spawn,
và có thể fail-open khi WMI/CIM lỗi. Khóa ở đây được lấy nguyên tử trước khi mở
DB/chạy vòng orchestrator và được hệ điều hành tự nhả khi process chết.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import BinaryIO


EXIT_ALREADY_RUNNING = 73


class AlreadyRunning(RuntimeError):
    """Một orchestrator khác đang giữ khóa của đúng DB này."""


class ProcessLock:
    def __init__(self, handle=None, file_obj: BinaryIO | None = None):
        self._handle = handle
        self._file = file_obj
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if os.name == "nt" and self._handle:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
            kernel32.ReleaseMutex.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.ReleaseMutex(self._handle)
            kernel32.CloseHandle(self._handle)
            self._handle = None
        elif self._file is not None:
            import fcntl
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            self._file.close()
            self._file = None

    def __enter__(self) -> "ProcessLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - lưới an toàn khi shutdown
        try:
            self.close()
        except Exception:
            pass


def _identity(db_path: Path) -> str:
    absolute = str(db_path.expanduser().resolve()).casefold().encode("utf-8")
    return hashlib.sha256(absolute).hexdigest()[:24]


def acquire(db_path: Path) -> ProcessLock:
    """Lấy khóa nguyên tử; lỗi lấy khóa cũng fail-closed, không tự cho chạy."""
    identity = _identity(db_path)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL,
                                          wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        name = f"Local\\MeetingxLark-V2-{identity}"
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, True, name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "không tạo được mutex V2")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            raise AlreadyRunning(f"orchestrator khác đang giữ mutex {name}")
        return ProcessLock(handle=handle)

    # Nhánh dành cho Linux/macOS khi chạy test/di trú. File tồn tại không có
    # nghĩa là bị khóa; flock mới là nguồn sự thật và tự nhả khi process chết.
    lock_path = db_path.expanduser().resolve().with_suffix(".orchestrator.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    file_obj = open(lock_path, "a+b")
    try:
        import fcntl
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        file_obj.close()
        raise AlreadyRunning(f"orchestrator khác đang giữ {lock_path}") from exc
    return ProcessLock(file_obj=file_obj)
