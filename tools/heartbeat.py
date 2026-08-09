"""
Heartbeat — thu thap trang thai CUC BO cua he MeetingxLark.

Hai cach dung chung ham `collect()`:
  * tools\\heartbeat.bat  -> ghi 1 dong/lan vao v2\\data\\logs\\heartbeat-<ngay>.log
    (Task V2_Heartbeat moi 5 phut + Startup VBS khi dang nhap)
  * tools\\heartbeat_monitor.py -> cua so song, hien lai moi 30s

Chi kiem CUC BO (khong goi Lark/LLM that, khong ton token):

  SONG/CHET  vong `v2 run` · whisper 8000 (+ do tre) · hermes 8642
  TIEN DO    scan moi nhat · hang doi theo status · tuoi job ket lau nhat
  KET QUA    so ban da phat 24h qua · so nguoi dang dung bot
  TAI NGUYEN dia con trong · co state.db · RAM + gio chay cua vong run
  LOI        loi 15 phut qua (bo tieng on event VC) · so ngay refresh token con

VI SAO THEM NHIEU CHI SO (06/08/2026): ban cu chi tra loi duoc "he thong con
song khong". No khong bat duoc mot lop hong THUC TE nguy hiem hon: moi thu song
nhung KHONG CHAY DUOC VIEC — job ket o `transcribing` vai tieng, hang doi phinh
dan, dia day nen whisper ghi hong, DB phinh vi WAL khong checkpoint. Nhung cai
do khong lam port chet, nen `OK` ma he thong da dung tu lau.

QUY TAC KHI THEM CHI SO MOI:
  1. Cuc bo. Mot lan goi mang la mot lan heartbeat treo 5 phut mot.
  2. Re. No chay moi 5 phut, mai mai. Truy van SQLite phai co chi muc hoac
     nho; dung quet file lon.
  3. Hong thi tra None/-1, KHONG duoc nem. Heartbeat chet la mat luon canh bao.
"""
from __future__ import annotations

import datetime
import glob
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGDIR = os.path.join(ROOT, "v2", "data", "logs")
HERMES_LOG = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "hermes", "logs", "gateway.log")
DB = os.path.join(ROOT, "v2", "data", "state.db")

# Job o cac status nay dang cho / dang duoc XU LY. Khac han `waiting_auth` —
# cai do cho NGUOI, cho ca tuan cung binh thuong.
ACTIVE_STATUSES = ("queued", "transcribing", "recapping")
# Status muon dem de nhin hinh dang hang doi. `held` = da phien am xong, dang
# cho ai do hoi; `waiting_auth` = cho nguoi lien quan tu OAuth.
COUNT_STATUSES = ACTIVE_STATUSES + ("held", "waiting_auth", "failed")

# Ma loi KHONG can nguoi lam gi ca. PHAI trung `alerts.SILENT_FAIL_CODES` —
# co test doi chieu hai ben trong selftest (nhom 34l).
#
# Vi sao quan trong (do that 06/08/2026, ngay hom them cac chi so nay): bon job
# `failed` tren may deu la `empty_transcript` — ban ghi 3s/37s khong co tieng
# noi. Chung se `failed` VINH VIEN, nen dem ca chung vao canh bao nghia la
# heartbeat WARN mai mai. Mot canh bao khong bao gio tat la mot canh bao nguoi
# ta hoc cach bo qua, va roi bo qua luon lan hong that.
SILENT_FAIL_CODES = frozenset({"empty_transcript"})
_ERR_CODE_RE = re.compile(r"^\[([a-z_]+)\]\s*")

# Nguong canh bao. Dat o day, mot cho, de doi khoi phai doi ca `collect` lan
# ban do mau cua monitor.
DISK_WARN_GB = 10.0          # duoi muc nay thi whisper/backup bat dau rui ro
# "Bao lau khong phien am xong CUOC NAO, trong khi van con job cho" — xem
# `db_stats`. 180 phut la ~6 lan thoi gian dich mot cuoc dai nhat da gap
# (9802s audio, xong trong ~27 phut).
IDLE_WARN_MIN = 180.0
SCAN_WARN_MIN = 20.0
TOKEN_WARN_DAYS = 2.0


