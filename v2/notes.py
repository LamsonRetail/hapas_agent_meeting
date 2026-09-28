"""Biên bản .md của từng cuộc họp + quyền vật chất hoá + Drive (V3 YC2).

    publish(token): chốt ai được xem (note_grants) -> ghi .md local -> (bật
                    DRIVE_ENABLED) đẩy lên folder Drive của CHỦ, share im lặng
                    cho người dự + chuỗi quản lý.

Gọi ở đúng các điểm PHÁT: `orchestrator._broadcast_notice` (lần đầu / bản hiệu
chỉnh), `confirm.approve`, và làm mới khi tóm tắt từ nguyên văn về
(`orchestrator._base_record_held`, chỉ khi đã phát).

Quyết định của chủ hệ thống (16/09/2026, docs/V3_SPECS.md §C.2–C.3):
  - Base chỉ làm INDEX, file chi tiết nằm trên Drive trong space của chủ.
  - Quản lý các cấp (tới CEO) thấy biên bản của nhánh mình; KHÔNG nhánh loại trừ.
  - Đổi sếp thì quyền CŨ vẫn giữ -> quyền được CHỤP lúc phát, không tính lại.
  - Folder tạo sẵn cho mọi người, share im lặng; người mới tự có.

`.md` chỉ chứa BIÊN BẢN (tóm tắt/quyết định/việc cần làm/liên kết), KHÔNG chứa
transcript nguyên văn: bản nguyên văn vẫn đi đường kéo (người dự hỏi mới gửi) —
luật cũ "không tự đẩy transcript cho người chưa hỏi" giữ nguyên.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import config, db, jobstore, lark_api, org, summarize
from .models import MeetingMeta, Recap


def _now_ms() -> int:
    return int(time.time() * 1000)


def _slug(text: str) -> str:
    s = re.sub(r'[\\/:*?"<>|#\[\]]', "", text or "").strip(" .")
    return re.sub(r"\s+", " ", s)[:60] or "Cuoc hop"


def path_of(meta: MeetingMeta) -> Path:
    month = time.strftime("%Y-%m", time.localtime(meta.start)) if meta.start else "khong-ro"
    return (config.NOTES_DIR / (meta.owner_open_id or "khong-ro-chu") / month
            / f"{_slug(meta.title)}-{meta.minute_token}.md")


# ------------------------------------------------------------------ quyền


def _participants(meta: MeetingMeta) -> list[tuple[str, str, str]]:
    """(open_id, union_id, source) của chủ + người dự ĐÃ XÁC MINH.

    Cùng luật với `qa.viewers_index`: nguồn `calendar[near…]` (ghép chỉ vì gần
    giờ) KHÔNG được tính — đó là gốc sự cố lộ chéo 05/08/2026."""
    out: list[tuple[str, str, str]] = []
    if meta.owner_open_id:
        out.append((meta.owner_open_id, org.union_id_of(meta.owner_open_id), "owner"))
    if "calendar[near" not in (meta.participants_source or ""):
        for a in meta.attendees:
            oid = a.open_id or org.open_id_of(a.union_id)
            if oid:
                out.append((oid, a.union_id or org.union_id_of(oid), "attendee"))
    return out


def _name(open_id: str) -> str:
    row = db.conn().execute("SELECT name FROM org_edges WHERE open_id=?",
                            (open_id,)).fetchone()
    return (row["name"] if row else "") or open_id[:10]


def grant(token: str, meta: MeetingMeta) -> int:
    """Chụp quyền lúc phát. Chỉ THÊM, không bao giờ xoá. Trả số quyền mới."""
    rows: list[tuple] = []
    now = _now_ms()
    for oid, uid, src in _participants(meta):
        rows.append((token, oid, uid, src, now))
        chain = org.chain_up(oid)
        for i, boss in enumerate(chain):
            path = ">".join(_name(x) for x in reversed([oid] + chain[:i + 1]))
            rows.append((token, boss, org.union_id_of(boss), f"chain:{path}", now))
    with db.tx() as c:
        before = c.execute("SELECT COUNT(*) FROM note_grants WHERE minute_token=?",
                           (token,)).fetchone()[0]
        c.executemany("INSERT OR IGNORE INTO note_grants(minute_token, open_id,"
                      " union_id, source, granted_at) VALUES (?,?,?,?,?)", rows)
        after = c.execute("SELECT COUNT(*) FROM note_grants WHERE minute_token=?",
                          (token,)).fetchone()[0]
    return after - before


def grants_of(token: str) -> list[dict[str, Any]]:
    return [dict(r) for r in db.conn().execute(
        "SELECT * FROM note_grants WHERE minute_token=? ORDER BY granted_at", (token,))]


# -------------------------------------------------------------------- .md


def render(meta: MeetingMeta, recap: Recap, *, reviewed: bool | None,
           related: list[str] | None = None) -> str:
    from . import cards
    names = [a.name for a in meta.attendees if a.name]
    state = {True: "✅ Chủ trì đã duyệt",
             False: "⚠️ Chưa được chủ trì review",
             None: "—"}[reviewed]
    lines = [f"# {meta.title or '(không tiêu đề)'}", "",
             f"> 🕐 {cards._fmt_time_range(meta) or '?'} · Chủ trì: "
             f"{meta.owner_name or '?'} · Người dự: {', '.join(names) or '?'}",
             f"> Trạng thái: {state}", "", "## Tóm tắt", "",
             (recap.summary or "").strip() or "_(chưa có)_", "", "## Quyết định", ""]
    lines += [f"- {d}" for d in recap.decisions] or ["_(không có)_"]
    lines += ["", "## Việc cần làm", ""]
    for a in recap.action_items:
        bits = [a.task.strip()] + ([f"— {a.owner}"] if a.owner else []) + (
            [f"(hạn {a.due})"] if a.due else [])
        lines.append("- [ ] " + " ".join(bits))
    if not recap.action_items:
        lines.append("_(không có)_")
    lines += ["", "## Liên kết", ""]
    if meta.app_link:
        lines.append(f"- [Bản Minute trên Lark]({meta.app_link})")
    lines += [f"- {x}" for x in (related or [])]
    lines += ["", f"<!-- minute_token: {meta.minute_token} -->", ""]
    return "\n".join(lines)


def _related_lines(token: str) -> list[str]:
    try:
        from . import links
        return links.related_lines(token)
    except Exception:                          # noqa: BLE001 — liên kết là phần phụ
        return []


def get_file(token: str) -> dict[str, Any] | None:
    r = db.conn().execute("SELECT * FROM note_files WHERE minute_token=?",
                          (token,)).fetchone()
    return dict(r) if r else None


def publish(token: str) -> Path | None:
    """Chụp quyền + ghi .md + (tuỳ công tắc) đẩy Drive. Không ném ra ngoài phần Drive."""
    from . import confirm
    job = jobstore.get(token)
    if not job:
        return None
    meta = jobstore.meta_from_json(job["meta_json"])
    recap = confirm.current_recap(token) or summarize.placeholder("chưa có tóm tắt")
    row = confirm.get(token)
    reviewed = None if row is None else row["state"] == "confirmed"
    grant(token, meta)
    try:
        from . import links
        links.update(token)                   # V3 YC5 — trước khi render mục Liên kết
    except Exception as exc:                  # noqa: BLE001 — liên kết là phần phụ
        print(f"[links] {token} tính liên kết hỏng (bỏ qua): {exc}")
    path = path_of(meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(meta, recap, reviewed=reviewed,
                           related=_related_lines(token)), encoding="utf-8")
    with db.tx() as c:
        c.execute("INSERT INTO note_files(minute_token, local_path, updated_at)"
                  " VALUES (?,?,?) ON CONFLICT(minute_token) DO UPDATE SET"
                  " local_path=excluded.local_path, updated_at=excluded.updated_at",
                  (token, str(path), _now_ms()))
    if config.DRIVE_ENABLED and config.SEND_MODE:
        try:
            _push_drive(token, meta, path)
        except lark_api.LarkError as exc:
            # Drive hỏng không làm hỏng việc phát: bản local vẫn là nguồn, lần
            # publish sau (hoặc `v2 notes-sync`) đẩy lại.
            print(f"[notes] {token} đẩy Drive hỏng (bản local vẫn có): {exc}")
    return path


# ------------------------------------------------------------------ Drive


def space_of(open_id: str, name: str = "", *, create: bool = True) -> str:
    """Folder Drive của một người; tạo + share IM LẶNG nếu chưa có."""
    row = db.conn().execute("SELECT folder_token FROM drive_spaces WHERE open_id=?",
                            (open_id,)).fetchone()
    if row or not create:
        return row["folder_token"] if row else ""
    root = config.DRIVE_ROOT_FOLDER or lark_api.drive_root_folder()
    folder = lark_api.drive_folder_create(
        f"Meeting Notes - {name or _name(open_id)}", root)
    lark_api.drive_member_add(folder, "folder", open_id, perm="edit", notify=False)
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO drive_spaces(open_id, folder_token, created_at)"
                  " VALUES (?,?,?)", (open_id, folder, _now_ms()))
    print(f"[notes] tạo folder Drive cho {name or open_id}")
    return folder


def _push_drive(token: str, meta: MeetingMeta, path: Path) -> None:
    # ponytail: mỗi lần nội dung đổi là một file mới (link đổi, Base cập nhật
    # theo). Muốn link cố định thì import thành Lark Doc và sửa tại chỗ.
    folder = space_of(meta.owner_open_id, meta.owner_name)
    new = lark_api.drive_upload(path, folder)
    old = (get_file(token) or {}).get("drive_token")
    if old and old != new:
        try:
            lark_api.drive_file_delete(old)
        except lark_api.LarkError as exc:
            print(f"[notes] {token} xoá bản Drive cũ hỏng (bỏ qua): {exc}")
    shared = []
    for g in grants_of(token):
        if g["open_id"] == meta.owner_open_id:
            continue                            # chủ thấy qua folder của mình
        try:
            lark_api.drive_member_add(new, "file", g["open_id"], perm="view",
                                      notify=False)
            shared.append(g["open_id"])
        except lark_api.LarkError as exc:
            print(f"[notes] {token} share cho {g['open_id'][:12]}… hỏng: {exc}")
    url = lark_api.drive_file_url(new)
    with db.tx() as c:
        c.execute("UPDATE note_files SET drive_token=?, drive_url=?, shared=?,"
                  " updated_at=? WHERE minute_token=?",
                  (new, url, json.dumps(shared), _now_ms(), token))


def provision(*, dry_run: bool = True) -> list[str]:
    """Folder cho MỌI người trong cây tổ chức chưa có. Trả tên những người (sẽ) được tạo."""
    todo = [p for p in org.people() if not space_of(p["open_id"], create=False)]
    if not dry_run:
        for p in todo:
            try:
                space_of(p["open_id"], p["name"])
            except lark_api.LarkError as exc:
                print(f"[notes] tạo folder cho {p['name']} hỏng: {exc}")
    return [p["name"] or p["open_id"] for p in todo]


def backfill_grants(open_id: str, *, since_ms: int = 0,
                    dry_run: bool = True) -> list[str]:
    """Cấp LÙI quyền các cuộc CŨ cho một quản lý (vd sếp mới tiếp quản đội).

    Hành động TƯỜNG MINH của admin (quyết định 16/09/2026): mặc định sếp mới chỉ
    thấy cuộc từ khi tiếp quản. Cấp theo cây quản lý HIỆN TẠI."""
    hits: list[str] = []
    for f in db.conn().execute("SELECT minute_token FROM note_files").fetchall():
        token = f["minute_token"]
        job = jobstore.get(token)
        if not job or (job.get("start_ts") or 0) < since_ms:
            continue
        meta = jobstore.meta_from_json(job["meta_json"])
        for oid, _uid, _src in _participants(meta):
            if open_id in org.chain_up(oid):
                hits.append(token)
                break
    if not dry_run:
        with db.tx() as c:
            c.executemany("INSERT OR IGNORE INTO note_grants(minute_token, open_id,"
                          " union_id, source, granted_at) VALUES (?,?,?,?,?)",
                          [(t, open_id, org.union_id_of(open_id),
                            f"backfill:{_name(open_id)}", _now_ms()) for t in hits])
        if config.DRIVE_ENABLED and config.SEND_MODE:
            for t in hits:
                ft = (get_file(t) or {}).get("drive_token")
                if ft:
                    try:
                        lark_api.drive_member_add(ft, "file", open_id, perm="view",
                                                  notify=False)
                    except lark_api.LarkError as exc:
                        print(f"[notes] backfill share {t} hỏng: {exc}")
    return hits
