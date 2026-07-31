#!/usr/bin/env python3
"""
Central multi-user poller — phu nhieu nhan su (Giai doan 2).

Van dung BOT de gui (khong doi). Diem moi: lap qua token cua TUNG nhan su da
enroll, moi nguoi tu dò cuoc hop HO co du, roi gui recap + transcript cho
nhung nguoi duoc moi.

Vi sao can nhieu token: mot app chi doc duoc minute cua cuoc hop ma CHU TOKEN
co du (scope minutes la user-specific). Muon phu ca cong ty -> phai co token
cua tung nguoi. Cach nhe nhat, khong phai tu viet OAuth refresh: moi nguoi mot
profile lark-cli tren may admin (lark-cli tu gia han token theo cua so truot).

    python multi_poller.py --once --dry-run        # quet 1 lan, khong gui
    python multi_poller.py --once --send           # quet 1 lan, gui that
    python multi_poller.py --send                  # chay nen, lap theo interval
    python multi_poller.py --once --only quythien  # chi 1 nguoi (test)
    python multi_poller.py --once --to ou_xxx --send   # ep gui 1 nguoi nhan

Nguon nhan su: users.json (xem users.example.json). Enroll bang enroll_user.py.

BAO MAT: may nay giu token toan-quyen cua moi nguoi da enroll. Cong ty da thong
nhat giao cho MOT nguoi quan tri. Xem docs/MULTI_USER.md muc "Bao mat".
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import meeting_poller as mp
import meeting_delivery as md

USERS_FILE = Path("users.json")
JOB_DIR = Path("jobs")

INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL", "300"))
TRANSCRIPT_SOURCE = os.environ.get("TRANSCRIPT_SOURCE", "whisper")
# Khong tra duoc nguoi du (hop mo truc tiep, khong co su kien lich) thi it nhat
# gui cho chinh chu token da phat hien ra minute nay - ho co du nen dang nhan.
FALLBACK_TO_OWNER = os.environ.get("FALLBACK_TO_OWNER", "1") != "0"


# --------------------------------------------------------------- users

def load_users(only: str | None = None) -> list[dict]:
    if not USERS_FILE.exists():
        raise SystemExit(
            f"Chua co {USERS_FILE}. Sao chep tu users.example.json roi enroll:\n"
            f"    python enroll_user.py <profile>")
    data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    users = data.get("users", []) if isinstance(data, dict) else data
    out = []
    for u in users:
        if not u.get("profile"):
            continue
        if only and u["profile"] != only:
            continue
        if not only and not u.get("active", True):
            continue
        out.append(u)
    if not out:
        raise SystemExit("Khong co nguoi dung nao phu hop (kiem tra active / --only).")
    return out


# ------------------------------------------------------- auth cho moi profile

def check_all_auth(users: list[dict]) -> None:
    """Canh bao som khi refresh token cua bat ky ai sap het han."""
    print(f"[auth] kiem tra {len(users)} profile...")
    for u in users:
        prof = u["profile"]
        try:
            proc = mp.subprocess.run(
                [mp._lark_bin(), "auth", "status", "--profile", prof, "--json"],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            info = json.loads(proc.stdout.strip())
        except Exception as exc:  # noqa: BLE001
            print(f"       [!] {prof}: khong kiem tra duoc auth ({exc})")
            continue

        user = info.get("identities", {}).get("user", {})
        raw = user.get("refreshExpiresAt")
        if not user.get("available"):
            print(f"       [X] {prof}: chua dang nhap. "
                  f"Chay: python enroll_user.py {prof} --relogin")
            continue
        if not raw:
            print(f"       [ok] {prof}")
            continue
        try:
            expires = datetime.fromisoformat(raw)
            days = (expires - datetime.now(expires.tzinfo)).total_seconds() / 86400
        except ValueError:
            print(f"       [ok] {prof}")
            continue
        if days < 0:
            print(f"       [X] {prof}: refresh token HET HAN. "
                  f"python enroll_user.py {prof} --relogin")
        elif days < 2:
            print(f"       [!] {prof}: refresh token con {days:.1f} ngay")
        else:
            print(f"       [ok] {prof}: con {days:.1f} ngay")


# ----------------------------------------------------------- quet 1 nguoi

def scan_user(user: dict, state: dict, dry_run: bool, source: str,
              force_to: str | None) -> tuple[int, int, int]:
    """Quet minute moi cua 1 nguoi, xu ly & gui. Tra (thay, gui_ok, loi)."""
    profile = user["profile"]
    name = user.get("display_name") or profile

    # Doc VA tai minute deu phai dung token cua CHINH nguoi nay (minute la
    # user-specific). Gui bot (`--as bot`) dung token bot cua app - cung app
    # nen khong bi anh huong boi profile.
    mp.LARK_PROFILE = profile
    md.LARK_PROFILE = profile

    try:
        minutes = mp.find_new_minutes(state)
    except mp.LarkCliError as exc:
        print(f"  [{name}] quet hong: {exc}", file=sys.stderr)
        return 0, 0, 1

    if not minutes:
        print(f"  [{name}] khong co minute moi")
        return 0, 0, 0

    seen, delivered, errors = 0, 0, 0
    for m in minutes:
        seen += 1
        print(f"  [{name}] minute moi: {m['title']}  ({m['minute_token']})")

        participants, src = mp.resolve_participants(m)
        union_ids = m.get("participant_union_ids", [])

        if participants:
            print(f"          {len(participants)} nguoi duoc moi  [{src}]")
        elif FALLBACK_TO_OWNER and user.get("open_id"):
            participants = [user["open_id"]]
            union_ids = [user.get("union_id")] if user.get("union_id") else []
            m["participant_union_ids"] = union_ids
            src = f"{src} -> chi gui cho {name}"
            print(f"          khong tra duoc nguoi du, {src}")
        else:
            print(f"          bo qua: khong co nguoi nhan [{src}]", file=sys.stderr)
            errors += 1
            continue

        job = {
            "minute_token": m["minute_token"],
            "title": m["title"],
            "app_link": m["app_link"],
            "owner": m["owner"],
            "started_at": m["started_at"],
            "duration_sec": m["duration_sec"],
            "participants": participants,
            "participant_union_ids": m.get("participant_union_ids", []),
            "participants_source": src,
            "discovered_by": profile,
            "queued_at": datetime.now().isoformat(timespec="seconds"),
        }

        JOB_DIR.mkdir(exist_ok=True)
        path = JOB_DIR / f"{m['minute_token']}.json"
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")

        try:
            ok = md.process(path, source, dry_run=dry_run, force_to=force_to)
        except Exception as exc:  # noqa: BLE001
            print(f"          [error] gui hong: {exc}", file=sys.stderr)
            ok = False

        if ok:
            delivered += 1
            # Danh dau da xu ly TOAN CUC -> nguoi du khac se khong lam lai
            state["seen_tokens"].append(m["minute_token"])
        else:
            errors += 1
            # giu file job -> vong sau thu lai (md.process khong xoa khi hong)

    return seen, delivered, errors


# -------------------------------------------------------------- vong chinh

def run_once(users: list[dict], dry_run: bool, source: str,
             force_to: str | None) -> None:
    state = mp.load_state()
    total_seen = total_deliv = total_err = 0

    for u in users:
        s, d, e = scan_user(u, state, dry_run, source, force_to)
        total_seen += s
        total_deliv += d
        total_err += e
        # Luu state sau MOI nguoi: dedup ngay trong cung vong quet, va khong
        # mat tien do neu dung giua chung.
        mp.save_state(state)

    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] xong: {len(users)} nguoi, {total_seen} minute moi, "
          f"gui {total_deliv}, loi {total_err}"
          f"{'  (DRY-RUN)' if dry_run else ''}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Central multi-user meeting-note poller")
    ap.add_argument("--once", action="store_true", help="quet 1 lan roi thoat")
    ap.add_argument("--interval", type=int, default=INTERVAL_SECONDS,
                    help=f"giay giua 2 lan quet (mac dinh {INTERVAL_SECONDS})")
    ap.add_argument("--dry-run", action="store_true", help="chay het luong, KHONG gui")
    ap.add_argument("--send", action="store_true", help="gui that")
    ap.add_argument("--source", choices=["whisper", "lark"], default=TRANSCRIPT_SOURCE,
                    help=f"nguon transcript (mac dinh {TRANSCRIPT_SOURCE})")
    ap.add_argument("--to", metavar="OPEN_ID", help="ep gui cho 1 nguoi (test)")
    ap.add_argument("--only", metavar="PROFILE", help="chi quet 1 profile (test)")
    args = ap.parse_args()

    # An toan: khong truyen co nao -> dry-run. Truyen ca hai -> dry-run.
    dry_run = True
    if args.send and not args.dry_run:
        dry_run = False

    users = load_users(only=args.only)
    print(f"[cau hinh] {len(users)} nguoi  nguon={args.source}  "
          f"che do={'KHONG gui' if dry_run else 'GUI THAT'}"
          f"{'  chi ' + args.only if args.only else ''}")

    if args.once:
        check_all_auth(users)
        run_once(users, dry_run, args.source, args.to)
        return

    check_all_auth(users)
    print(f"Poller da nguoi chay, quet moi {args.interval}s. Ctrl+C de dung.")
    loops = 0
    while True:
        try:
            run_once(users, dry_run, args.source, args.to)
        except KeyboardInterrupt:
            print("\nDung.")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[error] vong lap: {exc}", file=sys.stderr)
        loops += 1
        if loops % 48 == 0:
            check_all_auth(users)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