def port_up(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def whisper_health() -> tuple[str, int | None]:
    """(ma HTTP hoac 'DOWN', do tre ms).

    Do tre co ich rieng: whisper CPU van tra 200 nhung cham dan khi may bi
    chiem CPU, va do la dau hieu som cua "phien am mai khong xong" — thu ma
    ma trang thai khong noi duoc.
    """
    t0 = time.monotonic()
    try:
        import httpx
        code = str(httpx.get("http://127.0.0.1:8000/health",
                             timeout=3).status_code)
    except Exception:
        return "DOWN", None
    return code, int((time.monotonic() - t0) * 1000)


def v2_run_procs() -> tuple[int, float | None, float | None]:
    """(so vong `v2 run`, RAM MB cua vong dau, so gio da chay).

    Mot lan goi CIM lay ca ba: goi PowerShell la thu dat nhat trong ham
    `collect` (~1s), goi ba lan la lang phi thay ro.

    RAM va gio chay dung de bat ro ri va restart am tham: vong nay chay lien
    tuc nhieu ngay, RAM phinh dan hoac "gio chay" bat ngo ve 0 deu la chuyen
    can biet ma port song/chet khong noi duoc.
    """
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { "
             "$_.Name -eq 'python.exe' -and $_.CommandLine -match '-m v2 run' "
             "} | ForEach-Object { "
             "'{0};{1}' -f $_.WorkingSetSize, "
             "$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') }"],
            text=True, timeout=20, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return -1, None, None
    rows = [ln for ln in out.splitlines() if ln.strip()]
    if not rows:
        return 0, None, None
    mb = hours = None
    try:
        raw_mem, raw_start = rows[0].split(";", 1)
        mb = round(int(raw_mem) / 1048576, 1)
        started = datetime.datetime.strptime(raw_start.strip(),
                                             "%Y-%m-%d %H:%M:%S")
        hours = round((datetime.datetime.now() - started).total_seconds() / 3600, 1)
    except Exception:
        pass
    return len(rows), mb, hours


def disk_free_gb() -> float | None:
    """Dung luong trong con lai tren o chua v2\\data. None neu khong doc duoc."""
    try:
        return round(shutil.disk_usage(os.path.join(ROOT, "v2")).free / 2**30, 1)
    except Exception:
        return None


def db_size_mb() -> float | None:
    """Co state.db KE CA file -wal. WAL phinh = co ai do giu transaction mo."""
    try:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            path = DB + suffix
            if os.path.exists(path):
                total += os.path.getsize(path)
        return round(total / 2**20, 1) if total else None
    except Exception:
        return None


def _open_db():
    """Ket noi doc state.db, uu tien CHI ĐOC. None neu khong mo duoc.

    `mode=ro` truoc vi heartbeat la cong cu QUAN SAT: mo ghi vao DB dang chay
    thi chinh no co the tao -wal/-shm hoac cham vao khoa, tuc cong cu do dac
    gay ra su co no dang di tim.

    Nhung `mode=ro` co the HONG dung luc can nhat: DB o che do WAL ma khong con
    tien trinh nao mo no thi lan doc dau tien phai tao file -shm, va ket noi
    chi-doc khong lam duoc viec do. Tuc `v2 run` vua tat — dung luc ta muon
    biet hang doi con gi — thi moi chi so DB im lang bang 0. Nen co duong lui
    ve ket noi thuong (ban cu cua `min_token_days` van dang mo kieu nay).
    """
    for uri, kwargs in ((f"file:{DB}?mode=ro", {"uri": True}), (DB, {})):
        c = None
        try:
            c = sqlite3.connect(uri, timeout=5, **kwargs)
            # Doc thu MOT dong: `connect` khong cham vao file nen no thanh cong
            # ca khi DB hong hoac khi -shm khong tao duoc. Loi that chi hien ra
            # o truy van dau tien, va do la cho phai bat de con duong lui.
            c.execute("SELECT 1 FROM jobs LIMIT 1").fetchone()
            return c
        except Exception:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
    return None


def db_stats() -> dict:
    """Chi so doc tu state.db trong MOT ket noi.

    Tra dict rong khi khong doc duoc (DB chua tao, dang khoa lau, file hong) —
    heartbeat khong duoc chet vi mot truy van.
    """
    if not os.path.exists(DB):
        return {}
    out: dict = {}
    c = _open_db()
    if c is None:
        return {}
    try:
        now = int(time.time() * 1000)
        rows = c.execute(
            "SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
        counts = {str(st): int(n) for st, n in rows}
        out["queue"] = {st: counts[st] for st in COUNT_STATUSES if counts.get(st)}
        out["backlog"] = sum(counts.get(st, 0) for st in ACTIVE_STATUSES)

        # TAC NGHEN = "con job cho ma da lau khong phien am xong cuoc nao".
        #
        # Ban dau (06/08/2026) cho nay do "tuoi cua job cho lau nhat" — SAI, va
        # sai ngay lan chay dau: may dang nap bu mot loat buoi dao tao dai, job
        # cuoi hang doi 3-4 tieng la binh thuong vi nhung cuoc truoc no moi cai
        # 2-3 tieng audio. Chi so do bao dong dung luc he thong lam viec cham
        # chi nhat. Con so DUY NHAT phan biet duoc "dang chay cham" voi "dung
        # han" la: lan cuoi co mot cuoc DICH XONG.
        row = c.execute("SELECT MAX(transcribed_at) FROM jobs").fetchone()
        done = row[0] if row else None
        out["idle"] = (round((now - int(done)) / 60000, 1) if done else None)

        # Job hong CO THE LAM GI DUOC. `empty_transcript` (ban ghi khong co
        # tieng noi) bi tru ra: no vinh vien va khong ai sua duoc — dem no vao
        # la WARN khong bao gio tat.
        out["failed_act"] = sum(
            1 for (err,) in c.execute(
                "SELECT error FROM jobs WHERE status='failed'")
            if (_ERR_CODE_RE.match(err or "").group(1)
                if _ERR_CODE_RE.match(err or "") else "")
            not in SILENT_FAIL_CODES)

        row = c.execute(
            "SELECT COUNT(*) FROM deliveries WHERE ok=1 AND sent_at >= ?",
            (now - 86_400_000,)).fetchone()
        out["deliv24"] = int(row[0]) if row else 0

        row = c.execute(
            "SELECT COUNT(*) FROM tokens WHERE status='active'").fetchone()
        out["users"] = int(row[0]) if row else 0

        # Ve phien con song = so nguoi thuc su dang hoi bot. Chi so DUY NHAT o
        # day noi ve NGUOI DUNG; tat ca phan con lai noi ve may moc, nen khi
        # moi thu xanh ma cai nay bang 0 suot ca ngay lam viec thi van dang co
        # chuyen (bot cam, Hermes khong day tin toi).
        row = c.execute(
            "SELECT COUNT(*) FROM qa_sessions WHERE expires_at > ?",
            (now,)).fetchone()
        out["askers"] = int(row[0]) if row else 0
    except Exception:
        pass
    finally:
        c.close()
    return out


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


def problems(d: dict) -> list[str]:
    """Chi so -> danh sach van de. Ham THUAN: khong doc may, khong doc DB.

    Tach khoi `collect` (06/08/2026) de nguong bao dong kiem duoc bang test:
    hai luat quan trong nhat o day deu la luat KHONG bao — va mot luat khong
    bao thi khong ai phat hien ra la no gay im lang, tru khi co test.
    """
    p = []
    if d["v2run"] == 0:
        p.append("v2run=TAT")
    if d["whisper"] != "200":
        p.append("whisper=" + d["whisper"])
    if not d["hermes"]:
        p.append("hermes8642=DOWN")
    # Scan cu KHONG phai loi khi CON VIEC: vong `run` chay mot mach (tai ->
    # ffmpeg -> whisper -> recap) roi moi quet lai, nen mot cuoc 2 tieng audio
    # lam khoang cach giua hai lan quet dai ra 20-30 phut. Do la thiet ke, va
    # do 06/08/2026 no bao dong lien tuc suot dot nap bu.
    #
    # Vi sao dieu kien la `backlog` chu khong phai "co job dang transcribing":
    # ca giai doan tai file + ffmpeg (may phut voi file 277 MB) job van con o
    # `queued`, nen do theo status se ho ngay giua hai cuoc — dung nhu lan chay
    # dau bat duoc. Hang doi rong ma van khong quet moi that su la vong lap
    # dung; con hang doi day ma vong lap chet thi `tac_nghen` bat duoc.
    if (d["scan"] is not None and d["scan"] > SCAN_WARN_MIN
            and not d["backlog"]):
        p.append(f"scan_cu={d['scan']}m")
    if d["gwerr"] and d["gwerr"] > 0:
        p.append(f"gwerr15m={d['gwerr']}")
    if d["v2err"] and d["v2err"] > 0:
        p.append(f"v2err15m={d['v2err']}")
    if d["tokens"] is not None and d["tokens"] < TOKEN_WARN_DAYS:
        p.append(f"token={d['tokens']}d")
    if d["disk"] is not None and d["disk"] < DISK_WARN_GB:
        p.append(f"dia_con={d['disk']}GB")
    # Tac nghen: con job cho MA da lau khong dich xong cuoc nao. Hai ve deu
    # can — chi ve sau la bao dong moi dem khi khong con viec gi de lam, chi ve
    # truoc la bao dong suot mot dot nap bu binh thuong.
    if (d["backlog"] and d["idle"] is not None
            and d["idle"] > IDLE_WARN_MIN):
        p.append(f"tac_nghen={d['idle']}m/{d['backlog']}job")
    if d["failed_act"]:
        p.append(f"job_hong={d['failed_act']}")
    return p


def level_of(p: list[str]) -> str:
    """FAIL = mot thanh phan da CHET. WARN = van chay nhung can nguoi nhin."""
    if not p:
        return "OK"
    return ("FAIL" if any(x.startswith(("v2run", "whisper", "hermes"))
                          for x in p) else "WARN")


def collect() -> dict:
    """Thu thap tat ca chi so + tinh muc OK/WARN/FAIL. Tinh `now` moi lan."""
    now = datetime.datetime.now()
    whisper, wlat = whisper_health()
    v2run, mem, uptime = v2_run_procs()
    d = dict(
        now=now,
        v2run=v2run,
        v2mem=mem,
        v2up=uptime,
        whisper=whisper,
        wlat=wlat,
        hermes=port_up(8642),
        scan=scan_age_min(now),
        gwerr=real_errors(HERMES_LOG, now),
        v2err=real_errors(latest_log("v2-*.log") or "", now),
        tokens=min_token_days(),
        disk=disk_free_gb(),
        dbmb=db_size_mb(),
    )
    stats = db_stats()
    d["queue"] = stats.get("queue") or {}
    for key in ("idle", "backlog", "failed_act", "deliv24", "users", "askers"):
        d[key] = stats.get(key)
    d["problems"] = problems(d)
    d["level"] = level_of(d["problems"])
    return d


def _queue_str(queue: dict) -> str:
    """'queued=2 transcribing=1' — bo status bang 0 cho dong log ngan."""
    return " ".join(f"{k}={v}" for k, v in queue.items()) or "trong"


def format_line(d: dict) -> str:
    """Mot dong log. Them truong thi THEM VAO CUOI, dung doi thu tu cu.

    Log heartbeat duoc doc bang mat va bang grep tren nhieu ngay; doi cho cac
    truong cu lam moi so sanh voi log hom truoc thanh vo nghia.
    """
    line = (f"[{d['now']:%Y-%m-%d %H:%M:%S}] {d['level']:4} | "
            f"v2run={d['v2run']} whisper={d['whisper']} "
            f"hermes={'up' if d['hermes'] else 'DOWN'} scan={d['scan']}m "
            f"gwerr15m={d['gwerr']} v2err15m={d['v2err']} tokens={d['tokens']}d "
            f"| hangdoi[{_queue_str(d['queue'])}] xongcach={d['idle']}m "
            f"hong_can_sua={d['failed_act']} "
            f"phat24h={d['deliv24']} nguoidung={d['users']} "
            f"danghoi={d['askers']} dia={d['disk']}GB db={d['dbmb']}MB "
            f"ram={d['v2mem']}MB chay={d['v2up']}h wlat={d['wlat']}ms")
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
