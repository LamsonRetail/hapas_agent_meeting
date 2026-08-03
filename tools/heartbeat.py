"""
Heartbeat — thu thap trang thai CUC BO cua he MeetingxLark.

Hai cach dung chung ham `collect()`:
  * tools\\heartbeat.bat  -> ghi 1 dong/lan vao v2\\data\\logs\\heartbeat-<ngay>.log
    (Task V2_Heartbeat moi 5 phut + Startup VBS khi dang nhap)
  * tools\\heartbeat_monitor.py -> cua so song, hien lai moi 30s

Chi kiem CUC BO (khong goi Lark/LLM that, khong ton token):
  vong `v2 run` · whisper 8000 · hermes 8642 · scan moi nhat ·
  loi 15 phut qua (bo tieng on event VC) · so ngay refresh token con.
"""
from __future__ import annotations

import datetime
import glob
import os
import re
import socket
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGDIR = os.path.join(ROOT, "v2", "data", "logs")
HERMES_LOG = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "hermes", "logs", "gateway.log")
DB = os.path.join(ROOT, "v2", "data", "state.db")


def port_up(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def whisper_health() -> str:
    try:
        import httpx
        return str(httpx.get("http://127.0.0.1:8000/health",
                             timeout=3).status_code)
    except Exception:
        return "DOWN"


def v2_run_alive() -> int:
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process | Where-Object { "
             "$_.Name -eq 'python.exe' -and $_.CommandLine -match '-m v2 run' "
             "}).Count"],
            text=True, timeout=15, stderr=subprocess.DEVNULL).strip()
        return int(out or "0")
    except Exception:
        return -1


def latest_log(pattern: str) -> str | None:
    files = sorted(glob.glob(os.path.join(LOGDIR, pattern)),
                   key=os.path.getmtime, reverse=True)
    return files[0] if files else None


def scan_age_min(now: datetime.datetime) -> float | None:
    f = latest_log("v2-*.log")
    if not f:
        return None
    last = None
    for line in open(f, encoding="utf-8", errors="replace"):
        m = re.search(r"\[(\d\d):(\d\d):(\d\d)\] scan xong", line)
        if m:
            last = m.group(1, 2, 3)
    if not last:
        return None
    hh, mm, ss = (int(x) for x in last)
    t = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    if t > now:
        t -= datetime.timedelta(days=1)
    return round((now - t).total_seconds() / 60, 1)


def real_errors(path: str, now: datetime.datetime, minutes: int = 15) -> int:
    if not path or not os.path.exists(path):
        return -1
    cut = now - datetime.timedelta(minutes=minutes)
    n = 0
    IGN = ("processor not found", "all_meeting_started",
           "all_meeting_ended", "meeting_room.status_changed")
    for line in open(path, encoding="utf-8", errors="replace"):
        if "ERROR" not in line:
            continue
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", line)
        if m:
            t = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            if t < cut:
                continue
        if any(k in line for k in IGN):
            continue
        n += 1
    return n


def min_token_days() -> float | None:
    try:
        import sqlite3
        c = sqlite3.connect(DB)
        rows = [r[0] for r in c.execute("select refresh_exp from tokens")
                if r[0]]
        c.close()
        if not rows:
            return None
        soonest = min(float(v) for v in rows)
        if soonest > 1e12:
            soonest /= 1000.0
        exp = datetime.datetime.fromtimestamp(soonest, datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        return round((exp - now).total_seconds() / 86400, 1)
    except Exception:
        return None


def collect() -> dict:
    """Thu thap tat ca chi so + tinh muc OK/WARN/FAIL. Tinh `now` moi lan."""
    now = datetime.datetime.now()
    d = dict(
        now=now,
        v2run=v2_run_alive(),
        whisper=whisper_health(),
        hermes=port_up(8642),
        scan=scan_age_min(now),
        gwerr=real_errors(HERMES_LOG, now),
        v2err=real_errors(latest_log("v2-*.log") or "", now),
        tokens=min_token_days(),
    )
    p = []
    if d["v2run"] == 0:
        p.append("v2run=TAT")
    if d["whisper"] != "200":
        p.append("whisper=" + d["whisper"])
    if not d["hermes"]:
        p.append("hermes8642=DOWN")
    if d["scan"] is not None and d["scan"] > 20:
        p.append(f"scan_cu={d['scan']}m")
    if d["gwerr"] and d["gwerr"] > 0:
        p.append(f"gwerr15m={d['gwerr']}")
    if d["v2err"] and d["v2err"] > 0:
        p.append(f"v2err15m={d['v2err']}")
    if d["tokens"] is not None and d["tokens"] < 2:
        p.append(f"token={d['tokens']}d")
    d["problems"] = p
    d["level"] = "OK" if not p else (
        "FAIL" if any(x.startswith(("v2run", "whisper", "hermes"))
                      for x in p) else "WARN")
    return d


def format_line(d: dict) -> str:
    line = (f"[{d['now']:%Y-%m-%d %H:%M:%S}] {d['level']:4} | "
            f"v2run={d['v2run']} whisper={d['whisper']} "
            f"hermes={'up' if d['hermes'] else 'DOWN'} scan={d['scan']}m "
            f"gwerr15m={d['gwerr']} v2err15m={d['v2err']} tokens={d['tokens']}d")
    if d["problems"]:
        line += "  << " + " ".join(d["problems"])
    return line


def main() -> int:
    d = collect()
    line = format_line(d)
    os.makedirs(LOGDIR, exist_ok=True)
    hb = os.path.join(LOGDIR, f"heartbeat-{d['now']:%Y-%m-%d}.log")
    with open(hb, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if d["level"] != "OK":
        print(line)
    return 0 if d["level"] == "OK" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
