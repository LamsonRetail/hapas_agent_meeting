#!/usr/bin/env python3
"""
Cong cu TEST luong (a): bot-join co mo duoc minute cuoc hop NGUOI KHAC hay khong.

KHONG phai code san xuat. Chi de chay trong mot buoi test co that, ghi lai
TOAN BO ket qua tho (raw JSON + timestamp + ma loi) vao out/test_bot_join_log.txt
de phan tich sau. Con nguoi lam phan cuoc hop; script chi lo goi lark-cli va log.

Doc docs/TEST_bot_join.md truoc khi chay.

Ba gia thuyet can phan biet:
    H1  bot join (--as bot)   -> user token doc duoc minute?      (ky vong: KHONG)
    H2  user join (--as user) -> chinh user do doc duoc minute?   (ky vong: CO)
    H3  apply-permission tren minute token bat ky -> mo duoc?     (can approve?)

Cach dung (chay TUNG phase dung thoi diem trong runbook):

    # 0. Kiem tra danh tinh dang dung
    python test_bot_join.py whoami

    # 1. Lay so + id cuoc hop dang dien ra (chay khi cuoc hop da bat dau)
    python test_bot_join.py active

    # 2a. Cho BOT vao cuoc hop (gia thuyet H1)
    python test_bot_join.py join --number 123456789 --as bot

    # 2b. HOAC cho USER vao (gia thuyet H2)
    python test_bot_join.py join --number 123456789 --as user

    # 3. (tuy chon) doc su kien trong cuoc hop de xac nhan bot dang o trong phong
    python test_bot_join.py events --meeting-id 6911...

    # --- ket thuc hop, bam dung ghi, doi ~5 phut cho Minute sinh ra ---

    # 4. Tim minute vua sinh (ghi lai minute_token)
    python test_bot_join.py find --keyword "Test bot join"

    # 5. Thu DOC minute (khong xin quyen truoc) -> ma loi cho biet co bi chan khong
    python test_bot_join.py read --token abcd1234...

    # 6. Neu buoc 5 bi chan: xin quyen roi doc lai (gia thuyet H3)
    python test_bot_join.py apply --token abcd1234... --perm view
    python test_bot_join.py read --token abcd1234...

Moi lenh deu append vao out/test_bot_join_log.txt. Cuoi buoi gui lai file do.

Ghi chu profile: mac dinh dung profile mac dinh cua lark-cli (app
cli_a9bd0ff8d6619ed1 - da co scope vc:meeting.bot.join:write + user token cua
Tham). Muon dung profile khac thi them --profile <ten>.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOG = Path("out/test_bot_join_log.txt")

_BIN = None


def lark_bin():
    """Tren Windows lark-cli la file .cmd, subprocess khong tu do PATHEXT."""
    global _BIN
    if _BIN is None:
        found = shutil.which("lark-cli")
        if not found:
            sys.exit("Khong tim thay lark-cli trong PATH. Kiem tra: where lark-cli")
        _BIN = found
    return _BIN


def run(args, profile, note=""):
    """Goi lark-cli, in ra man hinh VA ghi raw output vao log co timestamp."""
    cmd = [lark_bin(), *args, "--json"]
    if profile:
        cmd += ["--profile", profile]

    stamp = datetime.now().isoformat(timespec="seconds")
    printable = "lark-cli " + " ".join(args) + (f" --profile {profile}" if profile else "")

    print(f"\n[{stamp}] {note}")
    print(f"$ {printable}")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        raw = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        raw, err = "", "TIMEOUT sau 120s"

    # Phan tich ok / ma loi neu la JSON
    verdict = ""
    try:
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict):
            if payload.get("ok") is True:
                verdict = "OK"
            elif "error" in payload:
                e = payload["error"]
                verdict = f"LOI code={e.get('code')} msg={e.get('message')}"
    except (json.JSONDecodeError, TypeError):
        pass

    print(raw or "(khong co stdout)")
    if verdict:
        print(f"=> {verdict}")
    if err:
        print(f"[stderr] {err}")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 70}\n[{stamp}] {note}\n$ {printable}\n")
        f.write((raw or "(khong co stdout)") + "\n")
        if verdict:
            f.write(f"=> {verdict}\n")
        if err:
            f.write(f"[stderr] {err}\n")

    return raw


def main():
    ap = argparse.ArgumentParser(description="Test bot-join -> minute access")
    ap.add_argument("phase", choices=[
        "whoami", "active", "join", "events", "find", "read", "apply",
    ])
    ap.add_argument("--profile", default="", help="profile lark-cli (mac dinh: mac dinh)")
    ap.add_argument("--number", help="so cuoc hop (phase join)")
    ap.add_argument("--as", dest="as_", choices=["bot", "user"], default="bot",
                    help="danh tinh join (phase join). H1=bot, H2=user")
    ap.add_argument("--password", help="mat khau cuoc hop neu can")
    ap.add_argument("--meeting-id", help="meeting id (phase events)")
    ap.add_argument("--keyword", help="tu khoa tim minute (phase find)")
    ap.add_argument("--token", help="minute token (phase read/apply)")
    ap.add_argument("--perm", choices=["view", "edit"], default="view",
                    help="quyen xin (phase apply)")
    a = ap.parse_args()
    p = a.profile

    if a.phase == "whoami":
        run(["auth", "status"], p, "Kiem tra danh tinh dang dung")

    elif a.phase == "active":
        run(["vc", "+meeting-list-active", "--as", "user"], p,
            "Liet ke cuoc hop dang dien ra (lay so + meeting id)")

    elif a.phase == "join":
        if not a.number:
            sys.exit("Thieu --number")
        args = ["vc", "+meeting-join", "--as", a.as_, "--meeting-number", a.number]
        if a.password:
            args += ["--password", a.password]
        run(args, p, f"BOT/USER join cuoc hop (--as {a.as_})  [gia thuyet "
                     f"{'H1' if a.as_ == 'bot' else 'H2'}]")

    elif a.phase == "events":
        if not a.meeting_id:
            sys.exit("Thieu --meeting-id")
        run(["vc", "+meeting-events", "--as", "bot", "--meeting-id", a.meeting_id], p,
            "Doc su kien trong cuoc hop (xac nhan bot dang o trong phong)")

    elif a.phase == "find":
        if not a.keyword:
            sys.exit("Thieu --keyword")
        run(["minutes", "+search", "--query", a.keyword], p,
            "Tim minute vua sinh -> lay minute_token")

    elif a.phase == "read":
        if not a.token:
            sys.exit("Thieu --token")
        run(["minutes", "+detail", "--as", "user", "--minute-tokens", a.token,
             "--summary", "--todo", "--transcript", "--overwrite"], p,
            "THU DOC minute (chua xin quyen) -> ma loi cho biet co bi chan khong")

    elif a.phase == "apply":
        if not a.token:
            sys.exit("Thieu --token")
        run(["minutes", "+apply-permission", "--as", "user",
             "--minute-token", a.token, "--perm", a.perm], p,
            f"Xin quyen {a.perm} tren minute  [gia thuyet H3]")


if __name__ == "__main__":
    main()
