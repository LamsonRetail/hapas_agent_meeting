#!/usr/bin/env python3
"""
Poller phát hiện cuộc họp vừa kết thúc -> dựng job -> đẩy sang server.

Bước 1 + 2 của luồng auto meeting note.
Bước đẩy sang server còn là stub, chờ anh Thiện xác nhận endpoint.

Chạy:
    python meeting_poller.py --once      # quét 1 lần rồi thoát (để test)
    python meeting_poller.py             # chạy nền, lặp theo INTERVAL

Yêu cầu: lark-cli đã `lark-cli auth login` sẵn.
Không cần tự viết OAuth refresh vì CLI giữ phiên đăng nhập.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- cấu hình

MY_OPEN_ID = os.environ.get(
    "MY_OPEN_ID", "ou_1bc55b6d5b20ee06cbee1326d5b72715")
MY_UNION_ID = os.environ.get(
    "MY_UNION_ID", "on_1f34d05d9ce11997c545cf1ec136bc41")

# Không tra được người được mời (họp mở trực tiếp, không có sự kiện lịch)
# thì vẫn gửi cho chủ tài khoản, hơn là bỏ luôn.
FALLBACK_TO_OWNER = os.environ.get("FALLBACK_TO_OWNER", "1") != "0"

INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL", "300"))
LOOKBACK_DAYS = 2               # quét lùi bao nhiêu ngày mỗi lần
SETTLE_MINUTES = 3              # chờ bao lâu sau khi họp xong mới xử lý

# Tự gửi luôn sau khi phát hiện, hay chỉ ghi job ra file
AUTO_DELIVER = os.environ.get("AUTO_DELIVER", "1") != "0"
TRANSCRIPT_SOURCE = os.environ.get("TRANSCRIPT_SOURCE", "whisper")

# Chế độ thử: chạy hết luồng nhưng KHÔNG gửi tin nhắn thật
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# Nới bao nhiêu tiếng hai đầu khi dò lịch tìm cuộc họp
CAL_WINDOW_HOURS = 3
# Mỗi sự kiện tốn 2-3 lệnh gọi API nên giới hạn số lượng thử
MAX_EVENTS_TO_CHECK = 12

# Giai đoạn pilot: chỉ gửi cho 1 người. Để trống thì gửi cho participant.
DELIVER_TO = os.environ.get("DELIVER_TO") or None

STATE_FILE = Path("poller_state.json")
JOB_DIR = Path("jobs")          # nơi ghi job trước khi xử lý

# ------------------------------------------------------------- lớp lark-cli


class LarkCliError(RuntimeError):
    pass


_LARK_BIN: str | None = None

# Profile của lark-cli. Để trống -> dùng profile mặc định (app anh Thiện).
# Đặt LARK_PROFILE=mine để đọc bằng app riêng.
LARK_PROFILE = os.environ.get("LARK_PROFILE", "")


def _lark_bin() -> str:
    """Tìm đường dẫn đầy đủ của lark-cli.

    Trên Windows lark-cli là file .cmd (cài qua npm). subprocess không
    tự dò PATHEXT như PowerShell nên phải phân giải trước, nếu không
    sẽ dính FileNotFoundError [WinError 2].
    """
    global _LARK_BIN
    if _LARK_BIN is None:
        found = shutil.which("lark-cli")
        if not found:
            raise LarkCliError(
                "Không tìm thấy lark-cli trong PATH.\n"
                "Kiểm tra bằng: where lark-cli"
            )
        _LARK_BIN = found
    return _LARK_BIN


def lark(*args: str) -> dict:
    """Gọi lark-cli, trả về data đã parse. Ném LarkCliError nếu hỏng."""
    cmd = [_lark_bin(), *args, "--json"]
    if LARK_PROFILE:
        cmd += ["--profile", LARK_PROFILE]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, encoding="utf-8"
        )
    except subprocess.TimeoutExpired as exc:
        raise LarkCliError(f"lark-cli timeout: {' '.join(args)}") from exc

    raw = proc.stdout.strip()
    if not raw:
        raise LarkCliError(f"lark-cli không trả gì: {proc.stderr.strip()}")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LarkCliError(f"không parse được JSON: {raw[:300]}") from exc

    if not payload.get("ok"):
        err = payload.get("error", {})
        raise LarkCliError(f"lark-cli lỗi {err.get('code')}: {err.get('message')}")

    return payload.get("data", {})


# ------------------------------------------------------------------ state


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"seen_tokens": [], "last_run": None}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[warn] state hỏng ({exc}), khởi tạo lại", file=sys.stderr)
        return {"seen_tokens": [], "last_run": None}


def save_state(state: dict) -> None:
    # giữ tối đa 500 token gần nhất, tránh file phình vô hạn
    state["seen_tokens"] = state["seen_tokens"][-500:]
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ------------------------------------------------------------- parse helper

# "Owner: Nguyễn Tiến Thẩm ... Start time: 2026.07.27 17:45:24 Duration: 10 min 57 sec"
_RE_OWNER = re.compile(r"Owner:\s*(.+?)\s+Start time:")
_RE_START = re.compile(r"Start time:\s*([\d.]+\s[\d:]+)")
_RE_DURATION = re.compile(r"Duration:\s*(?:(\d+)\s*min)?\s*(?:(\d+)\s*sec)?")


def parse_meta(item: dict) -> dict:
    """Bóc owner / start time / duration ra khỏi chuỗi description.

    Lark không trả các trường này riêng nên phải parse chuỗi.
    Nếu Lark đổi format thì hàm này hỏng -> trả None chứ không crash.
    """
    desc = item.get("meta_data", {}).get("description", "") or ""
    display = item.get("display_info", "") or ""

    started_at = None
    if m := _RE_START.search(desc):
        try:
            started_at = datetime.strptime(m.group(1), "%Y.%m.%d %H:%M:%S")
        except ValueError:
            pass

    duration_sec = None
    if m := _RE_DURATION.search(desc):
        mins, secs = m.group(1), m.group(2)
        if mins or secs:
            duration_sec = int(mins or 0) * 60 + int(secs or 0)

    return {
        "title": (display.split("\n", 1)[0] or "").strip() or "(không tiêu đề)",
        "owner": m.group(1).strip() if (m := _RE_OWNER.search(desc)) else None,
        "started_at": started_at,
        "duration_sec": duration_sec,
    }


# ------------------------------------------------------------ bước 1: quét


def find_new_minutes(state: dict) -> list[dict]:
    """Quét Minutes, trả về các minute chưa xử lý."""
    start = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    data = lark(
        "minutes", "+search",
        "--participant-ids", "me",
        "--start", start,
        "--page-size", "30",
    )

    seen = set(state["seen_tokens"])
    first_seen = state.setdefault("first_seen", {})
    now_ts = time.time()
    fresh = []

    for item in data.get("items", []):
        token = item.get("token")
        if not token or token in seen:
            continue

        # Chờ một lúc sau khi phát hiện, để Lark kịp liên kết bản ghi
        # với cuộc họp (vc +recording trả rỗng nếu hỏi quá sớm).
        #
        # KHÔNG dùng giờ Lark trả về để tính: chuỗi mô tả không kèm múi
        # giờ, thực tế lệch +1 tiếng (Asia/Shanghai vs Bangkok), khiến
        # phép so sánh sai hoàn toàn. Tự đếm từ lúc mình thấy lần đầu.
        seen_at = first_seen.get(token)
        if seen_at is None:
            first_seen[token] = now_ts
            print(f"[chờ] {token} vừa thấy lần đầu, "
                  f"đợi {SETTLE_MINUTES} phút cho Lark xử lý xong")
            continue

        waited = (now_ts - seen_at) / 60
        if waited < SETTLE_MINUTES:
            print(f"[chờ] {token} mới đợi {waited:.1f}/{SETTLE_MINUTES} phút")
            continue

        meta = parse_meta(item)
        first_seen.pop(token, None)
        fresh.append({
            "minute_token": token,
            "app_link": item.get("meta_data", {}).get("app_link"),
            **meta,
        })

    return fresh


# ------------------------------------------- bước 2: tìm người dự cuộc họp


def resolve_participants(minute: dict) -> tuple[list[str], str]:
    """Tìm những người ĐƯỢC MỜI vào cuộc họp sinh ra minute này.

    Chuỗi liên kết (đã kiểm chứng bằng dữ liệu thật):
        sự kiện lịch -> calendar +meeting -> meeting_id
                     -> vc +recording     -> minute_token
        khớp minute_token -> lấy attendees của sự kiện đó

    Trả về (danh sách open_id, nguồn).
    """
    started = minute.get("started_at")
    if not started:
        return [], "no_start_time"

    lo = (started - timedelta(hours=CAL_WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%S+07:00")
    hi = (started + timedelta(hours=CAL_WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%S+07:00")

    try:
        events = lark("calendar", "+agenda", "--start", lo, "--end", hi)
    except LarkCliError as exc:
        print(f"[warn] đọc lịch hỏng: {exc}", file=sys.stderr)
        return [], "agenda_failed"

    if not isinstance(events, list) or not events:
        return [], "no_calendar_event"

    # Ưu tiên sự kiện trùng tên, rồi tới sự kiện gần giờ nhất.
    # Mỗi sự kiện tốn 2 lệnh gọi nên phải giới hạn số lượng thử.
    title = (minute.get("title") or "").strip().lower()

    def rank(ev: dict) -> tuple[int, float]:
        same_title = 0 if (ev.get("summary") or "").strip().lower() == title else 1
        try:
            ev_start = datetime.fromisoformat(
                ev["start_time"]["datetime"]).replace(tzinfo=None)
            gap = abs((ev_start - started).total_seconds())
        except (KeyError, ValueError, TypeError):
            gap = 1e9
        return (same_title, gap)

    for ev in sorted(events, key=rank)[:MAX_EVENTS_TO_CHECK]:
        event_id = ev.get("event_id")
        cal_id = ev.get("organizer_calendar_id")
        if not event_id or not cal_id:
            continue

        try:
            info = lark("calendar", "+meeting", "--event-ids", event_id)
            meetings = info.get("meetings", [])
            meeting_id = meetings[0].get("meeting_id") if meetings else None
            if not meeting_id:
                continue

            rec = lark("vc", "+recording", "--meeting-ids", str(meeting_id))
            tokens = {r.get("minute_token") for r in rec.get("recordings", [])}
            if minute["minute_token"] not in tokens:
                continue

            ids: list[str] = []
            union_ids: list[str] = []
            for id_type, bucket in (("open_id", ids), ("union_id", union_ids)):
                att = lark("calendar", "event.attendees", "list",
                           "--calendar-id", cal_id,
                           "--event-id", event_id,
                           "--user-id-type", id_type,
                           "--page-all")
                for a in att.get("items", []):
                    if a.get("type") == "user" and a.get("user_id"):
                        bucket.append(a["user_id"])
        except LarkCliError:
            continue

        if ids or union_ids:
            name = ev.get("summary") or event_id
            # open_id gắn với từng app -> giữ cả user_id để app riêng dùng
            minute["participant_union_ids"] = list(dict.fromkeys(union_ids))
            return list(dict.fromkeys(ids)), f"calendar:{name}"

    return [], "no_match"


# ------------------------------------------------------ bước 3: đẩy job đi


def dispatch(job: dict) -> None:
    """Ghi job ra file rồi giao cho delivery xử lý luôn.

    Vẫn ghi file trước khi xử lý: nếu delivery hỏng giữa chừng thì job
    còn nguyên trong jobs/ để chạy lại, không mất.
    """
    JOB_DIR.mkdir(exist_ok=True)
    path = JOB_DIR / f"{job['minute_token']}.json"
    path.write_text(
        json.dumps(job, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    if not AUTO_DELIVER:
        print(f"       -> ghi job: {path}")
        return

    from meeting_delivery import process  # noqa: PLC0415

    ok = process(path, TRANSCRIPT_SOURCE, dry_run=DRY_RUN, force_to=DELIVER_TO)
    if not ok:
        # process() giữ nguyên file job khi hỏng -> vòng sau thử lại
        raise RuntimeError("delivery không hoàn tất")


# ------------------------------------------------------------- vòng chính


def check_auth() -> None:
    """Cảnh báo sớm khi refresh token sắp hết hạn.

    Refresh token của Lark chỉ sống khoảng 1 tuần. Không cảnh báo thì
    poller sẽ chết lặng lẽ mà không ai biết.
    """
    try:
        proc = subprocess.run(
            [_lark_bin(), "auth", "status", "--json"]
            + (["--profile", LARK_PROFILE] if LARK_PROFILE else []),
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        info = json.loads(proc.stdout.strip())
    except (LarkCliError, json.JSONDecodeError, subprocess.SubprocessError,
            OSError) as exc:
        print(f"[warn] không kiểm tra được auth: {exc}", file=sys.stderr)
        return

    user = info.get("identities", {}).get("user", {})
    raw = user.get("refreshExpiresAt")
    if not raw:
        return

    try:
        expires = datetime.fromisoformat(raw)
    except ValueError:
        return

    left = expires - datetime.now(expires.tzinfo)
    days = left.total_seconds() / 86400

    if days < 0:
        print("[LỖI] Refresh token đã hết hạn. Chạy: lark-cli auth login",
              file=sys.stderr)
    elif days < 2:
        print(f"[CẢNH BÁO] Refresh token còn {days:.1f} ngày. "
              f"Chạy lark-cli auth login để gia hạn.", file=sys.stderr)
    else:
        print(f"[auth] user OK, refresh token còn {days:.1f} ngày")


def run_once() -> int:
    state = load_state()

    try:
        minutes = find_new_minutes(state)
    except LarkCliError as exc:
        print(f"[error] quét hỏng: {exc}", file=sys.stderr)
        return 0

    if not minutes:
        print(f"[{datetime.now():%H:%M:%S}] không có minute mới")
        save_state(state)
        return 0

    for m in minutes:
        print(f"[mới] {m['title']}  ({m['minute_token']})")

        participants, source = resolve_participants(m)
        union_ids = m.get("participant_union_ids", [])

        if participants:
            print(f"       {len(participants)} người được mời  [{source}]")
        elif FALLBACK_TO_OWNER:
            participants = [MY_OPEN_ID]
            union_ids = [MY_UNION_ID]
            m["participant_union_ids"] = union_ids
            source = f"{source} -> chỉ gửi cho chủ tài khoản"
            print(f"       không tra được người được mời [{source}]")
            print(f"       (cuộc họp mở trực tiếp, không có sự kiện lịch?)")
        else:
            print(f"       chưa lấy được người dự  [{source}]")

        job = {
            "minute_token": m["minute_token"],
            "title": m["title"],
            "app_link": m["app_link"],
            "owner": m["owner"],
            "started_at": m["started_at"],
            "duration_sec": m["duration_sec"],
            "participants": participants,
            "participant_union_ids": m.get("participant_union_ids", []),
            "participants_source": source,
            "queued_at": datetime.now().isoformat(timespec="seconds"),
        }

        try:
            dispatch(job)
        except Exception as exc:  # noqa: BLE001
            # không đánh dấu đã xử lý -> vòng sau thử lại
            print(f"[error] đẩy job hỏng: {exc}", file=sys.stderr)
            continue

        state["seen_tokens"].append(m["minute_token"])

    save_state(state)
    return len(minutes)


def main() -> None:
    global DRY_RUN, DELIVER_TO

    ap = argparse.ArgumentParser(description="Poller meeting note")
    ap.add_argument("--once", action="store_true", help="quét 1 lần rồi thoát")
    ap.add_argument("--interval", type=int, default=INTERVAL_SECONDS,
                    help=f"giây giữa 2 lần quét (mặc định {INTERVAL_SECONDS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="chạy hết luồng nhưng KHÔNG gửi tin nhắn")
    ap.add_argument("--send", action="store_true",
                    help="gửi tin nhắn thật (ghi đè biến môi trường)")
    ap.add_argument("--to", metavar="OPEN_ID",
                    help="ép gửi cho 1 người thay vì toàn bộ người được mời")
    args = ap.parse_args()

    # Tham số dòng lệnh thắng biến môi trường.
    # Truyền cả hai cờ thì ưu tiên an toàn: không gửi.
    raw_env = os.environ.get("DRY_RUN")
    if args.dry_run:
        DRY_RUN = True
    elif args.send:
        DRY_RUN = False
    if args.to:
        DELIVER_TO = args.to

    print(f"[cấu hình] biến môi trường DRY_RUN={raw_env!r}  ->  "
          f"thực tế {'KHÔNG gửi' if DRY_RUN else 'GỬI THẬT'}")

    if args.once:
        check_auth()
        run_once()
        return

    check_auth()
    mode = "THU (khong gui that)" if DRY_RUN else "GUI THAT"
    print(f"Poller chạy, quét mỗi {args.interval}s. "
          f"nguồn={TRANSCRIPT_SOURCE}  chế độ={mode}"
          f"{'  chỉ gửi cho ' + DELIVER_TO if DELIVER_TO else '  gửi cho toàn bộ người được mời'}")
    print("Ctrl+C để dừng.")

    loops = 0
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\nDừng.")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[error] vòng lặp: {exc}", file=sys.stderr)

        loops += 1
        if loops % 48 == 0:        # ~4 tiếng/lần nếu quét mỗi 5 phút
            check_auth()

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
