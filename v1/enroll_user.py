#!/usr/bin/env python3
"""
Enroll MOT nhan su vao central multi-user poller.

Chay TREN MAY ADMIN. Moi nhan su = 1 profile lark-cli rieng, dung chung app
cli_a9bd0ff8d6619ed1. Nguoi do quet QR duyet MOT lan; sau do lark-cli tu gia
han token theo cua so truot (chi can poller goi API trong vong 7 ngay).

    python enroll_user.py quythien
    python enroll_user.py quythien --name "Le Quy Thien - AI Automation Leader"
    python enroll_user.py quythien --domains minutes,calendar,vc,contact
    python enroll_user.py quythien --relogin      # dang nhap lai (token het han)

App id/secret lay theo thu tu:
    1. Tham so --app-id / --app-secret
    2. Bien moi truong LARK_APP_ID / LARK_APP_SECRET
    3. config.json cua MCP (tu do tim ../lark-mcp-available/config.json)

TUYET DOI khong dung `lark-cli profile use` / `profile remove` -> hong MCP ca team.
Script nay chi dung `profile add` va `auth login --profile`.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

USERS_FILE = Path("users.json")
DEFAULT_APP_ID = "cli_a9bd0ff8d6619ed1"
# Scope tối thiểu để KHÁM PHÁ cuộc họp: đọc minute, dò lịch tìm người dự,
# tra recording. Không xin mail/drive -> giảm phạm vi token, duyệt nhanh hơn.
DEFAULT_DOMAINS = "minutes,calendar,vc,contact"

# Nơi có thể tìm config.json của MCP để lấy app secret tự động.
_MCP_CONFIG_CANDIDATES = [
    Path("../lark-mcp-available/config.json"),
    Path("../lark-mcp/config.json"),
    Path("config.json"),
]


def _lark_bin() -> str:
    found = shutil.which("lark-cli")
    if not found:
        raise SystemExit("Khong tim thay lark-cli trong PATH. Kiem tra: where lark-cli")
    return found


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([_lark_bin(), *args], encoding="utf-8",
                          errors="replace", **kw)


def _app_secret(cli_id: str, cli_secret: str) -> tuple[str, str]:
    import os
    app_id = cli_id or os.environ.get("LARK_APP_ID") or DEFAULT_APP_ID
    secret = cli_secret or os.environ.get("LARK_APP_SECRET")
    if secret:
        return app_id, secret
    for cand in _MCP_CONFIG_CANDIDATES:
        if cand.exists():
            try:
                cfg = json.loads(cand.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if cfg.get("appSecret") and (not cli_id or cfg.get("appId") == app_id):
                print(f"       (lay app secret tu {cand})")
                return cfg.get("appId", app_id), cfg["appSecret"]
    raise SystemExit(
        "Thieu app secret. Truyen --app-secret, dat LARK_APP_SECRET, "
        "hoac de canh file config.json cua MCP."
    )


def _profiles() -> set[str]:
    proc = _run(["profile", "list", "--json"], capture_output=True)
    try:
        data = json.loads(proc.stdout.strip())
    except (json.JSONDecodeError, AttributeError):
        return set()
    items = data if isinstance(data, list) else data.get("profiles", data.get("data", []))
    out = set()
    for it in items or []:
        if isinstance(it, str):
            out.add(it)
        elif isinstance(it, dict):
            name = it.get("name") or it.get("profile")
            if name:
                out.add(name)
    return out


def _ensure_profile(profile: str, app_id: str, app_secret: str) -> None:
    if profile in _profiles():
        print(f"       profile '{profile}' da ton tai, bo qua buoc add")
        return
    print(f"       tao profile '{profile}' (app {app_id})...")
    proc = _run(
        ["profile", "add", "--name", profile,
         "--app-id", app_id, "--app-secret-stdin", "--brand", "lark"],
        input=app_secret + "\n", capture_output=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"profile add hong: {proc.stderr.strip() or proc.stdout.strip()}")


def _login(profile: str, domains: str) -> None:
    print(f"\n>>> Dang nhap cho '{profile}'. Nguoi nay quet QR / mo link de duyet.")
    print(f">>> Scope: {domains}\n")
    # stdio inherit: QR ASCII + huong dan hien truc tiep, chan cho toi khi duyet.
    proc = _run(["auth", "login", "--profile", profile,
                 "--domain", domains])
    # lark-cli co the tra exit code != 0 khi Lark tu choi vai scope nhay cam
    # (BINH THUONG). Xac nhan lai bang auth status thay vi tin exit code.
    if proc.returncode != 0:
        print("       (exit code != 0 - kiem tra lai bang auth status)")


def _status(profile: str) -> dict:
    proc = _run(["auth", "status", "--profile", profile, "--json"],
                capture_output=True)
    try:
        return json.loads(proc.stdout.strip())
    except (json.JSONDecodeError, AttributeError):
        return {}


def _load_users() -> dict:
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "users" in data:
                return data
            if isinstance(data, list):
                return {"users": data}
        except (json.JSONDecodeError, OSError):
            pass
    return {"users": []}


def _save_users(doc: dict) -> None:
    USERS_FILE.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _upsert(doc: dict, record: dict) -> None:
    for u in doc["users"]:
        if u.get("profile") == record["profile"]:
            u.update(record)
            return
    doc["users"].append(record)


def main() -> None:
    ap = argparse.ArgumentParser(description="Enroll nhan su vao multi-user poller")
    ap.add_argument("profile", help="ten profile lark-cli (khong dau, khong khoang trang)")
    ap.add_argument("--name", help="ten hien thi (mac dinh lay tu Lark)")
    ap.add_argument("--domains", default=DEFAULT_DOMAINS,
                    help=f"scope xin quyen (mac dinh {DEFAULT_DOMAINS})")
    ap.add_argument("--app-id", default="")
    ap.add_argument("--app-secret", default="")
    ap.add_argument("--relogin", action="store_true",
                    help="dang nhap lai du profile da ton tai")
    args = ap.parse_args()

    if any(c in args.profile for c in ' \t"/\\'):
        raise SystemExit("Ten profile khong duoc co khoang trang / ky tu la.")

    app_id, app_secret = _app_secret(args.app_id, args.app_secret)
    print(f"[enroll] profile={args.profile}  app={app_id}")

    _ensure_profile(args.profile, app_id, app_secret)

    st = _status(args.profile)
    user = (st.get("identities", {}) or {}).get("user", {}) or {}
    if args.relogin or not user.get("available"):
        _login(args.profile, args.domains)
        st = _status(args.profile)
        user = (st.get("identities", {}) or {}).get("user", {}) or {}

    if not user.get("available"):
        raise SystemExit(
            "\nChua dang nhap thanh cong. Kiem tra nguoi do da bam 'Dong y' tren Lark.\n"
            f"Thu lai: python enroll_user.py {args.profile} --relogin")

    open_id = user.get("openId", "")
    name = args.name or user.get("userName") or args.profile

    doc = _load_users()
    _upsert(doc, {
        "profile": args.profile,
        "display_name": name,
        "open_id": open_id,
        "union_id": user.get("unionId", ""),
        "active": True,
        "enrolled_at": date.today().isoformat(),
    })
    _save_users(doc)

    print(f"\n[OK] Da enroll '{name}'")
    print(f"     open_id  : {open_id}")
    print(f"     scope    : {user.get('scope', '(xem auth status)')[:80]}...")
    print(f"     ghi vao  : {USERS_FILE}")
    print(f"\nKiem tra minute cua nguoi nay:")
    print(f"     lark-cli minutes +search --participant-ids me --start "
          f"{date.today().isoformat()} --profile {args.profile} --json")


if __name__ == "__main__":
    main()
