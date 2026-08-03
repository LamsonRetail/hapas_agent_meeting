"""
Cua so song hien trang thai heartbeat — go lai moi 30s, to mau OK/WARN/FAIL.

Chay:  tools\\heartbeat-monitor.bat   (hoac  python tools\\heartbeat_monitor.py)
Doi nhip:  python tools\\heartbeat_monitor.py 15   (15 giay)
Ctrl+C de dong.

Dung chung `collect()` voi heartbeat.py nen so lieu khong lech ban ghi log.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heartbeat  # noqa: E402

# --- bat ANSI mau tren console Windows ---
try:
    import ctypes
    k = ctypes.windll.kernel32
    h = k.GetStdHandle(-11)
    mode = ctypes.c_uint()
    k.GetConsoleMode(h, ctypes.byref(mode))
    k.SetConsoleMode(h, mode.value | 0x0004)   # VIRTUAL_TERMINAL_PROCESSING
except Exception:
    pass

R = "\033[0m"; B = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"; CYAN = "\033[36m"
LEVEL_COLOR = {"OK": GREEN, "WARN": YELLOW, "FAIL": RED}
W = 44                                          # be rong khung


def c(val: str, color: str) -> str:
    return f"{color}{val}{R}"


def render(d: dict) -> str:
    lv = d["level"]
    lc = LEVEL_COLOR.get(lv, "")
    # mau tung o
    whisper = c(str(d["whisper"]), GREEN if d["whisper"] == "200" else RED)
    v2run = c(str(d["v2run"]), GREEN if d["v2run"] == 1 else RED)
    hermes = c("up", GREEN) if d["hermes"] else c("DOWN", RED)
    scan = "?" if d["scan"] is None else (
        c(f"{d['scan']}m", GREEN if d["scan"] <= 20 else YELLOW))
    gw = d["gwerr"]; v2e = d["v2err"]
    gwc = c(str(gw), GREEN if gw == 0 else RED)
    v2c = c(str(v2e), GREEN if v2e == 0 else RED)
    tok = "?" if d["tokens"] is None else (
        c(f"{d['tokens']} ngay", GREEN if d["tokens"] >= 2 else RED))

    bar = "  " + "─" * W
    lines = [
        "",
        f"  {B}MeetingxLark — HEARTBEAT{R}        "
        f"{DIM}{d['now']:%H:%M:%S}{R}",
        bar,
        f"   TRANG THAI:  {lc}{B}● {lv}{R}",
        f"   vong run   : {v2run}        whisper : {whisper}",
        f"   hermes 8642: {hermes}       scan    : {scan}",
        f"   loi 15p    : gw {gwc} / v2 {v2c}",
        f"   token con  : {tok}",
        bar,
    ]
    if d["problems"]:
        lines.append(f"   {RED}{B}! {' · '.join(d['problems'])}{R}")
    lines.append(f"   {DIM}cap nhat moi {INTERVAL}s · Ctrl+C de dong{R}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    global INTERVAL
    INTERVAL = 30
    if len(sys.argv) > 1:
        try:
            INTERVAL = max(5, int(sys.argv[1]))
        except ValueError:
            pass
    try:
        while True:
            d = heartbeat.collect()
            sys.stdout.write("\033[2J\033[H")   # xoa + ve dau
            sys.stdout.write(render(d))
            sys.stdout.flush()
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        sys.stdout.write(R + "\n  (dong)\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
