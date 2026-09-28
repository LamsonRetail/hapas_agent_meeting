"""Cây tổ chức từ Lark Contact (V3 YC2) — nguồn DUY NHẤT của quyền "quản lý thấy
biên bản của nhánh mình".

Chỉ tin trường `leader_user_id` của Lark Contact (tenant token), KHÔNG suy từ
tên, lịch hay Base — cùng bài học sự cố lộ chéo 05/08/2026: nguồn quyền phải là
dữ liệu có thẩm quyền.
"""

from __future__ import annotations

import time

from . import db, lark_api

MAX_DEPTH = 10                     # trần độ sâu chuỗi quản lý (chặn dữ liệu vòng)


def sync(*, dry_run: bool = True) -> dict[str, int]:
    """Kéo toàn bộ nhân sự + leader về `org_edges`. Trả {users, changed}.

    Người đã nghỉ (`is_resigned`) bị bỏ khỏi cây — nhưng quyền đã cấp cho họ
    (`note_grants`) không bị xoá: không còn enroll thì cũng không còn đường hỏi.
    """
    users: dict[str, dict] = {}
    for dept in lark_api.contact_departments():
        for u in lark_api.contact_users_in(dept):
            oid = u.get("open_id") or ""
            if oid and not (u.get("status") or {}).get("is_resigned"):
                users[oid] = u
    have = {r["open_id"]: r["leader_open_id"] for r in
            db.conn().execute("SELECT open_id, leader_open_id FROM org_edges")}
    changed = sum(1 for oid, u in users.items()
                  if have.get(oid, "∅") != (u.get("leader_user_id") or ""))
    if not dry_run:
        now = int(time.time() * 1000)
        with db.tx() as c:
            c.execute("DELETE FROM org_edges")
            c.executemany(
                "INSERT INTO org_edges(open_id, union_id, name, leader_open_id,"
                " synced_at) VALUES (?,?,?,?,?)",
                [(oid, u.get("union_id") or "", u.get("name") or "",
                  u.get("leader_user_id") or "", now) for oid, u in users.items()])
    return {"users": len(users), "changed": changed}


def chain_up(open_id: str) -> list[str]:
    """[quản lý trực tiếp, quản lý của quản lý, …] tới đỉnh. Chặn vòng + trần sâu."""
    out: list[str] = []
    cur = open_id
    for _ in range(MAX_DEPTH):
        row = db.conn().execute("SELECT leader_open_id FROM org_edges WHERE open_id=?",
                                (cur,)).fetchone()
        nxt = (row["leader_open_id"] if row else "") or ""
        if not nxt or nxt == open_id or nxt in out:
            break
        out.append(nxt)
        cur = nxt
    return out


def open_id_of(union_id: str) -> str:
    row = db.conn().execute("SELECT open_id FROM org_edges WHERE union_id=?",
                            (union_id,)).fetchone()
    return row["open_id"] if row else ""


def union_id_of(open_id: str) -> str:
    row = db.conn().execute("SELECT union_id FROM org_edges WHERE open_id=?",
                            (open_id,)).fetchone()
    return (row["union_id"] if row else "") or ""


def people() -> list[dict]:
    return [dict(r) for r in db.conn().execute(
        "SELECT open_id, union_id, name FROM org_edges ORDER BY name")]
